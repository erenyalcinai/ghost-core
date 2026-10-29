"""ghost/vardiya.py — gece vardiyası: pencere hesabı, ayar doğrulama, tick (tavan/aralık/kota/FREEZE/sıra), sabah raporu (Faz 2.1)."""
import json
import os
import tempfile
import time
import unittest

from tests import common  # noqa: F401

from ghost import config, durum, goals, governance, vardiya


def _ts(y, mo, d, h, mi):
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


class TestVardiya(unittest.TestCase):
    def setUp(self):
        durum.sil(vardiya.DURUM_AD)
        c = goals._conn()
        c.execute("DELETE FROM goals"); c.commit(); c.close()
        governance.unfreeze()
        self.kuyruk, self.parcalar, self.yayin = [], [], []
        self.kota = [30]
        vardiya._kuyruk = lambda g: self.kuyruk.append(g["id"])
        vardiya._kota_yuzde = lambda: self.kota[0]
        vardiya._parca = lambda m: self.parcalar.append(m)
        vardiya._yayinla = lambda yol: self.yayin.append(yol) or 1
        self.jobs = os.path.join(config.OPS, "jobs")
        os.makedirs(self.jobs, exist_ok=True)
        for f in os.listdir(self.jobs):
            os.remove(os.path.join(self.jobs, f))
        self.tmp = tempfile.mkdtemp(prefix="vardiya-")
        self._rapor_dizini = goals._rapor_dizini
        goals._rapor_dizini = lambda: self.tmp
        self._vardiya_dizini = vardiya._rapor_dizini
        vardiya._rapor_dizini = lambda: os.path.join(self.tmp, "vardiya")

    def tearDown(self):
        vardiya._kuyruk = vardiya._kota_yuzde = vardiya._parca = vardiya._yayinla = None
        c = goals._conn()
        c.execute("DELETE FROM goals"); c.commit(); c.close()
        durum.sil(vardiya.DURUM_AD)
        goals._rapor_dizini = self._rapor_dizini
        governance.unfreeze()

    def _is(self, jid, gid, status, **kw):
        d = {"status": status, "kind": "vardiya:%d" % gid, "prompt": "x"}
        d.update(kw)
        with open(os.path.join(self.jobs, "%d.json" % jid), "w", encoding="utf-8") as f:
            json.dump(d, f)

    # ---- pencere / ayar ----
    def test_pencere_gece_yarisini_asar(self):
        a = dict(vardiya.VARSAYILAN)                       # 23:00–07:00
        self.assertEqual(vardiya.pencere(_ts(2026, 10, 30, 23, 30), a), (True, "2026-10-30"))
        self.assertEqual(vardiya.pencere(_ts(2026, 10, 31, 3, 0), a), (True, "2026-10-30"))
        self.assertEqual(vardiya.pencere(_ts(2026, 10, 31, 7, 0), a), (False, "2026-10-31"))
        self.assertEqual(vardiya.pencere(_ts(2026, 10, 31, 12, 0), a), (False, "2026-10-31"))
        g = dict(a, baslangic="13:00", bitis="15:00")      # gündüz penceresi
        self.assertEqual(vardiya.pencere(_ts(2026, 10, 31, 14, 0), g), (True, "2026-10-31"))
        self.assertEqual(vardiya.pencere(_ts(2026, 10, 31, 15, 0), g), (False, "2026-10-31"))

    def test_ayar_dogrulama(self):
        a = vardiya.ayar_yaz(baslangic="22:30", tavan=99, kota_tavan=5, aralik_dk=1)
        self.assertEqual((a["baslangic"], a["tavan"], a["kota_tavan"], a["aralik_dk"]), ("22:30", 8, 10, 5))
        self.assertEqual(vardiya.ayar_oku()["baslangic"], "22:30")
        with self.assertRaises(vardiya.VardiyaHata):
            vardiya.ayar_yaz(bitis="25:00")
        with self.assertRaises(vardiya.VardiyaHata):
            vardiya.ayar_yaz(bitis="22:30")
        vardiya.ayar_yaz(acik=False)
        self.assertFalse(vardiya.ayar_oku()["acik"])

    # ---- tick ----
    def test_hedefsiz_gece_uyku(self):
        self.assertEqual(vardiya.tick(_ts(2026, 10, 30, 23, 30)), [])
        self.assertEqual(self.kuyruk, [])

    def test_tick_oncelik_tavan_aralik_ve_sira(self):
        g1 = goals.add("düşük", oncelik=1)
        g2 = goals.add("yüksek", oncelik=3)
        g3 = goals.add("orta", oncelik=2)
        g4 = goals.add("durmuş", oncelik=9); goals.set_status(g4, goals.DURAKLATILDI)
        t = _ts(2026, 10, 30, 23, 10)
        self.assertEqual(vardiya.tick(t), ["#%d kuyruklandı (1/3)" % g2])
        self.assertEqual(vardiya.tick(t + 60), [])                      # aralık dolmadı
        self._is(10, g2, "çalışıyor")
        self.assertEqual(vardiya.tick(t + 30 * 60), [])                 # önceki iş sürüyor → sıra bekler
        self._is(10, g2, "bitti")
        self.assertEqual(vardiya.tick(t + 31 * 60), ["#%d kuyruklandı (2/3)" % g3])
        self._is(11, g3, "bitti")
        self.assertEqual(vardiya.tick(t + 62 * 60), ["#%d kuyruklandı (3/3)" % g1])
        self._is(12, g1, "hata", hata="zaman aşımı")
        self.assertEqual(vardiya.tick(t + 93 * 60), [])                 # tavan 3 doldu
        self.assertEqual(self.kuyruk, [g2, g3, g1])
        self.assertEqual(int(goals.goster(g2)["work_count"]), 1)
        # pencere dışı ilk tick → sabah raporu
        with open(os.path.join(self.tmp, "%d-son.md" % g2), "w", encoding="utf-8") as f:
            f.write("# rapor\nBitti: üç hata kapandı\nsonraki: test\n")
        sabah = _ts(2026, 10, 31, 7, 15)
        os.utime(os.path.join(self.tmp, "%d-son.md" % g2), (sabah - 3600, sabah - 3600))   # gece yazılmış rapor
        y = vardiya.tick(sabah)
        self.assertEqual(len(y), 1)
        self.assertIn("rapor 2026-10-30", y[0])
        self.assertEqual(len(self.parcalar), 1)
        m = self.parcalar[0]
        self.assertIn("✅ #%d yüksek — bitti · iş #10: Bitti: üç hata kapandı" % g2, m)
        self.assertIn("✅ #%d orta — bitti · iş #11" % g3, m)
        self.assertIn("⚠️ #%d düşük — yarım (hata) · iş #12" % g1, m)
        self.assertEqual(len(self.yayin), 1)
        self.assertEqual(vardiya.tick(sabah + 900), [])                 # rapor bir kez
        # ertesi gece yeni pencere, sayaç sıfır
        self._is(10, g2, "bitti"); self._is(11, g3, "bitti"); self._is(12, g1, "bitti")
        y = vardiya.tick(_ts(2026, 10, 31, 23, 5))
        self.assertEqual(len(y), 1)
        self.assertIn("(1/3)", y[0])

    def test_kota_freeze_kapali_engeller(self):
        goals.add("x")
        t = _ts(2026, 10, 30, 23, 30)
        self.kota[0] = 70
        self.assertEqual(vardiya.tick(t), [])
        self.kota[0] = None                                            # ölçüm bilinmiyor → engel yok
        governance.freeze("test")
        self.assertEqual(vardiya.tick(t), [])
        governance.unfreeze()
        vardiya.ayar_yaz(acik=False)
        self.assertEqual(vardiya.tick(t), [])
        vardiya.ayar_yaz(acik=True)
        self.assertEqual(len(vardiya.tick(t)), 1)

    def test_gunduz_disarida_kuyruklamaz(self):
        goals.add("x")
        self.assertEqual(vardiya.tick(_ts(2026, 10, 30, 14, 0)), [])
        self.assertEqual(self.kuyruk, [])

    def test_rapor_kuru_ve_cli(self):
        self.assertEqual(vardiya.rapor(kuru=True), "")
        self.assertEqual(vardiya._main(["durum"]), 0)
        self.assertEqual(vardiya._main(["ayar", "--baslangic", "22:00", "--tavan", "2"]), 0)
        self.assertEqual(vardiya.ayar_oku()["tavan"], 2)
        self.assertEqual(vardiya._main(["ayar", "--bitis", "x"]), 2)
        self.assertEqual(vardiya._main(["kapat"]), 0)
        self.assertEqual(vardiya._main(["ac"]), 0)
        self.assertEqual(vardiya._main(["tick"]), 0)
        self.assertEqual(vardiya._main(["rapor", "--kuru"]), 0)
        self.assertEqual(vardiya._main(["yok"]), 2)


if __name__ == "__main__":
    unittest.main()
