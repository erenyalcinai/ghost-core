"""Eller (Faz 6): telefona yazma görev kuyruğu + coğrafi tetik bölgeleri."""
import json
import os
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from tests import common  # noqa: F401  (yolları geçici dizine alır, GHOST_TEST=1)

from ghost import api, bolgeler, duyular, gorevler, kokpit  # noqa: E402


def _istek(base, method, path, key=None, did=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if key:
        ts = str(int(time.time()))
        nonce = os.urandom(8).hex()
        h.update({"X-Ghost-Device": did, "X-Ghost-Ts": ts, "X-Ghost-Nonce": nonce,
                  "X-Ghost-Sig": kokpit.imza(key, ts, nonce, method, path, data or b"")})
    req = urllib.request.Request(base + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def _yarin(saat="10:00"):
    return time.strftime("%Y-%m-%d ", time.localtime(time.time() + 86400)) + saat


class Kuyruk(unittest.TestCase):
    def setUp(self):
        c = gorevler._db()
        c.execute("DELETE FROM kokpit_gorevler")
        c.commit()
        c.close()

    def test_hatirlatici_ekle_ve_sonuclandir(self):
        gid, hata = gorevler.ekle("hatirlatici", {"ad": "Fabrikayı ara", "ne_zaman": _yarin()})
        self.assertIsNone(hata)
        b = gorevler.bekleyenler()
        self.assertEqual([x["id"] for x in b], [gid])
        self.assertEqual(b[0]["veri"]["ad"], "Fabrikayı ara")
        self.assertGreater(b[0]["veri"]["ts"], time.time())
        g = gorevler.sonuclandir(gid, True, "", "EK-123", "cihazA")
        self.assertEqual((g["durum"], g["yerel_id"]), ("tamam", "EK-123"))
        self.assertEqual(gorevler.bekleyenler(), [])

    def test_ayni_gorev_iki_kez_sonuclanmaz(self):
        """Sessiz push + öne gelme + arka plan aynı anda tetiklenebilir; aynı
        hatırlatıcının telefona iki kez yazılmasını sunucu engeller."""
        gid, _ = gorevler.ekle("hatirlatici", {"ad": "tek", "ne_zaman": _yarin()})
        self.assertIsNotNone(gorevler.sonuclandir(gid, True, "", "A"))
        self.assertIsNone(gorevler.sonuclandir(gid, True, "", "B"))   # ikincisi yutulur

    def test_gecmis_zaman_reddedilir(self):
        gid, hata = gorevler.ekle("hatirlatici", {"ad": "dün", "ne_zaman": "2020-01-01 10:00"})
        self.assertIsNone(gid)
        self.assertIn("geçmiş", hata)

    def test_anlasilmayan_zaman_ve_bos_baslik(self):
        self.assertIn("zaman anlaşılmadı", gorevler.ekle("hatirlatici", {"ad": "x", "ne_zaman": "bir ara"})[1])
        self.assertIn("başlık boş", gorevler.ekle("hatirlatici", {"ad": "  ", "ne_zaman": _yarin()})[1])
        self.assertIn("tür", gorevler.ekle("sarki", {"ad": "x", "ne_zaman": _yarin()})[1])

    def test_goreceli_zaman(self):
        gid, hata = gorevler.ekle("hatirlatici", {"ad": "yakın", "ne_zaman": "30dk"})
        self.assertIsNone(hata)
        ts = gorevler.bekleyenler()[0]["veri"]["ts"]
        self.assertAlmostEqual(ts, time.time() + 1800, delta=90)

    def test_etkinlik_bitis_varsayilani(self):
        gorevler.ekle("etkinlik", {"ad": "Toplantı", "bas": _yarin("14:00")})
        v = gorevler.bekleyenler()[0]["veri"]
        self.assertEqual(v["bit_ts"] - v["ts"], 3600)      # bitiş yoksa 1 saat

    def test_iptal_ve_liste(self):
        gid, _ = gorevler.ekle("hatirlatici", {"ad": "iptallik", "ne_zaman": _yarin()})
        self.assertTrue(gorevler.iptal(gid))
        self.assertFalse(gorevler.iptal(gid))              # ikinci kez yok
        self.assertIn("iptallik", gorevler.metin())


class Bolge(unittest.TestCase):
    def setUp(self):
        for ad in (bolgeler.DOSYA, duyular.DOSYA):
            y = os.path.join(os.environ["GHOST_OPS"], ad)
            if os.path.exists(y):
                os.remove(y)

    def _konum(self, enlem=39.9274, boylam=44.03475):
        duyular.yaz({"konum": {"enlem": enlem, "boylam": boylam, "dogruluk": 20,
                               "yer": "Taşra Merkez"}})

    def test_buradan_kur_ve_tetikle(self):
        self._konum()
        b, hata = bolgeler.buradan("fabrika", 300)
        self.assertIsNone(hata)
        self.assertEqual((b["ad"], b["yaricap"]), ("fabrika", 300))
        m = bolgeler.olay("fabrika", "giris")
        self.assertIn("GİRDİ", m)
        self.assertIn("uzatma", m)                     # Ghost'a davranış talimatı iliştirilir

    def test_soguma_ikinci_tetigi_yutar(self):
        self._konum()
        bolgeler.buradan("ev")
        self.assertIsNotNone(bolgeler.olay("ev", "giris"))
        self.assertIsNone(bolgeler.olay("ev", "giris"))  # kapı önünde volta = tek uyandırma

    def test_kapali_yon_ve_bilinmeyen_bolge(self):
        self._konum()
        bolgeler.buradan("depo")                        # cikis varsayılan KAPALI
        self.assertIsNone(bolgeler.olay("depo", "cikis"))
        self.assertIsNone(bolgeler.olay("yokbole", "giris"))

    def test_konum_yoksa_kurulmaz(self):
        b, hata = bolgeler.buradan("hiçbir yer")
        self.assertIsNone(b)
        self.assertIn("konum gelmemiş", hata)

    def test_bayat_konumla_kurulmaz(self):
        self._konum()
        d = duyular.oku()
        d["konum"]["ts"] = time.time() - 86400
        duyular._yaz_dosya(d)
        self.assertIn("bayat", bolgeler.buradan("yanlış yer")[1])

    def test_yaricap_tabani_ve_tavan(self):
        self._konum()
        self.assertEqual(bolgeler.buradan("kücük", 10)[0]["yaricap"], bolgeler.TABAN_YARICAP)
        self.assertEqual(bolgeler.buradan("kocaman", 99999)[0]["yaricap"], 20000)

    def test_ayni_ad_ustune_yazar_seq_artar(self):
        self._konum()
        bolgeler.buradan("ev")
        s1 = bolgeler.oku()["seq"]
        bolgeler.buradan("ev", 500)
        self.assertEqual(len(bolgeler.liste()), 1)
        self.assertEqual(bolgeler.liste()[0]["yaricap"], 500)
        self.assertGreater(bolgeler.oku()["seq"], s1)

    def test_tavan(self):
        self._konum()
        for i in range(bolgeler.TAVAN):
            self.assertIsNone(bolgeler.buradan("b%d" % i)[1])
        self.assertIn("tavan", bolgeler.buradan("fazlalık")[1])

    def test_icinde_ve_mesafe(self):
        self._konum()
        bolgeler.buradan("burası", 300)
        self.assertIn("burası", bolgeler.icinde())
        self._konum(enlem=39.9500)                       # ~2.5 km kuzey
        self.assertEqual(bolgeler.icinde(), [])


class Uc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), api.H)
        cls.srv.daemon_threads = True
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        cls.did, cls.key = kokpit.cihaz_ekle("eller-test")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_gorev_cek_ve_sonuc_yaz(self):
        gid, _ = gorevler.ekle("hatirlatici", {"ad": "uçtan uca", "ne_zaman": _yarin()})
        st, j = _istek(self.base, "GET", "/v1/gorevler", self.key, self.did)
        self.assertEqual(st, 200)
        self.assertIn(gid, [g["id"] for g in j["gorevler"]])
        st, j = _istek(self.base, "POST", "/v1/gorevler/%d" % gid, self.key, self.did,
                       {"ok": True, "yerel_id": "EK-9"})
        self.assertEqual((st, j["durum"]), (200, "tamam"))
        st, j = _istek(self.base, "POST", "/v1/gorevler/%d" % gid, self.key, self.did, {"ok": True})
        self.assertTrue(j.get("yinelenen"))          # ikinci yazım yutulur

    def test_bolge_uclari(self):
        duyular.yaz({"konum": {"enlem": 39.9274, "boylam": 44.03475, "yer": "Taşra"}})
        bolgeler.buradan("uctest")
        st, j = _istek(self.base, "GET", "/v1/bolgeler", self.key, self.did)
        self.assertEqual(st, 200)
        self.assertIn("uctest", [b["ad"] for b in j["bolgeler"]])
        st, j = _istek(self.base, "POST", "/v1/bolge", self.key, self.did,
                       {"ad": "uctest", "olay": "giris"})
        self.assertEqual((st, j["ok"]), (200, True))
        st, j = _istek(self.base, "POST", "/v1/bolge", self.key, self.did,
                       {"ad": "yok böyle", "olay": "giris"})
        self.assertTrue(j.get("yutuldu"))            # bilinmeyen bölge sessiz

    def test_gorev_id_sayi_degilse_400(self):
        st, _ = _istek(self.base, "POST", "/v1/gorevler/abc", self.key, self.did, {"ok": True})
        self.assertEqual(st, 400)


if __name__ == "__main__":
    unittest.main()
