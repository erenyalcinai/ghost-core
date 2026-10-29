"""ghost/gunluk.py — günlük tek yer: test kipi liste, ad temizleme, oku, CLI (Faz 2.9 journald)."""
import unittest

from tests import common  # noqa: F401

from ghost import gunluk


class TestGunluk(unittest.TestCase):
    def setUp(self):
        gunluk.KAYITLAR.clear()

    def test_test_kipinde_listeye_yazar_ve_okur(self):
        gunluk.yaz("uptime", "ray düştü")
        gunluk.al("uptime").info("ray döndü")
        gunluk.yaz("port watch!", "başka ad")
        self.assertEqual(gunluk.oku("uptime"), ["ray düştü", "ray döndü"])
        self.assertEqual(gunluk.oku("port_watch_", 5), ["başka ad"])
        self.assertEqual(gunluk.oku("uptime", 1), ["ray döndü"])
        self.assertIn("uptime", gunluk.adlar())
        # handler tek sefer kurulur; çift kayıt yok
        gunluk.al("uptime"); gunluk.al("uptime")
        gunluk.yaz("uptime", "üçüncü")
        self.assertEqual(gunluk.oku("uptime").count("üçüncü"), 1)

    def test_yaz_asla_firlatmaz(self):
        gunluk.yaz(None, 123)
        self.assertEqual(gunluk.oku("ghost"), ["123"])

    def test_ad_temizle(self):
        self.assertEqual(gunluk._ad_temizle("biz watch/x"), "biz_watch_x")
        self.assertEqual(gunluk._ad_temizle(""), "ghost")

    def test_cli(self):
        self.assertEqual(gunluk._main(["yaz", "deneme", "merhaba", "dünya"]), 0)
        self.assertEqual(gunluk.oku("deneme"), ["merhaba dünya"])
        self.assertEqual(gunluk._main(["oku", "deneme", "5", "--since", "1 hour ago"]), 0)
        self.assertEqual(gunluk._main(["oku"]), 2)
        self.assertEqual(gunluk._main(["adlar"]), 0)
        self.assertEqual(gunluk._main(["bilinmeyen"]), 2)
        self.assertEqual(gunluk._main([]), 0)


if __name__ == "__main__":
    unittest.main()
