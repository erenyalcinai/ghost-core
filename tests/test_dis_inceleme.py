"""Dış inceleme (2026-10-27) düzeltmeleri: denetim günlüğü sessizce kaybolmaz; kritik işte
'doğrulanamadı' başarı sayılmaz."""
import os
import tempfile
import unittest
from unittest import mock

from tests import common  # noqa: F401
from ghost import config, governance, verify


class AuditYedek(unittest.TestCase):
    def test_yazilamazsa_yedege_duser(self):
        d = tempfile.mkdtemp()
        yedek = os.path.join(d, "yedek.jsonl")
        with mock.patch.object(config, "AUDIT_LOG", os.path.join(d, "yok", "audit.jsonl")), \
             mock.patch.object(governance, "AUDIT_YEDEK", yedek):
            rec = governance.audit("test", "x", governance.READ, "ayrıntı")
        self.assertIn("audit_hatasi", rec)
        self.assertIn("ayrıntı", open(yedek, encoding="utf-8").read())


class Dogrulanamadi(unittest.TestCase):
    def test_kritik_iste_engeller_okumada_engellemez(self):
        self.assertTrue(verify.blocks(verify.BELIRSIZ, "siteyi deploy et"))
        self.assertTrue(verify.blocks(verify.BELIRSIZ, "aboneliği satın al"))
        self.assertFalse(verify.blocks(verify.BELIRSIZ, "son 7 günün trafiğini özetle"))
        self.assertFalse(verify.blocks(verify.BELIRSIZ))
        self.assertTrue(verify.blocks("eksik"))


if __name__ == "__main__":
    unittest.main()
