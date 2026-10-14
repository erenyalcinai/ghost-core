"""Kokpit rayları: outbox/inbox/kararlar, cihaz eşleme + HMAC, sağlık JSON, API uçları."""
import hashlib
import json
import os
import threading
import time
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

from tests import common  # noqa: F401  (yolları geçici dizine alır, GHOST_TEST=1)

from ghost import api, config, health, kokpit  # noqa: E402


def _istek(base, method, path, key=None, did=None, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if key:
        ts = str(int(time.time()))
        nonce = os.urandom(8).hex()
        h.update({"X-Ghost-Device": did, "X-Ghost-Ts": ts, "X-Ghost-Nonce": nonce,
                  "X-Ghost-Sig": kokpit.imza(key, ts, nonce, method, path, data or b"")})
    h.update(headers or {})
    req = urllib.request.Request(base + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


class OutboxKuyruk(unittest.TestCase):
    def test_outbox_yaz_oku_butonlar(self):
        i1 = kokpit.outbox_yaz("merhaba", "ghost")
        i2 = kokpit.outbox_yaz("onay?", "ghost", [[("✅ Onayla", "ok:ab12"), ("❌", "no:ab12")]])
        rows = kokpit.outbox_oku(since=i1 - 1)
        self.assertEqual([r["id"] for r in rows], [i1, i2])
        self.assertIsNone(rows[0]["butonlar"])
        self.assertEqual(rows[1]["butonlar"][0][0], {"etiket": "✅ Onayla", "veri": "ok:ab12"})
        self.assertEqual(kokpit.outbox_oku(since=i2), [])
        self.assertGreaterEqual(kokpit.outbox_son(), i2)

    def test_outbox_bekle_zaman_asimi(self):
        son = kokpit.outbox_son()
        t = time.time()
        self.assertEqual(kokpit.outbox_bekle(since=son, bekle=0.6), [])
        self.assertGreaterEqual(time.time() - t, 0.5)

    def test_inbox_ve_karar_kuyrugu(self):
        kokpit.mesaj_kuyrukla("/durum", "cihazX")
        kokpit.karar_kuyrukla("ab12cd34", True, "cihazX")
        m = kokpit.inbox_al()
        k = kokpit.kararlar_al()
        self.assertEqual(m[0]["metin"], "/durum")
        self.assertEqual((k[0]["pid"], k[0]["onay"]), ("ab12cd34", True))
        self.assertEqual(kokpit.inbox_al(), [])  # tüketildi, ikinci okuma boş
        self.assertEqual(os.listdir(os.path.join(config.OPS, "kokpit_inbox")), [])

    def test_bekleyen_kararlar_pending_json(self):
        with open(os.path.join(config.OPS, "pending.json"), "w") as f:
            json.dump({"p1": {"text": "sunucudaki dosyayı sil", "kind": "webhook"}}, f)
        k = kokpit.bekleyen_kararlar()
        self.assertEqual(k[0]["id"], "p1")
        self.assertIn(k[0]["sinif"], ("permanent", "spend", "reversible", "read"))


class CihazKimlik(unittest.TestCase):
    def test_eslesme_kod_akisi(self):
        kod = kokpit.kod_uret("iPhone")
        self.assertIsNone(kokpit.esle("000000" if kod != "000000" else "111111"))
        r = kokpit.esle(kod, "iPhone 17")
        self.assertIsNotNone(r)
        did, key = r
        self.assertEqual(len(key), 64)
        self.assertIsNone(kokpit.esle(kod))  # tek kullanım
        self.assertEqual([c["durum"] for c in kokpit.cihazlar() if c["id"] == did], ["aktif"])

    def test_kod_suresi(self):
        kod = kokpit.kod_uret()
        eski = kokpit.KOD_OMRU_SN
        try:
            import sqlite3
            c = sqlite3.connect(config.DB_PATH)
            c.execute("UPDATE kokpit_devices SET kod_son=? WHERE kod=?", (time.time() - 1, kod))
            c.commit(); c.close()
            self.assertIsNone(kokpit.esle(kod))
        finally:
            kokpit.KOD_OMRU_SN = eski

    def test_imza_dogrula_ve_tekrar(self):
        did, key = kokpit.cihaz_ekle("test")
        ts, nonce = str(int(time.time())), "abcdef0123456789"
        body = b'{"metin":"x"}'
        sig = kokpit.imza(key, ts, nonce, "POST", "/v1/mesaj", body)
        h = {"X-Ghost-Device": did, "X-Ghost-Ts": ts, "X-Ghost-Nonce": nonce, "X-Ghost-Sig": sig}
        self.assertEqual(kokpit.dogrula(h, "POST", "/v1/mesaj", body)["id"], did)
        self.assertIsNone(kokpit.dogrula(h, "POST", "/v1/mesaj", body), "nonce tekrarı geçti")
        h2 = dict(h, **{"X-Ghost-Nonce": "ffff0000ffff0000",
                         "X-Ghost-Sig": kokpit.imza(key, ts, "ffff0000ffff0000", "POST", "/v1/mesaj", b"{}")})
        self.assertIsNone(kokpit.dogrula(h2, "POST", "/v1/mesaj", body), "gövde değişti ama geçti")
        eski = str(int(time.time()) - 1000)
        h3 = {"X-Ghost-Device": did, "X-Ghost-Ts": eski, "X-Ghost-Nonce": "1234567890abcdef",
              "X-Ghost-Sig": kokpit.imza(key, eski, "1234567890abcdef", "POST", "/v1/mesaj", body)}
        self.assertIsNone(kokpit.dogrula(h3, "POST", "/v1/mesaj", body), "bayat ts geçti")
        kokpit.iptal(did)
        h4 = dict(h, **{"X-Ghost-Nonce": "0000000000000001",
                         "X-Ghost-Sig": kokpit.imza(key, ts, "0000000000000001", "POST", "/v1/mesaj", body)})
        self.assertIsNone(kokpit.dogrula(h4, "POST", "/v1/mesaj", body), "iptal cihaz geçti")


class KimlikNedeni(unittest.TestCase):
    """2026-09-07: 401 yalnız gerçek kimlik hatası — GET tekrarı kabul, POST tekrarı 'tekrar',
    saat penceresi 'saat'. (iOS aynı isteği ikinci kez yollayınca uygulama eşleme ekranına düşüyordu.)"""

    def test_get_tekrari_kabul_post_tekrari_ayrisir(self):
        did, key = kokpit.cihaz_ekle("tekrar-test")
        ts, nonce = str(int(time.time())), "aa" * 8
        h = {"X-Ghost-Device": did, "X-Ghost-Ts": ts, "X-Ghost-Nonce": nonce,
             "X-Ghost-Sig": kokpit.imza(key, ts, nonce, "GET", "/v1/kararlar", b"")}
        c1, n1 = kokpit.dogrula_neden(h, "GET", "/v1/kararlar", b"")
        c2, n2 = kokpit.dogrula_neden(h, "GET", "/v1/kararlar", b"")
        self.assertEqual((c1["id"], n1, c2["id"], n2), (did, "ok", did, "tekrar-get"))
        hp = dict(h, **{"X-Ghost-Nonce": "bb" * 8,
                        "X-Ghost-Sig": kokpit.imza(key, ts, "bb" * 8, "POST", "/v1/mesaj", b"{}")})
        self.assertEqual(kokpit.dogrula_neden(hp, "POST", "/v1/mesaj", b"{}")[1], "ok")
        self.assertEqual(kokpit.dogrula_neden(hp, "POST", "/v1/mesaj", b"{}"), (None, "tekrar"))
        self.assertIsNone(kokpit.dogrula(hp, "POST", "/v1/mesaj", b"{}"))   # eski sarmalayıcı aynı

    def test_saat_ve_imza_ayrisir(self):
        did, key = kokpit.cihaz_ekle("saat-test")
        eski = str(int(time.time()) - 1000)
        he = {"X-Ghost-Device": did, "X-Ghost-Ts": eski, "X-Ghost-Nonce": "cc" * 8,
              "X-Ghost-Sig": kokpit.imza(key, eski, "cc" * 8, "GET", "/v1/kararlar", b"")}
        self.assertEqual(kokpit.dogrula_neden(he, "GET", "/v1/kararlar", b""), (None, "saat"))
        # imza tutmuyorsa saat bilgisi sızmaz: önce imza
        hy = dict(he, **{"X-Ghost-Sig": "0" * 64})
        self.assertEqual(kokpit.dogrula_neden(hy, "GET", "/v1/kararlar", b""), (None, "imza"))
        self.assertEqual(kokpit.dogrula_neden({}, "GET", "/v1/kararlar", b""), (None, "baslik"))
        hc = dict(he, **{"X-Ghost-Device": "yok000000000"})
        self.assertEqual(kokpit.dogrula_neden(hc, "GET", "/v1/kararlar", b""), (None, "cihaz"))


class Saglik(unittest.TestCase):
    def test_nabiz_sekli(self):
        os.makedirs(os.path.join(config.OPS, "heartbeats"), exist_ok=True)
        with open(os.path.join(config.OPS, "heartbeats", "uptime.json"), "w") as f:
            json.dump({"ts": time.time() - 60, "at": "x"}, f)
        with open(os.path.join(config.OPS, "uptime_state.json"), "w") as f:
            json.dump({"https://app-a.example.com": True, "https://app-b.example.com": False}, f)
        n = health.nabiz()
        for k in ("at", "ozet", "freeze", "guard", "makineler", "siteler", "nobetciler", "isler", "baglam"):
            self.assertIn(k, n)
        self.assertEqual(n["ozet"], "kritik")  # bir site düşük
        self.assertTrue(any(s["ad"] == "app-b.example.com" and not s["ok"] for s in n["siteler"]))
        self.assertTrue(any(x["ad"] == "uptime" and x["ok"] for x in n["nobetciler"]))
        os.remove(os.path.join(config.OPS, "uptime_state.json"))


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), api.H)
        cls.srv.daemon_threads = True
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        cls.did, cls.key = kokpit.cihaz_ekle("api-test")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_ping_kimliksiz(self):
        st, j = _istek(self.base, "GET", "/v1/ping")
        self.assertEqual((st, j["ok"]), (200, True))

    def test_kimliksiz_401(self):
        st, _ = _istek(self.base, "GET", "/v1/nabiz")
        self.assertEqual(st, 401)
        st, _ = _istek(self.base, "GET", "/v1/nabiz", key="00" * 32, did=self.did)
        self.assertEqual(st, 401)

    def test_tekrar_ve_saat_401_degil(self):
        """2026-09-07: aynı isteğin ikinci gönderimi GET'te 200, POST'ta 409; saat 400 — 401 değil
        (uygulama 401'de eşleme ekranına düşer)."""
        def imzali(method, path, ts, nonce, body=None):
            data = json.dumps(body).encode() if body is not None else None
            h = {"Content-Type": "application/json", "X-Ghost-Device": self.did, "X-Ghost-Ts": ts,
                 "X-Ghost-Nonce": nonce,
                 "X-Ghost-Sig": kokpit.imza(self.key, ts, nonce, method, path, data or b"")}
            req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status
            except urllib.error.HTTPError as e:
                return e.code
        ts = str(int(time.time()))
        self.assertEqual(imzali("GET", "/v1/kararlar", ts, "11" * 8), 200)
        self.assertEqual(imzali("GET", "/v1/kararlar", ts, "11" * 8), 200)      # GET tekrarı: zararsız
        self.assertEqual(imzali("POST", "/v1/mesaj", ts, "22" * 8, {"metin": "a"}), 200)
        self.assertEqual(imzali("POST", "/v1/mesaj", ts, "22" * 8, {"metin": "a"}), 409)  # yazma tekrarı
        eski = str(int(time.time()) - 1000)
        self.assertEqual(imzali("GET", "/v1/kararlar", eski, "33" * 8), 400)   # saat: kimlik hatası değil
        kokpit.inbox_al()

    def test_imzali_uclar(self):
        st, j = _istek(self.base, "GET", "/v1/nabiz", self.key, self.did)
        self.assertEqual(st, 200); self.assertIn("ozet", j)
        st, j = _istek(self.base, "GET", "/v1/komutlar", self.key, self.did)
        self.assertEqual(st, 200)
        self.assertTrue(any(k["komut"] == "/durum" for k in j["komutlar"]))
        st, j = _istek(self.base, "GET", "/v1/siteler", self.key, self.did)
        self.assertEqual(st, 200); self.assertIn("siteler", j)
        st, j = _istek(self.base, "GET", "/v1/kararlar", self.key, self.did)
        self.assertEqual(st, 200); self.assertIn("kararlar", j)

    def test_mesaj_ve_karar_kuyruga_duser(self):
        kokpit.inbox_al(); kokpit.kararlar_al()  # temizle
        st, j = _istek(self.base, "POST", "/v1/mesaj", self.key, self.did, {"metin": "selam"})
        self.assertEqual((st, j["ok"]), (200, True))
        st, j = _istek(self.base, "GET", "/v1/outbox?since=%d" % (j["id"] - 1), self.key, self.did)
        self.assertEqual(j["mesajlar"][0]["kaynak"], "sahip")
        self.assertEqual(j["mesajlar"][0]["metin"], "selam")
        self.assertEqual([m["metin"] for m in kokpit.inbox_al()], ["selam"])
        st, j = _istek(self.base, "POST", "/v1/kararlar/ab12cd34", self.key, self.did, {"karar": "ret"})
        self.assertEqual(st, 200)
        self.assertEqual([(k["pid"], k["onay"]) for k in kokpit.kararlar_al()], [("ab12cd34", False)])
        st, _ = _istek(self.base, "POST", "/v1/kararlar/ab12cd34", self.key, self.did, {"karar": "belki"})
        self.assertEqual(st, 400)
        st, _ = _istek(self.base, "POST", "/v1/mesaj", self.key, self.did, {"metin": ""})
        self.assertEqual(st, 400)

    def test_eslesme_ucu(self):
        kod = kokpit.kod_uret("tel")
        st, _ = _istek(self.base, "POST", "/v1/eslesme", body={"kod": "999999" if kod != "999999" else "000000"})
        self.assertEqual(st, 403)
        st, j = _istek(self.base, "POST", "/v1/eslesme", body={"kod": kod, "ad": "iPhone 17"})
        self.assertEqual(st, 200); self.assertEqual(len(j["key"]), 64)
        st, _ = _istek(self.base, "GET", "/v1/nabiz", j["key"], j["device_id"])
        self.assertEqual(st, 200)


if __name__ == "__main__":
    unittest.main()
