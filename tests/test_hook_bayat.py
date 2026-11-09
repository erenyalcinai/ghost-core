"""ghost-hook bayat denetimi yalnız webhook'un açılışta yüklediği ghost/* dosyalarına bakar.

NEDEN (2026-11-03, Sahip "bu yubikey bildirimi ne, neden her gece geliyor"): denetim tüm ghost/
paketine + dpkg'ye bakıyordu; webhook'un yüklemediği bir dosyaya giren her commit gece YubiKey'li
"Root işleri" kartı açtırıyordu (6 gecede 6 yanlış alarm)."""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

from tests import common  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "ops"))
import selfcheck  # noqa: E402


class HookBayatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        g = os.path.join(self.tmp, "ghost")
        os.makedirs(g)
        dosyalar = {
            "__init__.py": "",
            "webhook.py": "import os\nfrom ghost import bus, config\ntry:\n    from ghost import ek\n"
                          "except ImportError:\n    pass\n\ndef f():\n    from ghost import tembel\n",
            "bus.py": "from . import derin\n",
            "config.py": "",
            "ek.py": "",
            "derin.py": "",
            "tembel.py": "",
            "ilgisiz.py": "",
        }
        self.eski = time.time() - 3600
        for ad, icerik in dosyalar.items():
            yol = os.path.join(g, ad)
            with open(yol, "w") as f:
                f.write(icerik)
            os.utime(yol, (self.eski, self.eski))
        self.g = g

    def _ts(self):
        with mock.patch.object(selfcheck, "BASE", self.tmp):
            return selfcheck._modul_kod_ts("webhook")

    def _dokun(self, ad):
        os.utime(os.path.join(self.g, ad), None)

    def test_ilgisiz_ve_tembel_dosya_bayat_saymaz(self):
        self._dokun("ilgisiz.py")
        self._dokun("tembel.py")
        self.assertAlmostEqual(self._ts(), self.eski, delta=2)

    def test_dogrudan_gecisli_ve_try_icindeki_bagimlilik_sayar(self):
        for ad in ("webhook.py", "derin.py", "ek.py"):
            self.setUp()
            self._dokun(ad)
            self.assertGreater(self._ts(), self.eski + 1000, ad)


if __name__ == "__main__":
    unittest.main()
