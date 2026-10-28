"""Onay servisi (Faz 1 madde 10, 2026-10-28): onay kaydı, Kokpit cihaz anahtarları ve karar onayı
ghost-onay kullanıcısında. Bu testler servis çekirdeğini (ops/onay/onay_servisi.py), gerçek Unix
soketi üzerinden istemciyi (ghost/onay.py) ve ghost tarafının servis kipine geçişini sınar."""
import hashlib
import importlib.util
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

import tests.common  # noqa: F401  (GHOST_OPS/GHOST_TEST)
from ghost import kokpit, onay, sahne

_YOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops", "onay", "onay_servisi.py")
_spec = importlib.util.spec_from_file_location("onay_servisi", _YOL)
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)

BEN = (4242, os.getuid(), os.getgid())     # sıradan ghost süreci
KOPRU_PID = 777
KOPRU = (KOPRU_PID, os.getuid(), os.getgid())
SAHIP = 5550001


def _imzali(key_hex, method, path, body=b"", ts=None, nonce=None):
    ts = str(ts if ts is not None else time.time())
    nonce = nonce or os.urandom(8).hex()
    sig = kokpit.imza(key_hex, ts, nonce, method, path, body)
    return {"device": "", "ts": ts, "nonce": nonce, "sig": sig}


class Cekirdek(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        with open(os.path.join(self.d, "ayar.json"), "w") as f:
            json.dump({"sahip_id": SAHIP, "telegram_token": "x", "izinli_uid": [os.getuid()]}, f)
        self.giden = []
        self.o = srv.Onay(self.d, kopru_pid=lambda: KOPRU_PID, telegram=lambda m: self.giden.append(m) or True)

    def isle(self, peer=BEN, **kw):
        return self.o.isle(kw, peer)

    def _cihaz(self):
        self.assertTrue(self.isle(peer=KOPRU, op="kod_uret", ad="tel")["ok"])
        kod = [w for w in self.giden[-1].split() if w.isdigit() and len(w) == 6][0]
        c = self.isle(op="esle", kod=kod, ad="tel")["cihaz"]
        self.assertTrue(c and len(c["key"]) == 64)
        return c["id"], c["key"]

    def _kokpit(self, did, key, method, path, body=b"", **kw):
        h = _imzali(key, method, path, body, **kw)
        h["device"] = did
        import base64
        return self.isle(op="kokpit", h=h, method=method, path=path, body_b64=base64.b64encode(body).decode())

    # -- yetki
    def test_yabanci_uid_reddedilir(self):
        r = self.o.isle({"op": "ping"}, (1, 65534, 65534))
        self.assertFalse(r["ok"])

    def test_bilinmeyen_op(self):
        self.assertFalse(self.isle(op="__init__")["ok"])
        self.assertFalse(self.isle(op="ice_aktar")["ok"])     # yalnız kurulum CLI'si, soketten değil

    # -- Telegram kanalı
    def test_telegram_onayi_yalniz_kopru_ana_sureci(self):
        self.isle(op="kart_ac", eid="onay-0011223344556677", onizleme="x")
        r = self.isle(op="sahip_onayi", metin="[Sahne eylem: onay-0011223344556677] onayla", uid=SAHIP)
        self.assertFalse(r["ok"])                        # model süreci (köprünün çocuğu) yazamaz
        self.assertFalse(self.isle(op="gecerli", eid="onay-0011223344556677")["gecerli"])
        r = self.isle(peer=KOPRU, op="sahip_onayi", metin="[Sahne eylem: onay-0011223344556677] onayla", uid=123)
        self.assertFalse(r["ok"])                        # sahip olmayan Telegram kullanıcısı
        r = self.isle(peer=KOPRU, op="sahip_onayi", metin="[Sahne eylem: onay-0011223344556677] onayla", uid=SAHIP)
        self.assertEqual(r["eid"], "onay-0011223344556677")
        self.assertTrue(self.isle(op="gecerli", eid="onay-0011223344556677")["gecerli"])

    def test_duz_evet_yalniz_tek_acik_kart(self):
        self.isle(op="kart_ac", eid="onay-aaaaaaaaaaaaaaaa", onizleme="a")
        self.isle(op="kart_ac", eid="onay-bbbbbbbbbbbbbbbb", onizleme="b")
        r = self.isle(peer=KOPRU, op="sahip_onayi", metin="evet", uid=SAHIP)
        self.assertIsNone(r["eid"])                      # iki açık kart: düz evet hiçbirini onaylamaz
        self.isle(op="kartlar_esitle", acik=["onay-bbbbbbbbbbbbbbbb"])
        r = self.isle(peer=KOPRU, op="sahip_onayi", metin="evet", uid=SAHIP)
        self.assertEqual(r["eid"], "onay-bbbbbbbbbbbbbbbb")
        for m in ("tamam, bekle", "olur mu?", "devam etme"):
            self.isle(op="kart_ac", eid="onay-cccccccccccccccc", onizleme="c")
            self.assertIsNone(self.isle(peer=KOPRU, op="sahip_onayi", metin=m, uid=SAHIP)["eid"], m)

    # -- tüketim
    def test_tuket_tek_kullanimlik_ve_onizleme_bagi(self):
        sha = hashlib.sha256("gösterilen".encode()).hexdigest()
        self.isle(op="kart_ac", eid="onay-0011223344556677", onizleme="gösterilen")
        self.assertFalse(self.isle(op="kart_ac", eid="onay-0011223344556677", onizleme="başka")["ok"])  # ilk kayıt kilitli
        self.isle(peer=KOPRU, op="sahip_onayi", metin="[Sahne eylem: onay-0011223344556677] onayla", uid=SAHIP)
        self.assertFalse(self.isle(op="tuket", eid="onay-0011223344556677", onizleme_sha="0" * 64)["tuketildi"])
        self.assertTrue(self.isle(op="tuket", eid="onay-0011223344556677", onizleme_sha=sha)["tuketildi"])
        self.assertTrue(self.isle(op="az_once_tuketildi", eid="onay-0011223344556677")["evet"])
        self.assertFalse(self.isle(op="tuket", eid="onay-0011223344556677")["tuketildi"])
        r = self.isle(peer=KOPRU, op="sahip_onayi", metin="[Sahne eylem: onay-0011223344556677] onayla", uid=SAHIP)
        self.assertIsNone(r["eid"])                      # tüketilmiş onay yeniden açılmaz

    def test_kirpilmis_onizleme_ozetle_tuketilemez(self):
        uzun = "x" * 9000
        self.isle(op="kart_ac", eid="onay-0011223344556677", onizleme=uzun)
        self.isle(peer=KOPRU, op="sahip_onayi", metin="[Sahne eylem: onay-0011223344556677] onayla", uid=SAHIP)
        sha = hashlib.sha256(uzun.encode()).hexdigest()
        self.assertFalse(self.isle(op="tuket", eid="onay-0011223344556677", onizleme_sha=sha)["tuketildi"])

    def test_onaysiz_tuketilmez(self):
        self.isle(op="kart_ac", eid="onay-0011223344556677", onizleme="x")
        self.assertFalse(self.isle(op="tuket", eid="onay-0011223344556677")["tuketildi"])

    # -- eşleme
    def test_kod_yalniz_kopruden_ve_cagirana_donmez(self):
        r = self.isle(op="kod_uret", ad="x")
        self.assertFalse(r["ok"])
        r = self.isle(peer=KOPRU, op="kod_uret", ad="x")
        self.assertTrue(r["ok"])
        self.assertNotIn("kod", r)
        self.assertFalse(any(len(str(v)) == 6 and str(v).isdigit() for v in r.values()))
        self.assertIn("eşleme kodu", self.giden[-1])

    def test_esle_kaba_kuvvet_tavani(self):
        self.isle(peer=KOPRU, op="kod_uret", ad="x")
        kod = [w for w in self.giden[-1].split() if w.isdigit() and len(w) == 6][0]
        yanlis = "000000" if kod != "000000" else "111111"
        for _ in range(srv.ESLESME_DENEME_TAVANI):
            self.assertIsNone(self.isle(op="esle", kod=yanlis)["cihaz"])
        self.assertIsNone(self.isle(op="esle", kod=kod)["cihaz"])   # bekleyen kod iptal edildi
        self.assertIn("İPTAL", self.giden[-1])

    def test_esleme_bildirimi_ve_iptal(self):
        did, key = self._cihaz()
        self.assertIn("yeni cihaz eşlendi", self.giden[-1])
        self.assertEqual(self._kokpit(did, key, "GET", "/v1/outbox")["neden"], "ok")
        self.assertEqual(self.isle(op="iptal", id=did)["n"], 1)
        self.assertEqual(self._kokpit(did, key, "GET", "/v1/outbox")["neden"], "cihaz")
        self.assertNotIn("key", json.dumps(self.isle(op="cihazlar")))

    # -- Kokpit imzası
    def test_kokpit_imza_nedenleri(self):
        did, key = self._cihaz()
        self.assertEqual(self._kokpit(did, "11" * 32, "GET", "/v1/x")["neden"], "imza")
        self.assertEqual(self._kokpit(did, key, "GET", "/v1/x", ts=time.time() - 9999)["neden"], "saat")
        n = os.urandom(8).hex()
        self.assertEqual(self._kokpit(did, key, "POST", "/v1/apns", b"{}", nonce=n)["neden"], "ok")
        self.assertEqual(self._kokpit(did, key, "POST", "/v1/apns", b"{}", nonce=n)["neden"], "tekrar")
        n = os.urandom(8).hex()
        self._kokpit(did, key, "GET", "/v1/x", nonce=n)
        self.assertEqual(self._kokpit(did, key, "GET", "/v1/x", nonce=n)["neden"], "tekrar-get")
        # gövde özeti imzayla bağlı: gövde değişirse imza tutmaz
        h = _imzali(key, "POST", "/v1/mesaj", b'{"metin":"a"}')
        h["device"] = did
        import base64
        r = self.isle(op="kokpit", h=h, method="POST", path="/v1/mesaj", body_b64=base64.b64encode(b'{"metin":"evet"}').decode())
        self.assertEqual(r["neden"], "imza")

    def test_kokpit_eylem_ve_mesaj_onaylar(self):
        did, key = self._cihaz()
        self.isle(op="kart_ac", eid="onay-0011223344556677", onizleme="x")
        r = self._kokpit(did, key, "POST", "/v1/eylem", json.dumps({"id": "onay-0011223344556677", "veri": "onayla"}).encode())
        self.assertEqual(r["onay"], "onay-0011223344556677")
        self.assertTrue(self.isle(op="gecerli", eid="onay-0011223344556677")["gecerli"])
        r = self._kokpit(did, key, "POST", "/v1/eylem", json.dumps({"id": "onay-1111111111111111", "veri": "vazgeç"}).encode())
        self.assertIsNone(r["onay"])
        self.isle(op="kart_ac", eid="onay-2222222222222222", onizleme="y")
        r = self._kokpit(did, key, "POST", "/v1/mesaj", json.dumps({"metin": "onayla"}).encode())
        self.assertEqual(r["onay"], "onay-2222222222222222")

    def test_karar_yalniz_imzali_istekle(self):
        did, key = self._cihaz()
        self.assertFalse(self.isle(op="karar_tuket", pid="ab12cd")["gecerli"])     # dosya bırakmak yetmez
        self._kokpit(did, key, "POST", "/v1/kararlar/ab12cd", b'{"karar": "ret"}')
        self.assertFalse(self.isle(op="karar_tuket", pid="ab12cd")["gecerli"])
        self._kokpit(did, key, "POST", "/v1/kararlar/ab12cd", b'{"karar": "onay"}')
        self.assertTrue(self.isle(op="karar_tuket", pid="ab12cd")["gecerli"])
        self.assertFalse(self.isle(op="karar_tuket", pid="ab12cd")["gecerli"])     # tek kullanımlık

    def test_ice_aktar(self):
        n = self.o.ice_aktar([{"id": "aa", "ad": "x", "key_hex": "ab" * 32, "durum": "aktif"},
                              {"id": "bb", "ad": "y", "key_hex": "", "durum": "aktif"},
                              {"id": "cc", "ad": "z", "key_hex": "cd" * 32, "durum": "iptal"}])
        self.assertEqual(n, 1)
        self.assertEqual(self._kokpit("aa", "ab" * 32, "GET", "/v1/x")["neden"], "ok")


class Soket(unittest.TestCase):
    """Gerçek Unix soketi + SO_PEERCRED + ghost/onay.py istemcisi + ghost tarafı servis kipi."""

    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        with open(os.path.join(cls.d, "ayar.json"), "w") as f:
            json.dump({"sahip_id": SAHIP, "telegram_token": "x", "izinli_uid": [os.getuid()]}, f)
        cls.giden = []
        cls.kopru = [0]
        cls.o = srv.Onay(cls.d, kopru_pid=lambda: cls.kopru[0], telegram=lambda m: cls.giden.append(m) or True)
        cls.yol = os.path.join(cls.d, "onay.sock")
        cls.s = srv.Sunucu(cls.yol, cls.o)
        threading.Thread(target=cls.s.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.s.shutdown()
        cls.s.server_close()

    def setUp(self):
        self.p = [mock.patch.object(onay, "SOKET", self.yol),
                  mock.patch.object(onay, "servis_modu", lambda: True)]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()
        self.kopru[0] = 0

    def test_ping_ve_durum(self):
        self.assertTrue(onay._cagir("ping")["ok"])
        self.assertIn("aktif_cihaz", onay.durum())

    def test_servis_yoksa_kapali_duser(self):
        with mock.patch.object(onay, "SOKET", os.path.join(self.d, "yok.sock")):
            self.assertFalse(onay.gecerli("onay-0011223344556677"))
            self.assertFalse(onay.tuket("onay-0011223344556677"))
            self.assertEqual(onay.kokpit_dogrula({}, "GET", "/v1/x"), (None, "servis"))
            self.assertFalse(sahne.onay_gecerli("onay-0011223344556677"))

    def test_peercred_kopru_pid(self):
        self.kopru[0] = os.getpid() + 1          # bu süreç köprü DEĞİL
        with self.assertRaises(onay.ServisHatasi):
            onay.kod_uret("x")
        self.kopru[0] = os.getpid()              # bu süreç köprü (SO_PEERCRED gerçek pid'i verir)
        self.assertTrue(onay.kod_uret("x"))

    def test_uctan_uca_kokpit_onayi_ve_mail_bagi(self):
        self.kopru[0] = os.getpid()
        onay.kod_uret("tel")
        kod = [w for w in self.giden[-1].split() if w.isdigit() and len(w) == 6][0]
        did, key = kokpit.esle(kod, "tel")
        # ghost.db'ye anahtar YAZILMAZ (yalnız meta satır)
        c = kokpit._db()
        self.assertEqual(c.execute("SELECT key_hex FROM kokpit_devices WHERE id=?", (did,)).fetchone()[0], "")
        c.close()
        with mock.patch.object(kokpit, "outbox_yaz", lambda *a, **k: 1):
            sahne.onay("Mail", "gösterilen önizleme", eylem_id="onay-0011223344556678")
        # model: kayıt dosyasını yazmak / yerel fonksiyonu çağırmak onay üretmez
        self.assertIsNone(sahne.sahip_onayi_kaydet("[Sahne eylem: onay-0011223344556678] onayla", "kokpit-eylem"))
        self.kopru[0] = os.getpid() + 1
        self.assertIsNone(sahne.sahip_onayi_kaydet("[Sahne eylem: onay-0011223344556678] onayla", "telegram", uid=SAHIP))
        self.assertFalse(sahne.onay_gecerli("onay-0011223344556678"))
        # Sahip: imzalı Kokpit isteği
        body = json.dumps({"id": "onay-0011223344556678", "veri": "onayla"}).encode()
        ts, nonce = str(time.time()), os.urandom(8).hex()
        h = {"X-Ghost-Device": did, "X-Ghost-Ts": ts, "X-Ghost-Nonce": nonce,
             "X-Ghost-Sig": kokpit.imza(key, ts, nonce, "POST", "/v1/eylem", body)}
        cihaz, neden = kokpit.dogrula_neden(h, "POST", "/v1/eylem", body)
        self.assertEqual((cihaz or {}).get("id"), did)
        self.assertTrue(sahne.onay_gecerli("onay-0011223344556678"))
        self.assertFalse(sahne.onay_tuket_dogrulayarak("onay-0011223344556678", sahne.onizleme_ozeti("değişmiş")))
        self.assertTrue(sahne.onay_tuket_dogrulayarak("onay-0011223344556678", sahne.onizleme_ozeti("gösterilen önizleme")))
        self.assertTrue(sahne.onay_az_once_tuketildi("onay-0011223344556678"))
        self.assertFalse(sahne.onay_gecerli("onay-0011223344556678"))

    def test_cihaz_ekle_servis_kipinde_kapali(self):
        with self.assertRaises(RuntimeError):
            kokpit.cihaz_ekle("x")
        self.kopru[0] = os.getpid()
        self.assertIsNone(kokpit.kod_uret("x"))       # kod çağırana (ve döküme) dönmez

    def test_buyuk_istek_reddi(self):
        s = socket.socket(socket.AF_UNIX)
        s.connect(self.yol)
        s.sendall(b"{" + b" " * (srv.ISTEK_TAVAN + 10) + b"}\n")
        r = json.loads(s.makefile().readline())
        self.assertFalse(r["ok"])


class YerelKip(unittest.TestCase):
    def test_test_ortaminda_servis_kipi_kapali(self):
        self.assertFalse(onay.servis_modu())      # GHOST_TEST: yerel yol

    def test_soket_yolu_ortamdan_degismez(self):
        self.assertEqual(onay.SOKET, "/run/ghost-onay/onay.sock")
        kaynak = open(onay.__file__, encoding="utf-8").read()
        self.assertNotIn("environ.get(\"GHOST_ONAY", kaynak)
        self.assertEqual(kaynak.count("os.environ"), 1)        # yalnız GHOST_TEST okunur


if __name__ == "__main__":
    unittest.main()
