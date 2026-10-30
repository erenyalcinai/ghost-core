"""ghost/sohbet_sunucu + sohbet_istemci (Faz 3.2): süreç köprüden bağımsız — tur, kopan istemci, yeniden bağlanma,
bekleyen sonuç, geç sonuç, meşgul reddi, kapat/köprü kapanışı ayrımı, kalici_sec seçimi, _run_kalici uyumu."""
import json
import os
import tempfile
import threading
import time
import unittest

from tests import common  # noqa: F401

from ghost import kalici_oturum, motor, sohbet_istemci, sohbet_sunucu
from tests.test_kalici_oturum import _sahte_claude


class TestSohbetSunucu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bin = _sahte_claude()
        cls.tmp = tempfile.mkdtemp(prefix="sohbet-")
        kalici_oturum.DURUM_DOSYASI = os.path.join(cls.tmp, "kalici.json")
        cls.yol = os.path.join(cls.tmp, "s.sock")
        cls.gunluk = []
        cls.srv = sohbet_sunucu.SohbetSunucu(claude_bin=cls.bin, ekstra_args=[], cwd=cls.tmp,
                                             env=dict(os.environ), bosta_sn=0, log_fn=cls.gunluk.append)
        cls.t = threading.Thread(target=cls.srv.servis, args=(cls.yol,), daemon=True)
        cls.t.start()
        for _ in range(200):           # dinlemeye başlayana dek (dosya bind'de doğar, listen sonra; yük altında geç)
            if sohbet_istemci.sunucu_var(cls.yol):
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("sahte sohbet sunucusu 10 sn'de dinlemeye başlamadı")

    @classmethod
    def tearDownClass(cls):
        cls.srv.durdur()
        cls.t.join(3)

    def setUp(self):
        self.srv.kalici.kapat()
        with self.srv.kilit:
            self.srv.tur = None
            self.srv.gec_sonuclar = []
        self.ist = sohbet_istemci.KaliciIstemci(log=self.gunluk.append, yol=self.yol)

    def _olaylar(self):
        o = []
        return o, o.append

    def test_durum_ve_sunucu_var(self):
        self.assertTrue(sohbet_istemci.sunucu_var(self.yol))
        d = sohbet_istemci.sunucu_durumu(self.yol)
        self.assertFalse(d["canli"])
        self.assertFalse(d["mesgul"])
        self.assertIsNone(sohbet_istemci.sunucu_durumu(os.path.join(self.tmp, "yok.sock")))

    def test_iki_tur_ayni_surecte_ve_olaylar(self):
        self.assertFalse(self.ist.uygun("m1", "sid-1"))
        self.ist.baslat("m1", "sid-1", False)
        self.assertTrue(self.ist.uygun("m1", "sid-1"))
        self.assertTrue(self.ist.canli())
        o, kaydet = self._olaylar()
        sonuc, kuyruk, err, za, oldu = self.ist.tur("selam", 10, on_event=kaydet)
        self.assertFalse(za or oldu, err)
        r1 = json.loads(sonuc)
        self.assertIn("cevap 1: selam", r1["result"])
        self.assertTrue(any('"init"' in s for s in o), "sentetik init olayı akmalı")
        self.assertTrue(any('"assistant"' in s for s in o))
        sonuc2, *_ = self.ist.tur("ikinci", 10)
        r2 = json.loads(sonuc2)
        self.assertIn("cevap 2: ikinci", r2["result"])
        self.assertEqual(r1["result"].split("pid ")[1], r2["result"].split("pid ")[1], "aynı süreç")
        self.assertFalse(sohbet_istemci.sunucu_durumu(self.yol)["bekleyen"])

    def test_istemci_koparsa_tur_surer_ve_yeniden_baglanir(self):
        """Köprü restart senaryosu: tur başlar, istemci bağlantıyı koparır; sunucu turu bitirir, sonucu bekletir;
        yeni istemci aynı tur_id ile BAĞLANIR, sonucu alır (yeniden koşma yok: cevap sayacı 1'de kalır)."""
        self.ist.baslat("m1", "sid-2", False)
        tid = sohbet_istemci.tur_kimligi()
        # ham bağlantı: tur isteğini yaz, ilk olayı oku, kopar (istemci süreci ölmüş gibi)
        with sohbet_istemci._bagla(self.yol, 10) as f:
            f.write((json.dumps({"op": "tur", "prompt": "uyu-kisa", "limit": 10, "tur_id": tid}) + "\n").encode())
            f.flush()
            ilk = f.readline()
            self.assertIn("olay", json.loads(ilk))
        # sunucu: tur sürüyor ya da bitti ama teslim edilmedi
        for _ in range(120):                      # sahte claude 1,5 sn uyur; yük payı
            d = sohbet_istemci.sunucu_durumu(self.yol)
            if d["bekleyen"]:
                break
            time.sleep(0.05)
        self.assertEqual(d["tur_id"], tid)
        self.assertTrue(d["bekleyen"] or d["mesgul"])
        yeni = sohbet_istemci.KaliciIstemci(log=self.gunluk.append, yol=self.yol)
        self.assertTrue(yeni.devralinabilir(tid))
        self.assertFalse(yeni.devralinabilir("baska"))
        yeni.bagla_id = tid
        self.assertTrue(yeni.uygun("farkli-model", "farkli-sid"), "devralma kipinde yeniden başlatma yok")
        sonuc, kuyruk, err, za, oldu = yeni.tur("(gönderilmez)", 10)
        self.assertFalse(za or oldu, err)
        self.assertIn("cevap 1: uyu-kisa", json.loads(sonuc)["result"])
        self.assertIsNone(yeni.bagla_id, "kimlik tüketildi")
        d = sohbet_istemci.sunucu_durumu(self.yol)
        self.assertFalse(d["bekleyen"])
        self.assertFalse(yeni.devralinabilir(tid), "teslim edilen tur bir daha devralınmaz")
        # ikinci bağlanma denemesi: bağlanacak tur yok
        yeni.bagla_id = tid
        sonuc, _k, err, _za, oldu = yeni.tur("x", 5)
        self.assertIsNone(sonuc)
        self.assertTrue(oldu)
        self.assertIn("bağlanacak tur yok", err)

    def test_kopru_tur_kimligi_sunucuya_gecer(self):
        self.ist.baslat("m1", "sid-7", False)
        self.ist.tur_id = "kopru-123"
        self.ist.tur("hey", 10)
        d = sohbet_istemci.sunucu_durumu(self.yol)
        self.assertEqual(d["tur_id"], "kopru-123")
        self.assertIsNone(self.ist.tur_id, "kimlik tek kullanımlık")

    def test_mesgulken_ikinci_tur_reddedilir(self):
        self.ist.baslat("m1", "sid-3", False)
        tid = sohbet_istemci.tur_kimligi()
        sonuc = {}

        def _uzun():
            sonuc["r"] = self.ist.tur("uyu", 3, on_event=None)   # sahte claude 30 sn uyur → 3 sn'de zaman aşımı
        th = threading.Thread(target=_uzun, daemon=True)
        th.start()
        for _ in range(60):                       # meşgul olana dek (yük altında gecikebilir)
            d = sohbet_istemci.sunucu_durumu(self.yol)
            if d and d["mesgul"]:
                break
            time.sleep(0.05)
        self.assertTrue(d["mesgul"])
        ikinci = sohbet_istemci.KaliciIstemci(yol=self.yol)
        s2, _k, err, _za, oldu = ikinci.tur("araya gir", 5)
        self.assertIsNone(s2)
        self.assertTrue(oldu)
        self.assertIn("meşgul", err)
        th.join(10)
        r = sonuc["r"]
        self.assertTrue(r[3], "zaman aşımı bayrağı")
        self.assertFalse(self.ist.canli(), "zaman aşımı süreci öldürür (eski davranış)")

    def test_gec_sonuc_istemciye_ulasir(self):
        self.ist.baslat("m1", "sid-4", False)
        self.ist.tur("bir", 10)
        # sahte claude kendiliğinden ikinci result basmaz; sunucu kuyruğuna doğrudan bırakıp yolu sınarız
        self.srv._gec_sonuc(json.dumps({"type": "result", "result": "geç"}))
        alinan = []
        self.ist.on_gec_sonuc = alinan.append
        s = self.ist.bosta_oku()
        self.assertEqual(len(s), 1)
        self.assertEqual(json.loads(alinan[0])["result"], "geç")
        self.assertEqual(self.ist.bosta_oku(), [])

    def test_kapat_ayrimi(self):
        self.ist.baslat("m1", "sid-5", False)
        self.assertTrue(self.ist.canli())
        self.ist.kapat("köprü kapanıyor")
        self.assertTrue(self.ist.canli(), "köprü kapanışı sunucudaki süreci ÖLDÜRMEZ")
        self.ist.kapat("FREEZE")
        self.assertFalse(self.ist.canli(), "FREEZE süreci kapatır")

    def test_run_kalici_istemciyle(self):
        """motor._run_kalici istemciyi KaliciOturum sanır: başlat + tur + sonuç çözümü."""
        kullanim = []
        res, sid, err = motor._run_kalici(self.ist, "merhaba", "m1", "sid-6", False, 10, kullanim.append, None)
        self.assertFalse(err, res)
        self.assertIn("cevap 1: merhaba", res)
        self.assertEqual(sid, "sid-6")
        self.assertTrue(kullanim)

    def test_kalici_sec(self):
        g = []
        secim = sohbet_istemci.kalici_sec
        with _soket(self.yol):
            k = secim("/bin/x", [], "/tmp", {}, g.append)
        self.assertIsInstance(k, sohbet_istemci.KaliciIstemci)
        self.assertTrue(any("ghost-sohbet" in s for s in g))
        with _soket(os.path.join(self.tmp, "yok2.sock")):
            k2 = secim("/bin/x", [], "/tmp", {}, g.append, yerel=lambda *a, **kw: ("yerel", a, kw))
        self.assertEqual(k2[0], "yerel")

    def test_soket_yolu_testte_gecici(self):
        self.assertTrue(sohbet_sunucu.soket_yolu().startswith(os.environ["GHOST_OPS"]))


class _soket:
    def __init__(self, yol):
        self.yol = yol

    def __enter__(self):
        self.eski = os.environ.get("GHOST_SOHBET_SOKET")
        os.environ["GHOST_SOHBET_SOKET"] = self.yol

    def __exit__(self, *a):
        if self.eski is None:
            os.environ.pop("GHOST_SOHBET_SOKET", None)
        else:
            os.environ["GHOST_SOHBET_SOKET"] = self.eski


if __name__ == "__main__":
    unittest.main()
