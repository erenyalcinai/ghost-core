"""ops/md_pdf.py (HTML üretimi, testte PDF yok) + ghost/surum.py (Faz 1.13/1.13b, 2026-10-29)."""
import os
import sys
import unittest

from tests import common  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops"))
import md_pdf  # noqa: E402
from ghost import surum  # noqa: E402


class TestMdPdf(unittest.TestCase):
    def test_html_tablo_ve_kacis(self):
        h = md_pdf.html_uret("# Başlık\n\n| a | b |\n|---|---|\n| 1 | <x> |\n", "Rapor <1>")
        self.assertIn("<title>Rapor &lt;1&gt;</title>", h)
        try:
            import markdown  # noqa: F401
        except ImportError:                          # CI'da paket yok → kaçışlı <pre> yedeği
            self.assertIn("<pre>", h)
            self.assertIn("&lt;x&gt;", h)
            return
        self.assertIn("<table>", h)
        self.assertIn("<td><x></td>", h)            # markdown ham HTML geçirir (Masa içeriği kendi metnimiz)

    def test_testte_pdf_yerine_html(self):
        veri, mime = md_pdf.pdf_uret("merhaba", "x")
        self.assertTrue(mime.startswith("text/html"))
        self.assertIn(b"merhaba", veri)


class TestSurum(unittest.TestCase):
    def test_bilgi(self):
        b = surum.bilgi()
        self.assertIn("surum", b)
        self.assertIsInstance(b["build"], int)
        self.assertEqual(surum.bilgi(), b)       # önbellek


if __name__ == "__main__":
    unittest.main()
