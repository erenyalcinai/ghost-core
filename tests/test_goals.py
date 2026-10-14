import os
import time
import unittest

from tests import common  # noqa: F401
from ghost import config, goals


class TestGoals(unittest.TestCase):
    def test_add_list_status(self):
        gid = goals.add("Test hedefi", "detay")
        self.assertTrue(any(g["id"] == gid for g in goals.list_goals()))
        goals.set_status(gid, "tamam")
        self.assertFalse(any(g["id"] == gid for g in goals.list_goals()))
        self.assertTrue(any(g["id"] == gid
                            for g in goals.list_goals(all_states=True)))

    def test_tick_queues_least_recent(self):
        gid = goals.add("Tik hedefi")
        goals._last_tick = 0
        fired = goals.tick(now=time.time())
        self.assertEqual(fired, gid)
        # olay veriyoluna istek düştü (Faz 3: ops/requests dosyası değil)
        from ghost import bus
        self.assertTrue(any(r["source"] == "goal" and r["payload"]["kind"] == "hedef"
                            for r in bus.bekleyen_istekler()))
        # 6 saat dolmadan aynı hedef tekrar çalışılmaz
        goals._last_tick = 0
        self.assertIsNone(goals.tick(now=time.time()))
        goals.set_status(gid, "tamam")

    def test_frozen_blocks(self):
        from ghost import governance
        gid = goals.add("Donuk hedef")
        governance.freeze("test")
        try:
            goals._last_tick = 0
            self.assertIsNone(goals.tick(now=time.time() + 10**6))
        finally:
            governance.unfreeze()
            goals.set_status(gid, "tamam")


if __name__ == "__main__":
    unittest.main()
