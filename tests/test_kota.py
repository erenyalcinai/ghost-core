"""Abonelik kotası (ghost/kota.py): API cevabı → Nabız satırları, /kota metni, test izolasyonu."""
import json
import os
import tempfile
import unittest

from tests import common  # noqa: F401

from ghost import kota

ORNEK = {
    "five_hour": {"utilization": 84.0, "resets_at": "2026-09-07T03:00:00.233705+00:00"},
    "seven_day": {"utilization": 42.0, "resets_at": "2026-09-09T05:00:00.233729+00:00"},
    "limits": [
        {"kind": "session", "group": "session", "percent": 85, "severity": "warning",
         "resets_at": "2026-09-07T03:00:00.503807+00:00", "scope": None, "is_active": True},
        {"kind": "weekly_all", "group": "weekly", "percent": 42, "severity": "normal",
         "resets_at": "2026-09-09T05:00:00.503827+00:00", "scope": None, "is_active": False},
        {"kind": "weekly_scoped", "group": "weekly", "percent": 81, "severity": "warning",
         "resets_at": "2026-09-09T05:00:00.504056+00:00",
         "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None},
         "is_active": False},
    ],
}


class TestNormalize(unittest.TestCase):
    def test_uc_limit_satiri(self):
        s = kota.normalize(ORNEK)
        self.assertEqual([x["ad"] for x in s],
                         ["Oturum (5 saat)", "Haftalık · tüm modeller", "Haftalık · Fable"])
        self.assertEqual([x["yuzde"] for x in s], [85, 42, 81])
        self.assertEqual(s[0]["onem"], "warning")
        self.assertTrue(s[0]["aktif"])
        self.assertTrue(s[0]["sifirlanma"].startswith("2026-09-07T03:00:00"))
        self.assertIn(":", s[0]["sifirlanma_metin"])   # yerel saat metni üretildi

    def test_limits_yoksa_eski_alanlar(self):
        s = kota.normalize({k: v for k, v in ORNEK.items() if k != "limits"})
        self.assertEqual(len(s), 2)
        self.assertEqual(s[0]["yuzde"], 84)
        self.assertEqual(s[0]["onem"], "warning")

    def test_bos_cevap(self):
        self.assertEqual(kota.normalize({}), [])


class TestDosyaVeMetin(unittest.TestCase):
    def setUp(self):
        kota.KOTA_DOSYASI = os.path.join(tempfile.mkdtemp(), "kota.json")

    def test_okunmamis(self):
        k = kota.oku()
        self.assertFalse(k["var"])
        self.assertIn("Kota", kota.metin())

    def test_yenile_testte_disari_cikmaz(self):
        self.assertIsNone(kota.yenile())
        self.assertFalse(os.path.exists(kota.KOTA_DOSYASI))

    def test_oku_ve_metin(self):
        kota._yaz({"ts": 1, "at": "2026-09-07 05:00:00", "plan": "Max 5x",
                   "limitler": kota.normalize(ORNEK), "hata": None})
        k = kota.oku()
        self.assertTrue(k["var"])
        self.assertTrue(k["bayat"])          # ts=1 → çok eski
        self.assertEqual(len(k["limitler"]), 3)
        m = kota.metin()
        self.assertIn("Max 5x", m)
        self.assertIn("Haftalık · Fable: %81", m)
        self.assertIn("ESKİ", m)

    def test_plan_adi(self):
        self.assertEqual(kota._plan_adi({"plan": "max", "tier": "default_claude_max_5x"}), "Max 5x")
        self.assertEqual(kota._plan_adi({"plan": "", "tier": ""}), "")


class TestNabizAlani(unittest.TestCase):
    def test_health_kota_alani(self):
        from ghost import health
        self.assertIn("kota", health.nabiz())


if __name__ == "__main__":
    unittest.main()
