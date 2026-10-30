import time
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


class TestIsKuyrugu(unittest.TestCase):
    """Faz 3.11 kuyruk birleşimi: type=job hedef=vps|mac tek kuyruk; Mac işçisi sahiplenir/bitirir; GC yalnız
    biten job/request olaylarını siler (chat zaman çizelgesine dokunmaz)."""

    def setUp(self):
        for h in ("vps", "mac"):
            while bus.is_sahiplen(h):
                pass

    def test_ekle_bekleyen_sahiplen_bitir_oku(self):
        m = bus.is_ekle("echo hi", kind="shell", hedef="mac", source="mac_cmd",
                        meta={"cmd": "echo hi", "timeout": 30, "sessiz": True, "cwd": None})
        v = bus.is_ekle("uzun iş", kind="iş", hedef="vps", model="opus", ust_id=7)
        self.assertEqual([e["id"] for e in bus.bekleyen_isler("vps")], [v])
        self.assertEqual(bus.bekleyen_isler("vps")[0]["ust_id"], 7)
        self.assertEqual(bus.bekleyen_isler("vps")[0]["payload"]["model"], "opus")
        ev = bus.is_sahiplen("mac")
        self.assertEqual(ev["id"], m)
        self.assertEqual(ev["payload"], {"prompt": "echo hi", "kind": "shell", "cmd": "echo hi",
                                         "timeout": 30, "sessiz": True})   # None meta alanı yazılmaz
        self.assertIsNone(bus.is_sahiplen("mac"))                          # ikinci sahiplenme yok
        self.assertEqual(bus.is_bitir(m, "# r\n## Sonuç\nçıktı", "done"), 1)
        d = bus.is_oku(m)
        self.assertEqual((d["status"], d["hedef"], d["kind"]), ("done", "mac", "shell"))
        self.assertIn("çıktı", d["sonuc"])
        self.assertEqual(bus.is_bitir(999999, None, "failed"), 0)
        self.assertEqual(bus.is_say("vps"), {"pending": 1})
        bus.mark(v, "done")

    def test_hedef_dogrulanir(self):
        with self.assertRaises(ValueError):
            bus.is_ekle("x", hedef="telefon")
        with self.assertRaises(ValueError):
            bus.is_bitir(1, None, "bitti")

    def test_geri_birak_ve_bayat(self):
        m = bus.is_ekle("x", hedef="mac")
        ev = bus.is_sahiplen("mac")
        self.assertEqual(bus.is_geri_birak(ev["id"]), 1)          # koşmadan hata → pending'e döner
        self.assertEqual(bus.is_geri_birak(ev["id"]), 0)          # zaten pending
        ev = bus.is_sahiplen("mac")
        self.assertEqual(ev["id"], m)
        self.assertEqual(bus.bayat_isler("mac", 3600), [])
        bayat = bus.bayat_isler("mac", -5)
        self.assertEqual([b["id"] for b in bayat], [m])
        self.assertEqual(bus.is_oku(m)["status"], "failed")
        self.assertIsNone(bus.is_sahiplen("mac"))                 # failed yeniden koşmaz

    def test_gc_yalniz_biten_is_ve_istek(self):
        c_id = bus.emit("chat", "test", {"x": 1}, status="done")
        j = bus.is_ekle("bitti", hedef="vps")
        bus.mark(j, "done")
        acik = bus.is_ekle("açık", hedef="vps")
        r = bus.istek("p", "k", "test")
        bus.mark(r, "failed")
        gelecek = time.time() + 40 * 86400
        kuru = bus.gc(uygula=False, simdi=gelecek)                     # kuru koşu silmez (öteki testlerin kalıntısı da sayılır)
        self.assertGreaterEqual(kuru, 2)
        self.assertIsNotNone(bus.is_oku(j))
        self.assertEqual(bus.gc(simdi=gelecek), kuru)
        self.assertIsNone(bus.is_oku(j))
        self.assertIsNotNone(bus.is_oku(acik))                        # pending kalır
        c = bus._conn()
        try:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM events WHERE id=?", (c_id,)).fetchone()[0], 1)
        finally:
            c.close()
        bus.mark(acik, "done")

    def test_cli_mac_claim_json_ve_bos(self):
        import contextlib
        import io
        import json
        m = bus.is_ekle("echo c", kind="shell", hedef="mac", meta={"cmd": "echo c"})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(bus._cli(["mac-claim"]), 0)
        ev = json.loads(out.getvalue().strip())
        self.assertEqual((ev["id"], ev["payload"]["cmd"]), (m, "echo c"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(bus._cli(["mac-claim"]), 0)
        self.assertEqual(out.getvalue(), "")                            # boş kuyruk = boş çıktı (işçi None sayar)
        bus.is_bitir(m, "", "done")

    def test_eski_semadan_goc_sutun_ekler(self):
        import os
        import sqlite3
        import tempfile
        from ghost import semalar, config
        d = tempfile.mkdtemp()
        yol = os.path.join(d, "eski.db")
        c = sqlite3.connect(yol)
        c.executescript("CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, type TEXT NOT NULL, "
                        "source TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', done_ts TEXT);"
                        "INSERT INTO events(ts,type,source,payload) VALUES('2026-01-01 00:00:00','chat','t','{}');")
        c.commit()
        c.close()
        eski = config.DB_PATH
        config.DB_PATH = yol
        try:
            c = semalar.baglan()
            self.assertEqual(semalar.surum(c), semalar.SURUM)
            sutun = [r[1] for r in c.execute("PRAGMA table_info(events)")]
            for ad in ("hedef", "kind", "sonuc", "ust_id"):
                self.assertIn(ad, sutun)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)   # veri korunur
            self.assertEqual(semalar.goc_uygula(c), [])
            c.close()
        finally:
            config.DB_PATH = eski
