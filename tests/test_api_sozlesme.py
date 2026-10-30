"""Kokpit API sözleşme testleri (Faz 3.12): ROTALAR'daki HER uç canlı sunucuda bir örnek istekle çağrılır; durum kodu ve
cevap gövdesinin üst düzey anahtarları (iOS/Mac uygulamasının okuduğu şekil) burada SABİTLENİR. Tabloya uç eklenip
sözleşme yazılmazsa test durur; şekil değişirse uygulama kırılmadan burada görünür."""
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from tests import common  # noqa: F401
from tests.test_kokpit import _istek

from ghost import api, kokpit, masa

# (yöntem, ROTALAR yolu) → sözleşme: ornek yol, gövde, kabul edilen durumlar, beklenen anahtarlar (durum→anahtar kümesi)
# "ham": JSON değil — yalnız durum + Content-Type öneki denetlenir.
SOZLESME = {
    ("GET", "/v1/ping"): dict(yol="/v1/ping", kimliksiz=True, durum={200}, anahtar={"ok", "at", "surum", "build", "surum_zaman"}),
    ("GET", "/v1/acik/nabiz"): dict(yol="/v1/acik/nabiz", kimliksiz=True, durum={200}, anahtar={"gun", "siteler_ok", "siteler_n", "durum"}),
    ("GET", "/v1/nabiz"): dict(yol="/v1/nabiz", durum={200},
                               anahtar={"at", "ozet", "freeze", "guard", "makineler", "siteler", "nobetciler", "isler", "baglam"}),
    ("GET", "/v1/ses/token"): dict(yol="/v1/ses/token", durum={200, 503}, anahtar_durum={200: {"token"}, 503: {"hata"}}),
    ("GET", "/v1/kararlar"): dict(yol="/v1/kararlar", durum={200}, anahtar={"kararlar"}),
    ("GET", "/v1/outbox"): dict(yol="/v1/outbox?since=0&limit=5", durum={200}, anahtar={"mesajlar", "son", "mesgul", "gts"}),
    ("GET", "/v1/medya/<id>"): dict(yol="/v1/medya/999999999", durum={404}, anahtar={"hata"}),
    ("GET", "/v1/duyular"): dict(yol="/v1/duyular", durum={200}, anahtar={"acik", "ozet", "bayat"}),
    ("GET", "/v1/gorevler"): dict(yol="/v1/gorevler", durum={200}, anahtar={"gorevler"}),
    ("GET", "/v1/bolgeler"): dict(yol="/v1/bolgeler", durum={200}, anahtar={"bolgeler", "seq", "tavan"}),
    ("GET", "/v1/komutlar"): dict(yol="/v1/komutlar", durum={200}, anahtar={"komutlar"}),
    ("GET", "/v1/siteler"): dict(yol="/v1/siteler", durum={200}, anahtar={"siteler"}),
    ("GET", "/v1/akis"): dict(yol="/v1/akis?limit=5", durum={200}, anahtar={"akis", "once"}),
    ("GET", "/v1/olaylar"): dict(yol="/v1/olaylar?since=0&limit=5", durum={200}, anahtar={"olaylar", "son"}),
    ("GET", "/v1/sse"): dict(yol="/v1/sse?olay=0&outbox=0&sahne=-1", ham="text/event-stream", durum={200}),
    ("GET", "/v1/masa"): dict(yol="/v1/masa", durum={200}, anahtar={"seq", "kartlar", "dolap_sayisi"}),
    ("GET", "/v1/masa/<id>[/dosya|/pdf]"): dict(yol="/v1/masa/999999999", durum={404}, anahtar={"hata"}),
    ("GET", "/v1/sahne"): dict(yol="/v1/sahne", durum={200}, anahtar={"bilesenler"}),
    ("GET", "/v1/sahne/gecmis"): dict(yol="/v1/sahne/gecmis", durum={200}, anahtar={"gecmis", "simdi"}),
    ("GET", "/v1/sahne/mac.jpg"): dict(yol="/v1/sahne/mac.jpg", durum={503}, anahtar={"hata"}),           # testte uzuv yok
    ("GET", "/v1/sahne/telefon.jpg"): dict(yol="/v1/sahne/telefon.jpg", durum={503}, anahtar={"hata"}),
    ("GET", "/v1/sahne/telefon.mjpeg"): dict(yol="/v1/sahne/telefon.mjpeg", durum={503}, anahtar={"hata"}),
    ("GET", "/v1/cihazlar"): dict(yol="/v1/cihazlar", durum={200}, anahtar={"cihazlar", "ben"}),
    ("POST", "/v1/eslesme"): dict(yol="/v1/eslesme", kimliksiz=True, govde={"kod": "000000"}, durum={403}, anahtar={"hata"}),
    ("POST", "/v1/ses/arac/<ad>"): dict(yol="/v1/ses/arac/durum", kimliksiz=True, govde={}, durum={401}, anahtar={"hata"}),
    ("POST", "/v1/mesaj"): dict(yol="/v1/mesaj", govde={"metin": "sözleşme testi"}, durum={200}, anahtar={"ok", "id"}),
    ("POST", "/v1/duyular"): dict(yol="/v1/duyular", govde={"pil": 50}, durum={200, 403},
                                  anahtar_durum={200: {"ok", "guncel"}, 403: {"hata", "acik"}}),
    ("POST", "/v1/gunluk"): dict(yol="/v1/gunluk", govde={"satirlar": ["kaydırma 1"]}, durum={200}, anahtar={"ok"}),
    ("POST", "/v1/apns"): dict(yol="/v1/apns", govde={"token": "ab" * 32, "env": "development"}, durum={200, 400}, anahtar={"ok"}),
    ("POST", "/v1/etkinlik"): dict(yol="/v1/etkinlik", govde={"tur": "start", "token": "ab" * 32}, durum={200, 400}, anahtar={"ok"}),
    ("POST", "/v1/eylem"): dict(yol="/v1/eylem", govde={"id": "sozlesme-1", "veri": "onayla"}, durum={200}, anahtar={"ok", "metin"}),
    ("POST", "/v1/paylas"): dict(yol="/v1/paylas", govde={"tur": "metin", "metin": "paylaşım sözleşme"}, durum={200}, anahtar={"ok", "metin"}),
    ("POST", "/v1/masa"): dict(yol="/v1/masa", govde={"tur": "not", "ad": "sözleşme notu", "icerik": "x"}, durum={200}, anahtar={"ok", "id", "seq"}),
    ("POST", "/v1/masa/<id>|dosya"): dict(yol="/v1/masa/999999999", govde={"eylem": "dolap"}, durum={400, 403, 404}, anahtar={"hata"}),
    ("POST", "/v1/gorevler/<id>"): dict(yol="/v1/gorevler/999999999", govde={"ok": True}, durum={200}, anahtar={"ok", "yinelenen"}),
    ("POST", "/v1/bolge"): dict(yol="/v1/bolge", govde={"ad": "yok-bolge", "olay": "giris"}, durum={200}, anahtar={"ok", "yutuldu"}),
    ("POST", "/v1/kararlar/<id>"): dict(yol="/v1/kararlar/ab12cd34", govde={"karar": "ret"}, durum={200}, anahtar={"ok"}),
}


class ApiSozlesme(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._sse = api.SSE_SURE
        api.SSE_SURE = 1
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), api.H)
        cls.srv.daemon_threads = True
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        cls.did, cls.key = kokpit.cihaz_ekle("sozlesme-test")

    @classmethod
    def tearDownClass(cls):
        api.SSE_SURE = cls._sse
        cls.srv.shutdown()

    def test_her_rota_icin_sozlesme_var(self):
        rotalar = {(r[0], r[1]) for r in api.ROTALAR}
        self.assertEqual(set(SOZLESME), rotalar,
                         "ROTALAR ile sözleşme tablosu ayrıştı: eksik=%s fazla=%s" % (rotalar - set(SOZLESME), set(SOZLESME) - rotalar))

    def _cagir(self, yontem, s):
        if s.get("ham"):
            h = {}
            ts = str(int(__import__("time").time()))
            nonce = os.urandom(8).hex()
            h.update({"X-Ghost-Device": self.did, "X-Ghost-Ts": ts, "X-Ghost-Nonce": nonce,
                      "X-Ghost-Sig": kokpit.imza(self.key, ts, nonce, yontem, s["yol"], b"")})   # imza sorguyu da kapsar
            req = urllib.request.Request(self.base + s["yol"], method=yontem, headers=h)
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status, r.headers.get("Content-Type", "")
            except urllib.error.HTTPError as e:
                return e.code, e.headers.get("Content-Type", "")
        imzali = not s.get("kimliksiz")
        return _istek(self.base, yontem, s["yol"], self.key if imzali else None, self.did if imzali else None,
                      s.get("govde"))

    def test_uclar_sozlesmeye_uyar(self):
        hatalar = []
        for (yontem, rota), s in SOZLESME.items():
            try:
                st, govde = self._cagir(yontem, s)
            except Exception as e:  # noqa: BLE001
                hatalar.append("%s %s: istek patladı %r" % (yontem, rota, e))
                continue
            if st not in s["durum"]:
                hatalar.append("%s %s: durum %s, beklenen %s (gövde %s)" % (yontem, rota, st, sorted(s["durum"]), str(govde)[:120]))
                continue
            if s.get("ham"):
                if not str(govde).startswith(s["ham"]):
                    hatalar.append("%s %s: Content-Type %r, beklenen %r" % (yontem, rota, govde, s["ham"]))
                continue
            beklenen = s.get("anahtar") or s.get("anahtar_durum", {}).get(st, set())
            if not isinstance(govde, dict):
                hatalar.append("%s %s: gövde dict değil: %r" % (yontem, rota, str(govde)[:80]))
                continue
            eksik = set(beklenen) - set(govde)
            if eksik:
                hatalar.append("%s %s: eksik anahtar %s (gelen %s)" % (yontem, rota, sorted(eksik), sorted(govde)[:12]))
        self.assertEqual(hatalar, [], "\n" + "\n".join(hatalar))

    def test_kimliksiz_korumali_uc_401_bilinmeyen_yol_404(self):
        st, j = _istek(self.base, "GET", "/v1/nabiz")
        self.assertEqual(st, 401)
        st, j = _istek(self.base, "GET", "/v1/olmayan-uc")
        self.assertEqual(st, 401, "bilinmeyen yol kimliksize uç adı sızdırmaz")
        st, j = _istek(self.base, "GET", "/v1/olmayan-uc", self.key, self.did)
        self.assertEqual((st, j.get("hata")), (404, "yok"))

    def test_masa_kart_okuma_ve_sil_sozlesmesi(self):
        st, j = _istek(self.base, "POST", "/v1/masa", self.key, self.did, {"tur": "not", "ad": "sözleşme kart", "icerik": "içerik"})
        self.assertEqual(st, 200)
        kid = j["id"]
        st, k = _istek(self.base, "GET", "/v1/masa/%d" % kid, self.key, self.did)
        self.assertEqual(st, 200)
        for a in ("id", "ad", "tur", "yol", "cocuklar", "icerik"):
            self.assertIn(a, k, a)
        self.assertEqual(k["icerik"], "içerik")
        st, d = _istek(self.base, "GET", "/v1/masa/%d/dosya" % kid, self.key, self.did)
        self.assertEqual(st, 404, "not kartının dosyası yok")
        st, j = _istek(self.base, "POST", "/v1/masa/%d" % kid, self.key, self.did, {"eylem": "sil"})
        self.assertEqual(st, 200)
        for a in ("ok", "id", "sonuc", "seq"):
            self.assertIn(a, j)
        k2 = masa.oku(kid)
        self.assertTrue(k2 is None or k2.get("durum") != "masa", "sil sonrası kart masada kalmamalı")

    def test_masa_dosya_ham_govde_sozlesmesi(self):
        import time
        ts = str(int(time.time()))
        nonce = os.urandom(8).hex()
        yol = "/v1/masa/dosya?ad=sozlesme.png"
        veri = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        h = {"Content-Type": "image/png", "X-Ghost-Device": self.did, "X-Ghost-Ts": ts, "X-Ghost-Nonce": nonce,
             "X-Ghost-Sig": kokpit.imza(self.key, ts, nonce, "POST", yol, veri)}
        req = urllib.request.Request(self.base + yol, data=veri, method="POST", headers=h)
        with urllib.request.urlopen(req, timeout=10) as r:
            j = json.loads(r.read().decode())
            self.assertEqual(r.status, 200)
        for a in ("ok", "id", "seq"):
            self.assertIn(a, j)
        st, d = _istek(self.base, "GET", "/v1/masa/%d" % j["id"], self.key, self.did)
        self.assertEqual(st, 200)
        self.assertEqual(d["tur"], "gorsel")


if __name__ == "__main__":
    unittest.main()
