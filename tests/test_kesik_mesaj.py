"""ghost/kesik_mesaj.py: köprü turun ortasında ölünce mesaj bir kez yeniden alınır."""
import os
import unittest

from tests import common  # noqa: F401

from ghost import kesik_mesaj as km


class KesikMesaj(unittest.TestCase):
    def setUp(self):
        km.bitti()

    def test_yok(self):
        self.assertIsNone(km.devral())

    def test_basladi_bitti_temiz(self):
        km.basladi("selam", spoken=True)
        self.assertTrue(os.path.exists(km._dosya()))
        km.bitti()
        self.assertFalse(os.path.exists(km._dosya()))
        self.assertIsNone(km.devral())

    def test_devral_bir_kez(self):
        km.basladi("napalım sence", spoken=False)
        k = km.devral()
        self.assertIsNotNone(k)
        self.assertFalse(k["vazgec"])
        self.assertEqual(k["deneme"], 1)
        self.assertTrue(k["text"].startswith(km.ONEK))
        self.assertTrue(k["text"].endswith("napalım sence"))
        self.assertFalse(os.path.exists(km._dosya()), "devral dosyayı silmeli")
        # İkinci deneme: aynı mesaj yine kesildiyse döngüye girme.
        km.basladi(k["text"], deneme=k["deneme"])
        k2 = km.devral()
        self.assertTrue(k2["vazgec"])
        self.assertEqual(k2["neden"], "deneme")
        self.assertIsNone(km.devral())

    def test_onek_tekrar_eklenmez(self):
        km.basladi(km.ONEK + "x")
        k = km.devral()
        self.assertEqual(k["text"].count(km.ONEK), 1)

    def test_bayat(self):
        km.basladi("eski")
        k = km.devral(simdi=__import__("time").time() + km.TAVAN_SN + 5)
        self.assertTrue(k["vazgec"])
        self.assertEqual(k["neden"], "bayat")

    def test_bozuk_dosya(self):
        with open(km._dosya(), "w") as f:
            f.write("{bozuk")
        self.assertIsNone(km.devral())
        self.assertFalse(os.path.exists(km._dosya()))


if __name__ == "__main__":
    unittest.main()
