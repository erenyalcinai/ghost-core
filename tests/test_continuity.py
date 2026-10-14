"""Süreklilik kancası testleri.

Alınan fikir şuydu: hafıza yazmayı iyi niyete bırakma, mekanikleştir. Bu
testler kancanın İKİ yönde de doğru olduğunu sabitler — unutulmuş oturumu
yakalar, ama sıradan muhabbeti suçlamaz. Yanlış alarm üreten bir uyarı
sistemi, uyarı olmayan sistemden daha kötüdür: gürültüyü öğrenir, susarız.
"""
from tests import common  # noqa: F401
import unittest

from ghost import continuity as c


class KararTest(unittest.TestCase):
    def test_kisa_oturum_suclanmaz(self):
        # "tamam / bak bakalım / sağ ol" — burada öğrenilecek bir şey yok.
        self.assertFalse(c.karar(1, (5, 2, 100), (5, 2, 100)))
        self.assertFalse(c.karar(5, (5, 2, 100), (5, 2, 100)))

    def test_uzun_oturum_hic_yazmadiysa_bayrak(self):
        self.assertTrue(c.karar(6, (5, 2, 100), (5, 2, 100)))
        self.assertTrue(c.karar(40, (5, 2, 100), (5, 2, 100)))

    def test_fts_veritabanina_yazildiysa_bayrak_yok(self):
        self.assertFalse(c.karar(20, (5, 2, 100), (6, 2, 100)))

    def test_markdown_hafizaya_yazildiysa_bayrak_yok(self):
        # İki ayrı hafıza var; yalnız birine bakmak yanlış alarm üretirdi.
        self.assertFalse(c.karar(20, (5, 2, 100), (5, 3, 100)))  # yeni dosya
        self.assertFalse(c.karar(20, (5, 2, 100), (5, 2, 900)))  # dosya güncellendi

    def test_olculemiyorsa_suclamaz(self):
        # İmza alınamadıysa (DB kilitli, dizin yok) sessiz kal — tahminle
        # uyarı üretmek gürültüdür.
        self.assertFalse(c.karar(20, None, (5, 2, 100)))
        self.assertFalse(c.karar(20, (5, 2, 100), None))


class AkisTest(unittest.TestCase):
    def setUp(self):
        c._yaz({})

    def test_ogrenmeden_biten_oturum_bayrak_birakir(self):
        c.oturum_basladi("sid-1")
        self.assertTrue(c.oturum_bitti(12, "AppA fiyatlandırması"))
        mesaj = c.bayrak_al()
        self.assertIsNotNone(mesaj)
        self.assertIn("12 tur", mesaj)
        self.assertIn("AppA fiyatlandırması", mesaj)

    def test_bayrak_bir_kez_tuketilir(self):
        c.oturum_basladi("sid-2")
        c.oturum_bitti(9, "konu")
        self.assertIsNotNone(c.bayrak_al())
        self.assertIsNone(c.bayrak_al())      # ikinci çağrıda yok

    def test_ayni_oturum_capayi_tazelemez(self):
        # Aynı sid ile ikinci çağrı çıpayı sıfırlarsa, oturum ortasında
        # yazılan hafıza "hiç yazılmamış" görünürdü.
        c.oturum_basladi("sid-3")
        ilk = c._oku()["imza"]
        c.oturum_basladi("sid-3")
        self.assertEqual(c._oku()["imza"], ilk)

    def test_kisa_oturum_bayrak_birakmaz(self):
        c.oturum_basladi("sid-4")
        self.assertFalse(c.oturum_bitti(2, "selam"))
        self.assertIsNone(c.bayrak_al())

    def test_capa_yoksa_bayrak_yok(self):
        # Köprü yeniden başladıysa çıpa olmayabilir; suçlama.
        c._yaz({})
        self.assertFalse(c.oturum_bitti(30, "konu"))


class HatirlatmaTest(unittest.TestCase):
    """Oturum yaşarken uyarı — bayrağın aksine kaybı ÖNLEYEBİLEN yarı."""

    def setUp(self):
        c._yaz({})

    def test_esigin_altinda_susar(self):
        self.assertFalse(c.hatirlatma_karari(11, (5, 2, 100), (5, 2, 100)))

    def test_esikte_ve_yazilmamissa_hatirlatir(self):
        self.assertTrue(c.hatirlatma_karari(12, (5, 2, 100), (5, 2, 100)))

    def test_zaten_yazildiysa_susar(self):
        # Asıl mesele bu: bu oturumda hafızaya yazıldıysa dürtmek dırdırdır.
        self.assertFalse(c.hatirlatma_karari(30, (5, 2, 100), (6, 2, 100)))

    def test_ayni_esikte_tekrar_tekrar_atesleme(self):
        # 12'de hatırlatıldı; 13..23 arası susmalı, 24'te yeniden.
        self.assertFalse(c.hatirlatma_karari(13, (5, 2, 100), (5, 2, 100), 12))
        self.assertFalse(c.hatirlatma_karari(23, (5, 2, 100), (5, 2, 100), 12))
        self.assertTrue(c.hatirlatma_karari(24, (5, 2, 100), (5, 2, 100), 12))

    def test_akis_capa_ve_tekrar(self):
        c.oturum_basladi("sid-h")
        self.assertIsNone(c.hatirlatma("sid-h", 5))       # eşik altı
        mesaj = c.hatirlatma("sid-h", 12)
        self.assertIsNotNone(mesaj)
        self.assertIn("12 tur", mesaj)
        self.assertIsNone(c.hatirlatma("sid-h", 13))      # ısrar yok
        self.assertIsNotNone(c.hatirlatma("sid-h", 24))   # bir eşik sonra yine

    def test_baska_oturumun_capasi_kullanilmaz(self):
        c.oturum_basladi("sid-a")
        self.assertIsNone(c.hatirlatma("sid-b", 30))


if __name__ == "__main__":
    unittest.main()
