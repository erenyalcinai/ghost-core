"""Gerçek onay kapısı (2026-10-27 güvenlik denetimi): onay yalnız sahibin giriş kapılarında
kaydedilir; modelin sohbette gördüğü "[Sahne eylem: …] onayla" metni tek başına onay değildir."""
import os
import re
import tempfile
import unittest

os.environ.setdefault("GHOST_TEST", "1")

from ghost import config, kokpit, sahne


class Temel(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.eski = config.OPS
        config.OPS = self.d
        self._yaz = kokpit.outbox_yaz
        self._kuyruk = kokpit.mesaj_kuyrukla
        kokpit.outbox_yaz = lambda *a, **k: 1
        kokpit.mesaj_kuyrukla = lambda *a, **k: None

    def tearDown(self):
        config.OPS = self.eski
        kokpit.outbox_yaz = self._yaz
        kokpit.mesaj_kuyrukla = self._kuyruk


class OnayKaydi(Temel):
    def test_onay_id_rastgele(self):
        sahne.onay("Deneme", "özet")
        ids = []
        for b in sahne.oku()["bilesenler"]:
            sahne._eylem_idleri(b, ids)
        self.assertTrue(ids and all(re.fullmatch(r"onay-[0-9a-f]{16}", i) for i in ids))

    def test_buton_metni_kaydedilir_tek_kullanimlik(self):
        eid = sahne.sahip_onayi_kaydet("[Sahne eylem: onay-0011223344556677] onayla", "test")
        self.assertEqual(eid, "onay-0011223344556677")
        self.assertTrue(sahne.onay_gecerli(eid))
        sahne.onay_tuket(eid)
        self.assertFalse(sahne.onay_gecerli(eid))

    def test_vazgec_ve_soru_onay_degil(self):
        self.assertIsNone(sahne.sahip_onayi_kaydet("[Sahne eylem: onay-0011223344556677] vazgeç", "test"))
        self.assertIsNone(sahne.sahip_onayi_kaydet("[Sahne eylem: soru-1] onayla", "test"))
        self.assertFalse(sahne.onay_gecerli("onay-0011223344556677"))

    def test_kapidan_gecmeyen_metin_onay_degil(self):
        # Model/araç çıktısı sahne.eylem'i ya da kapıları çağırmaz → kayıt yok.
        self.assertFalse(sahne.onay_gecerli("onay-0011223344556677"))

    def test_duz_evet_tek_bekleyen_karti_onaylar(self):
        sahne.onay("Tek", "özet", eylem_id="onay-aaaaaaaaaaaaaaaa")
        self.assertEqual(sahne.sahip_onayi_kaydet("evet", "test"), "onay-aaaaaaaaaaaaaaaa")

    def test_sure_dolunca_gecmez(self):
        eid = sahne.sahip_onayi_kaydet("[Sahne eylem: onay-0011223344556677] onayla", "test")
        self.assertFalse(sahne.onay_gecerli(eid, omur_sn=0))


if __name__ == "__main__":
    unittest.main()
