"""Alarm kapısı (ghost/alarm.py, 2026-10-29) + nöbetçi sınıflamaları.

NEDEN: 30 günde sahibe 94 "elinden bir şey gelmeyen" altyapı alarmı + 115 selfcheck mesajı gitti
(denetim 06-arayuz §5, 04-ozerklik Ö1). Burada: sınıf (ghost → gitmez), günlük tavan 5, aynı
anahtar soğuması, gün devri, kapat/ozet şeması; iPhone/tünel/giriş/selfcheck/uptime bağlantıları.
Gönderim enjekte, ağ yok (GHOST_TEST)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests import common  # noqa: F401

from ghost import alarm  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "ops"))
import selfcheck  # noqa: E402
import tunnel_watch  # noqa: E402
import uptime_watch  # noqa: E402
from ops import giris_nobeti  # noqa: E402

T0 = 1_800_000_000.0   # sabit "şimdi" (2027-01) — gün devri testleri bunun üstüne ekler


class _Toplayici:
    def __init__(self, basari=True):
        self.giden, self.basari = [], basari

    def __call__(self, metin):
        self.giden.append(metin)
        return self.basari


class AlarmTabani(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="alarm-")
        self.yol = os.path.join(self.d, "alarm_durum.json")
        os.environ["GHOST_ALARM_DURUM"] = self.yol
        self.g = _Toplayici()

    def tearDown(self):
        os.environ.pop("GHOST_ALARM_DURUM", None)

    def oku(self):
        with open(self.yol, encoding="utf-8") as f:
            return json.load(f)

    def bildir(self, kaynak, metin, **kw):
        kw.setdefault("gonder", self.g)
        kw.setdefault("simdi", T0)
        return alarm.bildir(kaynak, metin, **kw)


class SinifVeKayit(AlarmTabani):
    def test_ghost_sinifi_erene_gitmez_ama_kaydedilir(self):
        r = self.bildir("iphone", "📵 iPhone durumu 50 saattir güncellenmedi", sinif="ghost", anahtar="durum")
        self.assertFalse(r["gitti"])
        self.assertEqual(r["neden"], "ghost")
        self.assertEqual(self.g.giden, [])
        d = self.oku()
        k = d["kayitlar"]["iphone:durum"]
        self.assertEqual(k["sayac"], 1)
        self.assertIn("güncellenmedi", k["son_metin"])
        self.assertEqual(k["ilk_ts"], T0)
        self.assertEqual(d["gun_sayac"]["ghost"], 1)

    def test_sahip_sinifi_gider(self):
        r = self.bildir("giris", "🚪 yabancı giriş", sinif="sahip", anahtar="yabanci")
        self.assertTrue(r["gitti"])
        self.assertEqual(r["neden"], "gitti")
        self.assertEqual(self.g.giden, ["🚪 yabancı giriş"])

    def test_gecersiz_sinif(self):
        with self.assertRaises(ValueError):
            self.bildir("x", "y", sinif="herkes")

    def test_anahtar_yoksa_kaynak_anahtardir(self):
        r = self.bildir("tunnel", "a", sinif="ghost")
        self.assertEqual(r["kimlik"], "tunnel:tunnel")

    def test_gonderim_hatasi_sayilir_ama_gitti_degil(self):
        g = _Toplayici(basari=False)
        r = self.bildir("x", "y", sinif="sahip", gonder=g)
        self.assertFalse(r["gitti"])
        self.assertEqual(r["neden"], "gonderilemedi")
        # gitmeyen alarm tavandan düşmez
        self.assertEqual(self.oku()["gun_sayac"].get("gonderim", 0), 0)


class Soguma(AlarmTabani):
    def test_ayni_anahtar_sogumada_ikinci_kez_gitmez_sayac_artar(self):
        self.bildir("selfcheck", "🧠 Beyin YANIT VERMİYOR", sinif="sahip", anahtar="beyin", soguma_sn=7200)
        r = self.bildir("selfcheck", "🧠 Beyin YANIT VERMİYOR", sinif="sahip", anahtar="beyin",
                        soguma_sn=7200, simdi=T0 + 1800)
        self.assertEqual(r["neden"], "soguma")
        self.assertEqual(r["sayac"], 2)
        self.assertEqual(len(self.g.giden), 1)

    def test_soguma_bitince_tekrar_gider(self):
        self.bildir("s", "m", sinif="sahip", anahtar="k", soguma_sn=7200)
        r = self.bildir("s", "m", sinif="sahip", anahtar="k", soguma_sn=7200, simdi=T0 + 7201)
        self.assertEqual(r["neden"], "gitti")
        self.assertEqual(len(self.g.giden), 2)

    def test_farkli_anahtar_sogumadan_etkilenmez(self):
        self.bildir("s", "m1", sinif="sahip", anahtar="a")
        r = self.bildir("s", "m2", sinif="sahip", anahtar="b", simdi=T0 + 1)
        self.assertEqual(r["neden"], "gitti")

    def test_soguma_sifir_her_seferinde_gider(self):
        self.bildir("s", "m", sinif="sahip", anahtar="k", soguma_sn=0)
        r = self.bildir("s", "m", sinif="sahip", anahtar="k", soguma_sn=0, simdi=T0 + 1)
        self.assertEqual(r["neden"], "gitti")


class GunlukTavan(AlarmTabani):
    def _doldur(self, n=alarm.GUNLUK_TAVAN):
        for i in range(n):
            r = self.bildir("s", "alarm %d" % i, sinif="sahip", anahtar="k%d" % i, simdi=T0 + i)
            self.assertEqual(r["neden"], "gitti", i)

    def test_tavan_dolunca_nabiza_duser_tek_satir_bir_kez(self):
        self._doldur()
        r6 = self.bildir("s", "alarm 6", sinif="sahip", anahtar="k6", simdi=T0 + 10)
        self.assertEqual(r6["neden"], "tavan")
        self.assertFalse(r6["gitti"])
        self.assertEqual(len(self.g.giden), alarm.GUNLUK_TAVAN + 1)
        self.assertIn("Nabız'da", self.g.giden[-1])
        self.assertIn("1 alarm daha", self.g.giden[-1])
        r7 = self.bildir("s", "alarm 7", sinif="sahip", anahtar="k7", simdi=T0 + 20)
        self.assertEqual(r7["neden"], "tavan")
        self.assertEqual(len(self.g.giden), alarm.GUNLUK_TAVAN + 1)   # ikinci "ve N daha" YOK
        o = alarm.ozet(simdi=T0 + 30)
        self.assertEqual(o["sayac"]["nabiz"], 2)
        self.assertEqual(o["sayac"]["gonderim"], alarm.GUNLUK_TAVAN)
        nabizda = [s for s in o["sari"] if s["nabizda"]]
        self.assertEqual({s["anahtar"] for s in nabizda}, {"k6", "k7"})

    def test_ghost_sinifi_tavani_yemez(self):
        for i in range(20):
            self.bildir("iphone", "x", sinif="ghost", anahtar="durum", simdi=T0 + i)
        r = self.bildir("s", "önemli", sinif="sahip", anahtar="k", simdi=T0 + 100)
        self.assertEqual(r["neden"], "gitti")

    def test_gun_devri_sayaci_sifirlar_ve_tasan_satiri_yeniden_acar(self):
        self._doldur()
        self.bildir("s", "alarm 6", sinif="sahip", anahtar="k6", simdi=T0 + 10)   # tavan + satır
        yeni_gun = T0 + 86400 + 100
        r = self.bildir("s", "yeni gün", sinif="sahip", anahtar="yg", simdi=yeni_gun)
        self.assertEqual(r["neden"], "gitti")
        o = alarm.ozet(simdi=yeni_gun + 1)
        self.assertEqual(o["sayac"]["gonderim"], 1)
        self.assertEqual(o["sayac"]["nabiz"], 0)
        d = self.oku()
        self.assertIsNone(d["tasan_bildirildi"])

    def test_eski_kayitlar_budanir(self):
        self.bildir("s", "eski", sinif="ghost", anahtar="e", simdi=T0)
        self.bildir("s", "yeni", sinif="ghost", anahtar="y", simdi=T0 + alarm.KAYIT_OMRU_SN + 10)
        d = self.oku()
        self.assertNotIn("s:e", d["kayitlar"])
        self.assertIn("s:y", d["kayitlar"])


class KapatVeOzet(AlarmTabani):
    def test_ozet_bos_dosya(self):
        o = alarm.ozet(simdi=T0)
        self.assertEqual(o["sari"], [])
        self.assertEqual(o["sayac"]["tavan"], alarm.GUNLUK_TAVAN)
        self.assertEqual(set(o["sayac"]), {"sahip", "ghost", "nabiz", "gonderim", "tavan"})

    def test_ozet_semasi_ve_siralama(self):
        self.bildir("tunnel", "🔌 zombi", sinif="ghost", anahtar="zombi", simdi=T0)
        self.bildir("tunnel", "🔌 zombi", sinif="ghost", anahtar="zombi", simdi=T0 + 60)
        self.bildir("giris", "🚪 yabancı", sinif="sahip", anahtar="yabanci", simdi=T0 + 120)
        o = alarm.ozet(simdi=T0 + 130)
        self.assertEqual(set(o), {"gun", "sayac", "sari"})
        self.assertEqual([s["kaynak"] for s in o["sari"]], ["giris", "tunnel"])   # en yeni önce
        t = o["sari"][1]
        self.assertEqual(set(t), {"kaynak", "anahtar", "sinif", "metin", "sayac", "ilk", "son",
                                  "son_ts", "nabizda"})
        self.assertEqual(t["sayac"], 2)
        self.assertEqual(t["sinif"], "ghost")
        self.assertFalse(t["nabizda"])
        self.assertFalse(o["sari"][0]["nabizda"])   # sahibe gitti → Nabız'da değil

    def test_kapat_sarida_dusurur_sonraki_alarm_yeni_olay(self):
        self.bildir("iphone", "📵", sinif="ghost", anahtar="durum", simdi=T0)
        self.assertTrue(alarm.kapat("iphone", "durum", simdi=T0 + 10))
        self.assertEqual(alarm.ozet(simdi=T0 + 20)["sari"], [])
        r = self.bildir("iphone", "📵 yine", sinif="ghost", anahtar="durum", simdi=T0 + 30)
        self.assertEqual(r["sayac"], 1)        # sayaç yeni olayla başa döner
        self.assertEqual(len(alarm.ozet(simdi=T0 + 40)["sari"]), 1)

    def test_kapat_olmayan_kayit(self):
        self.assertFalse(alarm.kapat("yok", "yok", simdi=T0))

    def test_24_saatten_eski_sari_degil(self):
        self.bildir("s", "m", sinif="ghost", anahtar="k", simdi=T0)
        self.assertEqual(alarm.ozet(simdi=T0 + alarm.SARI_PENCERE_SN + 1)["sari"], [])

    def test_bozuk_dosya_sifirdan_baslar(self):
        with open(self.yol, "w") as f:
            f.write("{bozuk")
        r = self.bildir("s", "m", sinif="ghost")
        self.assertEqual(r["sayac"], 1)


class GirisNobetiSinifi(unittest.TestCase):
    """SSH giriş alarmı: bizim anahtar + yeni IP → ghost; yabancı/parola/root → sahip."""
    YETKILI = {"SHA256:abc": "ghost-runtime-nonroot"}

    def test_uyari_yoksa_none(self):
        self.assertIsNone(giris_nobeti.sinifla({"yontem": "publickey", "kul": "ghost"}, None, self.YETKILI))

    def test_kendi_anahtar_yeni_ip_ghost(self):
        g = {"yontem": "publickey", "kul": "ghost", "ip": "9.9.9.9", "parmak": "SHA256:abc"}
        self.assertEqual(giris_nobeti.sinifla(g, "yeni yerden giriş", self.YETKILI), "ghost")

    def test_tanimadigimiz_anahtar_eren(self):
        g = {"yontem": "publickey", "kul": "ghost", "ip": "9.9.9.9", "parmak": "SHA256:zzz"}
        self.assertEqual(giris_nobeti.sinifla(g, "yeni yerden giriş", self.YETKILI), "sahip")

    def test_parmak_izi_yoksa_eren(self):
        g = {"yontem": "publickey", "kul": "ghost", "ip": "9.9.9.9", "parmak": None}
        self.assertEqual(giris_nobeti.sinifla(g, "yeni yerden giriş", self.YETKILI), "sahip")

    def test_parola_ve_root_her_zaman_eren(self):
        g = {"yontem": "password", "kul": "ghost", "ip": "1.1.1.1", "parmak": "SHA256:abc"}
        self.assertEqual(giris_nobeti.sinifla(g, "PAROLA ile giriş", self.YETKILI), "sahip")
        g = {"yontem": "publickey", "kul": "root", "ip": "1.1.1.1", "parmak": "SHA256:abc"}
        self.assertEqual(giris_nobeti.sinifla(g, "root girişi", self.YETKILI), "sahip")

    def test_yetkili_anahtarlar_parmak_ve_ad_okur_deger_okumaz(self):
        cikti = "256 SHA256:abcDEF ghost-runtime-nonroot (ED25519)\n" \
                "3072 SHA256:ikinci sahip mac yedek (RSA)\n"
        with mock.patch.object(subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 0, cikti, "")) as r:
            y = giris_nobeti.yetkili_anahtarlar("/tmp/x")
        self.assertEqual(y, {"SHA256:abcDEF": "ghost-runtime-nonroot", "SHA256:ikinci": "sahip mac yedek"})
        self.assertEqual(r.call_args[0][0][:2], ["ssh-keygen", "-lf"])

    def test_yetkili_anahtarlar_hata_bos(self):
        with mock.patch.object(subprocess, "run", side_effect=OSError("yok")):
            self.assertEqual(giris_nobeti.yetkili_anahtarlar("/tmp/x"), {})


class TunelArdisik(unittest.TestCase):
    def test_zincir_sayimi(self):
        say = tunnel_watch.ardisik_say
        self.assertEqual(say({}, "zombi-cozulemedi"), 1)
        self.assertEqual(say({"durum": "saglikli"}, "zombi-cozulemedi"), 1)
        self.assertEqual(say({"durum": "zombi-cozulemedi", "ardisik": 1}, "zombi-cozulemedi"), 2)
        self.assertEqual(say({"durum": "zombi-cozulemedi"}, "zombi-cozulemedi"), 2)   # eski kayıt biçimi
        self.assertEqual(say({"durum": "zombi-cozulemedi", "ardisik": 2}, "saglikli"), 0)
        self.assertEqual(say({"durum": "zombi-cozulemedi", "ardisik": "bozuk"}, "zombi-cozulemedi"), 1)

    def test_sinif_esigi(self):
        self.assertEqual(tunnel_watch.cozulemedi_sinifi(1), ("ghost", "zombi-cozulemedi"))
        self.assertEqual(tunnel_watch.cozulemedi_sinifi(2), ("ghost", "zombi-cozulemedi"))
        self.assertEqual(tunnel_watch.cozulemedi_sinifi(3), ("sahip", "zombi-kalici"))
        self.assertEqual(tunnel_watch.cozulemedi_sinifi(9)[0], "sahip")

    def test_telegram_alarm_kapisina_gider(self):
        d = tempfile.mkdtemp()
        os.environ["GHOST_ALARM_DURUM"] = os.path.join(d, "a.json")
        try:
            with mock.patch.object(alarm, "_varsayilan_gonder") as g:
                tunnel_watch.telegram("🔌 test", sinif="ghost")
                g.assert_not_called()
                tunnel_watch.telegram("🔌 test", sinif="sahip", anahtar="zombi-kalici")
                g.assert_called_once()
            o = alarm.ozet()
            self.assertEqual({s["anahtar"] for s in o["sari"]}, {"zombi", "zombi-kalici"})
        finally:
            os.environ.pop("GHOST_ALARM_DURUM", None)


class SelfcheckSinifi(unittest.TestCase):
    def test_ghost_sinifi_satirlar(self):
        for satir in ("♻️ Köprü boştaydı, bayat kod (disk 01:00, süreç 28 Oct 00:00) → yenilendi: tamam",
                      "♻️ Kokpit API bayat koddaydı → yenilendi: tamam",
                      "🧟 Köprü BAYAT KOD koşuyor — disk 01:00, süreç ...; köprü boşalınca kendiliğinden yenilenecek",
                      "⬆️ Claude CLI 2.1 → 2.2 (model eşlemesi tazelendi)",
                      "⏳ İş #12 45 dakikadır takılı."):
            self.assertEqual(selfcheck.sinifla(satir)[0], "ghost", satir)

    def test_sahip_sinifi_ve_sogumalar(self):
        s = selfcheck.sinifla
        self.assertEqual(s("♻️ Köprü boştaydı, bayat kod → yenilendi: BAŞARISIZ, elle bak"),
                         ("sahip", "yenileme-basarisiz", 3600))
        self.assertEqual(s("🧠 Beyin (Claude) YANIT VERMİYOR — auth/API/limit? Yedek motor YOK"),
                         ("sahip", "beyin", 7200))
        self.assertEqual(s("🔐 Sır vekili CEVAP VERMİYOR (URLError) — git push etkilenir"),
                         ("sahip", "vekil-dusuk", 3600))
        self.assertEqual(s("🔐 Onay servisi CEVAP VERMİYOR (x) — Kokpit 503"), ("sahip", "onay-dusuk", 3600))
        self.assertEqual(s("🔒 Güvenlik kapısı: repo kaynağı ile canlı kopya FARKLI")[::2], ("sahip", 86400))
        self.assertEqual(s("🪝 Webhook sunucusu (ghost-hook) bayat kodla koşuyor — yenile")[::2], ("sahip", 86400))
        self.assertEqual(s("🔴 Köprü çalışmıyordu — diriltme başladı."), ("sahip", "kopru-dusuk", 3600))
        self.assertEqual(s("💀 NÖBETÇİ SESSİZ: uptime — 90 dakikadır atmıyor")[::2], ("sahip", 6 * 3600))

    def test_bilinmeyen_satir_erene_kararli_anahtarla(self):
        a = selfcheck.sinifla("🧾 Denetim günlüğü 3 kaydı yazamadı")
        b = selfcheck.sinifla("🧾 Denetim günlüğü 3 kaydı yazamadı")
        self.assertEqual(a, b)
        self.assertEqual(a[0], "sahip")
        self.assertTrue(a[1].startswith("diger-"))

    def test_dagit_tek_mesajda_toplar_ghost_nabizda_kalir(self):
        d = tempfile.mkdtemp()
        os.environ["GHOST_ALARM_DURUM"] = os.path.join(d, "a.json")
        try:
            problems = ["♻️ Köprü boştaydı, bayat kod → yenilendi: tamam",
                        "🧠 Beyin (Claude) YANIT VERMİYOR — auth?",
                        "🔐 Sır vekili CEVAP VERMİYOR (x)"]
            giden, kalan = selfcheck.alarmlari_dagit(problems, simdi=T0)
            self.assertEqual(giden, problems[1:])
            self.assertEqual(kalan, problems[:1])
            # aynı tur 30 dk sonra: beyin soğumada (2 sa), vekil soğumada (1 sa) → hiçbiri gitmez
            giden2, kalan2 = selfcheck.alarmlari_dagit(problems, simdi=T0 + 1800)
            self.assertEqual(giden2, [])
            self.assertEqual(len(kalan2), 3)
            # 90 dk sonra: vekil soğuması bitti, beyin sürüyor
            giden3, _ = selfcheck.alarmlari_dagit(problems, simdi=T0 + 5400)
            self.assertEqual(giden3, problems[2:])
            o = alarm.ozet(simdi=T0 + 5400)
            self.assertEqual({s["anahtar"] for s in o["sari"]}, {"bayat-yenilendi", "beyin", "vekil-dusuk"})
        finally:
            os.environ.pop("GHOST_ALARM_DURUM", None)


class UptimeMudahale(unittest.TestCase):
    """Olay müdahalesi eşiği: 2 ardışık DOWN + 5 dk, kesinti başına bir kez (d4)."""
    U = "https://ornek.test"

    def test_ilk_tur_tetiklemez(self):
        self.assertFalse(uptime_watch.mudahale_gerekir_mi(self.U, T0, {}, {}))

    def test_ikinci_tur_5dk_gecmisse_tetikler(self):
        self.assertTrue(uptime_watch.mudahale_gerekir_mi(self.U, T0 + 600, {self.U: T0}, {}))

    def test_ikinci_tur_5dk_dolmamissa_bekler(self):
        self.assertFalse(uptime_watch.mudahale_gerekir_mi(self.U, T0 + 120, {self.U: T0}, {}))

    def test_ayni_kesintide_ikinci_kez_acilmaz(self):
        self.assertFalse(uptime_watch.mudahale_gerekir_mi(self.U, T0 + 1200, {self.U: T0}, {self.U: T0 + 600}))

    def test_bozuk_ts_tetiklemez(self):
        self.assertFalse(uptime_watch.mudahale_gerekir_mi(self.U, T0, {self.U: "x"}, {}))


if __name__ == "__main__":
    unittest.main()
