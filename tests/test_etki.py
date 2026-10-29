"""ghost/etki.py — deploy → 24 sa / 7 gün etki kıyası (Faz 1.8, 2026-10-29)."""
import json
import unittest

from tests import common  # noqa: F401

from ghost import etki

M0 = {"appb_state": "READY_FOR_SALE", "appb_reviews": 1, "appb_puan7": 0, "sentry_open": 2, "asc_state": "PREPARE_FOR_SUBMISSION"}
M1 = {"appb_state": "READY_FOR_SALE", "appb_reviews": 3, "appb_puan7": 4.5, "sentry_open": 1, "asc_state": "READY_FOR_SALE"}


class TestEtki(unittest.TestCase):
    def setUp(self):
        c = etki._conn()
        c.execute("DELETE FROM surumler")
        c.commit()
        c.close()

    def test_kaydet_tekil_ve_olculer(self):
        a = etki.kaydet("appb", "1.1 (4)", "App Store", okuyucu=lambda: M0, simdi=1000.0)
        self.assertEqual(etki.kaydet("App B".lower().replace(" ", ""), "1.1 (4)"), a)
        k = etki.liste()[0]
        taban = json.loads(k["taban"])
        self.assertEqual(taban["appb_reviews"], 1)
        self.assertNotIn("asc_state", taban)                 # AppA ölçüsü App B kaydına girmez
        with self.assertRaises(ValueError):
            etki.kaydet("", "x")

    def test_tick_24s_ve_7g_sonra_tablo(self):
        yayinlanan = []
        a = etki.kaydet("appb", "1.1", okuyucu=lambda: M0, simdi=1000.0)
        self.assertEqual(etki.tick(simdi=1000.0 + 3600, okuyucu=lambda: M1, yayinla=lambda k, m: yayinlanan.append(m)), [])
        self.assertEqual(etki.tick(simdi=1000.0 + 25 * 3600, okuyucu=lambda: M1, yayinla=lambda k, m: yayinlanan.append(m)), [a])
        self.assertEqual(etki.liste()[0]["durum"], "bekliyor")
        self.assertIn("| yorum | 1 | 3 | +2 | … |  |", yayinlanan[-1])
        self.assertEqual(etki.tick(simdi=1000.0 + 8 * 86400, okuyucu=lambda: M1, yayinla=lambda k, m: yayinlanan.append(m)), [a])
        self.assertEqual(etki.liste()[0]["durum"], "tamam")
        self.assertIn("| açık hata | 2 | 1 | -1 | 1 | -1 |", yayinlanan[-1])
        self.assertEqual(etki.tick(simdi=1000.0 + 9 * 86400, okuyucu=lambda: M1, yayinla=lambda k, m: yayinlanan.append(m)), [])

    def test_otomatik_asc_ready(self):
        ids = etki.otomatik({"asc_state": "WAITING_FOR_REVIEW"}, {"asc_state": "READY_FOR_SALE", "d1_users": 5}, simdi=1000.0)
        self.assertEqual(len(ids), 1)
        k = etki.liste()[0]
        self.assertEqual(k["urun"], "appa")
        self.assertIn("mağaza", k["etiket"])
        self.assertEqual(json.loads(k["taban"])["d1_users"], 5)
        self.assertEqual(etki.otomatik({"asc_state": "READY_FOR_SALE"}, {"asc_state": "READY_FOR_SALE"}), [])   # değişim yok
        self.assertEqual(etki.otomatik({}, {"appb_state": "READY_FOR_SALE"}), [])                             # ilk görüş, taban yok

    def test_rapor_tabansiz(self):
        a = etki.kaydet("site:sahip.kim", "2026-10-29 deploy", okuyucu=lambda: {}, simdi=1.0)
        k = etki.liste()[0]
        self.assertIn("taban ölçüsü yok", etki.rapor(k))
        self.assertEqual(etki._main(["liste"]), 0)
        self.assertEqual(etki._main(["rapor", str(a)]), 0)


if __name__ == "__main__":
    unittest.main()
