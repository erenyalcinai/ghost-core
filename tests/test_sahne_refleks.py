"""Sahne refleksleri (2026-09-04): tarihçe, metinden tablo/diff, sor, onay, isten."""
import os
import tempfile
import unittest

os.environ.setdefault("GHOST_TEST", "1")

from ghost import config, kokpit, sahne


class Temel(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.eski = config.OPS
        config.OPS = self.d
        self.outbox = []
        self._yaz = kokpit.outbox_yaz
        kokpit.outbox_yaz = lambda metin, kaynak="ghost", butonlar=None, medya=None: self.outbox.append((metin, kaynak, butonlar)) or 1

    def tearDown(self):
        config.OPS = self.eski
        kokpit.outbox_yaz = self._yaz


class Tarihce(Temel):
    def test_onceki_sahne_saklanir_ve_seq_ile_okunur(self):
        s1, _ = sahne.goster([{"tur": "metin", "metin": "bir"}], "Bir")
        s2, _ = sahne.goster([{"tur": "metin", "metin": "iki"}], "İki")
        self.assertEqual([g["seq"] for g in sahne.gecmis()], [s1])
        self.assertEqual(sahne.oku_seq(s1)["baslik"], "Bir")
        self.assertEqual(sahne.oku_seq(s2)["baslik"], "İki")
        self.assertIsNone(sahne.oku_seq(999))

    def test_bos_sahne_tarihceye_girmez_ve_tavan(self):
        sahne.temizle()
        for i in range(sahne.GECMIS_TAVAN + 3):
            sahne.goster([{"tur": "metin", "metin": str(i)}], str(i))
        g = sahne.gecmis()
        self.assertEqual(len(g), sahne.GECMIS_TAVAN)
        self.assertTrue(all(x["baslik"] != "" for x in g))


class Metinden(Temel):
    def test_tablo_ve_diff(self):
        m = "| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n\n```diff\n-x\n+y\n```"
        b = sahne.metinden(m, "R")
        self.assertEqual([x["tur"] for x in b], ["metin", "diff", "tablo"])
        self.assertEqual(b[2]["satirlar"], [["1", "2"], ["3", "4"]])
        self.assertEqual(b[1]["metin"], "-x\n+y")

    def test_duz_metin_sahne_olmaz(self):
        self.assertIsNone(sahne.metinden("sadece yazı\nve satır"))
        self.assertIsNone(sahne.isten("sadece yazı", "İş"))

    def test_isten_basar(self):
        seq = sahne.isten("| k | v |\n|--|--|\n| x | 1 |", "İş #7")
        self.assertTrue(seq)
        self.assertEqual(sahne.oku()["baslik"], "İş #7")


class SorOnay(Temel):
    def test_sor_yalniz_sohbet_butonlari(self):
        """2026-10-04: soru Sahne'ye çizilmez (çift kopya + bayat kart); yalnız sohbet butonları."""
        onceki = sahne.oku()["seq"]
        sahne.sor("Hangisi?", ["A", "B", "C"], baslik="Seçim")
        self.assertEqual(sahne.oku()["seq"], onceki)             # sahne dokunulmadı
        self.assertEqual(self.outbox[-1][0], "Hangisi?")
        self.assertEqual(self.outbox[-1][2], [[("A", "A"), ("B", "B")], [("C", "C")]])

    def test_goster_dokume_satir_yazmaz(self):
        n = len(self.outbox)
        sahne.goster([{"tur": "metin", "metin": "x"}], "Rapor")
        self.assertEqual(len(self.outbox), n)                    # "Sahne güncellendi" satırı yok

    def test_isten_tek_ipucu(self):
        n = len(self.outbox)
        sahne.isten("| k | v |\n|--|--|\n| x | 1 |", "İş #7")
        self.assertEqual(len(self.outbox), n + 1)
        self.assertIn("Sahne'de", self.outbox[-1][0])

    def test_sor_secenek_sart(self):
        with self.assertRaises(ValueError):
            sahne.sor("?", [])

    def test_onay_diff_ve_butonlar(self):
        sahne.onay("Deploy app-a.example.com", "--- a\n+++ b\n-eski\n+yeni", eylem_id="onay-1")
        d = sahne.oku()
        self.assertEqual(d["bilesenler"][1]["tur"], "diff")
        self.assertEqual(d["bilesenler"][0]["durum"], "uyari")
        self.assertIn("onay-1", self.outbox[-1][2][0][0][1])
        self.assertEqual(sahne.onay("X", "düz özet")and sahne.oku()["bilesenler"][1]["tur"], "metin")


class Cozumle(Temel):
    """Cevaplanan soru/onay sahnede 'bekliyor' kalmaz (2026-09-23)."""
    def test_onay_cevabi_butonlari_dusurur_ve_durumu_gunceller(self):
        sahne.onay("Deploy app-a.example.com", "--- a\n+++ b\n-eski\n+yeni", eylem_id="onay-7")
        n = len(self.outbox)
        self.assertTrue(sahne.cozumle_metinden("[Sahne eylem: onay-7] onayla"))
        d = sahne.oku()
        self.assertEqual([b["tur"] for b in d["bilesenler"]], ["kart", "diff"])   # satir/butonlar düştü
        self.assertEqual(d["bilesenler"][0]["durum"], "iyi")
        self.assertIn("onayla", d["bilesenler"][0]["metin"])
        self.assertEqual(len(self.outbox), n)          # sessiz: "Sahne güncellendi" satırı yok
        self.assertFalse(sahne.cozumle("onay-7", "onayla"))  # ikinci kez: sahnede buton yok, no-op

    def _eski_soru_sahnesi(self, eid="soru-3"):
        """2026-10-04 öncesi 'sor' sahnesi (hâlâ ekranda duran eski kart biçimi)."""
        sahne.goster([{"tur": "kart", "baslik": "Soru", "metin": "Hangisi?"},
                      {"tur": "satir", "cocuklar": [{"tur": "eylem", "id": eid, "etiket": "a", "veri": "a"},
                                                    {"tur": "eylem", "id": eid, "etiket": "b", "veri": "b"}]}], "Soru")

    def test_vazgec_ve_alakasiz_id(self):
        self._eski_soru_sahnesi("soru-3")
        self.assertFalse(sahne.cozumle("soru-99", "a"))     # başka soru: dokunma
        self.assertTrue(sahne.cozumle("soru-3", "vazgeç"))
        d = sahne.oku()
        self.assertEqual(d["bilesenler"][0]["durum"], "bilinmiyor")
        self.assertFalse(any(b["tur"] == "satir" for b in d["bilesenler"]))
        self.assertFalse(sahne.cozumle_metinden("sıradan mesaj"))


class CozumleSerbest(Temel):
    """sahibin düz metin cevabı bekleyen sahne kartını kapatır (2026-10-04)."""
    def test_soru_her_mesajla_kapanir(self):
        Cozumle._eski_soru_sahnesi(self, "soru-5")
        self.assertEqual(sahne.cozumle_serbest("Yayınla, ama başlığı kısalt"), "soru-5")
        d = sahne.oku()
        self.assertFalse(any(b["tur"] == "satir" for b in d["bilesenler"]))
        self.assertIn("Yayınla", d["bilesenler"][0]["metin"])
        self.assertIsNone(sahne.cozumle_serbest("ikinci mesaj"))   # kapanmış, no-op

    def test_onay_yalniz_acik_evet_hayir(self):
        sahne.onay("Deploy app-a.example.com", "--- a\n+++ b\n-eski\n+yeni", eylem_id="onay-9")
        self.assertIsNone(sahne.cozumle_serbest("bence önce testleri koşalım"))   # tartışma: dokunma
        self.assertTrue(any(b["tur"] == "satir" for b in sahne.oku()["bilesenler"]))
        self.assertEqual(sahne.cozumle_serbest("Evet, yap"), "onay-9")
        self.assertEqual(sahne.oku()["bilesenler"][0]["durum"], "iyi")

    def test_onay_hayir_ve_bos_sahne(self):
        sahne.onay("Sil", "özet", eylem_id="onay-10")
        self.assertEqual(sahne.cozumle_serbest("hayır, şimdi değil"), "onay-10")
        self.assertEqual(sahne.oku()["bilesenler"][0]["durum"], "bilinmiyor")
        sahne.temizle()
        self.assertIsNone(sahne.cozumle_serbest("evet"))
        self.assertIsNone(sahne.cozumle_serbest("[Sahne eylem: x] y"))


if __name__ == "__main__":
    unittest.main()
