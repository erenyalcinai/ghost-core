"""ops/mac_cmd.py — yedek hat artık veriyolu kuyruğunda (Faz 3.11, 2026-10-30): iş type=job hedef=mac kind=shell
olarak düşer, sonuç events.sonuc'tan okunur; aynı dilimde iki iş ayrı kimlik alır (eski dosya çakışması yapısal yok)."""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

from tests import common  # noqa: F401

from ghost import bus

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops"))
import mac_cmd  # noqa: E402


class KuyrukVeriyolu(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p2 = mock.patch.object(mac_cmd, "AUDIT", os.path.join(self.d, "audit.jsonl"))
        self.p2.start()
        while bus.is_sahiplen("mac"):
            pass

    def tearDown(self):
        self.p2.stop()

    def test_ayni_dilimde_iki_is_ayri_kimlik(self):
        sabit = time.time()
        with mock.patch.object(mac_cmd.time, "time", lambda: sabit):
            a, _ = mac_cmd.calistir("echo a", bekleme=0)
            b, _ = mac_cmd.calistir("echo b", bekleme=0)
        bekleyen = bus.bekleyen_isler("mac")
        self.assertEqual(sorted(e["payload"]["cmd"] for e in bekleyen), ["echo a", "echo b"])
        for e in bekleyen:
            self.assertEqual(e["kind"], "shell")
            self.assertEqual(e["payload"]["kind"], "shell")
            self.assertTrue(e["payload"]["sessiz"])
        self.assertIn("kuyrukta bekliyor", a)
        self.assertIn("ghost.bus oku", b)

    def test_sonuc_veriyolundan_kimlige_gore_okunur(self):
        with mock.patch.object(mac_cmd.time, "sleep", lambda s: None), \
                mock.patch.object(bus, "is_oku", side_effect=lambda i: {"status": "done",
                                                                        "sonuc": "# r\n## Sonuç\nmerhaba\n"}):
            metin, bitti = mac_cmd.calistir("echo x", bekleme=1)
        self.assertTrue(bitti)
        self.assertEqual(metin, "merhaba")

    def test_failed_bos_sonuc_aciklanir(self):
        with mock.patch.object(mac_cmd.time, "sleep", lambda s: None), \
                mock.patch.object(bus, "is_oku", return_value={"status": "failed", "sonuc": ""}):
            metin, bitti = mac_cmd.calistir("echo x", bekleme=1)
        self.assertTrue(bitti)
        self.assertIn("failed", metin)


if __name__ == "__main__":
    unittest.main()
