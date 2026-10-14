"""Köprü koşum takımı: araç durumu notu + maliyet sayaçları.

İkisi de 24 Ağustos 2026'daki denetimden çıktı:
  1. Ghost yüklü olmayan MCP'yi "bağlantı düşmüş" diye rapor ediyordu.
  2. /durum satırı bu oturumun bağlamıyla tüm zamanların maliyetini yan yana
     basıyordu ("4 tur · $438").
"""
import json
import os
import time
import sys
import tempfile
import unittest

from tests import common  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "ops"))
import selfcheck  # noqa: E402
import telegram_bridge as tb  # noqa: E402


class TestAracDurumuNotu(unittest.TestCase):
    def test_hic_mcp_yoksa_ariza_degil_uyarisi(self):
        not_ = tb._tool_note([])
        self.assertIn("ARIZA DEĞİL", not_)
        self.assertIn("bağlantı düştü", not_)   # bu ifadeyi kullanma talimatı

    def test_kismi_yukte_eksikler_sayilir(self):
        not_ = tb._tool_note(["github"])
        self.assertIn("github", not_)
        self.assertIn("sentry", not_)
        self.assertIn("ARIZA DEĞİL", not_)

    def test_hepsi_yukluyse_not_yok(self):
        self.assertEqual(tb._tool_note(list(tb.MCP_CONFIGS)), "")

    def test_selamlasma_mcp_almaz_ama_not_alir(self):
        # "nasılsın" trivial → MCP yok → not MUTLAKA olmalı (yanlış alarmın
        # doğduğu tam nokta buydu).
        self.assertTrue(tb.TRIVIAL_CHAT.match("nasılsın"))
        self.assertNotEqual(tb._tool_note([]), "")


class TestMaliyetSayaclari(unittest.TestCase):
    def _mgr(self, chat_state):
        d = tempfile.mkdtemp()
        for attr, ad in (("CHAT_FILE", "chat_session.json"),
                         ("STATE_FILE", "state.json"),
                         ("PENDING_FILE", "pending.json"),
                         ("JOBS_DIR", "jobs")):
            setattr(tb, attr, os.path.join(d, ad))
        os.makedirs(tb.JOBS_DIR, exist_ok=True)
        with open(tb.CHAT_FILE, "w") as f:
            json.dump(chat_state, f)
        return tb.Manager(1, {}), d

    def setUp(self):
        self._yedek = {a: getattr(tb, a) for a in
                       ("CHAT_FILE", "STATE_FILE", "PENDING_FILE", "JOBS_DIR")}

    def tearDown(self):
        for a, v in self._yedek.items():
            setattr(tb, a, v)

    def test_eski_tek_alan_toplama_tasinir(self):
        # Eski dosyada yalnız `cost` var; o birikmiş TOPLAMDIR.
        mgr, _ = self._mgr({"sid": "x", "established": True, "turns": 4,
                            "cost": 438.45})
        self.assertEqual(mgr.chat_cost, 0.0)          # oturum sayacı taze
        self.assertEqual(mgr.chat_cost_total, 438.45)  # toplam korundu

    def test_iki_alan_ayri_yuklenir_ve_yazilir(self):
        mgr, _ = self._mgr({"cost_session": 1.5, "cost_total": 440.0})
        self.assertEqual(mgr.chat_cost, 1.5)
        self.assertEqual(mgr.chat_cost_total, 440.0)
        mgr._save_chat()
        with open(tb.CHAT_FILE) as f:
            yazilan = json.load(f)
        self.assertEqual(yazilan["cost_session"], 1.5)
        self.assertEqual(yazilan["cost_total"], 440.0)


class TestBayatKod(unittest.TestCase):
    """Köprü ayakta ama diskteki koddan eski süreç koşuyorsa alarm (24 Ağustos:
    deploy restart'a hiç ulaşmadı, her nöbetçi 'sağlıklı' dedi)."""

    def test_kod_surecten_yeniyse_alarm(self):
        self.assertTrue(selfcheck.bayat_kod(kod_ts=2000, surec_ts=1000))

    def test_taze_restart_sessiz(self):
        self.assertFalse(selfcheck.bayat_kod(kod_ts=1000, surec_ts=2000))

    def test_sistem_paketi_guncellemesi_bayat_sayilir(self):
        """2026-09-26: needrestart ghost-* için kapatıldı; apt bir kütüphane yenilediğinde
        köprünün boştayken yenilenmesi için paket veritabanı da 'kod' zamanına girer."""
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"x")
        gelecek = time.time() + 3600
        os.utime(f.name, (gelecek, gelecek))
        try:
            self.assertAlmostEqual(selfcheck._kod_ts(ek_yollar=[f.name]), gelecek, delta=2)
        finally:
            os.remove(f.name)
        # Dosya yoksa (Mac) sessizce atlanır, çökmez.
        self.assertTrue(selfcheck._kod_ts(ek_yollar=["/yok/boyle/dosya"]) > 0)

    def test_deploy_penceresi_tolere_edilir(self):
        # rsync ile restart arasındaki saniyeler alarm üretmemeli
        self.assertFalse(selfcheck.bayat_kod(kod_ts=1100, surec_ts=1000))
        self.assertTrue(selfcheck.bayat_kod(kod_ts=1000 + selfcheck.KOD_TOLERANS + 1,
                                            surec_ts=1000))

    def test_olculemiyorsa_susar(self):
        # ölçüm yoksa YANLIŞ ALARM üretme (nöbetçi güvenilirliği)
        self.assertFalse(selfcheck.bayat_kod(0, 2000))
        self.assertFalse(selfcheck.bayat_kod(2000, 0))

    def test_bosta_ve_yeterince_bayatsa_yenilenir(self):
        # 2026-09-07: köprü boştayken ve bayatlık ≥10 dk sürmüşken kendiliğinden yenile
        f = selfcheck.bayat_iyilestir_mi
        onceki = {"imza": "100", "ts": 1000.0, "ilk": 1000.0}
        self.assertTrue(f("100", 1000 + selfcheck.BAYAT_IYILESTIR_SN, onceki, bosta=True))
        self.assertFalse(f("100", 1000 + selfcheck.BAYAT_IYILESTIR_SN, onceki, bosta=False))   # meşgul: dokunma
        self.assertFalse(f("100", 1000 + 60, onceki, bosta=True))                              # henüz taze
        self.assertFalse(f("200", 1000 + 9999, onceki, bosta=True))                            # ilk görüş: kayda geç
        self.assertFalse(f("100", 5000, {"imza": "100"}, bosta=True))                          # bozuk durum: dur
        self.assertTrue(f("100", 1000 + selfcheck.BAYAT_IYILESTIR_SN, {"imza": "100", "ts": 1000.0}, bosta=True))  # eski biçim

    def test_kod_ts_gercek_dosyalari_okuyor(self):
        self.assertGreater(selfcheck._kod_ts(), 0)


class TestBayatUyariSusturma(unittest.TestCase):
    """Nöbetçi 15 dk'da bir koşuyor; restart bilerek manuel. Susturma olmazsa
    aynı uyarı restart'a kadar 15 dk'da bir düşer → alarm yorgunluğu."""

    def test_ilk_gorusde_uyarir(self):
        self.assertTrue(selfcheck.bayat_uyarilsin("100", 1000, {}))

    def test_ayni_surec_cooldown_dolmadan_susar(self):
        onceki = {"imza": "100", "ts": 1000}
        self.assertFalse(selfcheck.bayat_uyarilsin("100", 1000 + 15 * 60, onceki))

    def test_ayni_surec_cooldown_dolunca_hatirlatir(self):
        onceki = {"imza": "100", "ts": 1000}
        self.assertTrue(selfcheck.bayat_uyarilsin(
            "100", 1000 + selfcheck.BAYAT_COOLDOWN, onceki))

    def test_yeni_surec_hemen_uyarir(self):
        # restart oldu ve YENİDEN bayatladı: yeni olay, beklet me
        onceki = {"imza": "100", "ts": 1000}
        self.assertTrue(selfcheck.bayat_uyarilsin("200", 1000 + 60, onceki))

    def test_bozuk_durum_susturmaz(self):
        # durum dosyası bozuksa güvenli taraf UYARMAK, susmak değil
        self.assertTrue(selfcheck.bayat_uyarilsin("100", 1000, {"imza": "100",
                                                                "ts": "bozuk"}))
        self.assertTrue(selfcheck.bayat_uyarilsin("100", 1000, {"imza": "100"}))


if __name__ == "__main__":
    unittest.main()


class IsKartiTurEtiketiTest(unittest.TestCase):
    """2026-10-04: _job_one, Live Activity'yi başlatırken henüz tanımsız olan
    `kind` yereline dokunuyordu → departmansız her işte UnboundLocalError,
    kilit ekranı kartı hiç açılmıyordu (3 Pazar üst üste 20:07). Etiket iş
    kaydındaki 'kind' alanından gelmeli."""

    def test_departmansiz_iste_etiket_is_kaydindan_gelir(self):
        import inspect
        src = inspect.getsource(tb.Manager._job_one)
        bas = src.split("etkinlik_baslat(", 1)[1].split("except Exception", 1)[0]
        self.assertIn('get("kind"', bas)
        self.assertNotRegex(bas, r"or kind\b")
