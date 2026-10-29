"""ghost/metrik.py — tur gecikmesi kayıtları, p50/p95 özeti, gün bazlı sayaçlar (2026-10-29 Faz 0.1)."""
import os
import time
import unittest

from tests import common  # noqa: F401

from ghost import config, metrik


class TestTurKaydi(unittest.TestCase):
    def setUp(self):
        for ad in ("metrik.jsonl", "metrik_sayac.json"):
            try:
                os.remove(os.path.join(config.OPS, ad))
            except OSError:
                pass
        from ghost import durum
        durum.sil(metrik.SAYAC_AD)

    def test_kayit_ve_ozet(self):
        t0 = time.time() - 100
        for ilk, toplam in ((2, 10), (4, 20), (6, 30), (8, 40)):
            metrik.tur_kaydet("kokpit", t0, t0 + ilk, t0 + toplam, arac=3, uzunluk=500)
        metrik.tur_kaydet("kokpit", t0, None, t0 + 1800, zaman_asimi=True)
        o = metrik.ozet(7)
        self.assertTrue(o["var"])
        self.assertEqual(o["tur"], 5)
        self.assertEqual(o["kayip"], 1)
        self.assertAlmostEqual(o["ilk_p50"], 6.0, delta=2.0)
        self.assertGreaterEqual(o["toplam_p95"], 1000)      # zaman aşımı turu p95'i yukarı çeker
        self.assertEqual(metrik.bugun("kayip_cevap"), 1)      # zaman aşımı sayaç da yazar

    def test_eski_kayitlar_pencereye_girmez(self):
        eski = time.time() - 30 * 86400
        metrik.tur_kaydet("kokpit", eski, eski + 1, eski + 5)
        self.assertFalse(metrik.ozet(7)["var"])
        self.assertTrue(metrik.ozet(60)["var"])

    def test_bos_dosya_fırlatmaz(self):
        o = metrik.ozet(7)
        self.assertFalse(o["var"])
        self.assertIsNone(o["ilk_p50"])
        self.assertIn("henüz kayıt yok", metrik.metin(7))


class TestSarmalayici(unittest.TestCase):
    def test_ilk_cikti_ve_arac_sayisi(self):
        gorulen = []
        cb = metrik.ilk_cikti_sarmala(gorulen.append)
        self.assertEqual(metrik.ilk_cikti(), (None, 0))
        cb('{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash"}]}}')
        cb('{"type":"assistant","message":{"content":[{"type":"text","text":"x"}]}}')
        ts, arac = metrik.ilk_cikti()
        self.assertIsNotNone(ts)
        self.assertEqual(arac, 1)
        self.assertEqual(len(gorulen), 2)          # iç geri-çağrı aynen çağrıldı

    def test_ic_geri_cagri_yoksa_sorun_yok(self):
        cb = metrik.ilk_cikti_sarmala(None)
        cb("satır")
        self.assertIsNone(metrik.ilk_cikti()[0])      # tanınmayan satır görünür çıktı değil
        cb('{"type":"assistant","message":{"content":[{"type":"text","text":"x"}]}}')
        self.assertIsNotNone(metrik.ilk_cikti()[0])

    def test_init_olayi_ilk_cikti_sayilmaz(self):
        """ilk_sn hep 0,0 çıkıyordu: stream'in ilk satırı system/init anında geliyordu (2026-10-29)."""
        cb = metrik.ilk_cikti_sarmala(None)
        cb('{"type":"system","subtype":"init","model":"x","session_id":"s"}')
        self.assertIsNone(metrik.ilk_cikti()[0])
        cb('{"type":"user","message":{"content":[]}}')
        self.assertIsNone(metrik.ilk_cikti()[0])
        cb('{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash"}]}}')
        self.assertEqual(metrik.ilk_cikti()[1], 1)
        self.assertIsNotNone(metrik.ilk_cikti()[0])


class TestSayac(unittest.TestCase):
    def setUp(self):
        try:
            os.remove(os.path.join(config.OPS, "metrik_sayac.json"))
        except OSError:
            pass

    def test_gun_bazli_toplam(self):
        self.assertEqual(metrik.sayac("kota_dolu"), 1)
        self.assertEqual(metrik.sayac("kota_dolu", 2), 3)
        eski_gun = time.strftime("%F", time.localtime(time.time() - 10 * 86400))
        metrik.sayac("kota_dolu", 5, gun=eski_gun)
        self.assertEqual(metrik.sayac_toplam(7)["kota_dolu"], 3)
        self.assertEqual(metrik.sayac_toplam(30)["kota_dolu"], 8)
        self.assertEqual(metrik.bugun("kota_dolu"), 3)
        self.assertIn("kota_dolu 3", metrik.metin(7))

    def test_cok_eski_gun_budanir(self):
        cok_eski = time.strftime("%F", time.localtime(time.time() - 120 * 86400))
        metrik.sayac("x", 1, gun=cok_eski)
        metrik.sayac("y", 1)
        self.assertNotIn(cok_eski, metrik._sayac_oku())


if __name__ == "__main__":
    unittest.main()
