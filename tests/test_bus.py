import unittest

from tests import common  # noqa: F401
from ghost import bus, sandbox


class TestBus(unittest.TestCase):
    def test_emit_consume_cycle(self):
        eid = bus.emit("job", "test", {"prompt": "merhaba"})
        ev = bus.next_pending()
        self.assertIsNotNone(ev)
        self.assertEqual(ev["id"], eid)
        self.assertEqual(ev["payload"]["prompt"], "merhaba")
        bus.mark(eid, "done")
        nxt = bus.next_pending()
        self.assertTrue(nxt is None or nxt["id"] != eid)

    def test_fifo_order(self):
        a = bus.emit("job", "test", {"n": 1})
        bus.emit("job", "test", {"n": 2})
        ev = bus.next_pending()
        self.assertEqual(ev["id"], a)
        bus.mark(a)

    def test_stats(self):
        bus.emit("chat", "test", {})
        s = bus.stats()
        self.assertIn("pending", s)


class TestSandbox(unittest.TestCase):
    def test_run_ok(self):
        rc, out, err, secs = sandbox.run(["echo", "selam"], timeout=10)
        self.assertEqual(rc, 0)
        self.assertIn("selam", out)

    def test_timeout(self):
        rc, out, err, secs = sandbox.run(["sleep", "5"], timeout=1)
        self.assertEqual(rc, -9)
        self.assertIn("TIMEOUT", err)


if __name__ == "__main__":
    unittest.main()


class TestSahiplenme(unittest.TestCase):
    """Atomik sahiplenme (dış inceleme GPT, 2026-10-27): bir olay bir kez alınır; işlenirken
    çökülürse yeniden koşmaz, bayat sahiplenme olarak görünür olur."""

    def setUp(self):
        while bus.claim_next("sahip-test"):
            pass

    def test_ayni_olay_iki_kez_alinmaz(self):
        eid = bus.emit("sahip-test", "test", {"n": 1})
        ev = bus.claim_next("sahip-test")
        self.assertEqual(ev["id"], eid)
        self.assertIsNone(bus.claim_next("sahip-test"))
        bus.mark(eid, "done")

    def test_yarida_kalan_bayat_olur_yeniden_kosmaz(self):
        eid = bus.emit("sahip-test", "test", {"prompt": "x"})
        bus.claim_next("sahip-test")
        self.assertEqual(bus.bayat_sahiplenmeler("sahip-test", yas_sn=3600), [])
        bayat = bus.bayat_sahiplenmeler("sahip-test", yas_sn=-5)
        self.assertEqual([b["id"] for b in bayat], [eid])
        self.assertIsNone(bus.claim_next("sahip-test"))
        self.assertEqual(bus.bayat_sahiplenmeler("sahip-test", yas_sn=-5), [])
