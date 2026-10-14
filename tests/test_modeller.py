"""Takma ad → en güncel tam kimlik çözümü (ghost/modeller).

Sabitlenen olgu (2026-10-01): CLI 2.1.258 "opus"u claude-opus-5'e çözüyor ve
tanımadığı claude-opus-5-5'i reddediyordu; API ise 5.5'i sunuyordu. Çözüm, iki
listenin KESİŞİMİNDEKİ en yeni olmalı; API ileride CLI gerideyse uyarı çıkmalı.
"""
from tests import common  # noqa: F401
import json
import os
import tempfile
import unittest

from ghost import modeller as m

API = ["claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1", "claude-opus-5",
       "claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5-20251001",
       "claude-opus-4-5-20251101"]
CLI_ESKI = ["claude-opus-5", "claude-opus-4-8", "claude-sonnet-5", "claude-fable-5-1",
            "claude-fable-5", "claude-haiku-4-5", "claude-haiku-4-5-20251001",
            "claude-opus-4-5-20251101", "claude-sonnet-3-7"]
CLI_YENI = CLI_ESKI + ["claude-opus-5-5", "claude-sonnet-5-5"]


class SafTest(unittest.TestCase):
    def test_surum_ve_aile(self):
        self.assertEqual(m.surum("claude-opus-5-5"), (5, 5))
        self.assertEqual(m.surum("claude-opus-5"), (5,))
        self.assertEqual(m.surum("claude-haiku-4-5-20251001"), (4, 5, 20251001))
        self.assertEqual(m.surum("gpt-5"), ())
        self.assertEqual(m.aile("claude-sonnet-5-5"), "sonnet")
        self.assertIsNone(m.aile("opus"))
        self.assertGreater(m.surum("claude-opus-5-5"), m.surum("claude-opus-5"))
        self.assertGreater(m.surum("claude-opus-5"), m.surum("claude-opus-4-8"))
        self.assertGreater(m.surum("claude-opus-5"), m.surum("claude-opus-4-5-20251101"))

    def test_kesisimin_en_yenisi(self):
        # Bayat CLI: API 5.5 sunsa da CLI bilmiyor → 5 (çalışan) seçilir.
        self.assertEqual(m.coz("opus", API, CLI_ESKI), "claude-opus-5")
        # Güncel CLI: 5.5.
        self.assertEqual(m.coz("opus", API, CLI_YENI), "claude-opus-5-5")
        self.assertEqual(m.coz("sonnet", API, CLI_YENI), "claude-sonnet-5-5")
        self.assertEqual(m.coz("fable", API, CLI_YENI), "claude-fable-5-1")
        self.assertEqual(m.coz("haiku", API, CLI_YENI), "claude-haiku-4-5-20251001")

    def test_tek_liste_ve_bos(self):
        self.assertEqual(m.coz("opus", API, []), "claude-opus-5-5")       # ağ var, ikili yok
        self.assertEqual(m.coz("opus", [], CLI_ESKI), "claude-opus-5")    # ağ yok
        self.assertIsNone(m.coz("opus", [], []))                           # ikisi de yok
        self.assertIsNone(m.coz("claude-opus-5", API, CLI_YENI))           # takma ad değil
        self.assertIsNone(m.coz("mythos", API, CLI_YENI))                  # takma ad listesinde yok

    def test_bayat_cli_uyarisi(self):
        self.assertIn("claude-opus-5-5", m.uyari_metni("opus", API, CLI_ESKI))
        self.assertEqual(m.uyari_metni("opus", API, CLI_YENI), "")
        self.assertEqual(m.uyari_metni("fable", API, CLI_ESKI), "")

    def test_ikili_kimlikleri(self):
        veri = b'\x00junk opus:"claude-opus-5-5" x claude-sonnet-5 claude-haiku-4-5-20251001\x00claude-opus-5-5'
        self.assertEqual(m.ikili_kimlikleri(veri),
                         ["claude-haiku-4-5-20251001", "claude-opus-5-5", "claude-sonnet-5"])


class KabukTest(unittest.TestCase):
    def test_testte_aga_ve_ikiliye_dokunulmaz(self):
        self.assertEqual(m.api_modeller(), [])
        self.assertEqual(m.cli_modeller(), [])
        self.assertEqual(m.tam_model("opus"), "opus")                 # çözülemedi → takma ad
        self.assertEqual(m.tam_model("claude-opus-4-8"), "claude-opus-4-8")
        self.assertEqual(m.tam_model(""), "")
        self.assertEqual(m.uyari(), [])

    def test_cli_onbellek_imzasi(self):
        # GHOST_TEST kapısını geçici kaldırıp sahte ikiliyle tarama + önbellek.
        d = tempfile.mkdtemp()
        yol = os.path.join(d, "claude")
        with open(yol, "wb") as f:
            f.write(b"\x00" * 100 + b'opus:"claude-opus-5-5"' + b"\x00" * 100 + b"claude-fable-5-1")
        os.environ.pop("GHOST_TEST")
        try:
            ob = {}
            ids = m.cli_modeller(yol, onbellek=ob)
            self.assertEqual(ids, ["claude-fable-5-1", "claude-opus-5-5"])
            self.assertEqual(ob["cli"]["ids"], ids)
            # İkili değişmedi → önbellek kullanılır (içeriği bozsak da aynı liste).
            ob["cli"]["ids"] = ["claude-opus-5"]
            self.assertEqual(m.cli_modeller(yol, onbellek=ob), ["claude-opus-5"])
            # İkili değişti (boyut) → yeniden taranır.
            with open(yol, "ab") as f:
                f.write(b" claude-sonnet-5-5 ")
            self.assertIn("claude-sonnet-5-5", m.cli_modeller(yol, onbellek=ob))
        finally:
            os.environ["GHOST_TEST"] = "1"

    def test_api_onbellek_taze_ise_ag_yok(self):
        os.environ.pop("GHOST_TEST")
        try:
            ob = {"api": {"ids": API, "ts": 1000}}
            self.assertEqual(m.api_modeller(onbellek=ob, simdi=1000 + 60), API)
            # Az önce ağ hatası aldıysa 15 dk denemez, eski listeyi verir.
            ob = {"api": {"ids": API, "ts": 0, "hata_ts": 5000}}
            self.assertEqual(m.api_modeller(onbellek=ob, simdi=5000 + 60), API)
        finally:
            os.environ["GHOST_TEST"] = "1"


if __name__ == "__main__":
    unittest.main()
