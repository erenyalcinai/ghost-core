import unittest

from tests import common  # noqa: F401
from ghost import dersler


GOVDE = """Bir şey oldu, şöyle düzelttim.

Ders: Bir güvencenin kendisini versiyonlamak yetmiyor; onu AÇAN ayar da
versiyonlanmıyorsa güvence taşınmaz.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
"""


class TestDersAyikla(unittest.TestCase):
    def test_cok_satirli_blok_tek_paragrafa_birlesir(self):
        d = dersler.ders_ayikla(GOVDE)
        self.assertEqual(d, "Bir güvencenin kendisini versiyonlamak yetmiyor; "
                            "onu AÇAN ayar da versiyonlanmıyorsa güvence taşınmaz.")

    def test_ders_yoksa_none(self):
        self.assertIsNone(dersler.ders_ayikla("Sadece bir düzeltme.\n"))
        self.assertIsNone(dersler.ders_ayikla(""))
        self.assertIsNone(dersler.ders_ayikla(None))

    def test_satir_ortasindaki_kelime_ders_sayilmaz(self):
        # "dersi zaten kaybolmamıştı" gibi cümleler yanlış eşleşmemeli
        self.assertIsNone(dersler.ders_ayikla("bu dersi zaten biliyorduk\n"))

    def test_birden_cok_blok(self):
        d = dersler.ders_ayikla("Ders: bir\n\nara metin\n\nDers: iki\n")
        self.assertEqual(d, "bir\n\niki")

    def test_gövde_sonunda_biten_blok(self):
        self.assertEqual(dersler.ders_ayikla("Ders: son satır"), "son satır")


class TestGuncelle(unittest.TestCase):
    def test_bos_dosyaya_baslik_ve_girdi(self):
        metin, n = dersler.guncelle("", [("abc1234def", "2026-08-21", "konu", "ders metni")])
        self.assertEqual(n, 1)
        self.assertIn(dersler.BASLIK, metin)
        self.assertIn("abc1234", metin)
        self.assertIn("ders metni", metin)

    def test_ayni_commit_ikinci_kez_yazilmaz(self):
        kayit = [("abc1234def", "2026-08-21", "konu", "ders metni")]
        metin, _ = dersler.guncelle("", kayit)
        tekrar, n = dersler.guncelle(metin, kayit)
        self.assertEqual(n, 0)
        self.assertEqual(tekrar, metin)          # dosya hiç değişmemeli

    def test_uzun_sha_kisa_sha_ile_eslesir(self):
        metin, _ = dersler.guncelle("", [("abc1234def5678", "2026-08-21", "k", "d")])
        _, n = dersler.guncelle(metin, [("abc1234", "2026-08-21", "k", "d")])
        self.assertEqual(n, 0)

    def test_dersi_olmayan_commit_atlanir(self):
        metin, n = dersler.guncelle("", [("abc1234", "2026-08-21", "konu", None)])
        self.assertEqual((metin, n), ("", 0))

    def test_yeni_ders_sona_eklenir_eski_korunur(self):
        metin, _ = dersler.guncelle("", [("aaaaaaa", "2026-08-20", "ilk", "birinci ders")])
        metin2, n = dersler.guncelle(metin, [("bbbbbbb", "2026-08-21", "son", "ikinci ders")])
        self.assertEqual(n, 1)
        self.assertIn("birinci ders", metin2)
        self.assertLess(metin2.index("birinci ders"), metin2.index("ikinci ders"))

    def test_elle_yazilmis_dosya_bozulmaz(self):
        elde = "# Dersler\n\nElle yazdığım not.\n"
        metin, n = dersler.guncelle(elde, [("ccccccc", "2026-08-21", "k", "d")])
        self.assertIn("Elle yazdığım not.", metin)
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
