import os
import time
import unittest

from tests import common  # noqa: F401
from ghost import config, goals


class TestGoals(unittest.TestCase):
    def test_add_list_status(self):
        gid = goals.add("Test hedefi", "detay")
        self.assertTrue(any(g["id"] == gid for g in goals.list_goals()))
        goals.set_status(gid, "tamam")
        self.assertFalse(any(g["id"] == gid for g in goals.list_goals()))
        self.assertTrue(any(g["id"] == gid
                            for g in goals.list_goals(all_states=True)))

    def test_tick_queues_least_recent(self):
        gid = goals.add("Tik hedefi")
        goals._last_tick = 0
        fired = goals.tick(now=time.time())
        self.assertEqual(fired, gid)
        # olay veriyoluna istek düştü (Faz 3: ops/requests dosyası değil)
        from ghost import bus
        self.assertTrue(any(r["source"] == "goal" and r["payload"]["kind"] == "hedef"
                            for r in bus.bekleyen_istekler()))
        # 6 saat dolmadan aynı hedef tekrar çalışılmaz
        goals._last_tick = 0
        self.assertIsNone(goals.tick(now=time.time()))
        goals.set_status(gid, "tamam")

    def test_frozen_blocks(self):
        from ghost import governance
        gid = goals.add("Donuk hedef")
        governance.freeze("test")
        try:
            goals._last_tick = 0
            self.assertIsNone(goals.tick(now=time.time() + 10**6))
        finally:
            governance.unfreeze()
            goals.set_status(gid, "tamam")


if __name__ == "__main__":
    unittest.main()


class TestGoalsV2(unittest.TestCase):
    """Faz 1.5 (2026-10-29): plan, kill, puan, bütçe, önceki rapor."""

    def setUp(self):
        c = goals._conn()
        c.execute("DELETE FROM goals")
        c.commit()
        c.close()
        goals._last_tick = 0

    def test_plan_adim_puan_ikinci_plan(self):
        gid = goals.add("v2 hedefi", plan=["araştır", "yaz"], kill="10 tur", oncelik=2)
        g = goals.goster(gid)
        self.assertEqual([p["ad"] for p in g["plan"]], ["araştır", "yaz"])
        self.assertTrue(goals.adim(gid, 1, "tamam"))
        self.assertEqual(goals.goster(gid)["plan"][0]["durum"], "tamam")
        self.assertFalse(goals.adim(gid, 5))
        self.assertEqual(goals.puan(gid, +1), (1, False))
        self.assertEqual(goals.puan(gid, -1), (0, False))
        self.assertEqual(goals.puan(gid, -1), (-1, False))
        self.assertEqual(goals.puan(gid, -1), (-2, True))          # 👎👎 → duraklat
        self.assertEqual(goals.goster(gid)["status"], goals.DURAKLATILDI)
        self.assertTrue(goals.set_status(gid, "aktif"))
        self.assertTrue(goals.ikinci_plan(gid))
        self.assertEqual(goals.goster(gid)["notu"], "Sahip: ikinci plan")

    def test_kill_tarih_ve_tur(self):
        now = time.time()
        gid = goals.add("tarihli", kill="2026-01-01")
        self.assertIsNone(goals.tick(now=now, kota_uygun=True))     # kill dolu → kapanır, çalışmaz
        self.assertEqual(goals.goster(gid)["status"], "tamam")
        self.assertIn("kill tarihi", goals.goster(gid)["notu"])
        gid2 = goals.add("turlu", kill="1 tur")
        goals._last_tick = 0
        self.assertEqual(goals.tick(now=now, kota_uygun=True), gid2)  # ilk tur çalışır
        goals._last_tick = 0
        self.assertIsNone(goals.tick(now=now + 7 * 3600, kota_uygun=True))
        self.assertEqual(goals.goster(gid2)["status"], "tamam")
        self.assertEqual(goals.kill_durumu({"kill": "ölçüm gelince", "work_count": 99}), (False, ""))

    def test_butce_oncelik_ve_kota(self):
        now = time.time()
        dusuk = goals.add("düşük", oncelik=1, butce_hafta=1)
        yuksek = goals.add("yüksek", oncelik=5, butce_hafta=1)
        self.assertIsNone(goals.tick(now=now, kota_uygun=False))    # kota dolu → ertelenir
        goals._last_tick = 0
        self.assertEqual(goals.tick(now=now, kota_uygun=True), yuksek)
        goals._last_tick = 0
        self.assertEqual(goals.tick(now=now + 1, kota_uygun=True), dusuk)
        goals._last_tick = 0
        self.assertIsNone(goals.tick(now=now + 8 * 3600, kota_uygun=True))   # haftalık bütçe dolu
        self.assertEqual(goals.goster(yuksek)["hafta_tur"], 1)
        goals._last_tick = 0
        self.assertEqual(goals.tick(now=now + 8 * 86400, kota_uygun=True), yuksek)  # yeni hafta → sıfırlanır

    def test_son_rapor_istem_girdisi(self):
        gid = goals.add("raporlu", plan=["a"])
        goals.rapor_kaydet(gid, "önceki tur: X yapıldı")
        g = goals.goster(gid)
        self.assertEqual(goals.son_rapor_oku(g), "önceki tur: X yapıldı")
        goals._last_tick = 0
        self.assertEqual(goals.tick(now=time.time(), kota_uygun=True), gid)
        from ghost import bus
        istek = [r for r in bus.bekleyen_istekler() if r["source"] == "goal"][-1]
        self.assertIn("önceki tur: X yapıldı", istek["payload"]["prompt"])
        self.assertIn("1. [ ] a", istek["payload"]["prompt"])
        self.assertIn("dossier/hedefler/%d-son.md" % gid, istek["payload"]["prompt"])

    def test_cli(self):
        self.assertEqual(goals._main(["ekle", "cli hedefi", "detay", "--kill", "3 tur", "--oncelik", "2"]), 0)
        gid = goals.list_goals()[0]["id"]
        self.assertEqual(goals._main(["plan", str(gid), "a|b"]), 0)
        self.assertEqual(goals._main(["adim", str(gid), "2"]), 0)
        self.assertEqual(goals._main(["puan", str(gid), "+"]), 0)
        self.assertEqual(goals._main(["goster", str(gid)]), 0)
        self.assertEqual(goals._main(["liste"]), 0)
        self.assertEqual(goals.goster(gid)["kill"], "3 tur")
