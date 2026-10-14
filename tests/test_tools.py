"""Araç kayıt defteri (envanter) testleri.

NOT: run_tool + synthesize + güvenilirlik skoru söküldü (üretimde hiç
çağrılmıyordu — gerçek çalıştırma bridge'in run_claude+MCP yolundan gidiyor).
Registry artık Ghost'un YETENEK ENVANTERİ: register/get/seed/list_tools.
"""
from tests import common  # noqa: F401
import unittest

from ghost.tools import registry


class TestRegistry(unittest.TestCase):
    def test_seed_and_get(self):
        registry.seed()
        t = registry.get("claude_code")
        self.assertIsNotNone(t)
        self.assertEqual(t["kind"], "engine")
        self.assertIn("perm_class", t)
        self.assertTrue(len(registry.list_tools()) >= len(registry.SEED))

    def test_get_unknown(self):
        self.assertIsNone(registry.get("boyle_bir_alet_yok"))

    def test_register_ve_envanterde_gorunur(self):
        registry.register("yeni_alet", "script", "test aleti", "echo {args}")
        t = registry.get("yeni_alet")
        self.assertEqual(t["kind"], "script")
        self.assertEqual(t["origin"], "manual")
        names = [x["name"] for x in registry.list_tools()]
        self.assertIn("yeni_alet", names)

    def test_list_tools_alanlari(self):
        registry.seed()
        rows = registry.list_tools()
        self.assertTrue(rows)
        for f in ("name", "kind", "descr", "origin"):
            self.assertIn(f, rows[0])


if __name__ == "__main__":
    unittest.main()
