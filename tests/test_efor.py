"""ghost/efor.py — /efor ayarı: durum katmanı, CLI bayrağı, kalıcı oturum yeniden açılışı, özerk istisnası (2026-11-02)."""
import os
import tempfile
import unittest
from unittest import mock

from tests import common  # noqa: F401

from ghost import efor, kalici_oturum, motor, semalar
from tests.test_kalici_oturum import _sahte_claude


def _temizle():
    c = semalar.baglan()
    c.execute("DELETE FROM durum WHERE ad='efor'")
    c.commit()
    c.close()


class TestEfor(unittest.TestCase):
    def setUp(self):
        _temizle()

    tearDown = setUp

    def test_coz_ve_varsayilan(self):
        self.assertIsNone(efor.oku())
        self.assertEqual(efor.coz("YÜKSEK"), "high")
        self.assertEqual(efor.coz("default"), efor.VARSAYILAN)
        self.assertIsNone(efor.coz("çokçok"))
        with self.assertRaises(ValueError):
            efor.yaz("çokçok")
        self.assertEqual(efor.yaz("max"), "max")
        self.assertEqual(efor.oku(), "max")
        self.assertIsNone(efor.yaz("varsayilan"))
        self.assertIsNone(efor.oku())
        self.assertEqual(efor.arg(None), [])
        self.assertEqual(efor.arg("low"), ["--effort", "low"])

    def test_kalici_oturum_efor_degisince_yeniden_acilir(self):
        kalici_oturum.DURUM_DOSYASI = os.path.join(tempfile.mkdtemp(), "kalici.json")
        k = kalici_oturum.KaliciOturum(_sahte_claude(), [], bosta_sn=0)
        try:
            k.baslat("fable", "sid-1", resume=False, efor="high")
            self.assertIn("--effort", k.proc.args)
            self.assertTrue(k.uygun("fable", "sid-1", "high"))
            self.assertFalse(k.uygun("fable", "sid-1", "max"))
            self.assertFalse(k.uygun("fable", "sid-1"))          # varsayılana dönüş de yeniden açar
            k.baslat("fable", "sid-1", resume=True)
            self.assertNotIn("--effort", k.proc.args)
            self.assertTrue(k.uygun("fable", "sid-1", None))
        finally:
            k.kapat()

    def test_run_claude_bayrak_ve_ozerk_istisnasi(self):
        efor.yaz("xhigh")
        gorulen = []

        class Sahte:
            def __init__(self, cmd, **kw):
                gorulen.append(cmd)
                self.returncode = 0

            def communicate(self, timeout=None):
                return ('{"result": "ok", "session_id": "s"}', "")

        with mock.patch.object(motor.subprocess, "Popen", Sahte), \
                mock.patch.object(motor.modeller, "tam_model", lambda m: m):
            motor.run_claude("selam", "opus", use_mcp=False)
            motor.run_claude("selam", "opus", use_mcp=False, ozerk=True)
            motor.run_claude("selam", "opus", use_mcp=False, efor="low")
        self.assertEqual(gorulen[0][gorulen[0].index("--effort") + 1], "xhigh")
        self.assertNotIn("--effort", gorulen[1])
        self.assertEqual(gorulen[2][gorulen[2].index("--effort") + 1], "low")


if __name__ == "__main__":
    unittest.main()
