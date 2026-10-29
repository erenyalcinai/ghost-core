"""ghost/kararlar.py — kararlar defteri: ekle/durum/ara/bağlam notu (Faz 1.1, 2026-10-29)."""
import unittest

from tests import common  # noqa: F401

from ghost import kararlar


class TestKararlar(unittest.TestCase):
    def setUp(self):
        c, _ = kararlar._conn()
        c.execute("DELETE FROM kararlar")
        c.commit()
        c.close()

    def test_ekle_tekil_ve_say(self):
        a = kararlar.ekle("Apify actor fabrikası", "İptal; tekrar önerme", "iptal", "2026-09-03", "hafıza")
        b = kararlar.ekle("Apify actor fabrikası", "İptal; tekrar önerme", "gecerli")
        self.assertEqual(a, b)
        s = kararlar.say()
        self.assertEqual((s["toplam"], s["iptal"]), (1, 1))
        with self.assertRaises(ValueError):
            kararlar.ekle("x", "")
        with self.assertRaises(ValueError):
            kararlar.ekle("x", "y", durum="belirsiz")

    def test_durum_degistir(self):
        kid = kararlar.ekle("Hazır Site", "Van demosu beğenildi, rafa kaldırıldı", "rafta", "2026-10-20")
        self.assertTrue(kararlar.durum_degistir(kid, "gecerli", "Sahip rafı açtı"))
        r = kararlar.liste()[0]
        self.assertEqual((r["durum"], r["notu"]), ("gecerli", "Sahip rafı açtı"))
        self.assertFalse(kararlar.durum_degistir(999, "rafta"))

    def test_ara_ve_baglam_notu(self):
        kararlar.ekle("AppA TestFlight", "BEKLEMEDE, 'zamanı gelince söylerim' — gündeme getirme",
                      "rafta", "2026-10-24", "hafıza", "appa,testflight")
        kararlar.ekle("Uzun cevap → Masa", "Köprü uzun cevabı kesmez, Masa'ya taşımaz", "gecerli", "2026-10-29")
        rows = kararlar.ara("appa testflight açalım mı")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["durum"], "rafta")
        notu = kararlar.baglam_notu("AppA için TestFlight'ı tekrar açalım mı?")
        self.assertIn("[rafta] AppA TestFlight", notu)
        self.assertNotIn("Masa", notu)
        self.assertEqual(kararlar.baglam_notu("selam nasılsın"), "")
        self.assertEqual(kararlar.baglam_notu("bugün hava çok güzel"), "")

    def test_cli(self):
        self.assertEqual(kararlar._main(["ekle", "ecza.ci", "Deney iptal, tekrar önerme", "--durum", "iptal",
                                         "--tarih", "2026-09-11", "--kaynak", "hafıza", "--etiket", "seo"]), 0)
        self.assertEqual(kararlar._main(["liste"]), 0)
        self.assertEqual(kararlar._main(["ara", "ecza.ci"]), 0)
        self.assertEqual(kararlar._main(["say"]), 0)
        self.assertEqual(kararlar._main(["ekle", "eksik"]), 2)


if __name__ == "__main__":
    unittest.main()
