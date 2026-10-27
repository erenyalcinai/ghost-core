"""WDA USB yedeği yalnız 127.0.0.1'de dinler (2026-10-28): go-ios `forward` tüm arayüzlerde dinliyordu,
aynı Wi-Fi'daki herkes Ghost'un iPhone'unu kimliksiz sürebiliyordu. iproxy yoksa yedek hiç açılmaz."""
import os
import sys
import unittest
from unittest import mock

from tests import common  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops"))
import wda_relay as w  # noqa: E402


class WdaYerel(unittest.TestCase):
    def setUp(self):
        w._usb = None

    def _kos(self, iproxy):
        acilan = []

        class Surec:
            pid = 1
            def poll(self):
                return None
        with mock.patch.object(w, "IPROXY", iproxy), \
             mock.patch.object(w.os.path, "exists", return_value=True), \
             mock.patch.object(w.subprocess, "run", return_value=mock.Mock(stdout=w.UDID)), \
             mock.patch.object(w.subprocess, "Popen", side_effect=lambda cmd, **k: acilan.append(cmd) or Surec()), \
             mock.patch.object(w.time, "sleep"), mock.patch.object(w, "log"):
            return w._usb_yedegi(), acilan

    def test_iproxy_yalniz_yerel_adreste(self):
        uc, acilan = self._kos("/opt/homebrew/bin/iproxy")
        self.assertEqual(uc, ("127.0.0.1", w.USB_PORT))
        self.assertEqual(len(acilan), 1)
        self.assertIn("iproxy", acilan[0][0])
        self.assertEqual(acilan[0][acilan[0].index("-s") + 1], "127.0.0.1")
        self.assertNotIn("forward", acilan[0])

    def test_iproxy_yoksa_aga_acik_uc_acilmaz(self):
        uc, acilan = self._kos(None)
        self.assertIsNone(uc)
        self.assertEqual(acilan, [])


if __name__ == "__main__":
    unittest.main()
