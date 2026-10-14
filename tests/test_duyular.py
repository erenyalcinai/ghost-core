"""Duyular (Faz 5): konum/takvim/cihaz anlık görüntüsünün saklanması, bayatlık,
şalter, bağlam satırı ve /v1/duyular uçları."""
import json
import os
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from tests import common  # noqa: F401  (yolları geçici dizine alır, GHOST_TEST=1)

from ghost import api, duyular, health, kokpit  # noqa: E402


def _istek(base, method, path, key=None, did=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if key:
        ts = str(int(time.time()))
        nonce = os.urandom(8).hex()
        h.update({"X-Ghost-Device": did, "X-Ghost-Ts": ts, "X-Ghost-Nonce": nonce,
                  "X-Ghost-Sig": kokpit.imza(key, ts, nonce, method, path, data or b"")})
    req = urllib.request.Request(base + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def _ornek(gun=None):
    return {
        "konum": {"enlem": 41.0000, "boylam": 29.0000, "dogruluk": 35,
                  "yer": "Example City", "ulke": "Türkiye"},
        "takvim": {"gun": gun or time.strftime("%Y-%m-%d"), "etkinlikler": [
            {"ad": "Yalco görüşme", "bas": "00:05", "bit": "01:00", "yer": "Zoom"},
            {"ad": "Doğum günü", "tamgun": True}]},
        "cihaz": {"pil": 0.42, "sarj": False, "dusuk_guc": True, "ag": "wifi",
                  "model": "iPhone18,3", "ios": "26.0"},
    }


class Depo(unittest.TestCase):
    def setUp(self):
        y = os.path.join(os.environ["GHOST_OPS"], duyular.DOSYA)
        if os.path.exists(y):
            os.remove(y)

    def test_yaz_oku_ve_temizlik(self):
        self.assertEqual(sorted(duyular.yaz(_ornek(), "cihazA")), ["cihaz", "konum", "takvim"])
        d = duyular.oku()
        self.assertEqual(d["cihaz_id"], "cihazA")
        self.assertAlmostEqual(d["konum"]["enlem"], 41.0000, places=4)
        self.assertEqual(d["cihaz"]["model"], "iPhone18,3")
        self.assertTrue(duyular.taze("konum"))

    def test_kismi_gonderi_otekini_bozmaz(self):
        duyular.yaz(_ornek())
        self.assertEqual(duyular.yaz({"cihaz": {"pil": 0.9, "sarj": True}}), ["cihaz"])
        d = duyular.oku()
        self.assertEqual(d["cihaz"]["pil"], 0.9)
        self.assertEqual(d["konum"]["yer"], "Example City")   # dokunulmadı

    def test_bozuk_veri_reddedilir(self):
        self.assertEqual(duyular.yaz({"konum": {"enlem": 999, "boylam": 0}}), [])
        self.assertEqual(duyular.yaz({"konum": "iki nokta"}), [])
        self.assertEqual(duyular.yaz("hiç dict değil"), [])
        self.assertEqual(duyular.oku(), {})

    def test_etkinlik_tavani_ve_alan_suzgeci(self):
        et = [{"ad": "e%d" % i, "bas": "09:00", "notlar": "gizli", "katilimci": "x"} for i in range(30)]
        duyular.yaz({"takvim": {"gun": time.strftime("%Y-%m-%d"), "etkinlikler": et}})
        kayit = duyular.oku()["takvim"]["etkinlikler"]
        self.assertEqual(len(kayit), duyular.ETKINLIK_TAVAN)
        self.assertNotIn("notlar", kayit[0])       # yalnız beyaz listedeki alanlar
        self.assertNotIn("katilimci", kayit[0])

    def test_bayat_veri_baglama_girmez(self):
        duyular.yaz(_ornek())
        self.assertIn("Example City", duyular.satir())
        d = duyular.oku()
        for ad in duyular.DUYULAR:                  # hepsini bir gün geriye al
            d[ad]["ts"] = time.time() - 86400
        duyular._yaz_dosya(d)
        self.assertFalse(duyular.taze("konum"))
        self.assertEqual(duyular.satir(), "")       # sessizlik, eski konumla cevap değil

    def test_dunun_takvimi_bugun_sayilmaz(self):
        duyular.yaz(_ornek(gun="2020-01-01"))
        self.assertEqual(duyular.ozet()["etkinlik_sayisi"], 0)
        self.assertIsNone(duyular.siradaki())

    def test_salter_kapaliyken_satir_bos(self):
        duyular.yaz(_ornek())
        duyular.salter(False)
        self.assertFalse(duyular.acik())
        self.assertEqual(duyular.satir(), "")
        self.assertIn("KAPALI", duyular.metin())
        duyular.salter(True)
        self.assertNotEqual(duyular.satir(), "")

    def test_veri_yokken_sessiz(self):
        self.assertEqual(duyular.satir(), "")
        self.assertFalse(duyular.ozet()["var"])
        self.assertIn("Henüz duyu verisi yok", duyular.metin())

    def test_nabiz_duyulari_tasir(self):
        duyular.yaz(_ornek())
        n = health.nabiz()
        self.assertTrue(n["duyular"]["var"])
        self.assertEqual(n["duyular"]["yer"], "Example City")


class Uc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), api.H)
        cls.srv.daemon_threads = True
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        cls.did, cls.key = kokpit.cihaz_ekle("duyu-test")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        duyular.salter(True)

    def test_kimliksiz_401(self):
        st, _ = _istek(self.base, "POST", "/v1/duyular", body=_ornek())
        self.assertEqual(st, 401)

    def test_post_ve_get(self):
        st, j = _istek(self.base, "POST", "/v1/duyular", self.key, self.did, _ornek())
        self.assertEqual(st, 200)
        self.assertEqual(sorted(j["guncel"]), ["cihaz", "konum", "takvim"])
        st, j = _istek(self.base, "GET", "/v1/duyular", self.key, self.did)
        self.assertEqual((st, j["acik"]), (200, True))
        self.assertEqual(j["ozet"]["yer"], "Example City")

    def test_salter_kapaliyken_403(self):
        duyular.salter(False)
        st, j = _istek(self.base, "POST", "/v1/duyular", self.key, self.did, _ornek())
        self.assertEqual((st, j["acik"]), (403, False))
        duyular.salter(True)


class Komut(unittest.TestCase):
    """/nerede, /bugun, /duyular kayıt defterinde ve metin üretiyor mu."""

    class _Ctx:
        def __init__(self): self.cikti = []
        def send(self, chat, metin): self.cikti.append(metin)

    class _Mgr:
        chat_id = 1

    def _cagir(self, ad, arg=""):
        from ghost import komutlar
        h = komutlar.bul(ad)
        self.assertIsNotNone(h, "%s kayıtlı değil" % ad)
        ctx = self._Ctx()
        h(arg, self._Mgr(), ctx)
        return "\n".join(ctx.cikti)

    def test_komutlar(self):
        duyular.salter(True)
        duyular.yaz(_ornek())
        self.assertIn("Example City", self._cagir("/nerede"))
        self.assertIn("Yalco", self._cagir("/bugun"))
        self.assertIn("Yalco", self._cagir("/bugün"))      # takma ad
        self.assertIn("KAPALI", self._cagir("/duyular", "kapat"))
        self.assertFalse(duyular.acik())
        self.assertIn("AÇIK", self._cagir("/duyular", "ac"))
        self.assertTrue(duyular.acik())


if __name__ == "__main__":
    unittest.main()
