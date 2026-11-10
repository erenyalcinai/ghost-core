"""MEMORY.md hedef bazlı birleşimi — sıkıştırılmış (satır başına birden çok hafıza) indeks eşitlemede şişmez (2026-11-10)."""
import unittest

from tests import common  # noqa: F401
from ops import mac_worker


class TestIndeksBirlestir(unittest.TestCase):
    def test_cok_hedefli_satir_karsi_taraftan_tek_satirlari_geri_cagirmaz(self):
        taban = ["## Kokpit",
                 "- [Kokpit](kokpit.md) · [Kokpit Mac](kokpit-mac.md) · [Kaydırma](kaydirma.md) — özet",
                 "- [Vekil](vekil.md) — anahtarlar"]
        oteki = ["## Kokpit",
                 "- [Kokpit](kokpit.md) — uzun eski satır",
                 "- [Kokpit Mac](kokpit-mac.md) — uzun eski satır",
                 "- [Kaydırma](kaydirma.md) — uzun eski satır",
                 "- [Vekil](vekil.md) — eski"]
        self.assertEqual(mac_worker.indeks_birlestir(taban, oteki), taban)

    def test_yalniz_karsi_tarafta_olan_hafiza_eklenir(self):
        taban = ["- [A](a.md) · [B](b.md) — özet"]
        oteki = ["- [B](b.md) — eski", "- [Yeni](yeni.md) — Mac'te öğrenildi", "düz metin [X](x.md)"]
        self.assertEqual(mac_worker.indeks_birlestir(taban, oteki),
                         ["- [A](a.md) · [B](b.md) — özet", "- [Yeni](yeni.md) — Mac'te öğrenildi"])

    def test_tabandaki_tam_tekrar_toparlanir_baslik_korunur(self):
        taban = ["## Başlık", "- [A](a.md) — bir", "- [A](a.md) — iki", "", "- [A](a.md) · [C](c.md) — c yeni"]
        self.assertEqual(mac_worker.indeks_birlestir(taban, []),
                         ["## Başlık", "- [A](a.md) — bir", "", "- [A](a.md) · [C](c.md) — c yeni"])


if __name__ == "__main__":
    unittest.main()
