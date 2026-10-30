"""ghost/nabiz.py — nabız birimi (Faz 3.1): tur/damga, canlılık, kilit, köprü yedek yolu, döngü."""
import os
import time
import unittest

from tests import common  # noqa: F401

from ghost import durum, nabiz


class _Mod:
    def __init__(self, ad, kosan, patla=False):
        self.ad, self.kosan, self.patla = ad, kosan, patla

    def tick(self):
        if self.patla:
            raise RuntimeError("boom")
        self.kosan.append(self.ad)


class TestNabiz(unittest.TestCase):
    def setUp(self):
        durum.sil(nabiz.DURUM_AD)
        durum.sil("heartbeat." + nabiz.DURUM_AD)
        nabiz._kopru_onceki[0] = None
        nabiz._son_heartbeat[0] = 0.0
        self.kosan = []
        self.mods = (("monitors", _Mod("monitors", self.kosan, patla=True)), ("schedule", _Mod("schedule", self.kosan)),
                     ("goals", _Mod("goals", self.kosan)), ("kurallar", _Mod("kurallar", self.kosan)))

    def test_kilit_yolu_testte_gecici_dizinde(self):
        self.assertTrue(nabiz.kilit_yolu().startswith(os.environ["GHOST_OPS"]))

    def test_tur_dort_ticki_izole_kosar_ve_damgalar(self):
        sonuc = nabiz.tur(mods=self.mods)
        self.assertEqual(self.kosan, ["schedule", "goals", "kurallar"])
        self.assertIn("boom", sonuc["monitors"])
        self.assertIsNone(sonuc["goals"])
        d = durum.oku(nabiz.DURUM_AD)
        self.assertEqual(d["pid"], os.getpid())
        self.assertIn("monitors", d["hata"])
        self.assertNotIn("goals", d["hata"])
        self.assertIsNotNone(durum.oku("heartbeat.nabiz"), "Nabız damgası basılmalı")
        self.assertIn("CANLI", nabiz.durum_metni())
        self.assertIn("boom", nabiz.durum_metni())

    def test_heartbeat_dakikada_bir(self):
        nabiz.tur(mods=self.mods, now=1000.0)
        hb1 = durum.oku("heartbeat.nabiz")["ts"]
        nabiz.tur(mods=self.mods, now=1020.0)
        self.assertEqual(durum.oku("heartbeat.nabiz")["ts"], hb1, "20 sn sonra heartbeat yenilenmemeli")
        nabiz.tur(mods=self.mods, now=1070.0)
        self.assertEqual(durum.oku("heartbeat.nabiz")["ts"], 1070)

    def test_canli_tazelik(self):
        self.assertFalse(nabiz.canli(), "damga yokken canlı değil")
        self.assertIn("hiç damga yok", nabiz.durum_metni())
        now = time.time()
        nabiz.damga({}, now=now)
        self.assertTrue(nabiz.canli(now=now + 10))
        self.assertFalse(nabiz.canli(now=now + nabiz.TAZE_SN + 1))
        self.assertIn("BAYAT", nabiz.durum_metni(now=now + nabiz.TAZE_SN + 1))

    def test_kopru_birim_canliyken_dokunmaz(self):
        nabiz.damga({})
        gunluk = []
        self.assertFalse(nabiz.kopru_tick(self.mods, gunluk.append))
        self.assertEqual(self.kosan, [])
        self.assertEqual(len(gunluk), 1)
        self.assertIn("canlı", gunluk[0])
        # ikinci çağrı: kenar yok → günlük büyümez
        nabiz.kopru_tick(self.mods, gunluk.append)
        self.assertEqual(len(gunluk), 1)

    def test_kopru_birim_susunca_devralir_ve_donunce_birakir(self):
        gunluk = []
        self.assertTrue(nabiz.kopru_tick(self.mods, gunluk.append))
        self.assertEqual(self.kosan, ["schedule", "goals", "kurallar"])
        self.assertIn("SUSTU", gunluk[0])
        self.assertTrue(any("boom" in g for g in gunluk), "düşen tick köprü günlüğüne yazılmalı")
        self.assertIsNone(durum.oku(nabiz.DURUM_AD), "köprünün yedek yolu birim damgası BASMAZ")
        # birim geri geldi
        nabiz.damga({})
        del self.kosan[:]
        n = len(gunluk)
        self.assertFalse(nabiz.kopru_tick(self.mods, gunluk.append))
        self.assertEqual(self.kosan, [])
        self.assertEqual(len(gunluk), n + 1)
        self.assertIn("canlı", gunluk[-1])

    def test_kopru_kilit_meskulse_turu_atlar(self):
        """Birim tam o an tur atıyorsa (kilit onda) köprü beklemez, o turu atlar."""
        with nabiz.kilit(bloke=True) as bende:
            self.assertTrue(bende)
            # aynı süreçte ikinci açık dosya tanıtıcısı → flock çakışır
            self.assertFalse(nabiz.kopru_tick(self.mods, None))
        self.assertEqual(self.kosan, [])
        with nabiz.kilit(bloke=False) as bende:
            self.assertTrue(bende, "kilit bırakılınca yeniden alınabilmeli")

    def test_kos_bir_tur_ve_gunluk(self):
        gunluk = []
        uyku = []
        orijinal = nabiz.moduller
        nabiz.moduller = lambda: self.mods
        try:
            nabiz.kos(aralik=1, bir=True, log_fn=gunluk.append, uyku=uyku.append)
        finally:
            nabiz.moduller = orijinal
            nabiz._log = None
        self.assertEqual(self.kosan, ["schedule", "goals", "kurallar"])
        self.assertEqual(uyku, [], "--bir: uyumadan çıkar")
        self.assertTrue(gunluk[0].startswith("nabız birimi başladı"))
        self.assertEqual(gunluk[-1], "nabız birimi durdu")

    def test_moduller_gercek_dort_modul(self):
        adlar = [ad for ad, m in nabiz.moduller()]
        self.assertEqual(adlar, ["monitors", "schedule", "goals", "kurallar"])
        for _ad, m in nabiz.moduller():
            self.assertTrue(callable(getattr(m, "tick", None)), _ad)


if __name__ == "__main__":
    unittest.main()
