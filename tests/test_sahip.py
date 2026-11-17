"""Sahip profili (ghost/sahip.py): bozuk dosya importu kırmaz, örnek/boşa düşer, selfcheck uyarır."""
import json
import os
import tempfile
import unittest
from unittest import mock

from tests import common
from ghost import sahip


class Profil(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # tests/common profili sabitler (GHOST_SAHIP); bu testler sabitlemesiz davranışı sınar → kaldır, bitince GERİ KOY
        # (geri konmazsa sıradaki test dosyaları kurulu kopyada sahibin profilini okur).
        self._onceki = os.environ.pop("GHOST_SAHIP", None)
        sahip.profil(yenile=True)

    def tearDown(self):
        os.environ.pop("GHOST_SAHIP", None)
        if self._onceki is not None:
            os.environ["GHOST_SAHIP"] = self._onceki
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

    def test_takma_alan_asil_siteye_eslenir(self):
        self._yaz(json.dumps({"siteler": [
            {"alan": "a.example", "trafik": True, "takma": ["a.la", "b.la"]},
            {"alan": "c.example", "takma": ["izlenmeyen.la"]},
            {"alan": "d.example", "trafik": True}]}))
        self.assertEqual(sahip.trafik_takma(), {"a.la": "a.example", "b.la": "a.example"})
        self.assertEqual(sahip.trafik_siteleri(), ("a.example", "d.example"))

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


class TestProfiliSabit(unittest.TestCase):
    """tests/common: testler yazıldıkları profile sabitlenir; kurulu kopyanın sahibinin profili okunmaz."""

    def _agac(self, **dosyalar):
        kok = tempfile.mkdtemp()
        os.makedirs(os.path.join(kok, "ops"))
        for ad, icerik in dosyalar.items():
            with open(os.path.join(kok, "ops", ad.replace("_", ".")), "w", encoding="utf-8") as f:
                f.write(icerik if isinstance(icerik, str) else json.dumps(icerik))
        return kok

    def test_hangi_dosya_secilir(self):
        ref = {"siteler": [{"alan": common.REFERANS_SITE}]}
        uye = {"ad": "Ada Deneme", "siteler": []}
        son = lambda kok: os.path.basename(common.test_profili(kok) or "")   # noqa: E731
        self.assertEqual(son(self._agac(sahip_example_json=ref, sahip_json=uye)), "sahip.example.json")   # kurulu kopya
        self.assertEqual(son(self._agac(sahip_example_json=ref)), "sahip.example.json")                   # temiz çekirdek
        self.assertEqual(son(self._agac(sahip_example_json={"siteler": [{"alan": "baska.example"}]},
                                        sahip_json=ref)), "sahip.json")                                    # özel ağaç
        self.assertEqual(son(self._agac(sahip_example_json="{bozuk", sahip_json="[1]")), "")              # okunamıyor
        self.assertEqual(son(self._agac()), "")

    def test_bu_kosuda_sabit(self):
        """Bu ağaçta referans profil varsa süreç ona sabitlenmiş olmalı (dışarıdan verilmediyse)."""
        beklenen = common.test_profili()
        simdiki = os.environ.get("GHOST_SAHIP")
        if beklenen and simdiki in (None, beklenen):       # None = önceki bir test sabitlemeyi düşürmüş → kırıl
            self.assertEqual(simdiki, beklenen)


if __name__ == "__main__":
    unittest.main()
