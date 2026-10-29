"""ops/brifing_baglam.py — brifing tekrar filtresi: intel başlıkları + sentez ODAKLAN/ERTELE (Faz 1.16, 2026-10-29)."""
import os
import sys
import tempfile
import time
import unittest

from tests import common  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops"))
import brifing_baglam as bb  # noqa: E402


class TestBrifingBaglam(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="intel-")
        self.s = tempfile.mkdtemp(prefix="sentez-")
        bb.INTEL, bb.SENTEZ = self.d, self.s
        bugun = time.strftime("%Y-%m-%d")
        eski = time.strftime("%Y-%m-%d", time.localtime(time.time() - 20 * 86400))
        open(os.path.join(self.d, bugun + ".md"), "w").write(
            "👻 Ghost Brifingi\n\n■ BUGÜN BİLMEN GEREKEN 3 ŞEY\n\n1) Claude API sürümü kırıcı değişiklik getirdi. Uzun açıklama…\n"
            "Kaynak: https://x\n\n2) Apple App Review kuralı güncellendi.\n\n■ 1 FIRSAT\n\nVultr hackathon 3-8 Kasım.\nBaşvuru: https://y\n\n■ NOT\nyok\n")
        open(os.path.join(self.d, eski + ".md"), "w").write("1) Eski haber\n")

    def test_basliklar_ve_firsat(self):
        b = bb.intel_basliklar(7)
        self.assertEqual(len(b), 1)
        _, satirlar = b[0]
        self.assertEqual(satirlar[0], "Claude API sürümü kırıcı değişiklik getirdi. Uzun açıklama…")
        self.assertEqual(satirlar[2], "fırsat: Vultr hackathon 3-8 Kasım.")
        self.assertNotIn("Kaynak", " ".join(satirlar))

    def test_sentez_yoksa_bos_varsa_kararlar(self):
        self.assertEqual(bb.son_sentez_kararlari(), (None, []))
        open(os.path.join(self.s, "2026-W44.md"), "w").write("📊 Sentez\n3) Öneri: ODAKLAN → App B yorumları; ERTELE → saat markası adı.\nbaşka satır\n")
        ad, k = bb.son_sentez_kararlari()
        self.assertEqual(ad, "2026-W44.md")
        self.assertEqual(len(k), 1)
        blok = bb.blok(7)
        self.assertIn("ZATEN SÖYLEDİKLERİN", blok)
        self.assertIn("ERTELE → saat markası adı", blok)
        self.assertNotIn("Eski haber", blok)

    def test_bos_dizin_bos_cikti(self):
        bb.INTEL = tempfile.mkdtemp(prefix="bos-")
        self.assertEqual(bb.blok(7), "")


if __name__ == "__main__":
    unittest.main()
