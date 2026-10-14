"""canli — Kokpit canlı görüntü yardımcıları (boy sınırı; test kipinde uzuv yok)."""
import os
import unittest

os.environ.setdefault("GHOST_TEST", "1")

from ghost import canli


class BoySinirla(unittest.TestCase):
    def test_bos_ve_gecersiz_none(self):
        for v in ("", None, "abc", "0", "-5", " "):
            self.assertIsNone(canli.boy_sinirla(v), v)

    def test_aralik_icinde_aynen(self):
        self.assertEqual(canli.boy_sinirla("640"), 640)
        self.assertEqual(canli.boy_sinirla(" 1512 "), 1512)

    def test_ucuklar_kirpilir(self):
        self.assertEqual(canli.boy_sinirla("10"), canli.BOY_ARALIK[0])
        self.assertEqual(canli.boy_sinirla("99999"), canli.BOY_ARALIK[1])


class TestKipi(unittest.TestCase):
    def test_test_kipinde_uzuv_yok(self):
        self.assertIsNone(canli.mac_kare())
        self.assertIsNone(canli.mac_kare(boy=640))
        self.assertIsNone(canli.telefon_kare())


if __name__ == "__main__":
    unittest.main()
