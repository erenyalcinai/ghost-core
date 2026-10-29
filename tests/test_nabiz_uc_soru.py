"""Nabız'ın üç sorusu: gun (bugün + sorular), isnabzi (iş + fark), health.nabiz alanları."""
import json
import os
import tempfile
import unittest

from tests import common  # noqa: F401  (GHOST_OPS/GHOST_DB geçici — durum tablosu canlı ghost.db'ye yazmasın)

from ghost import durum, gun, isnabzi


class Gun(unittest.TestCase):
    def test_bugun_sekli(self):
        b = gun.bugun()
        self.assertEqual(set(b) >= {"tarih", "satirlar", "sayilar", "commitler", "isler"}, True)
        self.assertTrue(1 <= len(b["satirlar"]) <= 4)
        for s in b["satirlar"]:
            self.assertIn("metin", s)

    def test_senkron_commitleri_dislar(self):
        self.assertTrue(gun.SENKRON.match("oto: bilgi senkron (vps)"))
        self.assertFalse(gun.SENKRON.match("kokpit: sahne"))

    def test_sorular_kurali(self):
        rows = [
            {"id": 1, "kaynak": "ghost", "metin": "Deploy edeyim mi?", "butonlar": None},
            {"id": 2, "kaynak": "sahip", "metin": "evet", "butonlar": None},
            {"id": 3, "kaynak": "ghost", "metin": "Bitti.\nŞimdi vitrini de güncelleyeyim mi?", "butonlar": None},
            {"id": 4, "kaynak": "ghost", "metin": "🖼 Sahne güncellendi", "butonlar": None},
            {"id": 5, "kaynak": "ghost", "metin": "Onay?", "butonlar": [{"etiket": "Evet", "veri": "ok:1"}]},
        ]
        from ghost import kokpit
        eski_son, eski_oku = kokpit.outbox_son, kokpit.outbox_oku
        kokpit.outbox_son = lambda: 5
        kokpit.outbox_oku = lambda since=0, limit=50, once=None: rows
        try:
            s = gun.sorular()
        finally:
            kokpit.outbox_son, kokpit.outbox_oku = eski_son, eski_oku
        self.assertEqual([x["id"] for x in s], [3, 5])          # 1 cevaplandı, 4 sahne notu
        self.assertEqual(s[0]["metin"], "Şimdi vitrini de güncelleyeyim mi?")
        self.assertTrue(s[1]["butonlu"])


class IsNabzi(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        from ghost import config
        self.eski = config.OPS
        config.OPS = self.d
        durum.sil("biz_state")
        durum.sil("biz_history")

    def tearDown(self):
        from ghost import config
        config.OPS = self.eski

    def _yaz(self, ad, veri):
        with open(os.path.join(self.d, ad), "w", encoding="utf-8") as f:
            json.dump(veri, f)

    def test_veri_yoksa_var_false(self):
        self.assertEqual(isnabzi.oku(), {"var": False})

    def test_fark_ve_yon(self):
        self._yaz(isnabzi.STATE, {"date": "2026-09-04", "metrics": {
            "asc_state": "PREPARE_FOR_SUBMISSION", "d1_users": 8, "d1_paid": 1, "sentry_open": 6}})
        self._yaz(isnabzi.HISTORY, [
            {"date": "2026-09-03", "metrics": {"asc_state": "PREPARE_FOR_SUBMISSION", "d1_users": 6, "d1_paid": 0, "sentry_open": 4}},
            {"date": "2026-09-04", "metrics": {"d1_users": 8}}])
        o = isnabzi.oku()
        m = {x["anahtar"]: x for x in o["metrikler"]}
        self.assertEqual(o["onceki_tarih"], "2026-09-03")
        self.assertEqual(m["asc_state"]["goster"], "gönderime hazırlanıyor")
        self.assertNotIn("fark", m["asc_state"])
        self.assertEqual((m["d1_users"]["fark"], m["d1_users"]["yon"]), (2, "iyi"))
        self.assertEqual((m["sentry_open"]["fark"], m["sentry_open"]["yon"]), (2, "kotu"))
        self.assertEqual(m["d1_paid"]["fark"], 1)

    def test_tarihce_ayni_gun_gunceller(self):
        y = os.path.join(self.d, isnabzi.HISTORY)
        isnabzi.tarihce_ekle({"date": "2026-09-04", "metrics": {"a": 1}}, y)
        isnabzi.tarihce_ekle({"date": "2026-09-04", "metrics": {"a": 2}}, y)
        n = isnabzi.tarihce_ekle({"date": "2026-09-05", "metrics": {"a": 3}}, y)
        self.assertEqual(n, 2)
        self.assertEqual(durum.oku("biz_history")[0]["metrics"]["a"], 2)


class Nabiz(unittest.TestCase):
    def test_uc_soru_alanlari(self):
        from ghost import health
        n = health.nabiz()
        self.assertIn("kararlar", n["bekleyen"])
        self.assertIn("sorular", n["bekleyen"])
        self.assertIn("satir", n["altyapi"])
        self.assertIn(n["altyapi"]["durum"], ("iyi", "uyari", "kritik"))
        self.assertIn("var", n["is"])


if __name__ == "__main__":
    unittest.main()
