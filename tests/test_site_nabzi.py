"""Nabız "Siteler" bölümü: trafik.oku (state yok/var, erişim birleşimi, bayatlık), health alanı,
sahne tablosu. Ağa çıkmaz — olc() burada çağrılmaz."""
import json
import os
import tempfile
import time
import unittest

os.environ.setdefault("GHOST_TEST", "1")

from ghost import config, trafik


class Trafik(unittest.TestCase):
    # config.OPS'u öznitelik olarak ez (isnabzi testleriyle aynı yol): discover'da başka bir
    # test config.OPS'a gerçek öznitelik bırakınca GHOST_OPS ortam değişkeni artık okunmuyor.
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.eski = config.OPS
        config.OPS = self.d

    def tearDown(self):
        config.OPS = self.eski

    def _yaz(self, ad, veri):
        with open(os.path.join(self.d, ad), "w", encoding="utf-8") as f:
            json.dump(veri, f)

    def test_olcum_yoksa_yalniz_erisim(self):
        self._yaz("uptime_state.json", {"https://example.com": True, "https://app-a.example.com": False})
        o = trafik.oku()
        self.assertFalse(o["var"])
        adlar = {s["ad"]: s for s in o["siteler"]}
        self.assertTrue(adlar["example.com"]["ok"])
        self.assertFalse(adlar["app-a.example.com"]["ok"])
        self.assertIsNone(adlar["app-b.example.com"]["ok"])      # uptime'da yok → bilinmiyor
        self.assertNotIn("ziyaret_7g", adlar["example.com"])

    def test_olcum_varsa_sayilar_ve_kaynak(self):
        self._yaz("uptime_state.json", {"https://example.com": True,
                                        "https://owner-card.example/c/x/": True})
        self._yaz(trafik.STATE, {
            "at": "2026-09-06 01:38:50", "ts": int(time.time()) - 120, "gun": 7,
            "siteler": {"example.com": {"g7": 11, "k7": 8, "g1": 5, "k1": 3, "sahip7": 11, "bot7": 1,
                                      "kaynaklar": {"l.instagram.com": 3, "www.google.com": 2, "x.com": 1}},
                        "owner.example": {"g7": 0, "k7": 0, "g1": 0, "k1": 0, "sahip7": 0, "bot7": 1, "kaynaklar": {}}},
            "toplam_g7": 11, "toplam_g1": 5, "imza_uyari": 0})
        o = trafik.oku()
        self.assertTrue(o["var"])
        self.assertEqual((o["toplam_7g"], o["toplam_24s"], o["yas_dk"], o["bayat"]), (11, 5, 2, False))
        s = {x["ad"]: x for x in o["siteler"]}
        self.assertEqual((s["example.com"]["ziyaret_7g"], s["example.com"]["kimlik_7g"], s["example.com"]["ziyaret_24s"]),
                         (11, 8, 5))
        self.assertEqual(s["example.com"]["kaynak"], "l.instagram.com · www.google.com")   # en çok 2
        self.assertEqual(s["owner.example"]["ziyaret_7g"], 0)
        self.assertEqual(s["app-a.example.com"]["ziyaret_7g"], 0)          # state'te yok → sıfır, satır kaybolmaz
        self.assertEqual([x["ad"] for x in o["siteler"]][:2], ["example.com", "ghostinprod.com"])  # sıra sabit

    def test_bayat_olcum(self):
        self._yaz(trafik.STATE, {"at": "2026-09-05 20:00:00", "ts": int(time.time()) - 4 * 3600,
                                 "gun": 7, "siteler": {}, "toplam_g7": 0, "toplam_g1": 0})
        o = trafik.oku()
        self.assertTrue(o["bayat"])
        self.assertGreaterEqual(o["yas_dk"], 239)

    def test_tazele_taze_ise_olcmez(self):
        self._yaz(trafik.STATE, {"at": "x", "ts": int(time.time()) - 60, "gun": 7, "siteler": {}})
        self.assertFalse(trafik.tazele())           # ağa çıkmadan döner


class NabizAlani(unittest.TestCase):
    def test_health_site_nabzi(self):
        from ghost import health
        n = health.nabiz()
        self.assertIn("site_nabzi", n)
        self.assertIn("var", n["site_nabzi"])
        self.assertIsInstance(n["site_nabzi"]["siteler"], list)
        # Uzuv bağlantısı canlı bloğu (2026-09-06): Mac/telefon bool, tünel notu, damga.
        b = n["baglanti"]
        self.assertEqual(set(b) >= {"mac", "telefon", "tunel", "ts"}, True)
        self.assertIsInstance(b["mac"], bool)
        self.assertIsInstance(b["telefon"], bool)

    def test_iphone_dosya_yedegi_testte(self):
        # GHOST_TEST'te yoklama yapılmaz (None) → dosya yoksa 'bilinmiyor', çökmez.
        from ghost import health
        d = health._iphone()
        self.assertIn(d["durum"], ("iyi", "uyari", "bilinmiyor"))

    def test_sahne_tablosu(self):
        from ghost import sahne
        t = sahne._siteler_tablosu({"site_nabzi": {"var": True, "toplam_7g": 3, "toplam_24s": 1, "siteler": [
            {"ad": "a.b", "ok": True, "ziyaret_7g": 3, "ziyaret_24s": 1, "kaynak": "g"}]}})
        self.assertEqual(t["sutunlar"], ["site", "durum", "7g", "24s", "kaynak"])
        self.assertEqual(t["satirlar"], [["a.b", "iyi", 3, 1, "g"]])
        t2 = sahne._siteler_tablosu({"siteler": [{"ad": "a.b", "ok": False}]})
        self.assertEqual(t2["satirlar"], [["a.b", "kritik"]])


if __name__ == "__main__":
    unittest.main()
