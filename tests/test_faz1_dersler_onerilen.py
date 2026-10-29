"""Faz 1.4 dersler etiket/arama/Karar-Vade blokları + Faz 1.6 ÖNERİLEN EYLEM ayıklama (2026-10-29)."""
import unittest

from tests import common  # noqa: F401

from ghost import dersler, onerilen

MD = """# Dersler

## 2026-08-21 · 530b8a6 — Test kapısı: kod, testler geçmeden bare repoya gidemesin
Testin var olması kapı olduğu anlamına gelmiyor. Kapı, çalıştıran şeye bağlı olduğu yerde vardır.

## 2026-10-24 · 974d72a — VPS /tmp tmpfs: test kalıntısı RAM yiyordu #test #githook
Geçici dizin RAM'de ise temizlenmeyen test kalıntısı bellek sızıntısıdır; TMPDIR'ı diske al.

## 2026-10-29 · 3f8e1cd — İlk satır ölçümü: init olayı ilk çıktı değil #metrik
Akış ölçümünde ilk satır protokolün init olayıdır, görünür çıktı değil; assistant olayını say.
"""


class TestDersler(unittest.TestCase):
    def test_kayitlar_ve_etiket(self):
        k = dersler.kayitlar_oku(MD)
        self.assertEqual(len(k), 3)
        self.assertEqual(k[1]["etiket"], ["test", "githook"])
        self.assertEqual(k[1]["konu"], "VPS /tmp tmpfs: test kalıntısı RAM yiyordu")
        self.assertEqual(k[0]["etiket"], [])
        self.assertEqual(dersler.etiketler(["ghost/metrik.py", "tests/test_metrik.py", "kokpit/ios/A.swift", "ops/__init__.py", "README.md"]),
                         ["metrik", "test", "kokpit"])

    def test_girdi_etiketli_ve_guncelle(self):
        yeni, n = dersler.guncelle(MD, [("abcdef1234", "2026-10-30", "Konu", "Ders metni", ["kopru"]),
                                        ("530b8a6xxxx", "2026-08-21", "eski", "tekrar", [])])
        self.assertEqual(n, 1)
        self.assertIn("## 2026-10-30 · abcdef1 — Konu #kopru\nDers metni", yeni)

    def test_ara_ve_baglam_notu(self):
        notu = dersler.baglam_notu("metrik ilk satır ölçümü neden sıfır çıkıyor", metin=MD)
        self.assertIn("İlk satır ölçümü", notu)
        self.assertNotIn("Test kapısı", notu)
        self.assertEqual(dersler.baglam_notu("selam", metin=MD), "")
        self.assertEqual(dersler.baglam_notu("bugün hava güzel mi", metin=MD), "")
        self.assertIn("/tmp", dersler.baglam_notu("tmpfs", metin=MD))        # tek terim → 1 eşleşme yeter

    def test_karar_ve_vade_bloklari(self):
        govde = ("Açıklama satırı\n\nKarar[rafta]: AppA TestFlight — Sahip 'zamanı gelince' dedi\n"
                 "Karar: Kapı — push değil commit\nVade: GitHub PAT | 2026-11-09 | vekil durum ile doğrula\nVade: bozuk | yarın\n")
        self.assertEqual(dersler.karar_ayikla(govde), [("AppA TestFlight", "Sahip 'zamanı gelince' dedi", "rafta"),
                                                       ("Kapı", "push değil commit", "gecerli")])
        self.assertEqual(dersler.vade_ayikla(govde), [("GitHub PAT", "2026-11-09", "vekil durum ile doğrula")])
        self.assertEqual(dersler.ders_ayikla(govde), None)


class TestOnerilen(unittest.TestCase):
    def test_ayikla(self):
        m = ("Rapor…\n\nÖNERİLEN EYLEM: Zombi tünel portunu temizle\nkomut: python3 ops/tunnel_watch.py --temizle 8765\n"
             "neden: 3 gündür zombi.\n\nÖNERİLEN EYLEM: kötü\nkomut: rm -rf / \n\nÖNERİLEN EYLEM: uzun\nkomut: " + "x" * 400 + "\n")
        o = onerilen.ayikla(m)
        self.assertEqual(len(o), 1)
        self.assertEqual(o[0]["komut"], "python3 ops/tunnel_watch.py --temizle 8765")
        self.assertEqual(o[0]["neden"], "3 gündür zombi.")
        self.assertEqual(onerilen.ayikla("öneri yok"), [])

    def test_kart_ac_tavan(self):
        m = "".join("ÖNERİLEN EYLEM: e%d\nkomut: echo %d\n\n" % (i, i) for i in range(4))
        acilan = []
        ids = onerilen.kart_ac(m, "İş #7", acici=lambda b, k, a: (acilan.append((b, k, a)) or "onay-%016x" % len(acilan)))
        self.assertEqual(len(ids), 2)
        self.assertEqual(acilan[0][0], "İş #7 · e0")
        self.assertTrue(acilan[0][2])
        self.assertEqual(onerilen.kart_ac("yok", acici=lambda *a: "x"), [])


if __name__ == "__main__":
    unittest.main()
