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


class TestRebaseTekrari(unittest.TestCase):
    """2026-11-03: rebase'le yeni sha almış aynı commit dersi 5-6 kez yazılıyordu."""

    def test_ayni_icerik_yeni_sha_ile_yazilmaz(self):
        metin, _ = dersler.guncelle("", [("aaaaaaa", "2026-10-19", "hedef #7: konu", "ders  metni\nikinci satır")])
        tekrar, n = dersler.guncelle(metin, [("bbbbbbb", "2026-10-19", "hedef #7: konu", "ders metni ikinci satır")])
        self.assertEqual(n, 0)
        self.assertEqual(tekrar, metin)

    def test_farkli_ders_ayni_konu_yazilir(self):
        metin, _ = dersler.guncelle("", [("aaaaaaa", "2026-10-19", "konu", "bir")])
        _, n = dersler.guncelle(metin, [("bbbbbbb", "2026-10-19", "konu", "iki")])
        self.assertEqual(n, 1)

    def test_tekillestir_erisilebilen_shayi_secer_sirayi_korur(self):
        kayit = [("1111111", "2026-10-18", "önce", "ilk ders"),
                 ("aaaaaaa", "2026-10-19", "tekrar", "aynı ders"),
                 ("2222222", "2026-10-19", "arada", "başka ders")]
        metin, _ = dersler.guncelle("", kayit)
        metin += "\n" + dersler.girdi("bbbbbbb", "2026-10-19", "tekrar", "aynı ders", ("kokpit",))
        metin += "\n" + dersler.girdi("ccccccc", "2026-10-19", "tekrar", "aynı ders")
        yeni, silinen = dersler.tekillestir(metin, tercih=["1111111", "bbbbbbbdeadbeef", "2222222"])
        self.assertEqual(silinen, 2)
        k = dersler.kayitlar_oku(yeni)
        self.assertEqual([x["sha"] for x in k], ["1111111", "bbbbbbb", "2222222"])   # ilk yer, erişilebilen içerik
        self.assertTrue(yeni.startswith(dersler.BASLIK))
        self.assertEqual(dersler.tekillestir(yeni, tercih=["bbbbbbb"]), (yeni, 0))

    def test_tekrarsiz_dosya_degismez(self):
        metin, _ = dersler.guncelle("", [("1111111", "2026-10-18", "a", "x"), ("2222222", "2026-10-19", "b", "y")])
        self.assertEqual(dersler.tekillestir(metin), (metin, 0))


if __name__ == "__main__":
    unittest.main()
