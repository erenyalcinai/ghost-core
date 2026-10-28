"""ops/mac_cmd.py — yedek hat kuyruk kimliği (Codex bulgu 4, 2026-10-28): aynı zaman diliminde iki iş birbirini ezmesin,
yarım yazılmış iş dosyası kuyrukta görünmesin."""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

from tests import common  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops"))
import mac_cmd  # noqa: E402


class KuyrukKimligi(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.q = os.path.join(self.d, "mac-queue")
        self.p1 = mock.patch.object(mac_cmd, "QUEUE", self.q)
        self.p2 = mock.patch.object(mac_cmd, "AUDIT", os.path.join(self.d, "audit.jsonl"))
        self.p1.start(); self.p2.start()

    def tearDown(self):
        self.p1.stop(); self.p2.stop()

    def test_ayni_dilimde_iki_is_birbirini_ezmez(self):
        sabit = time.time()
        with mock.patch.object(mac_cmd.time, "time", lambda: sabit):
            a, _ = mac_cmd.calistir("echo a", bekleme=0)
            b, _ = mac_cmd.calistir("echo b", bekleme=0)
        dosyalar = sorted(f for f in os.listdir(self.q) if f.endswith(".json"))
        self.assertEqual(len(dosyalar), 2, dosyalar)
        isler = [json.load(open(os.path.join(self.q, f), encoding="utf-8")) for f in dosyalar]
        self.assertEqual(sorted(i["cmd"] for i in isler), ["echo a", "echo b"])
        for f, i in zip(dosyalar, isler):
            self.assertEqual(f, "%d.json" % i["id"])          # dosya adı = içerideki kimlik (işçi [0-9]*.json bekler)
        self.assertEqual([f for f in os.listdir(self.q) if f.startswith(".")], [])   # geçici dosya kalmaz

    def test_sonuc_dosyasi_kimlige_gore_okunur(self):
        os.makedirs(os.path.join(self.q, "done"))
        sabit = time.time()
        with mock.patch.object(mac_cmd.time, "time", lambda: sabit), \
                mock.patch.object(mac_cmd.time, "sleep", lambda s: None):
            jid = int(sabit * 1000) % 2147483647
            with open(os.path.join(self.q, "done", "%d.sonuc.md" % jid), "w", encoding="utf-8") as f:
                f.write("# r\n## Sonuç\nmerhaba\n")
            metin, bitti = mac_cmd.calistir("echo x", bekleme=1)
        self.assertTrue(bitti)
        self.assertEqual(metin, "merhaba")


if __name__ == "__main__":
    unittest.main()
