import unittest

from tests import common  # noqa: F401
from ghost import memory


class TestMemory(unittest.TestCase):
    def test_remember_recall(self):
        memory.remember("AppA backend Cloudflare Worker üzerinde koşuyor",
                        kind="bilgi")
        memory.remember("Sahip kahvesini sade sever", kind="kişisel")
        rows = memory.recall("Cloudflare")
        self.assertTrue(any("AppA" in r[2] for r in rows))

    def test_empty_inputs(self):
        self.assertIsNone(memory.remember("   "))
        self.assertEqual(memory.recall(""), [])

    def test_count_grows(self):
        before = memory.count()
        memory.remember("sayaç testi kaydı")
        self.assertEqual(memory.count(), before + 1)


if __name__ == "__main__":
    unittest.main()
