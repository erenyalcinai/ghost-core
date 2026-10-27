"""Echo (ses ajanı) araç uçları: her araç kısa düz metin döner, ham komut/yol sese gitmez,
kısa/boş görev Ghost'a iletilmez, araç listesi ile arac_calistir dalları birbirini tutar."""
import unittest
from unittest import mock

from tests import common  # noqa: F401  (yol/ağ izolasyonu)
from ghost import ses_ajani

NABIZ = {
    "siteler": [{"ad": "example.com", "ok": True}, {"ad": "app-b.example.com", "ok": False}],
    "site_nabzi": {"var": True, "yas_dk": 3, "toplam_7g": 40, "toplam_24s": 2, "bayat": False,
                   "siteler": [{"ad": "example.com", "ziyaret_7g": 30, "ziyaret_24s": 2, "kaynak": "l.instagram.com · x"},
                               {"ad": "app-b.example.com", "ziyaret_7g": 10, "ziyaret_24s": 0, "kaynak": ""}]},
    "is": {"var": True, "tarih": "2026-09-26", "yas_gun": 0,
           "metrikler": [{"ad": "kullanıcı", "goster": 7}, {"ad": "ödeyen", "goster": 0},
                         {"ad": "aktif (24 sa)", "goster": 6, "fark": -1}]},
    "makineler": [{"ad": "VPS", "durum": "iyi", "detay": "yük 0.1"}, {"ad": "Mac", "durum": "kötü", "detay": "tünel yok"}],
    "baglanti": {"mac": False, "telefon": True, "tunel": "kopuk"},
    "nobetciler": [{"ad": "uptime", "ok": True}, {"ad": "selfcheck", "ok": False}],
    "kota": {"var": True, "plan": "Max 5x", "limitler": [{"ad": "Oturum", "yuzde": 58, "sifirlanma_metin": "bugün 16:19"}]},
    "bugun": {"tarih": "2026-09-26", "sayilar": {"commit": 3, "mesaj": 10, "eylem": 2},
              "commitler": [{"saat": "13:03", "baslik": "Kokpit 0.15 — ayrıntı"}]},
    "freeze": False,
}


class Araclar(unittest.TestCase):
    def setUp(self):
        import os
        try:
            os.remove(ses_ajani._oturum_yolu())
        except FileNotFoundError:
            pass
        ses_ajani.oturum_ac()      # Kokpit'ten açılmış konuşma (imzalı /v1/ses/token)

    def test_hassas_araclar_oturumsuz_calismaz(self):
        import os
        os.remove(ses_ajani._oturum_yolu())
        with mock.patch("ghost.kokpit.outbox_yaz") as oy:
            for ad in ses_ajani.HASSAS_ARACLAR:
                out = ses_ajani.arac_calistir(ad, {"gorev": "sitelerin trafiğini çıkar", "sorgu": "x"})
                self.assertIn("açık oturum yok", out["metin"], ad)
            oy.assert_not_called()

    def test_gorev_saat_tavani(self):
        with mock.patch("ghost.kokpit.outbox_yaz", return_value=1), \
             mock.patch("ghost.kokpit.mesaj_kuyrukla") as mk, \
             mock.patch("ghost.channels.son_kanal_ayarla"):
            for _ in range(ses_ajani.GOREV_SAAT_TAVAN + 2):
                out = ses_ajani.arac_calistir("gorev", {"gorev": "bir iş yap lütfen"})
        self.assertEqual(mk.call_count, ses_ajani.GOREV_SAAT_TAVAN)
        self.assertIn("çok fazla", out["metin"])

    def test_arac_listesi_ile_dallar_tutarli(self):
        # ElevenLabs'e tanıtılan her araç arac_calistir'de bir dala düşmeli (bilinmeyen araç yok)
        for ad in ses_ajani.ARAC_ADLARI:
            kisa = ad.replace("ghost_", "")
            with mock.patch.object(ses_ajani, "_nabiz", return_value=NABIZ), \
                 mock.patch.object(ses_ajani, "hafiza_metni", return_value="h"), \
                 mock.patch.object(ses_ajani, "son_mesajlar_metni", return_value="s"), \
                 mock.patch.object(ses_ajani, "durum_metni", return_value="d"):
                out = ses_ajani.arac_calistir(kisa, {"sorgu": "x", "gorev": ""})
            self.assertIn("metin", out)
            self.assertNotEqual(out["metin"], "bilinmeyen araç", ad)
        # URL'ler araç adından türetilir: ghost_ öneki düşer
        for a in ses_ajani.ARACLAR:
            self.assertTrue(a["tool_config"]["api_schema"]["url"].endswith("/v1/ses/arac/" + a["tool_config"]["name"][6:]))

    def test_siteler_metni(self):
        m = ses_ajani.siteler_metni(NABIZ)
        self.assertIn("ERİŞİLEMEYEN: app-b.example.com", m)
        self.assertIn("son 7 günde 40", m)
        self.assertIn("example.com 30 (24 saat 2, kaynak l.instagram.com)", m)

    def test_urun_ve_makineler_metni(self):
        u = ses_ajani.urun_metni(NABIZ)
        self.assertIn("kullanıcı 7", u); self.assertIn("aktif (24 sa) 6 (dünden -1)", u)
        m = ses_ajani.makineler_metni(NABIZ)
        self.assertIn("Mac kötü (tünel yok)", m)
        self.assertIn("Mac KOPUK, telefon bağlı, tünel kopuk", m)
        self.assertIn("Nöbetçiler: 1/2 sağlıklı; sorunlu selfcheck", m)
        self.assertIn("Oturum yüzde 58", m)

    def test_bugun_metni(self):
        with mock.patch("ghost.duyular.bugun_metin", return_value="📅 Bugün takvimin boş."), \
             mock.patch("ghost.gorevler.metin", return_value=""):
            b = ses_ajani.bugun_metni(NABIZ)
        self.assertIn("3 commit", b)
        self.assertIn("13:03 Kokpit 0.15", b)
        self.assertNotIn("ayrıntı", b)          # uzun başlık kuyruğu sese gitmez
        self.assertIn("Takvim: Bugün takvimin boş", b)

    def test_is_fiili_ham_komutu_gizler(self):
        self.assertEqual(ses_ajani._is_fiili("Bash · cd ~/jarvis && rm -rf x"), "komut çalıştırıyor")
        self.assertEqual(ses_ajani._is_fiili("mcp__playwright__browser_click · {...}"), "tarayıcıda geziyor")
        self.assertEqual(ses_ajani._is_fiili(None), "bir iş üzerinde")
        self.assertEqual(ses_ajani._sure_metni(335), "6 dakikadır")

    def test_gorev_kisa_metin_iletilmez(self):
        with mock.patch("ghost.kokpit.outbox_yaz") as oy:
            out = ses_ajani.arac_calistir("gorev", {"gorev": "  ab "})
            self.assertIn("iletilmedi", out["metin"]); oy.assert_not_called()

    def test_gorev_iletilen_metni_geri_soyler(self):
        with mock.patch("ghost.kokpit.outbox_yaz", return_value=77) as oy, \
             mock.patch("ghost.kokpit.mesaj_kuyrukla") as mk, \
             mock.patch("ghost.channels.son_kanal_ayarla"):
            out = ses_ajani.arac_calistir("gorev", {"gorev": "sitelerin  trafiğini\nçıkar"})
        self.assertIn('no 77', out["metin"])
        self.assertIn('"sitelerin trafiğini çıkar"', out["metin"])
        self.assertTrue(oy.call_args[0][0].startswith("[Sesle, ses ajanı üzerinden] sitelerin trafiğini çıkar"))
        mk.assert_called_once()

    def test_hafiza_bos_sorgu(self):
        self.assertIn("boş", ses_ajani.hafiza_metni(" "))

    def test_hafiza_tum_kelimeler_once_ve_yeni_once(self):
        rows = [("2026-08-01 10:00:00", "proje", "site listesi"),
                ("2026-09-20 10:00:00", "iş", "Site ziyaret sayısı 128"),
                ("2026-09-10 10:00:00", "iş", "site ZİYARET ölçümü kuruldu")]
        with mock.patch("ghost.memory.recall", return_value=rows):
            m = ses_ajani.hafiza_metni("site ziyaret")
        self.assertIn("Hafıza (2 kayıt, yeniden eskiye)", m)
        self.assertLess(m.index("2026-09-20"), m.index("2026-09-10"))
        self.assertNotIn("site listesi", m)

    def test_istem_ve_ayarlar(self):
        # yanlış duyma + yapamadıkları bilgisi istemde; ASR sözlüğü ve skip_turn ajan gövdesinde
        self.assertIn("YANLIŞ DUYMA", ses_ajani.ISTEM)
        self.assertIn("e-posta GÖNDEREMEZ", ses_ajani.ISTEM)
        self.assertIn("{{system__time}}", ses_ajani.ISTEM)
        gov = ses_ajani._ajan_govde({ad: "t_" + ad for ad in ses_ajani.ARAC_ADLARI})
        cc = gov["conversation_config"]
        self.assertEqual(len(cc["agent"]["prompt"]["tool_ids"]), len(ses_ajani.ARACLAR))
        self.assertIn("Kokpit", cc["asr"]["keywords"])
        self.assertIn("skip_turn", cc["agent"]["prompt"]["built_in_tools"])
        self.assertGreaterEqual(cc["turn"]["turn_timeout"], 15)


if __name__ == "__main__":
    unittest.main()
