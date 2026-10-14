"""Köprü ↔ kokpit rayı: kuyruktaki mesaj/karar köprüde Telegram'dan gelmiş gibi işlenir,
send() dökümü besler, kararlar tek yoldan (_karar_uygula) geçer."""
import os
import sys
import unittest

from tests import common  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops"))
import telegram_bridge as tb  # noqa: E402
from ghost import kokpit  # noqa: E402


class SahteMgr:
    chat_id = 1
    def __init__(self, res=None):
        self.res, self.cozulen, self.sohbet = res, [], []
    def resolve_pending(self, pid, ok):
        self.cozulen.append((pid, ok))
        return self.res
    def chat(self, text, spoken=False):
        self.sohbet.append(text)


class KopruRayi(unittest.TestCase):
    def setUp(self):
        self.gonderilen = []
        self._send, self._api = tb.send, tb.api
        tb.api = lambda *a, **k: self.gonderilen.append(("api", a, k)) or {}
        kokpit.inbox_al(); kokpit.kararlar_al()

    def tearDown(self):
        tb.send, tb.api = self._send, self._api

    def test_send_dokume_yazar(self):
        son = kokpit.outbox_son()
        tb.send(1, "selam dünya", buttons=[[("✅ Onayla", "ok:p1")]])
        rows = kokpit.outbox_oku(son)
        self.assertEqual(rows[-1]["metin"], "selam dünya")
        self.assertEqual(rows[-1]["kaynak"], "ghost")
        self.assertEqual(rows[-1]["butonlar"][0][0]["veri"], "ok:p1")

    def test_karar_uygula_tek_yol(self):
        m = SahteMgr(res="missing")
        tb._karar_uygula(m, "p1", True, via="kokpit")
        self.assertEqual(m.cozulen, [("p1", True)])
        m = SahteMgr(res=None)
        tb._karar_uygula(m, "p2", False)
        self.assertEqual(m.cozulen, [("p2", False)])

    def test_check_kokpit_kuyrugu_isler(self):
        m = SahteMgr(res=None)
        kokpit.karar_kuyrukla("p9", False, "cihaz")
        kokpit.mesaj_kuyrukla("merhaba ghost", "cihaz")
        kokpit.mesaj_kuyrukla("/jobs", "cihaz")
        yollar = []
        eski_rt, eski_hc = tb.route_text, tb.handle_command
        tb.route_text = lambda t, mgr, spoken=False: yollar.append(("sohbet", t))
        tb.handle_command = lambda t, mgr: yollar.append(("komut", t))
        try:
            tb.check_kokpit(m)
        finally:
            tb.route_text, tb.handle_command = eski_rt, eski_hc
        self.assertEqual(m.cozulen, [("p9", False)])
        self.assertEqual(yollar, [("sohbet", "merhaba ghost"), ("komut", "/jobs")])
        self.assertEqual(kokpit.inbox_al(), [])  # tüketildi

    def test_check_kokpit_freeze_kapisi(self):
        m = SahteMgr()
        tb.governance.freeze()
        try:
            kokpit.mesaj_kuyrukla("deploy yap", "cihaz")
            yollar = []
            eski_rt = tb.route_text
            tb.route_text = lambda t, mgr, spoken=False: yollar.append(t)
            try:
                tb.check_kokpit(m)
            finally:
                tb.route_text = eski_rt
            self.assertEqual(yollar, [])
            self.assertTrue(any("FREEZE" in str(g) for g in self.gonderilen))
        finally:
            tb.governance.unfreeze()

    def test_komut_listesinde_cihaz(self):
        adlar = [k for k, _ in tb.BOT_COMMANDS]
        self.assertIn("cihaz", adlar); self.assertIn("cihaz_iptal", adlar)


if __name__ == "__main__":
    unittest.main()


class CokluGonderi(unittest.TestCase):
    """Aynı gönderideki N dosya tek sohbet turu olur (2026-09-04)."""

    def setUp(self):
        tb._GRUPLAR.clear()
        kokpit.inbox_al(); kokpit.kararlar_al()

    def test_grup_tamamlaninca_tek_tur(self):
        m = SahteMgr(res=None)
        sohbetler = []
        m.chat = lambda t: sohbetler.append(t)
        kokpit.mesaj_kuyrukla("[Paylaşım: görsel] /tmp/a.jpg", "c", yol="/tmp/a.jpg", tur="gorsel",
                              ad="a.jpg", not_="", grup="g1", sira=1, toplam=2)
        tb.check_kokpit(m)
        self.assertEqual(sohbetler, [])                       # ilk parça: bekler
        kokpit.mesaj_kuyrukla("[Paylaşım: görsel] /tmp/b.jpg", "c", yol="/tmp/b.jpg", tur="gorsel",
                              ad="b.jpg", not_="ikisini kıyasla", grup="g1", sira=2, toplam=2)
        tb.check_kokpit(m)
        self.assertEqual(len(sohbetler), 1)
        self.assertIn("2 görsel gönderdi", sohbetler[0])
        self.assertIn("1) /tmp/a.jpg", sohbetler[0])
        self.assertIn("2) /tmp/b.jpg", sohbetler[0])
        self.assertIn("ikisini kıyasla", sohbetler[0])
        self.assertEqual(tb._GRUPLAR, {})

    def test_eksik_grup_zaman_asimi(self):
        m = SahteMgr(res=None)
        sohbetler = []
        m.chat = lambda t: sohbetler.append(t)
        kokpit.mesaj_kuyrukla("x", "c", yol="/tmp/a.pdf", tur="belge", ad="a.pdf", grup="g2", sira=1, toplam=3)
        tb.check_kokpit(m)
        self.assertEqual(sohbetler, [])
        tb._GRUPLAR["g2"]["ts"] -= tb.GRUP_BEKLE + 1          # parça gelmedi, süre doldu
        tb.check_kokpit(m)
        self.assertEqual(len(sohbetler), 1)
        self.assertIn("1 dosya gönderdi", sohbetler[0])
        self.assertIn("(ad: a.pdf)", sohbetler[0])

    def test_tek_dosya_grup_yolu_kullanmaz(self):
        m = SahteMgr(res=None)
        sohbetler = []
        m.chat = lambda t: sohbetler.append(t)
        kokpit.mesaj_kuyrukla("x", "c", yol="/tmp/a.jpg", tur="gorsel", ad="a.jpg", grup="g3", sira=1, toplam=1)
        tb.check_kokpit(m)
        self.assertEqual(len(sohbetler), 1)
        self.assertIn("bir görsel gönderdi", sohbetler[0])
