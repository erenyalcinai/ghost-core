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
        self.assertFalse(self.isle(op="kartlar_esitle", acik=["onay-bbbbbbbbbbbbbbbb"])["ok"])   # 4. tur K1: model kapatamaz
        self.assertFalse(self.isle(op="kart_kapat", eid="onay-aaaaaaaaaaaaaaaa")["ok"])
        self.isle(peer=KOPRU, op="kartlar_esitle", acik=["onay-bbbbbbbbbbbbbbbb"])              # yalnız köprü
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
        self.assertFalse(self.isle(op="tuket", eid="onay-0011223344556677", onizleme_sha="0" * 64, tur="genel")["tuketildi"])
        self.assertFalse(self.isle(op="tuket", eid="onay-0011223344556677", onizleme_sha=sha)["tuketildi"])   # tür zorunlu
        self.assertFalse(self.isle(op="tuket", eid="onay-0011223344556677", tur="genel")["tuketildi"])        # özet zorunlu
        self.assertFalse(self.isle(op="tuket", eid="onay-0011223344556677", onizleme_sha=sha, tur="mail")["tuketildi"])
        self.assertTrue(self.isle(op="tuket", eid="onay-0011223344556677", onizleme_sha=sha, tur="genel")["tuketildi"])
        self.assertTrue(self.isle(op="az_once_tuketildi", eid="onay-0011223344556677")["evet"])
        self.assertFalse(self.isle(op="tuket", eid="onay-0011223344556677", onizleme_sha=sha, tur="genel")["tuketildi"])
        r = self.isle(peer=KOPRU, op="sahip_onayi", metin="[Sahne eylem: onay-0011223344556677] onayla", uid=SAHIP)
        self.assertIsNone(r["eid"])                      # tüketilmiş onay yeniden açılmaz

    def test_kapi_onayi_yalniz_kendi_komutuna(self):
        self.assertFalse(self.isle(op="kart_ac", eid="onay-00112233445566c0", onizleme="x", tur="kapi")["ok"])
        on = "Komut:\ncp a b\n\nKomut özeti: 0000000000000000"
        oz = srv.komut_ozeti("cp a b")
        # 4. tur K1: bağlamı servis önizlemeden hesaplar; çağıranın farklı bağlamı reddedilir
        self.assertFalse(self.isle(op="kart_ac", eid="onay-00112233445566c9", onizleme=on, tur="kapi", baglam="ozet-b")["ok"])
        self.assertTrue(self.isle(op="kart_ac", eid="onay-00112233445566c1", onizleme=on, tur="kapi")["ok"])
        self.isle(peer=KOPRU, op="sahip_onayi", metin="[Sahne eylem: onay-00112233445566c1] onayla", uid=SAHIP)
        self.assertFalse(self.isle(op="komut_tuket", eid="onay-00112233445566c1", baglam="ozet-b")["tuketildi"])
        self.assertTrue(self.isle(op="komut_tuket", eid="onay-00112233445566c1", baglam=oz)["tuketildi"])
        self.assertFalse(self.isle(op="komut_tuket", eid="onay-00112233445566c1", baglam=oz)["tuketildi"])
        self.isle(op="kart_ac", eid="onay-00112233445566c2", onizleme="özet")       # genel kart
        self.isle(peer=KOPRU, op="sahip_onayi", metin="[Sahne eylem: onay-00112233445566c2] onayla", uid=SAHIP)
        self.assertFalse(self.isle(op="komut_tuket", eid="onay-00112233445566c2", baglam="ozet-a")["tuketildi"])

    def test_inbox_imzasi_yalniz_cihaz_imzali_mesajdan_dogrulama_yalniz_kopruden(self):
        did, key = self._cihaz()
        r = self._kokpit(did, key, "POST", "/v1/mesaj", json.dumps({"metin": " /unfreeze "}).encode())
        imza, ts = r["cihaz"]["inbox_imza"], r["cihaz"]["inbox_ts"]
        self.assertFalse(self.isle(op="inbox_dogrula", metin="/unfreeze", imza=imza, ts=ts)["ok"])   # köprü değil
        self.assertTrue(self.isle(peer=KOPRU, op="inbox_dogrula", metin="/unfreeze", imza=imza, ts=ts)["gecerli"])
        # 2. tur bulgu 5: tek kullanımlık — aynı imza ikinci kez geçmez (tekrar oynatma)
        self.assertFalse(self.isle(peer=KOPRU, op="inbox_dogrula", metin="/unfreeze", imza=imza, ts=ts)["gecerli"])
        self.assertFalse(self.isle(peer=KOPRU, op="inbox_dogrula", metin="/freeze", imza=imza, ts=ts)["gecerli"])
        self.assertFalse(self.isle(peer=KOPRU, op="inbox_dogrula", metin="/unfreeze", imza="0" * 64, ts=ts)["gecerli"])
        r = self._kokpit(did, "11" * 32, "POST", "/v1/mesaj", json.dumps({"metin": "/unfreeze"}).encode())
        self.assertIsNone(r["cihaz"])                                   # yanlış cihaz anahtarı: jeton yok
        self.assertFalse(self.isle(op="inbox_imzala", metin="/unfreeze")["ok"])   # jetonsuz imzalama YOK

    def test_paylasim_inbox_imzasi_yalniz_cihaz_jetonuyla_tek_kullanimlik(self):
        """2026-10-28 (Codex sonrası): /v1/paylas ve Masa 'ilet' metni gövdede yok → op_kokpit geçerli cihaz
        imzasında tek kullanımlık jeton verir, ghost-api metni kurup imzalatır. Jeton: tek kullanım, 60 sn,
        yalnız cihaz imzası doğrulanınca; GET ya da bozuk imza jeton vermez."""
        did, key = self._cihaz()
        ham = b"%PDF-1.4 x"
        r = self._kokpit(did, key, "POST", "/v1/paylas?ad=a.pdf", ham)
        jeton = r["cihaz"]["inbox_jeton"]
        self.assertTrue(jeton and "inbox_imza" not in r["cihaz"])
        metin = "[Paylaşım: belge] /ops/kokpit_paylasim/x-a.pdf (0 KB)"
        i = self.isle(op="inbox_imzala", jeton=jeton, metin=metin)
        self.assertTrue(i["ok"])
        self.assertTrue(self.isle(peer=KOPRU, op="inbox_dogrula", metin=metin, imza=i["imza"], ts=i["ts"])["gecerli"])
        self.assertFalse(self.isle(op="inbox_imzala", jeton=jeton, metin=metin)["ok"])            # tek kullanımlık
        self.assertFalse(self.isle(op="inbox_imzala", jeton="0" * 32, metin=metin)["ok"])         # uydurma jeton
        self.assertFalse(self.isle(op="inbox_imzala", jeton="", metin=metin)["ok"])
        # bayat jeton
        r = self._kokpit(did, key, "POST", "/v1/paylas", b"y")
        j2 = r["cihaz"]["inbox_jeton"]
        self.o.inbox_jetonlar[j2] = (did, time.time() - srv.JETON_OMUR_SN - 1)
        self.assertFalse(self.isle(op="inbox_imzala", jeton=j2, metin="m")["ok"])
        # GET ve bozuk imza jeton vermez
        self.assertNotIn("inbox_jeton", self._kokpit(did, key, "GET", "/v1/outbox")["cihaz"])
        self.assertIsNone(self._kokpit(did, "11" * 32, "POST", "/v1/paylas", b"z")["cihaz"])

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
        self.assertFalse(self.isle(op="iptal", id=did)["ok"])                 # 4. tur: iptal yalnız köprüden
        self.assertEqual(self.isle(peer=KOPRU, op="iptal", id=did)["n"], 1)
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
            self.assertFalse(onay.tuket("onay-0011223344556677", "0" * 64, "genel"))
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

    def test_uctan_uca_cep_cihazi(self):
        """Cep cihazı: gerçek soket + istemci (ghost/onay.py) + kokpit sarmalayıcıları. Eşleşme sahiplik anahtarını
        verir; başlık servise taşınır; düz "tamam" onay yazmaz, önekli imzalanır; kart /v1/eylem ile onaylanır."""
        import hmac as _hmac
        self.kopru[0] = os.getpid()
        self.assertIsNone(kokpit.kod_uret("Cardputer", tur="cep"))
        kod = [w for w in self.giden[-1].split() if w.isdigit() and len(w) == 6][0]
        ek = {}
        did, key = kokpit.esle(kod, "Cardputer", ek)
        self.assertEqual(ek["tur"], "cep")
        with mock.patch.object(kokpit, "outbox_yaz", lambda *a, **k: 1):
            sahne.onay("Deploy", "gösterilen önizleme", eylem_id="onay-00aa11bb22cc33dd")

        def basliklar(method, path, body, sahiplik=True):
            ts, nonce = str(time.time()), os.urandom(8).hex()
            sig = kokpit.imza(key, ts, nonce, method, path, body)
            h = {"X-Ghost-Device": did, "X-Ghost-Ts": ts, "X-Ghost-Nonce": nonce, "X-Ghost-Sig": sig}
            if sahiplik:
                msg = "%s\n%s\n%s\n%s\n%s" % (ts, nonce, method, path, hashlib.sha256(body).hexdigest())
                h["X-Ghost-Sig0"] = _hmac.new(bytes.fromhex(ek["key0"]), (msg + "\n" + sig).encode(),
                                              hashlib.sha256).hexdigest()
            return h
        body = json.dumps({"metin": "tamam"}).encode()
        self.assertEqual(kokpit.dogrula_neden(basliklar("POST", "/v1/mesaj", body, sahiplik=False), "POST", "/v1/mesaj",
                                              body), (None, "imza"))               # sahiplik imzası yok
        cihaz, neden = kokpit.dogrula_neden(basliklar("POST", "/v1/mesaj", body), "POST", "/v1/mesaj", body)
        self.assertEqual((neden, cihaz["tur"]), ("ok", "cep"))
        self.assertEqual(cihaz["inbox_metin"], "[Cep cihazı: Cardputer; onay sayılmaz] tamam")
        self.assertTrue(onay.inbox_dogrula(cihaz["inbox_metin"], cihaz["inbox_imza"], cihaz["inbox_ts"]))
        self.assertFalse(sahne.onay_gecerli("onay-00aa11bb22cc33dd"))              # kör onay yok
        body = json.dumps({"id": "onay-00aa11bb22cc33dd", "veri": "onayla"}).encode()
        cihaz, neden = kokpit.dogrula_neden(basliklar("POST", "/v1/eylem", body), "POST", "/v1/eylem", body)
        self.assertEqual(neden, "ok")
        self.assertTrue(sahne.onay_gecerli("onay-00aa11bb22cc33dd"))               # kartı bilerek onayladı
        self.assertIn("cep", {c["id"]: c["tur"] for c in kokpit.cihazlar()}[did])

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


class CepCihazi(unittest.TestCase):
    """SURUM 3 (2026-11-23): cep cihazı — telefonla aynı yetki, TEK farkla: düz metin onay sayılmaz (kör onay yok).
    Sahiplik imzası zorunlu; yanlış PIN yalnız cihazın kendisinden gelirse sayılır ve beşincide kilitler."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        with open(os.path.join(self.d, "ayar.json"), "w") as f:
            json.dump({"sahip_id": SAHIP, "telegram_token": "x", "izinli_uid": [os.getuid()]}, f)
        self.giden = []
        self.o = srv.Onay(self.d, kopru_pid=lambda: KOPRU_PID, telegram=lambda m: self.giden.append(m) or True)

    def isle(self, peer=BEN, **kw):
        return self.o.isle(kw, peer)

    def _cihaz(self, tur=None, ad="Cardputer"):
        kw = {"tur": tur} if tur else {}
        r = self.isle(peer=KOPRU, op="kod_uret", ad=ad, **kw)
        self.assertTrue(r["ok"], r)
        kod = [w for w in self.giden[-1].split() if w.isdigit() and len(w) == 6][0]
        return self.isle(op="esle", kod=kod, ad=ad)["cihaz"]

    def _kokpit(self, c, method, path, body=b"", key=None, key0="cihaz", ts=None, nonce=None, boz=False):
        """İmzalı istek. key: asıl imza anahtarı (varsayılan cihazınki; yanlış PIN = başka anahtar).
        key0: sahiplik anahtarı ("cihaz" = cihazınki, None = başlık yok, başka değer = o anahtar).
        boz: ağdaki biri yakaladığı isteğin asıl imzasını bozar, sig0'ı aynen taşır."""
        import base64
        import hmac as _hmac
        ts = str(ts if ts is not None else time.time())
        nonce = nonce or os.urandom(8).hex()
        sig = kokpit.imza(key or c["key"], ts, nonce, method, path, body)
        h = {"device": c["id"], "ts": ts, "nonce": nonce, "sig": sig}
        k0 = c.get("key0") if key0 == "cihaz" else key0
        if k0:
            msg = "%s\n%s\n%s\n%s\n%s" % (ts, nonce, method, path, hashlib.sha256(body).hexdigest())
            h["sig0"] = _hmac.new(bytes.fromhex(k0), (msg + "\n" + sig).encode(), hashlib.sha256).hexdigest()
        if boz:
            h["sig"] = "f" * 64
        return self.isle(op="kokpit", h=h, method=method, path=path, body_b64=base64.b64encode(body).decode())

    def _durum(self, c):
        return {x["id"]: x["durum"] for x in self.isle(op="cihazlar")["cihazlar"]}[c["id"]]

    def test_kod_ve_esleme_turu_tasir(self):
        c = self._cihaz("cep")
        self.assertEqual(c["tur"], "cep")
        self.assertRegex(c["key0"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(c["key0"], c["key"])
        self.assertIn("CEP", self.giden[-2])                          # kod mesajı türü söyler
        self.assertIn("cep cihazı", self.giden[-1])                   # eşleme bildirimi de
        tam = self._cihaz()
        self.assertEqual(tam["tur"], "tam")
        self.assertNotIn("key0", tam)
        turler = {x["id"]: x["tur"] for x in self.isle(op="cihazlar")["cihazlar"]}
        self.assertEqual((turler[c["id"]], turler[tam["id"]]), ("cep", "tam"))
        self.assertFalse(self.isle(peer=KOPRU, op="kod_uret", ad="x", tur="root")["ok"])
        self.assertFalse(self.isle(op="kod_uret", ad="x", tur="cep")["ok"])         # yalnız köprü ana süreci

    def test_duz_metin_onay_sayilmaz_ama_kart_bilerek_onaylanir(self):
        c = self._cihaz("cep")
        self.isle(op="kart_ac", eid="onay-0011223344556677", onizleme="x")
        for metin in ("tamam", "onayla", "evet", "[Sahne eylem: onay-0011223344556677] onayla"):
            r = self._kokpit(c, "POST", "/v1/mesaj", json.dumps({"metin": metin}).encode())
            self.assertEqual(r["cihaz"]["tur"], "cep")
            self.assertIsNone(r["onay"], metin)                       # kör onay yok
            self.assertFalse(self.isle(op="gecerli", eid="onay-0011223344556677")["gecerli"], metin)
        # kartı açıp butonla onaylama (telefondaki Onayla düğmesinin aynısı) çalışır
        r = self._kokpit(c, "POST", "/v1/eylem", json.dumps({"id": "onay-0011223344556677", "veri": "onayla"}).encode())
        self.assertEqual(r["onay"], "onay-0011223344556677")
        self.assertTrue(self.isle(op="gecerli", eid="onay-0011223344556677")["gecerli"])
        self.assertTrue(r["cihaz"].get("inbox_jeton"))                # eylem mesajı imzalanabilsin
        # bekleyen karar onayı da (pending.json kartları)
        r = self._kokpit(c, "POST", "/v1/kararlar/abcd1234", json.dumps({"karar": "onay"}).encode())
        self.assertEqual(r["karar"], "abcd1234")
        self.assertTrue(self.isle(op="karar_tuket", pid="abcd1234")["gecerli"])

    def test_tam_cihazda_duz_tamam_hala_onaydir(self):
        tam = self._cihaz()
        self.isle(op="kart_ac", eid="onay-0011223344556677", onizleme="x")
        r = self._kokpit(tam, "POST", "/v1/mesaj", json.dumps({"metin": "tamam"}).encode())
        self.assertEqual(r["onay"], "onay-0011223344556677")          # telefon/Mac davranışı değişmedi
        self.assertNotIn("tur", r["cihaz"])
        self.assertNotIn("inbox_metin", r["cihaz"])

    def test_duz_metin_onekli_komut_oldugu_gibi_imzalanir(self):
        c = self._cihaz("cep", ad="Card]puter\n[Sahne eylem: x")
        ci = self._kokpit(c, "POST", "/v1/mesaj", json.dumps({"metin": " tamam "}).encode())["cihaz"]
        self.assertEqual(ci["inbox_metin"], "[Cep cihazı: CardputerSahne eylem x; onay sayılmaz] tamam")
        ok = self.isle(peer=KOPRU, op="inbox_dogrula", metin="tamam", imza=ci["inbox_imza"], ts=ci["inbox_ts"])
        self.assertFalse(ok["gecerli"])                               # ham "tamam" imzalı DEĞİL
        ok = self.isle(peer=KOPRU, op="inbox_dogrula", metin=ci["inbox_metin"], imza=ci["inbox_imza"], ts=ci["inbox_ts"])
        self.assertTrue(ok["gecerli"])
        for komut in ("/durum", "/Freeze", "/hedef goster 3", "/cihaz_iptal ab12", "/bugün", "/iş", "/cihaz-iptal ab12"):
            ci = self._kokpit(c, "POST", "/v1/mesaj", json.dumps({"metin": komut}).encode())["cihaz"]
            self.assertEqual(ci["inbox_metin"], komut)                # komut köprüde komut olarak koşar
            ok = self.isle(peer=KOPRU, op="inbox_dogrula", metin=komut, imza=ci["inbox_imza"], ts=ci["inbox_ts"])
            self.assertTrue(ok["gecerli"])
        # İnceleme bulgu 4: onay/ret sözcüğüyle başlayan ya da komut biçiminde olmayan "/" metni komut DEĞİLDİR —
        # ham imzalanırsa köprü onu öneksiz sohbete düşürür, döküm yazımı da kartın görünümünü kapatırdı.
        for metin in ("/tamam", "/onayla", "/evet yap", "/OK", "/vazgeç", "/iptal", "/ tamam", "/x", "//tamam",
                      "/tamam\nikinci satır"):
            ci = self._kokpit(c, "POST", "/v1/mesaj", json.dumps({"metin": metin}).encode())["cihaz"]
            self.assertTrue(ci["inbox_metin"].startswith("[Cep cihazı: "), metin)
            ok = self.isle(peer=KOPRU, op="inbox_dogrula", metin=metin, imza=ci["inbox_imza"], ts=ci["inbox_ts"])
            self.assertFalse(ok["gecerli"], metin)
        bos = self._kokpit(c, "POST", "/v1/mesaj", json.dumps({"metin": "  "}).encode())["cihaz"]
        self.assertNotIn("inbox_metin", bos)

    def test_uc_kisiti_yok(self):
        c = self._cihaz("cep")
        for method, path, body in (("GET", "/v1/nabiz", b""), ("GET", "/v1/outbox?since=3&bekle=3", b""),
                                   ("GET", "/v1/sahne", b""), ("GET", "/v1/masa", b""), ("POST", "/v1/masa", b"{}")):
            self.assertEqual(self._kokpit(c, method, path, body)["neden"], "ok", path)

    def test_yanlis_pin_kilidi(self):
        c = self._cihaz("cep")
        yanlis = "0" * 64
        for _ in range(srv.CEP_PIN_TAVANI - 1):
            self.assertEqual(self._kokpit(c, "GET", "/v1/nabiz", key=yanlis)["neden"], "imza")
        self.assertEqual(self._kokpit(c, "GET", "/v1/nabiz")["neden"], "ok")       # doğru PIN: sayaç sıfır
        for _ in range(srv.CEP_PIN_TAVANI - 1):
            self._kokpit(c, "GET", "/v1/nabiz", key=yanlis)
        # yakalanmış geçerli bir GET'in TEKRARI sayacı sıfırlamaz
        ts, nonce = str(time.time()), "a1b2c3d4e5f60718"
        self.assertEqual(self._kokpit(c, "GET", "/v1/kararlar", ts=ts, nonce=nonce)["neden"], "ok")
        for _ in range(srv.CEP_PIN_TAVANI - 1):
            self._kokpit(c, "GET", "/v1/nabiz", key=yanlis)
            self.assertEqual(self._kokpit(c, "GET", "/v1/kararlar", ts=ts, nonce=nonce)["neden"], "tekrar-get")
        self.assertEqual(self._durum(c), "aktif")
        self.assertEqual(self._kokpit(c, "GET", "/v1/nabiz", key=yanlis)["neden"], "imza")      # beşinci → kilit
        self.assertIn("kilitlendi", self.giden[-1])
        self.assertEqual(self._kokpit(c, "GET", "/v1/nabiz")["neden"], "cihaz")    # doğru anahtar da geçmez
        self.assertEqual(self._durum(c), "kilitli")

    def test_sahiplik_imzasi_zorunlu_kehanet_yok(self):
        """Codex 2026-11-23 (2. tur): başlığı atan saldırgan sınırsız PIN deneyememeli — sig0 yoksa DOĞRU anahtar
        bile geçmez; yani cihazdaki açık anahtar olmadan tahminin doğru olup olmadığı öğrenilemez."""
        c = self._cihaz("cep")
        for _ in range(srv.CEP_PIN_TAVANI * 3):
            self.assertEqual(self._kokpit(c, "GET", "/v1/nabiz", key0=None)["neden"], "imza")               # doğru K
            self.assertEqual(self._kokpit(c, "GET", "/v1/nabiz", key="0" * 64, key0=None)["neden"], "imza")  # yanlış K
            self.assertEqual(self._kokpit(c, "GET", "/v1/nabiz", key0="1" * 64)["neden"], "imza")           # yanlış K0
        self.assertEqual(self._durum(c), "aktif")                     # hiçbiri sayılmadı
        self.assertEqual(self._kokpit(c, "GET", "/v1/nabiz")["neden"], "ok")

    def test_agdan_kilitlenemez(self):
        """Codex 2026-11-23 (1. tur): cihaz kimliğini bilen ya da trafiği yakalayan biri cihazı kilitleyememeli."""
        c = self._cihaz("cep")
        for _ in range(srv.CEP_PIN_TAVANI * 3):
            self._kokpit(c, "GET", "/v1/nabiz", key="0" * 64, key0=None)           # sahiplik imzası yok
            self._kokpit(c, "GET", "/v1/nabiz", key="0" * 64, key0="1" * 64)       # sahiplik imzası uydurma
            self._kokpit(c, "GET", "/v1/nabiz", boz=True)                          # yakalanmış sig0 + bozulmuş imza
        ts, nonce = str(time.time()), "0011223344556677"
        for _ in range(srv.CEP_PIN_TAVANI * 3):                                    # yakalanmış yanlış-PIN isteğinin BİREBİR tekrarı
            self._kokpit(c, "GET", "/v1/nabiz", key="0" * 64, ts=ts, nonce=nonce)
        self.assertEqual(self._durum(c), "aktif")
        self.assertEqual(self._kokpit(c, "GET", "/v1/nabiz")["neden"], "ok")
        self.assertFalse([m for m in self.giden if "kilitlendi" in m])
        self.assertEqual(len([m for m in self.giden if "yanlış PIN denendi" in m]), 1)   # tekrar yalnız bir kez sayıldı

    def test_ayni_nonce_ile_sinirsiz_deneme_yok(self):
        """İnceleme bulgu 1: cihazı alan kişi sabit nonce ile her PIN adayına ayrı imza üretirse HER BİRİ sayılır."""
        c = self._cihaz("cep")
        ts, nonce = str(time.time()), "feedfacecafebeef"
        for i in range(srv.CEP_PIN_TAVANI):
            self.assertEqual(self._durum(c), "aktif")
            self._kokpit(c, "GET", "/v1/nabiz", key="%064x" % (i + 1), ts=ts, nonce=nonce)
        self.assertEqual(self._durum(c), "kilitli")

    def test_bayat_saatte_pin_kehaneti_yok(self):
        """İnceleme bulgu 2: pencere dışı istekte doğru ve yanlış anahtar AYNI cevabı alır, hiçbiri sayılmaz."""
        c = self._cihaz("cep")
        for ts in (time.time() - 2 * srv.PENCERE_SN, time.time() + 2 * srv.PENCERE_SN, "nan", "inf"):
            dogru = self._kokpit(c, "GET", "/v1/nabiz", ts=ts)
            yanlis = self._kokpit(c, "GET", "/v1/nabiz", key="0" * 64, ts=ts)
            self.assertEqual((dogru["neden"], yanlis["neden"]), ("saat", "saat"), ts)
            self.assertIsNone(dogru["cihaz"])
        for _ in range(srv.CEP_PIN_TAVANI * 2):
            self._kokpit(c, "GET", "/v1/nabiz", key="0" * 64, ts=time.time() - 2 * srv.PENCERE_SN)
        self.assertEqual(self._durum(c), "aktif")
        self.assertFalse([m for m in self.giden if "PIN" in m and "eşleme" not in m])     # ne uyarı ne kilit

    def test_gunluk_tavan_ve_ilk_yanlista_uyari(self):
        """İnceleme bulgu 3: kartı kopyalayan kişi, sahibin her geçerli isteğinden sonra 4 PIN deneyip sayacı
        sıfırlatamaz — sıfırlanmayan günlük tavan kilitler; dizinin ilk yanlışında sahibe uyarı gider (saatte bir)."""
        c = self._cihaz("cep")
        toplam = 0
        while self._durum(c) == "aktif" and toplam < 100:
            for _ in range(srv.CEP_PIN_TAVANI - 1):
                if self._durum(c) != "aktif":
                    break
                self._kokpit(c, "GET", "/v1/nabiz", key="%064x" % (toplam + 7))
                toplam += 1
            if self._durum(c) == "aktif":
                self.assertEqual(self._kokpit(c, "GET", "/v1/nabiz")["neden"], "ok")       # sahibin olağan isteği
        self.assertEqual(self._durum(c), "kilitli")
        self.assertEqual(toplam, srv.CEP_PIN_GUN_TAVANI)
        self.assertEqual(len([m for m in self.giden if "yanlış PIN denendi" in m]), 1)
        self.assertIn("24 saatte", self.giden[-1])

    def test_tam_cihaz_kilitlenmez(self):
        c = self._cihaz()
        for _ in range(srv.CEP_PIN_TAVANI + 3):
            self._kokpit(c, "GET", "/v1/nabiz", key="0" * 64, key0="2" * 64)
        self.assertEqual(self._kokpit(c, "GET", "/v1/masa")["neden"], "ok")

    def test_iptal_iki_anahtari_da_siler(self):
        c = self._cihaz("cep")
        self.isle(peer=KOPRU, op="iptal", id=c["id"])
        r = self.o.db.execute("SELECT key_hex, key0_hex, durum FROM cihazlar WHERE id=?", (c["id"],)).fetchone()
        self.assertEqual(tuple(r), ("", "", "iptal"))

    def test_eski_veritabani_goc(self):
        import sqlite3
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "ayar.json"), "w") as f:
            json.dump({"sahip_id": SAHIP, "izinli_uid": [os.getuid()]}, f)
        c = sqlite3.connect(os.path.join(d, "onay.db"))
        c.execute("CREATE TABLE cihazlar(id TEXT PRIMARY KEY, ad TEXT, key_hex TEXT, durum TEXT, kod TEXT, "
                  "kod_son REAL, created TEXT, last_seen TEXT)")
        c.execute("INSERT INTO cihazlar(id, ad, key_hex, durum) VALUES('eskicihaz001', 'iPhone', ?, 'aktif')", ("ab" * 32,))
        c.commit()
        c.close()
        self.o = srv.Onay(d, kopru_pid=lambda: KOPRU_PID, telegram=lambda m: True)
        self.isle(op="kart_ac", eid="onay-0011223344556677", onizleme="x")
        eski = {"id": "eskicihaz001", "key": "ab" * 32}
        self.assertEqual(self._kokpit(eski, "GET", "/v1/masa")["neden"], "ok")     # sütunsuz eski kayıt = tam cihaz
        r = self._kokpit(eski, "POST", "/v1/mesaj", json.dumps({"metin": "tamam"}).encode())
        self.assertEqual(r["onay"], "onay-0011223344556677")                       # eski cihazın onay yolu aynen
        self.assertEqual(self.isle(op="cihazlar")["cihazlar"][0]["tur"], "tam")
        self.assertEqual(self.isle(op="ping")["surum"], 3)

    def test_ice_aktar_yalniz_ilk_kurulumda(self):
        """İnceleme (yama dışı bulgu): ghost.db'yi model yazabilir; oraya ekilen anahtarlı satır sonraki kur.sh
        koşularında serviste tam cihaz olmamalı, var olan cep cihazını da tam'a çevirmemeli."""
        ekili = {"id": "ekilicihaz01", "ad": "x", "key_hex": "cd" * 32, "durum": "aktif"}
        self.assertEqual(self.o.ice_aktar([dict(ekili)]), 1)                       # boş serviste (ilk kurulum) alınır
        c = self._cihaz("cep")
        self.assertEqual(self.o.ice_aktar([dict(ekili, id="ekilicihaz02"), dict(ekili, id=c["id"])]), 0)
        kimlikler = {x["id"]: x["tur"] for x in self.isle(op="cihazlar")["cihazlar"]}
        self.assertNotIn("ekilicihaz02", kimlikler)
        self.assertEqual(kimlikler[c["id"]], "cep")
        self.assertEqual(self._kokpit(c, "GET", "/v1/nabiz", key0=None)["neden"], "imza")   # hâlâ sig0 ister
        # 2. tur Y1: bütün cihazlar iptal edilse de (etkin cihaz 0) ekim yine alınmaz
        self.isle(peer=KOPRU, op="iptal")
        self.assertEqual(self.o.ice_aktar([dict(ekili, id="ekilicihaz03")]), 0)
        self.assertNotIn("ekilicihaz03", {x["id"] for x in self.isle(op="cihazlar")["cihazlar"]})


class CepIstemci(unittest.TestCase):
    def test_eski_serviste_cep_kodu_istenmez(self):
        cagrilar = []

        def sahte(op, **kw):
            cagrilar.append(op)
            return {"ok": True, "surum": 2} if op == "ping" else {"ok": True, "gonderildi": True}
        with mock.patch.object(onay, "_cagir", sahte):
            with self.assertRaises(onay.ServisHatasi):
                onay.kod_uret("Cardputer", tur="cep")
            self.assertEqual(cagrilar, ["ping"])                      # eski servis TAM kod üretirdi: hiç sorulmadı
            self.assertTrue(onay.kod_uret("iPhone"))                  # tam cihaz yolu değişmedi

    def test_yerel_kipte_cep_yok(self):
        with self.assertRaises(RuntimeError):
            kokpit.kod_uret("Cardputer", tur="cep")

    def test_sahiplik_basligi_servise_iletilir(self):
        """İnceleme bulgu 7a: X-Ghost-Sig0 servise taşınmazsa cep cihazı hiç doğrulanamaz; bağlantı noktası sınanır."""
        gorulen = {}

        def sahte(op, **kw):
            gorulen.update(kw)
            return {"ok": True, "cihaz": {"id": "d"}, "neden": "ok"}
        with mock.patch.object(onay, "_cagir", sahte):
            onay.kokpit_dogrula({"X-Ghost-Device": "d", "X-Ghost-Ts": "1", "X-Ghost-Nonce": "n" * 8,
                                 "X-Ghost-Sig": "a" * 64, "X-Ghost-Sig0": "b" * 64}, "GET", "/v1/nabiz")
        self.assertEqual(gorulen["h"]["sig0"], "b" * 64)
        self.assertEqual(gorulen["h"]["sig"], "a" * 64)

    def test_cihaz_komutu_turu_gecirir(self):
        """İnceleme bulgu 7b: /cihaz cep türü kod_uret'e geçirmezse TAM cihaz kodu üretilir."""
        from ghost import komutlar
        cagri, giden = [], []
        ctx = mock.Mock()
        ctx.send = lambda chat, metin: giden.append(metin)
        mgr = mock.Mock(chat_id=1)
        with mock.patch.object(kokpit, "kod_uret", lambda ad=None, tur=None: cagri.append((ad, tur))):
            komutlar.bul("/cihaz")("cep Cebim", mgr, ctx)
            komutlar.bul("/cihaz")("cep", mgr, ctx)
            komutlar.bul("/cihaz")("iPhone", mgr, ctx)
        self.assertEqual(cagri, [("Cebim", "cep"), ("Cardputer", "cep"), ("iPhone", None)])
        with mock.patch.object(kokpit, "cihazlar", lambda: [
                {"id": "a1", "ad": "Cardputer", "durum": "aktif", "last_seen": None, "tur": "cep"},
                {"id": "b2", "ad": "iPhone", "durum": "aktif", "last_seen": None, "tur": "tam"}]):
            komutlar.bul("/cihazlar")("", mgr, ctx)
        self.assertIn("[aktif · cep]", giden[-1])
        self.assertIn("iPhone [aktif]", giden[-1])


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
