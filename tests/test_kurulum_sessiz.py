"""Mac'siz ve özel modülsüz kurulum (açık çekirdek kopyası) sessiz kalır (2026-11-15, kurucu §8.4):
öz denetim Mac'e bağlı denetimleri ve bulunmayan organ modüllerini günlüğe hata diye yazmaz; durum listesi
hiç kurulmamış servisi göstermez. Mac'i ve modülleri OLAN kurulumun davranışı değişmez."""
import os
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

from tests import common  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops"))
import selfcheck  # noqa: E402
from ghost import health  # noqa: E402


class MacTanimli(unittest.TestCase):
    def test_belirtec_olcutu(self):
        self.assertTrue(selfcheck.mac_tanimli({"GHOST_MACLINK_TOKEN": "sahte-belirtec"}))
        self.assertTrue(selfcheck.mac_tanimli({"GHOST_MACLINK_TOKEN": '"sahte-belirtec"'}))
        for bos in ({}, {"GHOST_MACLINK_TOKEN": ""}, {"GHOST_MACLINK_TOKEN": "  "}, {"GHOST_MACLINK_TOKEN": '""'}):
            self.assertFalse(selfcheck.mac_tanimli(bos))

    def _sahte_mac(self, komut):
        paket = types.ModuleType("ops")
        paket.mac = types.ModuleType("ops.mac")
        paket.mac.komut = komut
        return mock.patch.dict(sys.modules, {"ops": paket, "ops.mac": paket.mac})

    def test_mac_yokken_cli_guncelleme_sessiz(self):
        komut = mock.Mock(side_effect=AssertionError("Mac'siz kurulumda Mac aranmamalı"))
        with mock.patch.dict(os.environ, {"GHOST_TEST": ""}), self._sahte_mac(komut), \
                mock.patch.object(selfcheck, "mac_tanimli", return_value=False), \
                mock.patch.object(selfcheck, "MAC_CLI_STAMP", os.path.join(tempfile.mkdtemp(), "yok")), \
                mock.patch.object(selfcheck, "log") as log:
            self.assertEqual(selfcheck.mac_cli_guncel_tut(), [])
        komut.assert_not_called()
        log.assert_not_called()

    def test_mac_varken_cli_guncelleme_eskisi_gibi(self):
        komut = mock.Mock(return_value={"hata": "ulaşılamadı"})
        with mock.patch.dict(os.environ, {"GHOST_TEST": ""}), self._sahte_mac(komut), \
                mock.patch.object(selfcheck, "mac_tanimli", return_value=True), \
                mock.patch.object(selfcheck, "MAC_CLI_STAMP", os.path.join(tempfile.mkdtemp(), "yok")), \
                mock.patch.object(selfcheck, "log") as log:
            self.assertEqual(selfcheck.mac_cli_guncel_tut(), [])
        komut.assert_called_once()
        log.assert_called_once()          # Mac tanımlı ama ulaşılamıyor → günlük satırı (eski davranış)

    def test_mac_yokken_tunel_yedegi_kosmaz(self):
        with mock.patch.object(selfcheck, "IS_MAC", False), mock.patch.object(selfcheck, "mac_tanimli", return_value=False), \
                mock.patch.object(selfcheck.subprocess, "run", side_effect=AssertionError("koşmamalı")) as run, \
                mock.patch.object(selfcheck, "log") as log:
            self.assertIsNone(selfcheck.tunel_nobetcisi_yerinde())
        run.assert_not_called()
        log.assert_not_called()

    def test_mac_varken_bayat_tunel_nobetcisi_kosar(self):
        from ghost import durum
        with mock.patch.object(selfcheck, "IS_MAC", False), mock.patch.object(selfcheck, "mac_tanimli", return_value=True), \
                mock.patch.object(durum, "ts", return_value=1.0), \
                mock.patch.object(selfcheck.subprocess, "run",
                                  return_value=subprocess.CompletedProcess([], 1, "", "")) as run, \
                mock.patch.object(selfcheck, "log"):
            sorun = selfcheck.tunel_nobetcisi_yerinde()
        run.assert_called_once()
        self.assertIn("tünel", sorun)       # nöbetçi düzeltemedi → uyarı (eski davranış)


class IstegeBagli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        sys.path.insert(0, self.d)
        self.addCleanup(sys.path.remove, self.d)

    def _modul(self, ad, kaynak):
        with open(os.path.join(self.d, ad + ".py"), "w", encoding="utf-8") as f:
            f.write(kaynak)
        import importlib
        importlib.invalidate_caches()
        self.addCleanup(sys.modules.pop, ad, None)

    def test_yoksa_none(self):
        self.assertIsNone(selfcheck.istege_bagli("boyle_bir_organ_yok_zz"))
        self.assertIsNone(selfcheck.istege_bagli("boyle_bir_organ_yok_zz", "ghost"))
        self.assertIsNone(selfcheck.istege_bagli("x", "boyle_bir_paket_yok_zz"))

    def test_varsa_modul(self):
        self._modul("organ_var_zz", "def tick():\n    return ['iş']\n")
        self.assertEqual(selfcheck.istege_bagli("organ_var_zz").tick(), ["iş"])
        self.assertIs(selfcheck.istege_bagli("config", "ghost"), sys.modules["ghost.config"])

    def test_bozuk_modul_gizlenmez(self):
        """Dosya var ama içe aktarılamıyor: sessiz geçilmez, hata çağırana (ve günlüğe) çıkar."""
        self._modul("organ_bozuk_zz", "import boyle_bir_bagimlilik_yok_zz\n")
        with self.assertRaises(ImportError):
            selfcheck.istege_bagli("organ_bozuk_zz")


class DurumServisleri(unittest.TestCase):
    def _kos(self, aktif, yuk):
        """aktif: {birim: is-active çıktısı} · yuk: {birim: LoadState ya da Exception}. Döner (liste, sorulanlar)."""
        sorulan = []

        def sahte_run(argv, **kw):
            if argv[:2] == ["systemctl", "show"]:
                sorulan.append(argv[2])
                y = yuk.get(argv[2], "loaded")
                if isinstance(y, Exception):
                    raise y
                return subprocess.CompletedProcess(argv, 0, y + "\n", "")
            return subprocess.CompletedProcess(argv, 0, aktif.get(argv[-1], "active") + "\n", "")
        with mock.patch.object(health.platform, "system", return_value="Linux"), \
                mock.patch.object(health.subprocess, "run", side_effect=sahte_run):
            return dict(health.servisler()), sorulan

    def test_kurulmamis_webhook_listelenmez(self):
        d, _ = self._kos({"ghost-hook": "inactive"}, {"ghost-hook": "not-found"})
        self.assertNotIn("ghost-hook", d)
        self.assertEqual(set(d), {"ghost-bridge", "ghost-api", "ghost-nabiz", "ghost-sohbet"})

    def test_kurulu_ama_durmus_webhook_gorunur(self):
        for durum in ("inactive", "failed"):
            d, _ = self._kos({"ghost-hook": durum}, {"ghost-hook": "loaded"})
            self.assertEqual(d["ghost-hook"], durum)

    def test_calisan_kurulumda_ek_sorgu_yok(self):
        d, sorulan = self._kos({}, {})
        self.assertEqual(d["ghost-hook"], "active")
        self.assertEqual(sorulan, [])

    def test_sorulamazsa_eski_davranis(self):
        d, _ = self._kos({"ghost-hook": "inactive"}, {"ghost-hook": OSError("systemctl yok")})
        self.assertEqual(d["ghost-hook"], "inactive")

    def test_kopru_her_zaman_listelenir(self):
        d, sorulan = self._kos({"ghost-bridge": "inactive"}, {"ghost-bridge": "not-found"})
        self.assertEqual(d["ghost-bridge"], "inactive")
        self.assertNotIn("ghost-bridge", sorulan)


if __name__ == "__main__":
    unittest.main()
