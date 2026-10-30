"""Faz 3.11 kuyruk birleşimi (2026-10-30): Mac işçisi veriyolundan iş çeker (bus-first), dosya kuyruğuna geriye dönük
düşer; köprü işi veriyoluna yazar, açılışta pending vps işlerini geri alır, koşarken kesileni failed yapar."""
import json
import os
import sys
import unittest
from unittest import mock

from tests import common  # noqa: F401

from ghost import bus

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops"))
import telegram_bridge as tb  # noqa: E402  — ÖNCE: mac_worker ~/jarvis/ops'u yola ekler, köprü bu ağaçtan gelsin
import mac_worker  # noqa: E402


class _R:
    def __init__(self, out="", rc=0):
        self.stdout, self.stderr, self.returncode = out, "", rc


class MacIsciVeriyolu(unittest.TestCase):
    def setUp(self):
        mac_worker._FREEZE_ONBELLEK[:] = [0.0, False]
        self.patches = [mock.patch.object(mac_worker, "log", lambda *a: None),
                        mock.patch.object(mac_worker, "send_telegram", lambda *a, **k: True),
                        mock.patch.object(mac_worker, "_kapi_kilitli", return_value=False),
                        mock.patch.object(mac_worker, "_dondu", return_value=False)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        mac_worker._FREEZE_ONBELLEK[:] = [0.0, False]

    def test_bus_isi_once_cekilir_kosar_bitirilir(self):
        ev = {"id": 41, "payload": {"prompt": "echo merhaba", "kind": "shell", "cmd": "echo merhaba",
                                    "timeout": 20, "sessiz": True}}
        ssh_cagri, bitir = [], []

        def ssh(cmd, timeout=30):
            ssh_cagri.append(cmd)
            if cmd.endswith("mac-claim"):
                return _R(json.dumps(ev) if len([c for c in ssh_cagri if c.endswith("mac-claim")]) == 1 else "")
            return _R("")       # ls: dosya kuyruğu boş

        def ssh_girdi(cmd, data, timeout=60):
            bitir.append((cmd, data))
            return _R("ok")
        with mock.patch.object(mac_worker, "ssh", ssh), mock.patch.object(mac_worker, "ssh_girdi", ssh_girdi):
            self.assertTrue(mac_worker.poll_once(quiet=True))
        self.assertEqual(len(bitir), 1)
        self.assertIn("mac-bitir 41 done", bitir[0][0])
        self.assertIn("merhaba", bitir[0][1])
        self.assertIn("## Sonuç", bitir[0][1])
        self.assertTrue(any("mac-claim" in c for c in ssh_cagri))
        self.assertTrue(any("ls " in c and "mac-queue" in c for c in ssh_cagri))   # dosya yolu hâlâ yoklanır

    def test_eski_bus_bos_cikti_dosya_kuyruguna_duser(self):
        job = {"id": 7, "kind": "shell", "cmd": "echo eski", "timeout": 20, "sessiz": True}
        yazilan = []

        def ssh(cmd, timeout=30):
            if cmd.endswith("mac-claim"):
                return _R("")                      # VPS'te eski bus: CLI yok, boş çıktı
            if cmd.startswith("ls "):
                return _R("/home/ghost/jarvis/ops/mac-queue/7.json\n")
            if "mv " in cmd and "cat " in cmd:
                return _R(json.dumps(job))
            yazilan.append(cmd)
            return _R("")
        with mock.patch.object(mac_worker, "ssh", ssh), \
                mock.patch.object(mac_worker, "ssh_write", lambda *a, **k: _R("")), \
                mock.patch.object(mac_worker, "ssh_girdi", side_effect=AssertionError("bus'a yazılmamalı")):
            self.assertTrue(mac_worker.poll_once(quiet=True))
        self.assertTrue(any("done/7.json" in c for c in yazilan))     # eski arşiv yolu korunur

    def test_vps_ulasilmazsa_sessiz(self):
        with mock.patch.object(mac_worker, "ssh", side_effect=OSError("ağ yok")):
            self.assertFalse(mac_worker.poll_once(quiet=True))

    def test_kosmadan_hata_geri_birakilir(self):
        ev = {"id": 5, "payload": {"prompt": "x", "reset": True}}
        geri = []

        def ssh(cmd, timeout=30):
            if cmd.endswith("mac-claim"):
                return _R(json.dumps(ev))
            if "mac-geri" in cmd:
                geri.append(cmd)
                return _R("ok")
            return _R("")
        with mock.patch.object(mac_worker, "ssh", ssh), \
                mock.patch.object(mac_worker, "_run_job", side_effect=RuntimeError("koşmadan patladı")):
            mac_worker.poll_once(quiet=True)
        self.assertEqual(len(geri), 1)
        self.assertIn("mac-geri 5", geri[0])

    def test_bus_claim_bozuk_cikti_none(self):
        with mock.patch.object(mac_worker, "ssh", return_value=_R("bu json değil")):
            self.assertIsNone(mac_worker._bus_claim())
        with mock.patch.object(mac_worker, "ssh", return_value=_R("", rc=1)):
            self.assertIsNone(mac_worker._bus_claim())


class _Mgr:
    def __init__(self):
        self.calls = []
        self.jobs = {}

    def enqueue_job(self, text, kind="iş", model=None, bus_id=None, ust_id=None):
        self.calls.append((text, kind, model, bus_id))
        return 100 + len(self.calls)


class KopruVeriyolu(unittest.TestCase):
    def setUp(self):
        for h in ("vps", "mac"):
            while bus.is_sahiplen(h):
                pass
        self.pl = mock.patch.object(tb, "log", lambda *a: None)
        self.pl.start()

    def tearDown(self):
        self.pl.stop()

    def test_acilista_pending_geri_alinir_claimed_failed(self):
        bekleyen = bus.is_ekle("kuyrukta kalan", kind="rapor", hedef="vps", model="sonnet")
        kosan = bus.is_ekle("koşarken kesilen", hedef="vps")
        bus.mark(kosan, "claimed")
        bos = bus.is_ekle("", hedef="vps")                      # prompt'suz kayıt → failed, kuyruğa girmez
        mac = bus.is_ekle("mac işi", hedef="mac")               # mac işi köprüye GİRMEZ
        mgr = _Mgr()
        geri = tb._bekleyen_isleri_yukle(mgr)
        self.assertEqual(geri, [(101, bekleyen)])
        self.assertEqual(mgr.calls, [("kuyrukta kalan", "rapor", "sonnet", bekleyen)])
        self.assertEqual(bus.is_oku(kosan)["status"], "failed")
        self.assertEqual(bus.is_oku(bos)["status"], "failed")
        self.assertEqual(bus.is_oku(mac)["status"], "pending")
        bus.mark(mac, "done")

    def test_queue_mac_veriyoluna_yazar_dosya_yazmaz(self):
        class _S:
            chat_id = 42
        with mock.patch("builtins.open", side_effect=AssertionError("dosya kuyruğu yazılmamalı")):
            jid = tb.Manager.queue_mac(_S(), "ekranı al", rc=True)
        d = bus.is_oku(jid)
        self.assertEqual((d["hedef"], d["kind"], d["status"]), ("mac", "mac", "pending"))
        self.assertEqual(d["payload"], {"prompt": "ekranı al", "kind": "mac", "reset": False, "rc": True,
                                        "chat_id": 42})
        bus.mark(jid, "done")

    def test_reconcile_bus_id_tasir(self):
        self.assertTrue(callable(tb._reconcile_jobs))
        import inspect
        kaynak = inspect.getsource(tb._reconcile_jobs)
        self.assertIn('j.get("bus_id")', kaynak)


if __name__ == "__main__":
    unittest.main()


class FreezeGeriAlma(unittest.TestCase):
    def setUp(self):
        while bus.is_sahiplen("vps"):
            pass
        self.pl = mock.patch.object(tb, "log", lambda *a: None)
        self.pl.start()

    def tearDown(self):
        self.pl.stop()
        tb._FREEZE_ONCEKI[0] = None

    def test_freezede_kuyruga_alinmaz_unfreeze_kenarinda_alinir(self):
        b = bus.is_ekle("donmuşken bekleyen", hedef="vps")
        mgr = _Mgr()
        with mock.patch.object(tb.governance, "is_frozen", return_value=True):
            self.assertEqual(tb._bekleyen_isleri_yukle(mgr), [])
        self.assertEqual(bus.is_oku(b)["status"], "pending")
        tb._FREEZE_ONCEKI[0] = True                      # önceki tur FREEZE'deydi
        with mock.patch.object(tb.governance, "is_frozen", return_value=False):
            self.assertIsNone(tb.freeze_uygula(mgr))     # düşen kenar
        self.assertEqual(mgr.calls, [("donmuşken bekleyen", "iş", None, b)])
