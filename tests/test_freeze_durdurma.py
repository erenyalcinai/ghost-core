"""FREEZE gerçek acil durdurma (2026-10-28, dış inceleme GPT: '/freeze yeni işi durduruyor ama koşanı
kesmiyor'). Köprü FREEZE kenarında çalışan işleri iptal edip sohbet oturumunu keser; Mac işçisi FREEZE'de
yeni iş almaz, koşan sürecini grubuyla öldürür."""
import os
import sys
import threading
import time
import unittest
from unittest import mock

from tests import common  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops"))
import telegram_bridge as tb  # noqa: E402
from ghost import motor  # noqa: E402
from ghost import governance  # noqa: E402
from ops import mac_worker  # noqa: E402


class SahteKalici:
    def __init__(self):
        self.kapandi = None
    def canli(self):
        return self.kapandi is None
    def kapat(self, neden=""):
        self.kapandi = neden


class SahteMgr:
    chat_id = 1
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = {1: {"status": "çalışıyor"}, 2: {"status": "bitti"}}
        self.iptal = []
        self.kalici = SahteKalici()
    def cancel(self, jid):
        self.iptal.append(jid)


class KopruFreeze(unittest.TestCase):
    def setUp(self):
        governance.unfreeze()
        tb._FREEZE_ONCEKI[0] = False
        self.giden = []
        self.p = mock.patch.object(tb, "send", lambda cid, m: self.giden.append(m))
        self.p.start()

    def tearDown(self):
        self.p.stop()
        governance.unfreeze()

    def test_freeze_kenarinda_isler_ve_sohbet_kesilir_bir_kez(self):
        mgr = SahteMgr()
        self.assertIsNone(tb.freeze_uygula(mgr))           # FREEZE yok → dokunma
        self.assertEqual(mgr.iptal, [])
        governance.freeze("test")
        self.assertEqual(tb.freeze_uygula(mgr), 1)          # 1 aktif iş
        self.assertEqual(mgr.iptal, ["all"])
        self.assertEqual(mgr.kalici.kapandi, "FREEZE")
        self.assertTrue(any("FREEZE uygulandı" in m for m in self.giden))
        self.assertIsNone(tb.freeze_uygula(mgr))            # kenar: ikinci turda tekrar etmez
        self.assertEqual(mgr.iptal, ["all"])

    def test_acilista_freeze_zaten_varsa_uygulanir(self):
        tb._FREEZE_ONCEKI[0] = None
        governance.freeze("test")
        mgr = SahteMgr()
        tb.freeze_uygula(mgr)
        self.assertEqual(mgr.iptal, ["all"])


class OzerkIsaret(unittest.TestCase):
    """2026-10-28: özerk iş Claude sürecine GHOST_OZERK=1 ile koşar (kapı eylemi reddeder); diğerleri işaretsiz."""
    def _env(self, **kw):
        gorulen = {}

        def sahte_popen(cmd, **k):
            gorulen.update(k.get("env") or {})
            raise RuntimeError("koşma")
        with mock.patch.object(motor.subprocess, "Popen", sahte_popen):
            tb.run_claude("x", "sonnet", use_mcp=False, **kw)
        return gorulen

    def test_ozerk_isaretli_digeri_degil(self):
        self.assertEqual(self._env(ozerk=True).get("GHOST_OZERK"), "1")
        self.assertNotIn("GHOST_OZERK", self._env())
        self.assertNotIn("GHOST_OZERK", motor.ORTAM)         # genel ortam kirlenmez

    def test_ozerk_onek_ayraci(self):
        self.assertTrue((tb.AUTONOMOUS_GUARD + "iş").startswith(tb.AUTONOMOUS_GUARD))


class MacFreeze(unittest.TestCase):
    def setUp(self):
        mac_worker._FREEZE_ONBELLEK[:] = [0.0, False]
        self.pl = mock.patch.object(mac_worker, "log", lambda *a: None)
        self.pl.start()

    def tearDown(self):
        self.pl.stop()
        mac_worker._FREEZE_ONBELLEK[:] = [0.0, False]

    def test_freezede_yeni_is_alinmaz(self):
        with mock.patch.object(mac_worker, "_kapi_kilitli", return_value=False), \
             mock.patch.object(mac_worker, "_dondu", return_value=True), \
             mock.patch.object(mac_worker, "ssh", side_effect=AssertionError("kuyruk okunmamalı")):
            self.assertFalse(mac_worker.poll_once(quiet=True))

    def test_kosan_surec_freezede_oldurulur(self):
        durum = {"n": 0}

        def dondu(*a, **k):
            durum["n"] += 1
            return durum["n"] >= 1
        with mock.patch.object(mac_worker, "_dondu", side_effect=dondu), \
             mock.patch.object(mac_worker.threading.Event, "wait", lambda self, t=None: False):
            t0 = time.time()
            r = mac_worker._kosu(["/bin/sh", "-c", "sleep 30; echo bitti"], 60)
        self.assertLess(time.time() - t0, 15)
        self.assertNotIn("bitti", r.stdout or "")
        self.assertIn("FREEZE ile durduruldu", r.stderr)

    def test_freeze_yoksa_normal_cikti(self):
        with mock.patch.object(mac_worker, "_dondu", return_value=False):
            r = mac_worker._kosu(["/bin/sh", "-c", "echo merhaba"], 30)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "merhaba"))


if __name__ == "__main__":
    unittest.main()
