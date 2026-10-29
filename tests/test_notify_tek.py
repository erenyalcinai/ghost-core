"""Telegram gönderimi tek yerde (Faz 2.9, 2026-10-29): ghost/notify.telegram_api / telegram / medya / sahip_id.
Ağ yok: GHOST_TEST kapısı GONDERILENLER'e yazar; parçalama ve multipart gövdesi taklit gönderici ile doğrulanır."""
import json
import os
import tempfile
import unittest
from unittest import mock

from tests import common  # noqa: F401

from ghost import notify  # noqa: E402

ENV = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_OWNER_ID": "42"}


class SahipId(unittest.TestCase):
    def test_env_once_sonra_owner_json(self):
        self.assertEqual(notify.sahip_id(ENV), 42)
        with open(os.path.join(os.environ["GHOST_OPS"], "owner.json"), "w") as f:
            json.dump({"id": 7}, f)
        self.assertEqual(notify.sahip_id({}), 7)
        os.remove(os.path.join(os.environ["GHOST_OPS"], "owner.json"))
        self.assertIsNone(notify.sahip_id({}))


class TelegramApi(unittest.TestCase):
    def test_test_kipinde_aga_cikmaz_kaydeder(self):
        notify.GONDERILENLER.clear()
        r = notify.telegram_api("sendMessage", {"chat_id": 42, "text": "x"}, ENV)
        self.assertTrue(r.get("ok"))
        self.assertEqual(notify.GONDERILENLER[-1], ("telegram:sendMessage", {"chat_id": 42, "text": "x"}))


class TelegramParcali(unittest.TestCase):
    def test_uzun_metin_4000lik_parcalar(self):
        gelen = []
        with mock.patch.object(notify, "_test_kipi", lambda: False), \
                mock.patch.object(notify, "telegram_api", lambda m, p, env, **k: gelen.append((m, p["text"]))), \
                mock.patch.object(notify, "ntfy", lambda *a, **k: False):
            self.assertTrue(notify.telegram("a" * 9000, ENV, dokum=False))
        self.assertEqual([len(t) for _, t in gelen], [4000, 4000, 1000])
        self.assertTrue(all(m == "sendMessage" for m, _ in gelen))

    def test_telegram_duserse_ntfy(self):
        def patla(*a, **k):
            raise OSError("ağ yok")
        with mock.patch.object(notify, "_test_kipi", lambda: False), \
                mock.patch.object(notify, "telegram_api", patla), \
                mock.patch.object(notify, "ntfy", lambda text, env: "ntfy:" + text):
            self.assertEqual(notify.telegram("selam", ENV, dokum=False), "ntfy:selam")

    def test_dokum_kapaliyken_kokpite_yazmaz(self):
        yazilan = []
        from ghost import kokpit
        with mock.patch.object(kokpit, "outbox_yaz", lambda *a, **k: yazilan.append(a)):
            notify.telegram("⚠️ ham", ENV, dokum=False)
            self.assertEqual(yazilan, [])
            notify.telegram("⚠️ dökümlü", ENV)
            self.assertEqual(len(yazilan), 1)


class Medya(unittest.TestCase):
    def test_multipart_govde_ve_kayit(self):
        d = tempfile.mkdtemp()
        yol = os.path.join(d, "r.png")
        with open(yol, "wb") as f:
            f.write(b"\x89PNG12345")
        notify.GONDERILENLER.clear()
        cevap = notify.medya("photo", yol, "açıklama", ENV, dokum=False)
        self.assertTrue(cevap.get("ok"))
        m, p = notify.GONDERILENLER[-1]
        self.assertEqual(m, "telegram:sendPhoto")
        self.assertEqual((p["chat_id"], p["caption"], p["dosya"]), (42, "açıklama", "r.png"))

    def test_gercek_yolda_govde_multipart(self):
        d = tempfile.mkdtemp()
        yol = os.path.join(d, "v.mp4")
        with open(yol, "wb") as f:
            f.write(b"VID")
        gorulen = {}

        def sahte_api(method, params, env, timeout=60, govde=None, basliklar=None):
            gorulen.update(method=method, govde=govde, basliklar=basliklar)
            return {"ok": True}
        with mock.patch.object(notify, "telegram_api", sahte_api):
            notify.medya("video", yol, "k", ENV, dokum=False)
        self.assertEqual(gorulen["method"], "sendVideo")
        self.assertIn(b'name="video"; filename="v.mp4"', gorulen["govde"])
        self.assertIn(b'name="supports_streaming"', gorulen["govde"])
        self.assertIn(b"VID", gorulen["govde"])
        self.assertIn("multipart/form-data; boundary=", gorulen["basliklar"]["Content-Type"])

    def test_bilinmeyen_tur_reddedilir(self):
        with self.assertRaises(ValueError):
            notify.medya("ses", "/yok", "", ENV)

    def test_sahipsiz_hata(self):
        with self.assertRaises(RuntimeError):
            notify.medya("photo", "/yok", "", {"TELEGRAM_BOT_TOKEN": "t"})


if __name__ == "__main__":
    unittest.main()
