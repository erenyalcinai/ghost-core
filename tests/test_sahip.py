"""Sahip profili (ghost/sahip.py): bozuk dosya importu kırmaz, örnek/boşa düşer, selfcheck uyarır."""
import json
import os
import tempfile
import unittest
from unittest import mock

from ghost import sahip


class Profil(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        sahip.profil(yenile=True)

    def tearDown(self):
        os.environ.pop("GHOST_SAHIP", None)
        sahip.profil(yenile=True)

    def _yaz(self, metin):
        p = os.path.join(self.tmp, "sahip.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write(metin)
        os.environ["GHOST_SAHIP"] = p
        return p

    def test_gecerli_profil_okunur(self):
        self._yaz(json.dumps({"ad": "Test Kişi", "masa_kok": ["A", "B"], "siteler": [
            {"alan": "a.example", "url": "https://a.example", "izle": True, "trafik": True, "strict200": True}]}))
        self.assertEqual(sahip.ad(), "Test Kişi")
        self.assertEqual(sahip.masa_kok(), ("A", "B"))
        self.assertEqual(sahip.izlenen_urller(), ["https://a.example"])
        self.assertEqual(sahip.strict200(), {"https://a.example"})
        self.assertEqual(sahip.trafik_siteleri(), ("a.example",))
        self.assertIsNone(sahip.HATA)
        self.assertIsNone(sahip.durum())

    def test_bozuk_json_importu_kirmaz_ve_uyarir(self):
        self._yaz("{bozuk")
        self.assertEqual(sahip.profil(yenile=True), {})
        self.assertIn("sahip.json", sahip.HATA)
        self.assertEqual(sahip.ad(), "Sahip")
        self.assertEqual(sahip.masa_kok(), ("Ghost", "Raporlar", "Paylaşımlar"))
        self.assertIn("BOZUK", sahip.durum())

    def test_liste_profil_sozluk_degil(self):
        self._yaz("[1,2]")
        self.assertEqual(sahip.profil(yenile=True), {})
        self.assertIn("sözlük değil", sahip.HATA)

    def test_gercek_dosya_yoksa_ornege_duser(self):
        with mock.patch.object(sahip, "adaylar", return_value=[os.path.join(self.tmp, "yok.json"),
                                                               os.path.join(self.tmp, "sahip.example.json")]):
            with open(os.path.join(self.tmp, "sahip.example.json"), "w", encoding="utf-8") as f:
                json.dump({"ad": "Örnek"}, f)
            self.assertEqual(sahip.profil(yenile=True).get("ad"), "Örnek")
            self.assertIn("örnek profille", sahip.durum())


if __name__ == "__main__":
    unittest.main()
