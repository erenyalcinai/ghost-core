"""ghost/vadeler.py — vade tablosu, yaklaşan 14 gün, gecikme etiketi (Faz 1.2, 2026-10-29)."""
import datetime
import unittest

from tests import common  # noqa: F401

from ghost import vadeler


class TestVadeler(unittest.TestCase):
    def setUp(self):
        c = vadeler._conn()
        c.execute("DELETE FROM vadeler")
        c.commit()
        c.close()

    def test_ekle_liste_tekil(self):
        a = vadeler.ekle("GitHub token", "2026-11-09", "vekil durum ile doğrula", "hafıza")
        b = vadeler.ekle("GitHub token", "2026-11-09")
        self.assertEqual(a, b)                    # aynı ad+tarih açıkken ikinci kez eklenmez
        self.assertEqual(len(vadeler.liste()), 1)
        self.assertEqual(vadeler.liste()[0]["kaynak"], "hafıza")

    def test_tarih_bicimleri_ve_hata(self):
        vadeler.ekle("Marka bülteni", "30.11.2026")
        self.assertEqual(vadeler.liste()[0]["tarih"], "2026-11-30")
        with self.assertRaises(ValueError):
            vadeler.ekle("x", "yarın")
        with self.assertRaises(ValueError):
            vadeler.ekle("", "2026-11-09")

    def test_yaklasan_ve_gecikme(self):
        bugun = datetime.date(2026, 10, 29)
        vadeler.ekle("yakın", "2026-11-02")
        vadeler.ekle("uzak", "2027-09-28")
        vadeler.ekle("geçmiş", "2026-10-20")
        rows = vadeler.yaklasan(14, bugun)
        adlar = [r["ad"] for r in rows]
        self.assertEqual(adlar, ["geçmiş", "yakın"])       # tarihe göre sıralı, uzak dışarıda
        self.assertEqual(rows[0]["kalan"], -9)
        m = vadeler.metin(14, bugun)
        self.assertIn("GECİKTİ 9 gün", m)
        self.assertIn("(4 gün) yakın", m)
        self.assertNotIn("uzak", m)

    def test_kapat_sonra_gorunmez(self):
        vid = vadeler.ekle("kapanacak", "2026-11-01")
        self.assertTrue(vadeler.kapat(vid))
        self.assertFalse(vadeler.kapat(vid))               # ikinci kez kapanmaz
        self.assertEqual(vadeler.metin(14, datetime.date(2026, 10, 29)), "")
        self.assertEqual(vadeler.liste("kapandi")[0]["ad"], "kapanacak")

    def test_bos_metin(self):
        self.assertEqual(vadeler.metin(14), "")

    def test_cli(self):
        self.assertEqual(vadeler._main(["ekle", "Instagram geri alma", "2026-11-02", "erenyal", "--kaynak", "hafıza"]), 0)
        self.assertEqual(vadeler._main(["liste"]), 0)
        self.assertEqual(vadeler._main(["yaklasan", "3650"]), 0)
        self.assertEqual(vadeler._main(["ekle", "eksik"]), 2)


if __name__ == "__main__":
    unittest.main()
