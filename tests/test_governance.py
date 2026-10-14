import json
import os
import unittest

from tests import common  # noqa: F401  (yolları temp'e yönlendirir)
from ghost import config, governance


class TestClassify(unittest.TestCase):
    def test_read_default(self):
        self.assertEqual(governance.classify("bugün hava nasıl"), governance.READ)
        self.assertEqual(governance.classify(""), governance.READ)
        self.assertEqual(governance.classify(None), governance.READ)

    def test_permanent(self):
        for t in ("siteyi deploy et", "git push yap", "şu dosyayı sil",
                  "rm -rf ./build", "eas submit", "force push", "kaldir sunu"):
            self.assertEqual(governance.classify(t), governance.PERMANENT, t)

    def test_spend(self):
        for t in ("bir domain satın al", "aboneliği öde", "buy credits",
                  "subscribe to plan"):
            self.assertEqual(governance.classify(t), governance.SPEND, t)

    def test_spend_beats_permanent(self):
        self.assertEqual(governance.classify("eski domaini sil ve yenisini satın al"),
                         governance.SPEND)

    def test_reversible(self):
        self.assertEqual(governance.classify("bir rapor oluştur"),
                         governance.REVERSIBLE)

    def test_requires_approval(self):
        self.assertTrue(governance.requires_approval(governance.PERMANENT))
        self.assertTrue(governance.requires_approval(governance.SPEND))
        self.assertFalse(governance.requires_approval(governance.REVERSIBLE))
        self.assertFalse(governance.requires_approval(governance.PERMANENT,
                                                      guard_on=False))


class TestFreezeAudit(unittest.TestCase):
    def test_freeze_cycle(self):
        self.assertFalse(governance.is_frozen())
        governance.freeze("test")
        self.assertTrue(governance.is_frozen())
        governance.unfreeze()
        self.assertFalse(governance.is_frozen())

    def test_audit_writes_jsonl(self):
        governance.audit("test", "deneme", governance.READ, "detay")
        with open(config.AUDIT_LOG, encoding="utf-8") as f:
            last = json.loads(f.readlines()[-1])
        self.assertEqual(last["action"], "deneme")
        self.assertIn("ts", last)

    def test_audit_tail(self):
        governance.audit("test", "kuyruk", governance.READ)
        tail = governance.audit_tail(5)
        self.assertTrue(any(r["action"] == "kuyruk" for r in tail))


if __name__ == "__main__":
    unittest.main()
