"""ghost/uzun_is.py — uzun ufuklu iş: plan, adım zinciri, soru/hata durağı, bütçe, bekçi, FREEZE (Faz 2.6)."""
import json
import os
import tempfile
import time
import unittest

from tests import common  # noqa: F401

from ghost import config, governance, uzun_is


class TestUzunIs(unittest.TestCase):
    def setUp(self):
        c = uzun_is._conn()
        c.execute("DELETE FROM uzun_is"); c.commit(); c.close()
        self.kuyruk, self.sorular, self.bildirimler = [], [], []
        self.tmp = tempfile.mkdtemp(prefix="uzunis-")
        self._kart = uzun_is._kart_dizini
        uzun_is._kart_dizini = lambda: os.path.join(self.tmp, "uzun_is")
        uzun_is._kuyruk = lambda p, k, s: self.kuyruk.append((p, k, s)) or len(self.kuyruk)
        uzun_is._sor = lambda m, sec: self.sorular.append((m, sec)) or 1
        uzun_is._bildir = lambda m: self.bildirimler.append(m)
        uzun_is._yayinla = lambda yol: 77
        os.environ.pop("GHOST_OZERK", None)
        governance.unfreeze()
        self.jobs = os.path.join(config.OPS, "jobs")
        os.makedirs(self.jobs, exist_ok=True)
        for f in os.listdir(self.jobs):
            os.remove(os.path.join(self.jobs, f))

    def tearDown(self):
        uzun_is._kart_dizini = self._kart
        uzun_is._kuyruk = uzun_is._sor = uzun_is._bildir = uzun_is._yayinla = None
        os.environ.pop("GHOST_OZERK", None)
        governance.unfreeze()

    def _is(self, jid, kind, status, ended=None, **kw):
        d = {"status": status, "kind": kind, "prompt": "x", "started": "2026-10-30 10:00:00"}
        if ended:
            d["ended"] = ended
        d.update(kw)
        with open(os.path.join(self.jobs, "%d.json" % jid), "w", encoding="utf-8") as f:
            json.dump(d, f)

    # ---- başlatma ----
    def test_baslat_ilk_adimi_kuyruklar_ve_kart_yazar(self):
        uid = uzun_is.baslat("Sentry hatalarını kapat", "hataları listele|ilkini düzelt|test koş", detay="AppA", butce_sa=2)
        is_ = uzun_is.oku(uid)
        self.assertEqual(is_["durum"], "kosuyor")
        self.assertEqual([a["durum"] for a in is_["adimlar"]], ["kosuyor", "acik", "acik"])
        self.assertEqual(is_["butce_sn"], 7200)
        self.assertEqual(is_["masa_id"], 77)
        self.assertEqual(len(self.kuyruk), 1)
        prompt, kind, sinif = self.kuyruk[0]
        self.assertEqual(kind, "uzun-is:%d:1" % uid)
        self.assertIn("adım 1/3: hataları listele", prompt)
        self.assertIn("python3 -m ghost.uzun_is adim %d 1 tamam" % uid, prompt)
        self.assertIn("python3 -m ghost.uzun_is aktif %d 1" % uid, prompt)
        self.assertEqual(sinif, "Sentry hatalarını kapat — hataları listele")   # KURALLAR metni sınıfa girmez
        self.assertEqual(governance.classify(sinif), governance.READ)
        self.assertNotEqual(governance.classify(prompt), governance.READ)       # istemde deploy/push sözcükleri var
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "uzun_is", "%d.md" % uid)))
        self.assertEqual(uzun_is.aktif(uid, 1), (True, ""))
        self.assertFalse(uzun_is.aktif(uid, 2)[0])

    def test_ozerk_ve_freeze_baslatamaz(self):
        os.environ["GHOST_OZERK"] = "1"
        with self.assertRaises(uzun_is.UzunIsHata):
            uzun_is.baslat("x", "a|b")
        os.environ.pop("GHOST_OZERK")
        governance.freeze("test")
        with self.assertRaises(uzun_is.UzunIsHata):
            uzun_is.baslat("x", "a|b")
        governance.unfreeze()
        with self.assertRaises(uzun_is.UzunIsHata):
            uzun_is.baslat("x", "|".join("a%d" % i for i in range(uzun_is.ADIM_TAVAN + 1)))
        with self.assertRaises(uzun_is.UzunIsHata):
            uzun_is.baslat("x", "  ")
        self.assertEqual(self.kuyruk, [])

    # ---- zincir ----
    def test_tamam_sonraki_adimi_kuyruklar_son_adim_bitirir(self):
        uid = uzun_is.baslat("İş", "a|b")
        uzun_is.adim(uid, 1, "tamam", "a bitti\nçıktı: tablo")
        is_ = uzun_is.oku(uid)
        self.assertEqual(is_["adimlar"][0]["durum"], "tamam")
        self.assertEqual(is_["adimlar"][1]["durum"], "kosuyor")
        self.assertEqual(self.kuyruk[-1][1], "uzun-is:%d:2" % uid)
        self.assertIn("1. a: a bitti", self.kuyruk[-1][0])          # önceki özet istemde
        uzun_is.adim(uid, 2, "tamam", "b bitti")
        is_ = uzun_is.oku(uid)
        self.assertEqual(is_["durum"], "bitti")
        self.assertEqual(len(self.bildirimler), 1)
        self.assertIn("Uzun iş #%d bitti" % uid, self.bildirimler[0])
        self.assertEqual(self.sorular, [])
        with self.assertRaises(uzun_is.UzunIsHata):      # biten işe rapor alınmaz
            uzun_is.adim(uid, 2, "tamam", "yine")
        self.assertFalse(uzun_is.aktif(uid, 2)[0])

    def test_soru_durdurur_devam_cevapla_ayni_adimi_tekrar_kosar(self):
        uid = uzun_is.baslat("İş", "a|b")
        uzun_is.adim(uid, 1, "soru", "Deploy edelim mi?")
        is_ = uzun_is.oku(uid)
        self.assertEqual(is_["durum"], "soru")
        self.assertEqual(len(self.sorular), 1)
        self.assertEqual(self.sorular[0][1], list(uzun_is.SECENEKLER))
        self.assertLessEqual(len(self.sorular[0][1]), 3)
        self.assertIn("Deploy edelim mi?", self.sorular[0][0])
        self.assertFalse(uzun_is.aktif(uid, 1)[0])                # duran işin adımı koşmaz
        self.assertEqual(len(self.kuyruk), 1)
        uzun_is.devam(uid, "Evet, ama önce test")
        is_ = uzun_is.oku(uid)
        self.assertEqual(is_["durum"], "kosuyor")
        self.assertEqual(is_["adimlar"][0]["durum"], "kosuyor")
        self.assertEqual(is_["adimlar"][0]["deneme"], 2)
        self.assertEqual(self.kuyruk[-1][1], "uzun-is:%d:1" % uid)
        self.assertIn("Evet, ama önce test", self.kuyruk[-1][0])

    def test_hata_sonra_atla_sonraki_adima_gecer(self):
        uid = uzun_is.baslat("İş", "a|b")
        uzun_is.adim(uid, 1, "hata", "xcodebuild yok")
        self.assertEqual(uzun_is.oku(uid)["durum"], "soru")
        self.assertIn("xcodebuild yok", self.sorular[-1][0])
        uzun_is.atla(uid)
        is_ = uzun_is.oku(uid)
        self.assertEqual(is_["adimlar"][0]["durum"], "atlandi")
        self.assertEqual(is_["adimlar"][1]["durum"], "kosuyor")
        self.assertEqual(self.kuyruk[-1][1], "uzun-is:%d:2" % uid)

    def test_iptal(self):
        uid = uzun_is.baslat("İş", "a|b")
        uzun_is.iptal(uid, "vazgeçtim")
        is_ = uzun_is.oku(uid)
        self.assertEqual(is_["durum"], "iptal")
        self.assertFalse(uzun_is.aktif(uid, 1)[0])
        with self.assertRaises(uzun_is.UzunIsHata):
            uzun_is.devam(uid)
        self.assertEqual(uzun_is.liste(), [])
        self.assertEqual(len(uzun_is.liste(hepsi=True)), 1)

    def test_deneme_tavani_soruya_dusurur_ve_atlanabilir(self):
        uid = uzun_is.baslat("İş", "a|b")
        for _ in range(uzun_is.DENEME_TAVAN - 1):
            uzun_is.adim(uid, 1, "soru", "?")
            uzun_is.devam(uid)
        uzun_is.adim(uid, 1, "soru", "?")
        uzun_is.devam(uid)                                       # tavan → tekrar soru
        is_ = uzun_is.oku(uid)
        self.assertEqual(is_["durum"], "soru")
        self.assertIn("kez koştu", is_["soru"])
        self.assertEqual(len(self.kuyruk), uzun_is.DENEME_TAVAN)
        uzun_is.atla(uid)
        self.assertEqual(uzun_is.oku(uid)["adimlar"][1]["durum"], "kosuyor")

    # ---- bütçe ----
    def test_butce_dolunca_durur_devam_ikiye_katlar(self):
        uid = uzun_is.baslat("İş", "a|b", butce_sa=0.001)         # 3 sn
        c = uzun_is._conn()
        adimlar = uzun_is.oku(uid)["adimlar"]
        adimlar[0]["bas"] = int(time.time()) - 600
        c.execute("UPDATE uzun_is SET adimlar=? WHERE id=?", (json.dumps(adimlar), uid)); c.commit(); c.close()
        uzun_is.adim(uid, 1, "tamam", "a")
        is_ = uzun_is.oku(uid)
        self.assertEqual(is_["durum"], "soru")
        self.assertIn("bütçesi doldu", is_["soru"])
        self.assertGreaterEqual(is_["kullanilan_sn"], 600)
        self.assertEqual(is_["adimlar"][1]["durum"], "acik")
        uzun_is.devam(uid)
        is_ = uzun_is.oku(uid)
        self.assertGreater(is_["butce_sn"], is_["kullanilan_sn"])
        self.assertEqual(is_["adimlar"][1]["durum"], "kosuyor")

    # ---- bekçi ----
    def test_tick_is_hata_verdi_rapor_yok_soruya_duser(self):
        uid = uzun_is.baslat("İş", "a|b")
        self._is(90, "uzun-is:%d:1" % uid, "hata", ended="2026-10-30 10:05:00", hata="5-hour limit")
        y = uzun_is.tick()
        self.assertEqual(len(y), 1)
        is_ = uzun_is.oku(uid)
        self.assertEqual(is_["durum"], "soru")
        self.assertEqual(is_["adimlar"][0]["jid"], 90)
        self.assertIn("5-hour limit", self.sorular[-1][0])
        self.assertEqual(uzun_is.tick(), [])                      # ikinci tik tekrar sormaz

    def test_tick_is_bitti_rapor_yok_tamam_sayar_ve_surer(self):
        uid = uzun_is.baslat("İş", "a|b")
        taze = time.strftime("%F %T", time.localtime(time.time() - 10))
        self._is(91, "uzun-is:%d:1" % uid, "bitti", ended=taze, verify="geçti")
        self.assertEqual(uzun_is.tick(), [])                      # rapor için bekle
        eski = time.strftime("%F %T", time.localtime(time.time() - uzun_is.RAPOR_BEKLE_SN - 5))
        self._is(91, "uzun-is:%d:1" % uid, "bitti", ended=eski, verify="geçti")
        y = uzun_is.tick()
        self.assertEqual(len(y), 1)
        is_ = uzun_is.oku(uid)
        self.assertEqual(is_["adimlar"][0]["durum"], "tamam")
        self.assertIn("raporu yok", is_["adimlar"][0]["ozet"])
        self.assertEqual(is_["adimlar"][1]["durum"], "kosuyor")
        self.assertEqual(self.kuyruk[-1][1], "uzun-is:%d:2" % uid)

    def test_tick_henuz_is_yoksa_bekler_ve_yabanci_is_dosyasini_yok_sayar(self):
        uid = uzun_is.baslat("İş", "a")
        self._is(5, "zamanlı görev", "hata")
        with open(os.path.join(self.jobs, "bozuk.json"), "w") as f:
            f.write("{")
        self.assertEqual(uzun_is.tick(), [])
        self.assertEqual(uzun_is.oku(uid)["durum"], "kosuyor")

    def test_freeze_aktif_isleri_iptal_eder(self):
        uid1 = uzun_is.baslat("İş1", "a|b")
        uid2 = uzun_is.baslat("İş2", "a")
        uzun_is.adim(uid2, 1, "soru", "?")
        governance.freeze("test")
        y = uzun_is.tick()
        self.assertEqual(len(y), 2)
        self.assertEqual(uzun_is.oku(uid1)["durum"], "iptal")
        self.assertEqual(uzun_is.oku(uid2)["durum"], "iptal")
        governance.unfreeze()
        self.assertEqual(uzun_is.tick(), [])

    # ---- kart / CLI ----
    def test_kart_metni_tablo_ve_soru(self):
        uid = uzun_is.baslat("İş", "a|b", detay="ayrıntı")
        uzun_is.adim(uid, 1, "soru", "Hangisi?")
        m = uzun_is.kart_metni(uzun_is.oku(uid))
        self.assertIn("# 🧭 Uzun iş #%d · İş" % uid, m)
        self.assertIn("**Bekleyen soru:** Hangisi?", m)
        self.assertIn("| 1 | a | hata |", m)
        self.assertIn("| 2 | b | acik |", m)

    def test_cli(self):
        self.assertEqual(uzun_is._main(["baslat", "CLI işi", "a|b", "--butce-sa", "1"]), 0)
        uid = uzun_is.liste()[0]["id"]
        self.assertEqual(uzun_is._main(["aktif", str(uid), "1"]), 0)
        self.assertEqual(uzun_is._main(["aktif", str(uid), "2"]), 3)
        self.assertEqual(uzun_is._main(["adim", str(uid), "1", "tamam", "oldu", "bitti"]), 0)
        self.assertEqual(uzun_is.oku(uid)["adimlar"][0]["ozet"], "oldu bitti")
        self.assertEqual(uzun_is._main(["adim", str(uid), "2", "yanlis"]), 2)
        self.assertEqual(uzun_is._main(["liste"]), 0)
        self.assertEqual(uzun_is._main(["goster", str(uid)]), 0)
        self.assertEqual(uzun_is._main(["tick"]), 0)
        self.assertEqual(uzun_is._main(["iptal", str(uid), "test"]), 0)
        self.assertEqual(uzun_is._main(["bilinmeyen"]), 2)


if __name__ == "__main__":
    unittest.main()
