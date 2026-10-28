import json
import os
import unittest

from tests import common  # noqa: F401
from ghost import config, verify


EMIR = "ntfy yedek kanalını kur ve çalıştığını doğrula"
RAPOR = "NTFY_TOPIC .env'e yazıldı, kanal kuruldu ve test bildirimi gitti."


class TestKapsam(unittest.TestCase):
    """Tetikleyici ajanın kanaatine bırakılmamalı — kural burada sınanır."""

    def test_iddia_iceren_sonuc_denetlenir(self):
        self.assertTrue(verify.needs_review("bir şeye bak", RAPOR))

    def test_buyuk_harfli_turkce_iddia_kacmaz(self):
        # 'ÇALIŞIYOR'.lower() Python'da 'çalişiyor' üretir; ham re.I kaçırırdı.
        self.assertTrue(verify.needs_review(
            "kontrol et", "SERVİS KURULDU VE ÇALIŞIYOR, hepsi tamam demektir."))

    def test_durum_degistiren_emir_iddiasiz_da_denetlenir(self):
        r = "Şu dosyaya baktım, içinde üç ayar var ve hepsi normal görünüyor."
        self.assertNotEqual(verify.governance.classify("siteyi deploy et"),
                            verify.governance.READ)
        self.assertTrue(verify.needs_review("siteyi deploy et", r))

    def test_salt_okuma_isi_ve_iddiasiz_sonuc_denetlenmez(self):
        r = "Dosyada üç ayar görünüyor; hangisini istersin diye soruyorum."
        self.assertFalse(verify.needs_review("dosyayı oku ve anlat", r))

    def test_hata_iptal_kisa_ve_muaf_denetlenmez(self):
        self.assertFalse(verify.needs_review(EMIR, RAPOR, err=True))
        self.assertFalse(verify.needs_review(EMIR, "kuruldu"))       # çok kısa
        self.assertFalse(verify.needs_review(EMIR, RAPOR, kind="istihbarat"))

    def test_acil_kacis_kapatir(self):
        os.environ["GHOST_VERIFY"] = "0"
        try:
            self.assertFalse(verify.needs_review(EMIR, RAPOR))
        finally:
            os.environ.pop("GHOST_VERIFY", None)
        bayrak = os.path.join(config.OPS, "verify_disabled")
        open(bayrak, "w").close()
        try:
            self.assertFalse(verify.needs_review(EMIR, RAPOR))
        finally:
            os.remove(bayrak)


class TestKorluk(unittest.TestCase):
    """Denetçi ajanın gerekçesini görmemeli — körlük tasarımın kendisi."""

    def test_istem_emri_ve_raporu_icerir(self):
        p = verify.build_prompt(EMIR, RAPOR)
        self.assertIn(EMIR, p)
        self.assertIn(RAPOR, p)

    def test_istem_curutmeye_ayarli(self):
        p = verify.build_prompt(EMIR, RAPOR)
        self.assertIn("bitmemiştir", p)
        self.assertIn("çürütmeye", p)
        self.assertIn("gerekçesi sana bilerek verilmedi", p)

    def test_istem_denetciyi_salt_okumaya_baglar(self):
        p = verify.build_prompt(EMIR, RAPOR)
        for yasak in ("değiştirme", "deploy", "push"):
            self.assertIn(yasak, p)


class TestAyristirma(unittest.TestCase):
    def test_uc_alan_okunur(self):
        k, g, c = verify.parse(
            "KARAR: eksik\nGEREKÇE: .env'de NTFY_TOPIC yok.\n"
            "KONTROL: grep NTFY_TOPIC .env → boş")
        self.assertEqual(k, verify.EKSIK)
        self.assertIn("NTFY_TOPIC yok", g)
        self.assertIn("grep", c)

    def test_cok_satirli_gerekce_birlesir(self):
        k, g, _ = verify.parse("KARAR: yanlış\nGEREKÇE: birinci satır\n"
                               "ikinci satır.\nKONTROL: dosya")
        self.assertEqual(k, verify.YANLIS)
        self.assertEqual(g, "birinci satır ikinci satır.")

    def test_karar_satiri_tam_sozcuk_ister(self):
        """Codex S7 (2026-10-28): 'geçti değil, yanlış' alt dizeyle GEÇTİ sayılıyordu. İlk sözcük karar olmalı,
        satırda ikinci karar sözcüğü ya da olumsuzlama olmamalı; noktalama/kalın yazı/aksansız yazım serbest."""
        self.assertEqual(verify.parse("KARAR: geçti değil, yanlış\nGEREKÇE: x")[0], verify.BELIRSIZ)
        self.assertEqual(verify.parse("KARAR: eksik, geçti sayılmaz")[0], verify.BELIRSIZ)
        self.assertEqual(verify.parse("KARAR: yanlış değil")[0], verify.BELIRSIZ)
        self.assertEqual(verify.parse("KARAR: tamam")[0], verify.BELIRSIZ)
        self.assertEqual(verify.parse("KARAR: sonuç geçti")[0], verify.BELIRSIZ)      # ilk sözcük karar değil
        self.assertEqual(verify.parse("KARAR: **geçti**.")[0], verify.GECTI)
        self.assertEqual(verify.parse("KARAR: geçti — kontrol koştu")[0], verify.GECTI)
        self.assertEqual(verify.parse("KARAR: GEÇTİ")[0], verify.GECTI)
        self.assertEqual(verify.parse("KARAR: gecti")[0], verify.GECTI)
        self.assertEqual(verify.parse("KARAR: yanlis")[0], verify.YANLIS)
        self.assertEqual(verify.parse("KARAR: doğrulanamadı (kontrol koşmadı)")[0], verify.BELIRSIZ)

    def test_bicimsiz_cikti_gectiye_dusmez(self):
        # En tehlikeli sessiz hata: denetçi saçmaladı diye iş onaylanmış saymak.
        self.assertEqual(verify.parse("bilmiyorum ki")[0], verify.BELIRSIZ)
        self.assertEqual(verify.parse("")[0], verify.BELIRSIZ)

    def test_veto_yalniz_eksik_ve_yanlista(self):
        self.assertTrue(verify.blocks(verify.EKSIK))
        self.assertTrue(verify.blocks(verify.YANLIS))
        self.assertFalse(verify.blocks(verify.GECTI))
        self.assertFalse(verify.blocks(verify.BELIRSIZ))


class TestReview(unittest.TestCase):
    def _runner(self, cikti, hata=False):
        kayit = {}

        def r(prompt, model, use_mcp=None, timeout=None, ozerk=False):
            kayit["prompt"], kayit["model"] = prompt, model
            kayit["use_mcp"], kayit["ozerk"] = use_mcp, ozerk
            return (cikti, "sid", hata)
        return r, kayit

    def test_karar_gunluge_duser(self):
        r, _ = self._runner("KARAR: geçti\nGEREKÇE: doğrulandı.\nKONTROL: curl")
        karar, not_, rec = verify.review(EMIR, RAPOR, r, kind="iş", jid=7)
        self.assertEqual(karar, verify.GECTI)
        self.assertIn("geçti", not_)
        with open(config.VERIFY_LOG, encoding="utf-8") as f:
            satir = json.loads(f.read().strip().split("\n")[-1])
        self.assertEqual(satir["jid"], 7)
        self.assertEqual(satir["karar"], verify.GECTI)
        self.assertEqual(rec["kind"], "iş")

    def test_denetci_coktugunde_is_dusmez_ama_belirsiz_kalir(self):
        def patlayan(*a, **k):
            raise RuntimeError("model yok")
        karar, not_, _ = verify.review(EMIR, RAPOR, patlayan, jid=8)
        self.assertEqual(karar, verify.BELIRSIZ)
        self.assertIn("DOĞRULANAMADI", not_)

    def test_denetci_mcp_siz_kosar(self):
        r, kayit = self._runner("KARAR: geçti\nGEREKÇE: ok\nKONTROL: -")
        verify.review(EMIR, RAPOR, r, jid=9)
        self.assertEqual(kayit["use_mcp"], [])
        self.assertFalse(kayit["ozerk"])
        verify.review(EMIR, RAPOR, r, jid=9, ozerk=True)   # özerk iş → denetçi de özerk (2. tur bulgu 1)
        self.assertTrue(kayit["ozerk"])


if __name__ == "__main__":
    unittest.main()
