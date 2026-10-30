"""ops/veri_tasi.py — veri dizinleri ops/ → ~/.ghost taşıma (Faz 3.1): plan, taşıma+symlink, tekrar koşum, çatışma, mcp json."""
import json
import os
import tempfile
import unittest

from tests import common  # noqa: F401

from ghost import config
from ops import veri_tasi


class TestVeriTasi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="veri-tasi-")
        self.ops = os.path.join(self.tmp, "ops")
        self.kok = os.path.join(self.tmp, "ghostveri")
        os.makedirs(self.ops)
        self.mcp = os.path.join(self.tmp, "mcp-playwright.json")
        with open(self.mcp, "w") as f:
            json.dump({"mcpServers": {"playwright": {"command": "npx", "args": [
                "-y", "@playwright/mcp@0.0.83", "--headless", "--user-data-dir", os.path.join(self.ops, "browser-profile")]}}}, f)

    def _dolu(self, ad, dosya="a.bin"):
        d = os.path.join(self.ops, ad)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, dosya), "wb") as f:
            f.write(b"x" * 10)
        return d

    def test_plan_ve_tasima_symlink_birakir(self):
        self._dolu("kokpit_medya")
        self._dolu("masa_dosya", "foto.jpg")
        p = {ad: eylem for ad, _k, _h, eylem in veri_tasi.plan(self.kok, self.ops)}
        self.assertEqual(p["kokpit_medya"], "tasi")
        self.assertEqual(p["masa_dosya"], "tasi")
        self.assertEqual(p["browser-profile"], "yok")
        satirlar = veri_tasi.uygula(self.kok, self.ops, mcp_json=self.mcp)
        self.assertTrue(os.path.islink(os.path.join(self.ops, "masa_dosya")))
        self.assertTrue(os.path.isfile(os.path.join(self.kok, "masa_dosya", "foto.jpg")))
        # eski mutlak yol symlink üzerinden hâlâ okunur (DB'deki yollar kırılmaz)
        self.assertTrue(os.path.isfile(os.path.join(self.ops, "masa_dosya", "foto.jpg")))
        self.assertTrue(any("taşındı" in s and "masa_dosya" in s for s in satirlar))
        with open(self.mcp) as f:
            args = json.load(f)["mcpServers"]["playwright"]["args"]
        self.assertEqual(args[args.index("--user-data-dir") + 1], os.path.join(self.kok, "browser-profile"))

    def test_ikinci_kosum_dokunmaz(self):
        self._dolu("kokpit_paylasim")
        veri_tasi.uygula(self.kok, self.ops, mcp_json=self.mcp)
        p = {ad: eylem for ad, _k, _h, eylem in veri_tasi.plan(self.kok, self.ops)}
        self.assertEqual(p["kokpit_paylasim"], "tamam")
        satirlar = veri_tasi.uygula(self.kok, self.ops, mcp_json=self.mcp)
        self.assertTrue(any(s.startswith("= kokpit_paylasim") for s in satirlar))
        self.assertTrue(any("mcp-playwright.json zaten" in s for s in satirlar))

    def test_kuru_kosum_degistirmez(self):
        self._dolu("kokpit_medya")
        veri_tasi.uygula(self.kok, self.ops, kuru=True, mcp_json=self.mcp)
        self.assertTrue(os.path.isdir(os.path.join(self.ops, "kokpit_medya")))
        self.assertFalse(os.path.islink(os.path.join(self.ops, "kokpit_medya")))
        self.assertFalse(os.path.exists(os.path.join(self.kok, "kokpit_medya")))

    def test_catisma_dokunmaz(self):
        self._dolu("masa_dosya", "eski.jpg")
        os.makedirs(os.path.join(self.kok, "masa_dosya"))
        with open(os.path.join(self.kok, "masa_dosya", "yeni.jpg"), "wb") as f:
            f.write(b"y")
        satirlar = veri_tasi.uygula(self.kok, self.ops, mcp_json=self.mcp)
        self.assertTrue(any("ÇATIŞMA" in s for s in satirlar))
        self.assertFalse(os.path.islink(os.path.join(self.ops, "masa_dosya")))
        self.assertTrue(os.path.isfile(os.path.join(self.ops, "masa_dosya", "eski.jpg")))

    def test_hedef_var_kaynak_yok_link_kurar(self):
        os.makedirs(os.path.join(self.kok, "kokpit_medya"))
        satirlar = veri_tasi.uygula(self.kok, self.ops, mcp_json=self.mcp)
        self.assertTrue(os.path.islink(os.path.join(self.ops, "kokpit_medya")))
        self.assertTrue(any("symlink kuruldu" in s for s in satirlar))

    def test_config_veri_dizin_testte_gecici(self):
        d = config.veri_dizin("masa_dosya")
        self.assertTrue(d.startswith(os.environ["GHOST_OPS"]))
        self.assertTrue(os.path.isdir(d))
        self.assertEqual(set(config.VERI_DIZINLERI), {"kokpit_medya", "kokpit_paylasim", "masa_dosya", "browser-profile"})


if __name__ == "__main__":
    unittest.main()
