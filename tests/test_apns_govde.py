"""Bildirim gövdesi (Kokpit Watch aşama 1, yama 02): cevabın ilk paragrafı, Markdown işaretlerinden temiz,
devamı varsa "devamı telefonda"; sohbet bildirimi "sohbet" kategorisi taşır."""
import unittest

from tests import common  # noqa: F401

from ghost import apns, channels, kokpit  # noqa: E402

EK = apns.DEVAM_EKI


class GovdeTest(unittest.TestCase):
    def test_tek_paragraf_eksiz(self):
        self.assertEqual(apns.bildirim_govdesi("Deploy bitti, site açık."), "Deploy bitti, site açık.")

    def test_ilk_paragraf_ve_ek(self):
        m = "Deploy bitti, site açık.\n\nAyrıntı: üç dosya değişti.\n\nSon."
        self.assertEqual(apns.bildirim_govdesi(m), "Deploy bitti, site açık." + EK)

    def test_isaretler_temizlenir(self):
        m = "**Sonuç:** `main` dalına [commit](https://x.y/z) atıldı, *iki* test ~~kırık~~ geçti."
        self.assertEqual(apns.bildirim_govdesi(m), "Sonuç: main dalına commit atıldı, iki test kırık geçti.")

    def test_carpim_bozulmaz(self):
        self.assertEqual(apns.bildirim_govdesi("Tutar 2 * 3 * 4 eder."), "Tutar 2 * 3 * 4 eder.")
        self.assertEqual(apns.bildirim_govdesi("dosya_adi_boyle kalır"), "dosya_adi_boyle kalır")

    def test_madde_listesi(self):
        m = "- bir\n* iki\n+ üç"
        self.assertEqual(apns.bildirim_govdesi(m), "• bir\n• iki\n• üç")

    def test_baslik_alttaki_paragrafla(self):
        m = "## Durum\n\nÜç organ sağlıklı.\n\nGerisi."
        self.assertEqual(apns.bildirim_govdesi(m), "Durum\nÜç organ sağlıklı." + EK)
        self.assertEqual(apns.bildirim_govdesi("# Yalnız başlık"), "Yalnız başlık")

    def test_tablo_ve_kod_girmez(self):
        m = "Özet aşağıda.\n\n| a | b |\n|---|---|\n| 1 | 2 |"
        self.assertEqual(apns.bildirim_govdesi(m), "Özet aşağıda." + EK)
        m = "```diff\n- eski\n+ yeni\n```\n\nYama hazır."
        self.assertEqual(apns.bildirim_govdesi(m), "Yama hazır." + EK)
        self.assertEqual(apns.bildirim_govdesi("| a | b |\n|---|---|"), "(tablo ya da kod — telefonda)")
        self.assertEqual(apns.bildirim_govdesi("```\nx\n```"), "(tablo ya da kod — telefonda)")

    def test_bos(self):
        self.assertEqual(apns.bildirim_govdesi(""), "")
        self.assertEqual(apns.bildirim_govdesi(None), "")
        self.assertEqual(apns.bildirim_govdesi("  \n\n "), "")

    def test_uzun_paragraf_kelime_sinirinda(self):
        m = " ".join(["kelime%d" % i for i in range(200)])
        g = apns.bildirim_govdesi(m)
        self.assertTrue(g.endswith(EK))
        cekirdek = g[:-len(EK)]
        self.assertLessEqual(len(cekirdek), apns.GOVDE_TAVAN)
        self.assertGreater(len(cekirdek), apns.GOVDE_TAVAN * 0.6)
        self.assertTrue(m.startswith(cekirdek))
        self.assertRegex(cekirdek, r"kelime\d+$")              # kelime ortasında kesilmedi
        self.assertNotEqual(m[len(cekirdek)], "e")

    def test_bosluksuz_uzun_metin(self):
        g = apns.bildirim_govdesi("x" * 5000)
        self.assertEqual(g, "x" * apns.GOVDE_TAVAN + EK)

    def test_alintisi(self):
        self.assertEqual(apns.bildirim_govdesi("> alıntı satırı\n> ikinci"), "alıntı satırı\nikinci")

    def test_yuk_tavan_ve_kategori(self):
        y = apns.yuk("b", "g" * 5000, {"sekme": "sohbet"}, kategori="sohbet")
        self.assertEqual(len(y["aps"]["alert"]["body"]), apns.GOVDE_YUK_TAVAN)
        self.assertEqual(y["aps"]["category"], "sohbet")
        self.assertNotIn("category", apns.yuk("b", "g")["aps"])
        en_uzun = apns.bildirim_govdesi("ğ" * 5000)             # iki baytlık harflerle de 4 KB'ın altında
        import json
        self.assertLess(len(json.dumps(apns.yuk("Ghost", en_uzun, {"sekme": "sohbet"}, kategori="sohbet"),
                                       ensure_ascii=False).encode("utf-8")), 4096)

    def test_ek_ve_kategori_yalniz_saate(self):
        """Telefon bildirimi gövde metni dışında değişmez: "devamı telefonda" eki ve "sohbet" kategorisi yalnız saate."""
        c = kokpit._db()
        c.execute("DELETE FROM kokpit_devices")
        for did, tok in (("telG", "aa" * 32), ("saatG", "bb" * 32)):
            c.execute("INSERT INTO kokpit_devices(id, ad, key_hex, durum, created, last_seen, apns_token, apns_env) "
                      "VALUES(?,?,'','aktif','x','y',?,'development')", (did, did, tok))
        c.commit()
        c.close()
        giden = {}
        eski_t, eski_g = apns.cihaz_turleri, apns.gonder
        apns.cihaz_turleri = lambda: {"telG": "tam", "saatG": "cep"}

        def sahte(token, env, baslik, govde, veri=None, ses=True, rozet=None, collapse=None, onem=None, konu=None,
                  kategori=None):
            giden[token[:2]] = (govde, kategori)
            return True, 200, ""
        apns.gonder = sahte
        try:
            apns.herkese("Ghost", apns.bildirim_govdesi("Bitti.\n\nAyrıntı."), {"sekme": "sohbet"}, collapse="sohbet")
            self.assertEqual(giden, {"aa": ("Bitti. …", None), "bb": ("Bitti." + EK, "sohbet")})
            giden.clear()
            apns.herkese("Ghost", apns.bildirim_govdesi("Tek paragraf."), {"sekme": "sohbet"}, collapse="sohbet")
            self.assertEqual(giden, {"aa": ("Tek paragraf.", None), "bb": ("Tek paragraf.", "sohbet")})
            giden.clear()                                       # karar kartı ve iş sonucu: saatte de kategori YOK
            apns.herkese("Ghost onay bekliyor", "Onaylıyor musun?", {"sekme": "kararlar"}, collapse="karar")
            apns.herkese("✅ İş #3", "özet", {"sekme": "akis"}, collapse="is:3")
            self.assertEqual({k: v[1] for k, v in giden.items()}, {"aa": None, "bb": None})
        finally:
            apns.cihaz_turleri, apns.gonder = eski_t, eski_g

    def test_telefon_govdesi(self):
        self.assertEqual(apns.telefon_govdesi("a" + EK), "a …")
        self.assertEqual(apns.telefon_govdesi("ek yok"), "ek yok")
        self.assertEqual(apns.telefon_govdesi(""), "")
        self.assertEqual(apns.telefon_govdesi(None), "")

    def test_kanal_ilk_paragrafi_gonderir(self):
        gorulen = []
        eski_h, eski_b = apns.herkese, channels.birincil
        apns.herkese = lambda baslik, govde, veri=None, collapse=None, onem=None, **kw: gorulen.append((baslik, govde, collapse))
        channels.birincil = lambda: "kokpit"
        try:
            uzun = "**Bitti.** Site açık.\n\n" + "ayrıntı " * 100
            channels.AppKanal().send(uzun)
            channels.AppKanal().send("Onaylıyor musun?\n\nAyrıntı", buttons=[[("Onayla", "ok:1")]])
        finally:
            apns.herkese, channels.birincil = eski_h, eski_b
        self.assertEqual(gorulen[0], ("Ghost", "Bitti. Site açık." + EK, "sohbet"))
        self.assertEqual(gorulen[1], ("Ghost onay bekliyor", "Onaylıyor musun?" + EK, "karar"))
        self.assertEqual(kokpit.outbox_oku(-1, 2)[0]["metin"], uzun)   # döküm HAM kalır; yalnız bildirim kısalır


if __name__ == "__main__":
    unittest.main()
