"""Faz 1.10 yarım çıktı (akis.yarim_ozet) + Faz 1.12 onay sonucu (onay_sonuc.izle) + push kesinti düzeyi (2026-10-29)."""
import json
import os
import unittest

from tests import common  # noqa: F401

from ghost import akis, apns, config, onay_sonuc


class TestYarim(unittest.TestCase):
    def test_yarim_ozet_yalniz_bu_turun_metni(self):
        bas = akis.son_id()
        cb = akis.olay_kaydedici("sohbet")
        cb(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Önce vekili kontrol ediyorum."}]}}))
        cb(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}}))
        cb(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Vekil ayakta, şimdi testler."}]}}))
        metin, arac = akis.yarim_ozet(bas)
        self.assertEqual(metin, "Önce vekili kontrol ediyorum.\nVekil ayakta, şimdi testler.")
        self.assertEqual(arac, 1)
        self.assertEqual(akis.yarim_ozet(akis.son_id()), ("", 0))     # yeni tur → boş

    def test_tavan_sondan(self):
        bas = akis.son_id()
        cb = akis.olay_kaydedici("sohbet")
        for i in range(5):
            cb(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "x%d " % i + "a" * 500}]}}))
        metin, _ = akis.yarim_ozet(bas, tavan=600)
        self.assertTrue(metin.startswith("…"))
        self.assertLessEqual(len(metin), 601)
        self.assertIn("x4", metin)


class TestOnaySonuc(unittest.TestCase):
    def setUp(self):
        onay_sonuc._BEKLEYEN.clear()
        try:
            os.remove(onay_sonuc._yol())
        except OSError:
            pass

    def test_onayli_komut_sonucu(self):
        gidenler = []
        eid = "onay-0123456789abcdef"
        self.assertIsNone(onay_sonuc.izle(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "git apply x.patch ; : " + eid}}]}}), simdi=100.0))
        self.assertIsNone(onay_sonuc.izle(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "toolu_2", "name": "Bash", "input": {"command": "ls"}}]}}), simdi=100.5))
        self.assertIsNone(onay_sonuc.izle(json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_2", "content": "dosyalar"}]}}), simdi=101.0))   # onaysız → yok
        k = onay_sonuc.izle(json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": [{"type": "text", "text": "uygulandı\nsatır 2"}]}]}}),
            simdi=103.2, bildir=gidenler.append)
        self.assertEqual((k["eid"], k["sure_sn"], k["hata"], k["ozet"]), (eid, 3.2, False, "uygulandı satır 2"))
        self.assertEqual(len(k["hash"]), 12)
        self.assertEqual(len(gidenler), 1)                   # bildir kancası çağrılır; gerçek push yalnız hatada
        self.assertEqual(onay_sonuc.son()[0]["eid"], eid)
        self.assertEqual(onay_sonuc._BEKLEYEN, {})

    def test_hata_sonucu(self):
        onay_sonuc.izle(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "cp a b ; : onay-ffffffffffffffff"}}]}}), simdi=1.0)
        k = onay_sonuc.izle(json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t", "is_error": True, "content": "Permission denied"}]}}), simdi=2.0, bildir=lambda k: None)
        self.assertTrue(k["hata"])
        self.assertEqual(onay_sonuc.izle("bozuk satır"), None)


class TestPushOnem(unittest.TestCase):
    def test_onem_secimi_ve_yuk(self):
        self.assertEqual(apns.onem_sec("karar"), "active")
        self.assertEqual(apns.onem_sec("onay-0123456789abcdef"), "active")
        self.assertEqual(apns.onem_sec(None, {"sekme": "kararlar"}), "active")
        self.assertEqual(apns.onem_sec("sohbet", {"sekme": "sohbet"}), "passive")
        y = apns.yuk("Ghost", "mesaj", {"sekme": "sohbet"})
        self.assertEqual(y["aps"]["interruption-level"], "passive")
        self.assertNotIn("sound", y["aps"])                      # passive → ses yok
        y = apns.yuk("Onay", "kart", {"sekme": "kararlar"})
        self.assertEqual(y["aps"]["interruption-level"], "active")
        self.assertEqual(y["aps"]["sound"], "default")


if __name__ == "__main__":
    unittest.main()
