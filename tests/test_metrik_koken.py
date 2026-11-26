"""Tur ölçümünde köken cihaz (Kokpit Watch hazırlığı, 2026-11-24): sohbet turu satırı turu başlatan cihazın sınıfını
ve adını taşır; köken tur BAŞINDA yakalanır; yönlendirme yaması (yamalar/03, `channels.son_cihaz`) yokken de
bozulmaz (köken kanal düzeyinde kalır). Köprü değişmez: `ilk_cikti_sarmala` + `tur_kaydet` aynı çağrılarla."""
import json
import os
import threading
import time
import unittest
from unittest import mock

from tests import common  # noqa: F401  (yolları geçici dizine alır, GHOST_TEST=1)

from ghost import channels, config, kokpit, metrik  # noqa: E402

CIHAZLAR = [{"id": "tel000000001", "ad": "iPhone", "tur": "tam", "durum": "aktif"},
            {"id": "mac000000001", "ad": "Sahip MacBook Pro", "tur": "tam", "durum": "aktif"},
            {"id": "tam000000001", "ad": "Bilinmeyen", "tur": "tam", "durum": "aktif"},
            {"id": "saat00000001", "ad": "Saat", "tur": "cep", "durum": "aktif"},
            {"id": "kart00000001", "ad": "Cardputer", "tur": "cep", "durum": "aktif"},
            {"id": "cep000000001", "ad": "Yeni   cihaz adı çok çok uzun bir ad", "tur": "cep", "durum": "aktif"}]
SINIF = {"saat00000001": "saat", "kart00000001": "cardputer"}


def _satirlar():
    with open(os.path.join(config.OPS, "metrik.jsonl"), encoding="utf-8") as f:
        return [json.loads(s) for s in f if s.strip()]


class Temel(unittest.TestCase):
    def setUp(self):
        open(os.path.join(config.OPS, "metrik.jsonl"), "w").close()
        metrik._ILK_CIKTI.koken = None
        self._kanal, self._cihaz = "kokpit", None
        for hedef, ad, deger, kw in (
                (channels, "son_kanal", lambda: self._kanal, {}),
                (channels, "son_cihaz", lambda: self._cihaz, {"create": True}),      # yama 03'ün alanı
                (kokpit, "cihazlar", lambda: [dict(c) for c in CIHAZLAR], {}),
                (kokpit, "apns_cihazlar", lambda: [{"id": "tel000000001", "ad": "iPhone"}], {})):
            y = mock.patch.object(hedef, ad, deger, **kw)
            y.start()
            self.addCleanup(y.stop)
        from ghost import cihaz_sinif
        y = mock.patch.object(cihaz_sinif, "sinif", lambda cid: SINIF.get(cid))
        y.start()
        self.addCleanup(y.stop)

    def _koken(self, cihaz, kanal="kokpit"):
        self._kanal, self._cihaz = kanal, cihaz
        return metrik.koken_oku()


class Siniflama(Temel):
    def test_cihaz_siniflari(self):
        self.assertEqual(self._koken("tel000000001"), {"koken": "telefon", "koken_kanal": "kokpit",
                                                       "koken_id": "tel000000001", "koken_cihaz": "iPhone"})
        self.assertEqual(self._koken("mac000000001")["koken"], "mac")
        self.assertEqual(self._koken("tam000000001")["koken"], "tam")              # belirteçsiz, adı Mac demiyor
        self.assertEqual(self._koken("saat00000001")["koken"], "saat")
        self.assertEqual(self._koken("kart00000001")["koken"], "cardputer")
        k = self._koken("cep000000001")
        self.assertEqual((k["koken"], k["koken_cihaz"]), ("cep", "Yeni cihaz adı çok çok u"))   # sınıfı kayıtsız; ad ≤24, tek boşluk

    def test_cihaz_bilinmiyorsa_kanal_duzeyi(self):
        self.assertEqual(self._koken(None), {"koken": "kokpit", "koken_kanal": "kokpit"})      # otomatik tetik / yama 03 yok
        self.assertEqual(self._koken("silinmis0001"), {"koken": "kokpit", "koken_kanal": "kokpit", "koken_id": "silinmis0001"})
        self.assertEqual(self._koken("tel000000001", "telegram"), {"koken": "telegram", "koken_kanal": "telegram"})
        self.assertEqual(self._koken(None, None), {"koken": "bilinmiyor", "koken_kanal": ""})

    def test_yonlendirme_yamasi_yokken(self):
        with mock.patch.object(channels, "son_cihaz", None, create=True):          # alan yok: çağrılabilir değil
            self.assertEqual(metrik.koken_oku(), {"koken": "kokpit", "koken_kanal": "kokpit"})

    def test_hata_yutulur(self):
        self._cihaz = "tel000000001"
        with mock.patch.object(kokpit, "cihazlar", mock.Mock(side_effect=RuntimeError("servis yok"))):
            self.assertEqual(metrik.koken_oku(), {"koken": "bilinmiyor", "koken_kanal": ""})
        from ghost import cihaz_sinif
        with mock.patch.object(cihaz_sinif, "sinif", mock.Mock(side_effect=RuntimeError("db"))):
            self.assertEqual(self._koken("saat00000001")["koken"], "cep")
        for sinif in (self._koken(c["id"])["koken"] for c in CIHAZLAR):
            self.assertIn(sinif, metrik.KOKEN_SINIFLARI)


class TurSatiri(Temel):
    def test_koken_tur_basinda_yakalanir(self):
        t0 = time.time() - 60
        self._cihaz = "saat00000001"
        metrik.ilk_cikti_sarmala(None)                    # köprü tur başında çağırır
        self._cihaz = "mac000000001"                      # tur sürerken Mac'ten başka mesaj geldi
        metrik.tur_kaydet("kokpit", t0, t0 + 2, t0 + 30)
        r = _satirlar()[-1]
        self.assertEqual((r["koken"], r["koken_cihaz"], r["kanal"]), ("saat", "Saat", "kokpit"))
        metrik.tur_kaydet("kokpit", t0, t0 + 2, t0 + 30)  # yakalanmış köken tek turluk: bu tur şimdiki kökeni okur
        self.assertEqual(_satirlar()[-1]["koken"], "mac")

    def test_is_turuna_koken_yazilmaz_ve_cekirdek_alan_ezilmez(self):
        t0 = time.time() - 60
        self._cihaz = "tel000000001"
        metrik.ilk_cikti_sarmala(None)
        metrik.tur_kaydet("is", t0, None, t0 + 30, tur="is", ek={"departman": "muhendislik"})
        self.assertNotIn("koken", _satirlar()[-1])
        metrik.tur_kaydet("kokpit", t0, t0 + 1, t0 + 5, ek={"koken": "elle", "kanal": "x"})
        r = _satirlar()[-1]
        self.assertEqual((r["koken"], r["kanal"], r["koken_kanal"]), ("elle", "kokpit", "kokpit"))   # ek öncelikli, çekirdek sabit

    def test_is_parcacigina_ozel(self):
        self._cihaz = "saat00000001"
        metrik.ilk_cikti_sarmala(None)
        gorulen = []
        t = threading.Thread(target=lambda: gorulen.append(getattr(metrik._ILK_CIKTI, "koken", None)))
        t.start()
        t.join()
        self.assertEqual(gorulen, [None])                 # başka iş parçacığı bu turun kökenini görmez

    def test_ozet_ve_metin(self):
        t0 = time.time() - 600
        for cihaz, toplam in (("tel000000001", 40), ("tel000000001", 60), ("tel000000001", 200), ("saat00000001", 20)):
            self._cihaz = cihaz
            metrik.ilk_cikti_sarmala(None)
            metrik.tur_kaydet("kokpit", t0, t0 + 2, t0 + toplam)
        with open(os.path.join(config.OPS, "metrik.jsonl"), "a", encoding="utf-8") as f:       # alanı taşımayan eski satır
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "tur": "sohbet", "kanal": "kokpit", "toplam_sn": 9}) + "\n")
        o = metrik.ozet(7)
        self.assertEqual(o["tur"], 5)
        self.assertEqual(o["koken"]["telefon"], {"tur": 3, "toplam_p50": 60.0, "toplam_p95": 200.0})
        self.assertEqual(o["koken"]["saat"]["tur"], 1)
        self.assertEqual(sum(v["tur"] for v in o["koken"].values()), 4)            # eski satır kökene dökülmez
        self.assertIn("köken: telefon 3 (p50 60.0s), saat 1 (p50 20.0s)", metrik.metin(7))
        open(os.path.join(config.OPS, "metrik.jsonl"), "w").close()
        self.assertEqual(metrik.ozet(7)["koken"], {})
        self.assertNotIn("köken", metrik.metin(7))


if __name__ == "__main__":
    unittest.main()
