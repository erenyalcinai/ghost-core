"""Köprü ↔ sohbet sunucusu bağı (Faz 3.2): kesik mesajın tura BAĞLANMASI (yeniden koşma yok), tur kimliği
plumbing'i (_tur_hazirla), kesik_mesaj kaydının tur_id taşıması, köprü kapanışının uzak süreci öldürmemesi."""
import os
import sys
import unittest
from unittest import mock

from tests import common  # noqa: F401

from ghost import kesik_mesaj

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops"))
import telegram_bridge as tb  # noqa: E402


class _Kalici:
    uzak = True

    def __init__(self, devralinir=True):
        self.devralinir = devralinir
        self.bagla_id = self.tur_id = None
        self.soruldu = []

    def devralinabilir(self, tid):
        self.soruldu.append(tid)
        return self.devralinir


class _Mgr:
    def __init__(self, kalici):
        self.kalici = kalici
        self.chat_cagri = []

    def chat(self, text, spoken=False, deneme=0, tur_id=None):
        self.chat_cagri.append((text, spoken, deneme, tur_id))


def _kesik(tur_id="t-1", vazgec=False, neden=None, deneme=1):
    d = {"text": kesik_mesaj.ONEK + "merhaba dünya", "ham": "merhaba dünya", "spoken": False,
         "deneme": deneme, "yas": 30, "tur_id": tur_id, "vazgec": vazgec}
    if neden:
        d["neden"] = neden
    return d


class TestKesikDevral(unittest.TestCase):
    def setUp(self):
        self.gonderilen = []
        self._send = mock.patch.object(tb, "send", lambda cid, t, **k: self.gonderilen.append(t))
        self._send.start()

    def tearDown(self):
        self._send.stop()

    def test_sunucudaki_tura_baglanir_yeniden_kosmaz(self):
        k = _Kalici(devralinir=True)
        m = _Mgr(k)
        self.assertEqual(tb._kesik_devral(m, 111, _kesik()), "bagla")
        self.assertEqual(k.soruldu, ["t-1"])
        self.assertEqual(len(m.chat_cagri), 1)
        text, spoken, deneme, tid = m.chat_cagri[0]
        self.assertEqual(tid, "t-1")
        self.assertEqual(text, "merhaba dünya", "bağlanırken ÖNEKSİZ metin: istem yeniden gönderilmez")
        self.assertEqual(self.gonderilen, [], "bağlanma sahibe ayrı mesaj atmaz; cevap olağan yoldan gelir")

    def test_vazgec_kuralindan_once_baglanma_denenir(self):
        """Mesaj iki kez kesilmiş olsa da sunucuda tur sürüyorsa bağlanmak koşmak değildir."""
        m = _Mgr(_Kalici(devralinir=True))
        self.assertEqual(tb._kesik_devral(m, 111, _kesik(vazgec=True, neden="deneme", deneme=1)), "bagla")
        self.assertEqual(m.chat_cagri[0][3], "t-1")

    def test_sunucuda_tur_yoksa_eski_yol_tekrar(self):
        m = _Mgr(_Kalici(devralinir=False))
        self.assertEqual(tb._kesik_devral(m, 111, _kesik()), "tekrar")
        text, _s, deneme, tid = m.chat_cagri[0]
        self.assertTrue(text.startswith(kesik_mesaj.ONEK), "yeniden koşarken önek kalır")
        self.assertIsNone(tid)
        self.assertEqual(deneme, 1)
        self.assertTrue(any("yeniden alıyorum" in g for g in self.gonderilen))

    def test_yerel_surecte_eski_yol(self):
        """Uzak sunucu yoksa (kalici yerel KaliciOturum) devralma sorulmaz bile."""
        k = _Kalici(devralinir=True)
        k.uzak = False
        m = _Mgr(k)
        self.assertEqual(tb._kesik_devral(m, 111, _kesik()), "tekrar")
        self.assertEqual(k.soruldu, [])

    def test_vazgec(self):
        m = _Mgr(_Kalici(devralinir=False))
        self.assertEqual(tb._kesik_devral(m, 111, _kesik(vazgec=True, neden="bayat")), "vazgec")
        self.assertEqual(m.chat_cagri, [])
        self.assertTrue(any("2 saatten eski" in g for g in self.gonderilen))

    def test_devralma_sorgusu_patlarsa_eski_yol(self):
        k = _Kalici()
        k.devralinabilir = lambda tid: (_ for _ in ()).throw(OSError("soket yok"))
        m = _Mgr(k)
        self.assertEqual(tb._kesik_devral(m, 111, _kesik()), "tekrar")


class TestTurHazirla(unittest.TestCase):
    def test_uzak_yeni_tur_kimligi_sunucuya_gecer(self):
        k = _Kalici()
        sahte = mock.Mock(kalici=k)
        tid = tb.Manager._tur_hazirla(sahte, None)
        self.assertTrue(tid)
        self.assertEqual(k.tur_id, tid)
        self.assertIsNone(k.bagla_id)

    def test_uzak_baglanma(self):
        k = _Kalici()
        sahte = mock.Mock(kalici=k)
        tid = tb.Manager._tur_hazirla(sahte, "t-9")
        self.assertEqual(tid, "t-9")
        self.assertEqual(k.bagla_id, "t-9")
        self.assertIsNone(k.tur_id, "bağlanırken yeni istem gitmez")

    def test_yerel_surecte_yalniz_kimlik(self):
        k = _Kalici()
        k.uzak = False
        sahte = mock.Mock(kalici=k)
        tid = tb.Manager._tur_hazirla(sahte, None)
        self.assertTrue(tid)
        self.assertIsNone(k.tur_id)
        self.assertIsNone(k.bagla_id)

    def test_kalici_yokken(self):
        sahte = mock.Mock(kalici=None)
        self.assertTrue(tb.Manager._tur_hazirla(sahte, None))


class TestKesikKaydiTurId(unittest.TestCase):
    def test_kayit_tur_id_ve_ham_tasir(self):
        kesik_mesaj.basladi("selam", False, 0, tur_id="t-42")
        k = kesik_mesaj.devral()
        self.assertEqual(k["tur_id"], "t-42")
        self.assertEqual(k["ham"], "selam")
        self.assertTrue(k["text"].startswith(kesik_mesaj.ONEK))
        self.assertFalse(k["vazgec"])
        self.assertIsNone(kesik_mesaj.devral(), "devral kaydı siler")

    def test_eski_kayit_tur_idsiz(self):
        kesik_mesaj.basladi("eski", False, 0)
        k = kesik_mesaj.devral()
        self.assertIsNone(k["tur_id"])
        self.assertEqual(k["ham"], "eski")


if __name__ == "__main__":
    unittest.main()
