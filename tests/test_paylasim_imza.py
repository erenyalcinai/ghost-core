"""Kokpit paylaşımı ve Masa 'ilet' gelen kutusu kaydı İMZALI yazılır (2026-10-28, Codex sonrası bulgu):
imzasız kayıt köprüde özerk işe düşüyordu, sahibin belgeli sorusu cevapsız kalıyordu."""
import os
import unittest
from unittest import mock

from tests import common  # noqa: F401

from ghost import api, kokpit, masa, onay  # noqa: E402


def _imzala(jeton, metin):
    return ("imza-%s" % jeton, 1234) if jeton == "j-gecerli" else None


class PaylasimImzasi(unittest.TestCase):
    def setUp(self):
        kokpit.inbox_al()

    def test_belge_ve_metin_paylasimi_jetonla_imzalanir(self):
        cihaz = {"id": "cihazA", "inbox_jeton": "j-gecerli"}
        with mock.patch.object(onay, "inbox_imzala", _imzala):
            metin, hata = api._paylas_kaydet(cihaz, {}, b"%PDF-1.4 test", {"ad": ["fatura.pdf"], "not": ["oku"]})
            self.assertIsNone(hata)
            m2, _ = api._paylas_kaydet(cihaz, {"tur": "url", "url": "https://ornek.com/x"}, None, {})
        gelen = kokpit.inbox_al()
        self.assertEqual([g["metin"] for g in gelen], [metin, m2])
        for g in gelen:
            self.assertEqual((g["imza"], g["imza_ts"]), ("imza-j-gecerli", 1234))
        self.assertTrue(gelen[0]["metin"].startswith("[Paylaşım: belge] "))

    def test_jetonsuz_cihaz_imzasiz_kalir(self):
        """Jeton yoksa (yerel kip / eski servis) kayıt yine düşer ama imzasız — köprü özerk işe yollar, kaybolmaz."""
        with mock.patch.object(onay, "inbox_imzala", _imzala):
            api._paylas_kaydet({"id": "cihazA"}, {"tur": "metin", "metin": "selam"}, None, {})
        g = kokpit.inbox_al()[0]
        self.assertNotIn("imza", g)

    def test_masa_ilet_imzalar(self):
        k = masa.kart_ekle("not", "AppA", None, icerik="deneme notu", yazar="sahip")
        kid = k["id"] if isinstance(k, dict) else int(k)
        metin = masa.ilet(kid, "cihazA", imzala=lambda m: ("imza-ilet", 99))
        g = kokpit.inbox_al()[-1]
        self.assertEqual(g["metin"], metin)
        self.assertEqual((g["imza"], g["imza_ts"]), ("imza-ilet", 99))
        masa.ilet(kid, "cihazA")                          # imzala yoksa eski davranış: imzasız kayıt
        self.assertNotIn("imza", kokpit.inbox_al()[-1])


if __name__ == "__main__":
    unittest.main()
