import os
import time
import unittest

from tests import common  # noqa: F401
from ghost import config, goals


class TestGoals(unittest.TestCase):
    def setUp(self):
        # testler gerçek saatle koşar; gece vardiyası penceresine denk gelirse tick susar → saatten bağımsız kıl
        self._vardiya_icinde = goals._vardiya_icinde
        goals._vardiya_icinde = lambda now: False

    def tearDown(self):
        goals._vardiya_icinde = self._vardiya_icinde

    def test_vardiya_penceresinde_tur_acmaz(self):
        # 2026-11-03 çift tur: gece hem goals.tick hem vardiya.tick aynı hedefi açıyordu
        from ghost import durum, vardiya
        goals._vardiya_icinde = self._vardiya_icinde
        for g in goals.list_goals():
            goals.set_status(g["id"], "tamam")
        gid = goals.add("Gece hedefi")
        gece = time.mktime((2026, 11, 3, 2, 31, 0, 0, 0, -1))
        gunduz = time.mktime((2026, 11, 3, 14, 0, 0, 0, 0, -1))
        eski = durum.oku(vardiya.DURUM_AD)
        try:
            durum.sil(vardiya.DURUM_AD)
            goals._last_tick = 0
            self.assertIsNone(goals.tick(now=gece, kota_uygun=True))      # pencere içi → vardiyanın işi
            vardiya.ayar_yaz(acik=False)
            goals._last_tick = 0
            self.assertEqual(goals.tick(now=gece, kota_uygun=True), gid)  # vardiya kapalı → eski davranış
            durum.sil(vardiya.DURUM_AD)
            goals.set_status(gid, "tamam")
            gid2 = goals.add("Gündüz hedefi")
            goals._last_tick = 0
            self.assertEqual(goals.tick(now=gunduz, kota_uygun=True), gid2)
            goals.set_status(gid2, "tamam")
        finally:
            durum.sil(vardiya.DURUM_AD)
            if eski:
                durum.yaz(vardiya.DURUM_AD, eski)

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

    def test_engelde_tur_acmaz(self):
        # Sahip 2026-10-30: Sahip/Mac bekleyen hedef 6 saatte bir boş tur açmasın
        for g in goals.list_goals():
            goals.set_status(g["id"], "tamam")
        gid = goals.add("Bekleyen hedef")
        self.assertEqual(goals._main(["engel", str(gid), "Mac", "uykuda"]), 0)
        g = goals.goster(gid)
        self.assertEqual((g["status"], g["notu"]), (goals.ENGELDE, "Mac uykuda"))
        goals._last_tick = 0
        self.assertIsNone(goals.tick(now=time.time() + 10**6, kota_uygun=True))
        goals._main(["devam", str(gid)])
        self.assertEqual(goals.goster(gid)["status"], "aktif")
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
        # TestGoals'daki gibi saatten bağımsız: 22:00-07:00 vardiya penceresinde tick susuyor, iki test düşüyordu
        # (2026-11-04 gecesi pre-push kapısı GitHub itişini 10 commit durdurdu)
        self._vardiya_icinde = goals._vardiya_icinde
        goals._vardiya_icinde = lambda now: False

    def tearDown(self):
        goals._vardiya_icinde = self._vardiya_icinde

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
        # Sabit Çarşamba öğlesi: time.time() Pazar akşamı koşunca +8 sa ISO haftayı aşıyor, bütçe sıfırlanıyordu (CI 2026-11-01).
        now = time.mktime((2026, 11, 4, 12, 0, 0, 0, 0, -1))
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


class TestTurModeli(unittest.TestCase):
    """Sahip 2026-10-30 'opusa geçelim': hedef turları durum `hedef_model` ile motor seçer, bitiş geçince köprü varsayılanı."""

    def tearDown(self):
        goals.tur_modeli_ayarla("kapat")

    def test_ayar_ve_bitis(self):
        import datetime
        self.assertIsNone(goals.tur_modeli())
        goals.tur_modeli_ayarla("opus", "2026-11-04T08:00")
        self.assertEqual(goals.tur_modeli(simdi=datetime.datetime(2026, 11, 1, 12, 0)), "opus")
        self.assertIsNone(goals.tur_modeli(simdi=datetime.datetime(2026, 11, 4, 8, 0)))
        goals.tur_modeli_ayarla("kapat")
        self.assertIsNone(goals.tur_modeli())

    def test_kuyruga_model_gecer(self):
        from ghost import bus
        from unittest import mock
        goals.tur_modeli_ayarla("opus", "2099-01-01T00:00")
        gid = goals.add("Model testi", "detay")
        g = goals.goster(gid)
        with mock.patch.object(bus, "istek") as istek:
            goals._queue_work(g, announce=False)
        self.assertEqual(istek.call_args.kwargs.get("model"), "opus")
        goals.tur_modeli_ayarla("kapat")
        with mock.patch.object(bus, "istek") as istek:
            goals._queue_work(g, announce=False)
        self.assertIsNone(istek.call_args.kwargs.get("model"))
