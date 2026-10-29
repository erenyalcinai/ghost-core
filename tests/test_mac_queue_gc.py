"""ops/mac_queue_gc.py — varsayılan kuru koşu siler mi? (silmemeli), --uygula yalnız eski düz dosyaları siler."""
import contextlib
import io
import os
import sys
import tempfile
import time
import unittest

from tests import common  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops"))
import mac_queue_gc as gc  # noqa: E402


class MacQueueGC(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.simdi = time.time()
        for ad, yas in (("1.json", 90), ("1.sonuc.md", 90), ("2.json", 10), ("3.json", 61)):
            p = os.path.join(self.d.name, ad)
            with open(p, "w") as f:
                f.write("x" * 10)
            os.utime(p, (self.simdi - yas * 86400, self.simdi - yas * 86400))
        os.mkdir(os.path.join(self.d.name, "altdizin"))
        os.utime(os.path.join(self.d.name, "altdizin"), (self.simdi - 400 * 86400,) * 2)

    def test_kuru_kosu_silmez(self):
        s = gc.topla(self.d.name, 60, uygula=False, simdi=self.simdi)
        self.assertEqual(s["aday"], 3)
        self.assertEqual(s["silinen"], 0)
        self.assertEqual(sorted(os.listdir(self.d.name)), ["1.json", "1.sonuc.md", "2.json", "3.json", "altdizin"])

    def test_uygula_yalniz_eski_duz_dosyalar(self):
        s = gc.topla(self.d.name, 60, uygula=True, simdi=self.simdi)
        self.assertEqual(s["silinen"], 3)
        self.assertEqual(s["bayt"], 30)
        self.assertEqual(sorted(os.listdir(self.d.name)), ["2.json", "altdizin"])

    def test_esik_degisir(self):
        self.assertEqual(len(gc.adaylar(self.d.name, 5, simdi=self.simdi)), 4)
        self.assertEqual(len(gc.adaylar(self.d.name, 100, simdi=self.simdi)), 0)

    def test_olmayan_dizin_bos(self):
        self.assertEqual(gc.adaylar(os.path.join(self.d.name, "yok")), [])

    def test_cli_varsayilan_kuru(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(gc.main(["--kok", self.d.name]), 0)
        self.assertIn("KURU KOŞU", out.getvalue())
        self.assertEqual(len(os.listdir(self.d.name)), 5)


if __name__ == "__main__":
    unittest.main()
