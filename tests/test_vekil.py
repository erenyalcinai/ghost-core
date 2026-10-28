"""Sır vekili (Faz 2 madde 16, 2026-10-28): anahtar ghost-vekil kullanıcısında, çağrıya vekil ekler.
Ağ yok: üst sunucuya gönderim (gonder) enjekte edilir."""
import importlib.util
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

import tests.common  # noqa: F401
from ghost import vekil

_YOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops", "vekil", "vekil_servisi.py")
_spec = importlib.util.spec_from_file_location("vekil_servisi", _YOL)
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)

SIR = {"CLOUDFLARE_API_TOKEN": "cf-" + "a1b2c3d4e5f6" * 3, "POSTHOG_API_KEY": "phx_" + "z9y8x7w6v5u4" * 3,
       "POSTHOG_HOST": "https://eu.posthog.com", "GHOST_GITHUB_TOKEN": "ghp_" + "q1w2e3r4t5y6" * 3,
       "TELEGRAM_BOT_TOKEN": "123456789:" + "AbCdEfGhIjKl" * 3, "TELEGRAM_OWNER_ID": "111",
       "CLOUDFLARE_ACCOUNT_ID": "acc1", "POSTHOG_PROJECT_ID": "000000"}
TEMIZ = lambda port, hedef: False  # noqa: E731  (kimlik: gözetimli oturum)


class Sahte:
    def __init__(self, cevap=b'{"ok":true}', durum=200):
        self.cagri, self.cevap, self.durum = [], cevap, durum

    def __call__(self, host, yontem, yol, bas, govde):
        self.cagri.append((host, yontem, yol, dict(bas), govde))
        return self.durum, [("Content-Type", "application/json"), ("Connection", "close")], self.cevap


class Yonlendirme(unittest.TestCase):
    def setUp(self):
        self.g = Sahte()
        self.v = srv.Vekil(dict(SIR), gonder=self.g)

    def test_cloudflare_kimlik_eklenir_istemci_basligi_atilir(self):
        d, _, _ = self.v.isle("GET", "/cloudflare/client/v4/zones?name=x.com",
                              [("Authorization", "Bearer vekil"), ("Accept", "application/json")], None)
        self.assertEqual(d, 200)
        host, yontem, yol, bas, _ = self.g.cagri[0]
        self.assertEqual((host, yontem, yol), ("api.cloudflare.com", "GET", "/client/v4/zones?name=x.com"))
        self.assertEqual(bas["Authorization"], "Bearer " + SIR["CLOUDFLARE_API_TOKEN"])
        self.assertEqual(bas["Accept"], "application/json")

    def test_istemcinin_sikistirma_istegi_dusurulur(self):
        self.v.isle("GET", "/cloudflare/x", [("accept-encoding", "gzip, br")], None)
        bas = self.g.cagri[-1][3]
        self.assertEqual([k for k in bas if k.lower() == "accept-encoding"], ["Accept-Encoding"])
        self.assertEqual(bas["Accept-Encoding"], "identity")

    def test_telegram_on_ek_ve_dosya(self):
        self.v.isle("POST", "/telegram/sendMessage", [], b"chat_id=111&text=x")
        self.v.isle("GET", "/telegram-dosya/photos/a.jpg", [], None)
        self.assertEqual(self.g.cagri[0][2], "/bot%s/sendMessage" % SIR["TELEGRAM_BOT_TOKEN"])
        self.assertEqual(self.g.cagri[1][2], "/file/bot%s/photos/a.jpg" % SIR["TELEGRAM_BOT_TOKEN"])

    def test_posthog_host_degiskenden_github_token(self):
        self.v.isle("POST", "/posthog/api/projects/000000/query/", [], b"{}")
        self.v.isle("GET", "/github/repos/a/b", [], None)
        self.assertEqual(self.g.cagri[0][0], "eu.posthog.com")
        self.assertEqual(self.g.cagri[1][3]["Authorization"], "token " + SIR["GHOST_GITHUB_TOKEN"])

    def test_host_yoldan_degistirilemez(self):
        for yol in ("/cloudflare//evil.com/x", "/cloudflare/@evil.com", "/cloudflare/../../x"):
            self.v.isle("GET", yol, [], None)
        self.assertEqual({c[0] for c in self.g.cagri}, {"api.cloudflare.com"})

    def test_bilinmeyen_servis_eksik_sir(self):
        self.assertEqual(self.v.isle("GET", "/evil/x", [], None)[0], 404)
        v = srv.Vekil({}, gonder=self.g)
        self.assertEqual(v.isle("GET", "/cloudflare/x", [], None)[0], 503)
        self.assertEqual(self.g.cagri, [])

    def test_cevapta_sir_maskelenir(self):
        g = Sahte(cevap=("echo " + SIR["TELEGRAM_BOT_TOKEN"]).encode())
        v = srv.Vekil(dict(SIR), gonder=g)
        _, bas, veri = v.isle("GET", "/telegram/getMe", [], None)
        self.assertNotIn(SIR["TELEGRAM_BOT_TOKEN"].encode(), veri)
        self.assertNotIn("connection", [k.lower() for k, _ in bas])

    def test_ust_sunucu_hatasi_502_ve_sir_sizmaz(self):
        def patla(*a):
            raise OSError("bağlantı " + SIR["CLOUDFLARE_API_TOKEN"])
        v = srv.Vekil(dict(SIR), gonder=patla)
        d, _, veri = v.isle("GET", "/cloudflare/x", [], None)
        self.assertEqual(d, 502)
        self.assertNotIn(SIR["CLOUDFLARE_API_TOKEN"].encode(), veri)


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = Sahte(cevap=b'{"result": []}')
        cls.s = srv.Sunucu(("127.0.0.1", 0), srv.Vekil(dict(SIR), gonder=cls.g), kimlik=TEMIZ)
        cls.port = cls.s.server_address[1]
        threading.Thread(target=cls.s.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.s.shutdown()
        cls.s.server_close()

    def test_uctan_uca_istemci(self):
        with mock.patch.object(vekil, "aktif", lambda *a, **k: True), \
                mock.patch.object(vekil, "TABAN", "http://127.0.0.1:%d" % self.port):
            u = vekil.url("cloudflare", "/client/v4/zones")
            self.assertEqual(vekil.basliklar("cloudflare"), {})
            req = urllib.request.Request(u, data=b'{"a":1}', method="POST",
                                         headers={"Content-Type": "application/json"})
            r = json.loads(urllib.request.urlopen(req, timeout=5).read())
        self.assertEqual(r, {"result": []})
        host, yontem, yol, bas, govde = self.g.cagri[-1]
        self.assertEqual((host, yontem, yol, govde), ("api.cloudflare.com", "POST", "/client/v4/zones", b'{"a":1}'))
        d = json.loads(urllib.request.urlopen("http://127.0.0.1:%d/_durum" % self.port, timeout=5).read())
        self.assertIn("telegram", d["servisler"])
        self.assertNotIn(SIR["CLOUDFLARE_API_TOKEN"], json.dumps(d))


def _ec_p8(yol):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    k = ec.generate_private_key(ec.SECP256R1())
    with open(yol, "wb") as f:
        f.write(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()))
    return k.public_key()


def _jwt_kutuphanesi_var():
    try:
        import cryptography  # noqa: F401
        import jwt  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(_jwt_kutuphanesi_var(), "cryptography/PyJWT yok (CI) — vekil sunucusunda kurulu")
class Asama2(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.d = tempfile.mkdtemp()
        self.pub = _ec_p8(os.path.join(self.d, "asc.p8"))
        _ec_p8(os.path.join(self.d, "apns.p8"))
        self.sir = dict(SIR, ASC_ISSUER_ID="iss-1", ASC_KEY_ID="KID123", APNS_TEAM_ID="TEAM1", APNS_KEY_ID="APK1",
                        ELEVENLABS_API_KEY="el_" + "k9j8h7g6f5d4" * 2, SENTRY_ACCESS_TOKEN="sntrys_" + "m1n2b3v4c5x6" * 3,
                        GITHUB_MCP_TOKEN="github_pat_" + "p0o9i8u7y6t5" * 3)
        self.g = Sahte()
        self.v = srv.Vekil(self.sir, gonder=self.g, dizin=self.d)

    def test_istemcinin_kendi_tokeni_gecer_yer_tutucu_degistirilir(self):
        self.v.isle("POST", "/cloudflare/client/v4/pages/assets/upload", [("Authorization", "Bearer gecici-jwt")], b"x")
        self.assertEqual(self.g.cagri[-1][3]["Authorization"], "Bearer gecici-jwt")
        self.v.isle("GET", "/elevenlabs/v1/voices", [("xi-api-key", "vekil")], None)
        self.assertEqual(self.g.cagri[-1][3]["xi-api-key"], self.sir["ELEVENLABS_API_KEY"])
        self.v.isle("GET", "/sentry/api/0/organizations/", [], None)
        self.assertEqual(self.g.cagri[-1][0], "sentry.io")

    def test_asc_jwt_vekilde_imzalanir(self):
        import jwt
        self.v.isle("GET", "/asc/v1/apps", [], None)
        host, _, yol, bas, _ = self.g.cagri[-1]
        self.assertEqual((host, yol), ("api.appstoreconnect.apple.com", "/v1/apps"))
        tok = bas["Authorization"].split()[1]
        d = jwt.decode(tok, self.pub, algorithms=["ES256"], audience="appstoreconnect-v1")
        self.assertEqual(d["iss"], "iss-1")
        self.assertEqual(jwt.get_unverified_header(tok)["kid"], "KID123")
        self.v.isle("GET", "/asc/v1/apps", [], None)
        self.assertEqual(self.g.cagri[-1][3]["Authorization"], bas["Authorization"])   # önbellek

    def test_jwt_ucu_yalniz_apns_ve_akis(self):
        def akan(host, yontem, yol, bas, govde):
            return 200, [("Content-Type", "text/event-stream")], iter([b"data: 1\n\n", b"data: " + self.sir["GITHUB_MCP_TOKEN"].encode()])
        s = srv.Sunucu(("127.0.0.1", 0), srv.Vekil(self.sir, gonder=akan, dizin=self.d), kimlik=TEMIZ)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        try:
            taban = "http://127.0.0.1:%d" % s.server_address[1]
            t = urllib.request.urlopen(taban + "/_jwt/apns", timeout=5).read().decode()
            self.assertEqual(t.count("."), 2)
            with self.assertRaises(urllib.error.HTTPError) as c:
                urllib.request.urlopen(taban + "/_jwt/asc", timeout=5)
            self.assertEqual(c.exception.code, 404)
            r = urllib.request.urlopen(taban + "/github-mcp/mcp/readonly", timeout=5)
            govde = r.read()
            self.assertIn(b"data: 1", govde)
            self.assertNotIn(self.sir["GITHUB_MCP_TOKEN"].encode(), govde)        # akışta da maskelenir
            d = json.loads(urllib.request.urlopen(taban + "/_durum", timeout=5).read())
            self.assertNotIn("asc", d["eksik"])
        finally:
            s.shutdown()
            s.server_close()


class Istemci(unittest.TestCase):
    def test_kurulu_degilken_dogrudan(self):
        self.assertFalse(vekil.aktif())
        self.assertEqual(vekil.url("cloudflare", "/client/v4/x", SIR), "https://api.cloudflare.com/client/v4/x")
        self.assertEqual(vekil.url("telegram", "/sendMessage", SIR),
                         "https://api.telegram.org/bot%s/sendMessage" % SIR["TELEGRAM_BOT_TOKEN"])
        self.assertEqual(vekil.url("posthog", "/api/x", {"POSTHOG_HOST": "eu.posthog.com"}), "https://eu.posthog.com/api/x")
        self.assertEqual(vekil.basliklar("github", SIR), {"Authorization": "token " + SIR["GHOST_GITHUB_TOKEN"]})
        self.assertFalse(vekil.hazir("cloudflare", {}))

    def test_kuruluyken_anahtar_yok(self):
        with mock.patch.object(vekil, "aktif", lambda *a, **k: True):
            self.assertEqual(vekil.url("telegram", "/sendMessage", SIR), "http://127.0.0.1:8797/telegram/sendMessage")
            self.assertEqual(vekil.basliklar("cloudflare", SIR), {})
            self.assertTrue(vekil.hazir("cloudflare", {}))

    def test_vekil_dusukse_anahtar_eldeyse_dogrudan(self):
        with mock.patch.object(vekil, "kurulu", lambda: True), mock.patch.dict(os.environ, {"GHOST_TEST": ""}), \
                mock.patch.object(vekil, "_erisilir", lambda: False):
            self.assertTrue(vekil.url("telegram", "/x", SIR).startswith("https://api.telegram.org/bot"))
            self.assertEqual(vekil.url("telegram", "/x", {}), "http://127.0.0.1:8797/telegram/x")
        with mock.patch.object(vekil, "kurulu", lambda: True), mock.patch.dict(os.environ, {"GHOST_TEST": ""}), \
                mock.patch.object(vekil, "_erisilir", lambda: True):
            self.assertEqual(vekil.url("telegram", "/x", SIR), "http://127.0.0.1:8797/telegram/x")
            self.assertEqual(vekil.basliklar("cloudflare", SIR), {})

    def test_servis_listeleri_ayni(self):
        self.assertEqual(set(vekil._DOGRUDAN), set(srv.SERVISLER))


if __name__ == "__main__":
    unittest.main()


class Gmail(unittest.TestCase):
    """SMTP/IMAP aktarıcı: sahte üst sunucu (düz TCP) — gerçek Gmail'e çıkılmaz."""

    def setUp(self):
        import socket
        self.sir = dict(SIR, GMAIL_ADDRESS="owner@example.com", GMAIL_APP_PASSWORD="abcd efgh ijkl mnop")
        self.v = srv.Vekil(self.sir, gonder=Sahte())
        self.ust_gelen = []
        self.l = socket.socket()
        self.l.bind(("127.0.0.1", 0))
        self.l.listen(5)

    def tearDown(self):
        self.l.close()

    def _sahte_smtp(self):
        c, _ = self.l.accept()
        f = c.makefile("rb")
        while True:
            satir = f.readline()
            if not satir:
                break
            self.ust_gelen.append(satir)
            k = satir.strip().upper()
            if k.startswith(b"EHLO"):
                c.sendall(b"250-smtp.example\r\n250 AUTH LOGIN PLAIN\r\n")
            elif k.startswith(b"AUTH"):
                c.sendall(b"235 ok\r\n")
            elif k == b"DATA":
                c.sendall(b"354 go\r\n")
                while f.readline() not in (b".\r\n", b""):
                    pass
                c.sendall(b"250 queued\r\n")
            elif k == b"QUIT":
                c.sendall(b"221 bye\r\n")
                break
            else:
                c.sendall(b"250 ok\r\n")
        c.close()

    def _sunucu(self, isl, onay):
        import socket
        port = self.l.getsockname()[1]
        ac = lambda: srv._Satirlar(socket.create_connection(("127.0.0.1", port)))  # noqa: E731
        s = srv.AkisSunucu(("127.0.0.1", 0), isl, self.v, ac, onay=onay)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        self.addCleanup(s.server_close)
        self.addCleanup(s.shutdown)
        return s.server_address[1]

    def _onay_servisi(self, kartlar):
        """Sahte onay servisi: {eid: (tür, önizleme)} — tüketim tek kez, tür + özet zorunlu."""
        import hashlib
        tuketilen = set()

        def onay(op, **kw):
            eid = kw.get("eid")
            if eid not in kartlar:
                return {"ok": True, "onizleme": None, "gecerli": False}
            tur, on = kartlar[eid]
            sha = hashlib.sha256(on.encode()).hexdigest()
            if op == "onizleme":
                return {"ok": True, "onizleme": on, "sha": sha, "tur": tur, "kirpildi": False,
                        "gecerli": eid not in tuketilen}
            if op == "tuket":
                ok = eid not in tuketilen and kw.get("onizleme_sha") == sha and kw.get("tur") == tur
                if ok:
                    tuketilen.add(eid)
                return {"ok": True, "tuketildi": ok}
            return {"ok": False}
        return onay, tuketilen

    def _mail(self, kime="x@y.z", konu="Merhaba", govde="Selam\n", cc=None):
        from email.message import EmailMessage
        m = EmailMessage()
        m["From"] = "Sahip <owner@example.com>"
        m["To"] = kime
        if cc:
            m["Cc"] = cc
        m["Subject"] = konu
        m.set_content(govde)
        return m

    KART = "Kime: x@y.z\nKonu: Merhaba\nEkler:\n  (yok)\n\nSelam\n"

    def test_smtp_onayli_kartla_ayni_mail_bir_kez_gider(self):
        import smtplib
        threading.Thread(target=self._sahte_smtp, daemon=True).start()
        onay, tuketilen = self._onay_servisi({"onay-00112233445566aa": ("mail", self.KART)})
        port = self._sunucu(srv._SmtpIsleyici, onay)
        with smtplib.SMTP("127.0.0.1", port, timeout=5) as s:
            s.ehlo()
            with self.assertRaises(smtplib.SMTPAuthenticationError):
                s.login("onay-ffffffffffffffff", "vekil")          # kartı olmayan onay
            s.login("onay-00112233445566aa", "vekil")
            s.send_message(self._mail())
            with self.assertRaises(smtplib.SMTPSenderRefused):
                s.send_message(self._mail())                        # bir onay = bir mail
        self.assertEqual(tuketilen, {"onay-00112233445566aa"})      # tüketen vekil
        import base64
        auth = [x for x in self.ust_gelen if x.upper().startswith(b"AUTH")]
        self.assertEqual(len(auth), 1)
        self.assertEqual(base64.b64decode(auth[0].split()[2]), b"\0owner@example.com\0abcdefghijklmnop")
        self.assertEqual(sum(1 for x in self.ust_gelen if x.strip().upper() == b"DATA"), 1)

    def test_smtp_kartla_ayni_olmayan_mail_gitmez_onay_kalir(self):
        import smtplib
        threading.Thread(target=self._sahte_smtp, daemon=True).start()
        onay, tuketilen = self._onay_servisi({"onay-00112233445566aa": ("mail", self.KART),
                                               "onay-00112233445566bb": ("genel", self.KART)})
        port = self._sunucu(srv._SmtpIsleyici, onay)
        with smtplib.SMTP("127.0.0.1", port, timeout=5) as s:
            s.ehlo()
            with self.assertRaises(smtplib.SMTPAuthenticationError):
                s.login("onay-00112233445566bb", "vekil")          # mail kartı değil
            s.login("onay-00112233445566aa", "vekil")
            for m in (self._mail(kime="baska@y.z"), self._mail(konu="Başka"), self._mail(govde="Başka gövde"),
                      self._mail(cc="z@y.z")):
                with self.assertRaises(smtplib.SMTPDataError):
                    s.send_message(m)
        self.assertEqual(tuketilen, set())
        self.assertFalse(any(x.strip().upper() == b"DATA" for x in self.ust_gelen))

    def test_kart_onizlemesi_ekli_cozulur(self):
        k = srv.mail_karti_coz("Kime: a@b.c\nCc: d@e.f\nKonu: K\nEkler:\n  • cv.pdf (12 bayt, sha256 0123456789ab)\n\nG\n\nH")
        self.assertEqual(k["ekler"], [("cv.pdf", 12, "0123456789ab")])
        self.assertEqual((k["kime"], k["cc"], k["konu"], k["govde"]), ("a@b.c", "d@e.f", "K", "G\n\nH"))

    def test_smtp_test_kipi_yalniz_kendine(self):
        import smtplib
        threading.Thread(target=self._sahte_smtp, daemon=True).start()
        port = self._sunucu(srv._SmtpIsleyici, lambda op, **kw: {})
        with smtplib.SMTP("127.0.0.1", port, timeout=5) as s:
            s.ehlo()
            s.login("test-kendine", "vekil")
            with self.assertRaises(smtplib.SMTPRecipientsRefused):
                s.sendmail("owner@example.com", ["baska@y.z"], "Subject: a\r\n\r\nb")
            s.sendmail("owner@example.com", ["owner@example.com"], "Subject: a\r\n\r\nb")

    def test_imap_login_satirina_gercek_kimlik(self):
        import imaplib

        def sahte_imap():
            c, _ = self.l.accept()
            c.sendall(b"* OK IMAP hazir\r\n")
            f = c.makefile("rb")
            while True:
                satir = f.readline()
                if not satir:
                    break
                self.ust_gelen.append(satir)
                etiket = satir.split()[0]
                if b" CAPABILITY" in satir.upper():
                    c.sendall(b"* CAPABILITY IMAP4rev1 AUTH=PLAIN\r\n" + etiket + b" OK tamam\r\n")
                elif b" LOGIN " in satir.upper():
                    c.sendall(etiket + b" OK giris\r\n")
                elif b" LOGOUT" in satir.upper():
                    c.sendall(b"* BYE\r\n" + etiket + b" OK tamam\r\n")
                    break
                else:
                    c.sendall(etiket + b" OK tamam\r\n")
            c.close()
        threading.Thread(target=sahte_imap, daemon=True).start()
        port = self._sunucu(srv._ImapIsleyici, None)
        m = imaplib.IMAP4("127.0.0.1", port)
        m.login("vekil", "vekil")
        m.noop()
        m.logout()
        login = [x for x in self.ust_gelen if b" LOGIN " in x.upper()]
        self.assertEqual(len(login), 1)
        self.assertIn(b'"owner@example.com" "abcdefghijklmnop"', login[0])
        self.assertNotIn(b"vekil", login[0])


class Asama3(unittest.TestCase):
    def test_git_basic_wikidata_govde_cerez(self):
        import base64
        g = Sahte()
        sir = dict(SIR, WIKIDATA_BOT_PASS="p@ss w0rd&x")
        v = srv.Vekil(sir, gonder=g)
        v.isle("POST", "/github-git/ornek-org/jarvis.git/git-receive-pack", [("Authorization", "vekil")], b"PACK")
        host, _, yol, bas, _ = g.cagri[-1]
        self.assertEqual((host, yol), ("github.com", "/ornek-org/jarvis.git/git-receive-pack"))
        self.assertEqual(base64.b64decode(bas["Authorization"].split()[1]).decode(),
                         "x-access-token:" + SIR["GHOST_GITHUB_TOKEN"])
        v.isle("POST", "/wikidata/w/api.php", [], b"action=login&lgname=E&lgpassword=__WIKIDATA_BOT_PASS__&lgtoken=t")
        self.assertIn(b"lgpassword=p%40ss%20w0rd%26x&", g.cagri[-1][4])
        v.isle("POST", "/github/repos/ornek-org/jarvis/issues", [], b"__WIKIDATA_BOT_PASS__")          # başka servise yer tutucu açılmaz
        self.assertEqual(g.cagri[-1][4], b"__WIKIDATA_BOT_PASS__")
        self.assertEqual(srv._cerez_yerel("a=1; Domain=.wikidata.org; Path=/; Secure; HttpOnly"), "a=1; Path=/; HttpOnly")

    def test_parcali_govde(self):
        g = Sahte()
        s = srv.Sunucu(("127.0.0.1", 0), srv.Vekil(dict(SIR), gonder=g), kimlik=TEMIZ)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        try:
            import http.client
            c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
            c.request("POST", "/github-git/ghost-bot/b.git/git-receive-pack", body=iter([b"abc", b"defg"]),
                      encode_chunked=True, headers={"Transfer-Encoding": "chunked"})
            self.assertEqual(c.getresponse().status, 200)
            self.assertEqual(g.cagri[-1][4], b"abcdefg")
        finally:
            s.shutdown()
            s.server_close()


class IzinListesi(unittest.TestCase):
    """Faz 2 devamı: vekil kimliği yalnız kendi hesaplarımıza yazar; okuma her yerde serbest.
    Biçim: izinli hedef geçer, gerisi reddedilir."""

    def setUp(self):
        self.g = Sahte()
        self.v = srv.Vekil(dict(SIR), gonder=self.g)

    def _sinif(self, servis, yontem, yol, govde=None, tur="application/json"):
        return srv.izin(servis, yontem, yol, govde, tur, SIR)[0]

    def test_github_kendi_repolarimiza_yazma_gecer(self):
        for yol in ("/repos/ornek-org/jarvis/issues", "/repos/ghost-bot/x/contents/README.md",
                    "/repos/ornek-org/r/collaborators/ghost-bot", "/user", "/user/repository_invitations/5"):
            self.assertEqual(self._sinif("github", "POST", yol, b"{}"), "yazma", yol)

    def test_github_baska_hedefe_yazma_reddedilir(self):
        self.assertEqual(self._sinif("github", "POST", "/repos/baska-hesap/r/issues", b"{}"), "red")
        self.assertEqual(self._sinif("github", "POST", "/gists", b"{}"), "red")
        self.assertEqual(self._sinif("github", "POST", "/repos/ornek-org/r/hooks", b"{}"), "red")
        self.assertEqual(self._sinif("github", "PUT", "/repos/ornek-org/r/collaborators/baska-hesap"), "red")
        self.assertEqual(self._sinif("github", "PATCH", "/repos/ornek-org/r", b'{"private": false}'), "red")
        self.assertEqual(self._sinif("github", "PATCH", "/repos/ornek-org/r", b'{"description": "x"}'), "yazma")

    def test_github_okuma_her_yerde(self):
        self.assertEqual(self._sinif("github", "GET", "/repos/rorkai/App-Store-Connect-CLI/releases/latest"), "okuma")
        self.assertEqual(self._sinif("github", "POST", "/graphql", b'{"query": "query { viewer { login } }"}'), "okuma")
        self.assertEqual(self._sinif("github", "POST", "/graphql", b'{"query": "mutation { x }"}'), "red")

    def test_git_push_yalniz_bizim_repolara_fetch_serbest(self):
        self.assertEqual(self._sinif("github-git", "POST", "/ghost-bot/a.git/git-receive-pack"), "yazma")
        self.assertEqual(self._sinif("github-git-sahip", "POST", "/ornek-org/ghost-core.git/git-receive-pack"), "yazma")
        self.assertEqual(self._sinif("github-git", "POST", "/baska-hesap/a.git/git-receive-pack"), "red")
        self.assertEqual(self._sinif("github-git", "GET", "/baska-hesap/a.git/info/refs?service=git-upload-pack"), "okuma")
        self.assertEqual(self._sinif("github-git", "POST", "/baska-hesap/a.git/git-upload-pack"), "okuma")

    def test_mcp_okuma_araclari_her_yerde_yazma_bizim_repolara(self):
        def cagri(ad, **arg):
            return json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": ad, "arguments": arg}}).encode()
        self.assertEqual(self._sinif("github-mcp", "POST", "/mcp/readonly", cagri("get_file_contents", owner="rorkai")), "okuma")
        self.assertEqual(self._sinif("github-mcp-yaz", "POST", "/mcp/", cagri("create_issue", owner="ornek-org")), "yazma")
        self.assertEqual(self._sinif("github-mcp-yaz", "POST", "/mcp/", cagri("create_issue", owner="baska-hesap")), "red")
        self.assertEqual(self._sinif("github-mcp-yaz", "POST", "/mcp/", cagri("create_gist")), "red")

    def test_telegram_yalniz_sahip(self):
        f = "application/x-www-form-urlencoded"
        self.assertEqual(self._sinif("telegram", "POST", "/sendMessage", b"chat_id=111&text=x", f), "okuma")
        self.assertEqual(self._sinif("telegram", "POST", "/sendMessage", b"chat_id=222&text=x", f), "red")
        self.assertEqual(self._sinif("telegram", "GET", "/sendMessage?chat_id=222&text=x"), "red")
        self.assertEqual(self._sinif("telegram", "POST", "/sendDocument",
                                     b'--b\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n111\r\n--b--',
                                     "multipart/form-data; boundary=b"), "okuma")
        self.assertEqual(self._sinif("telegram", "POST", "/getUpdates", b"{}"), "yazma")
        self.assertEqual(self._sinif("telegram", "POST", "/setMyCommands", b"{}"), "yazma")
        self.assertEqual(self._sinif("telegram", "POST", "/setWebhook", b"{}"), "red")
        self.assertEqual(self._sinif("telegram-dosya", "GET", "/photos/a.jpg"), "okuma")

    def test_cloudflare_posthog_sentry_kendi_hesabimiz(self):
        self.assertEqual(self._sinif("cloudflare", "POST", "/client/v4/accounts/acc1/pages/projects", b"{}"), "yazma")
        self.assertEqual(self._sinif("cloudflare", "POST", "/client/v4/accounts/acc2/pages/projects", b"{}"), "red")
        self.assertEqual(self._sinif("cloudflare", "POST", "/client/v4/graphql", b"{}"), "okuma")
        self.assertEqual(self._sinif("cloudflare", "POST", "/client/v4/accounts/acc1/d1/database/d/query",
                                     b'{"sql": "SELECT 1"}'), "okuma")
        self.assertEqual(self._sinif("cloudflare", "POST", "/client/v4/accounts/acc1/d1/database/d/query",
                                     b'{"sql": "UPDATE t SET a=1"}'), "yazma")
        self.assertEqual(self._sinif("posthog", "POST", "/api/projects/000000/query/", b"{}"), "okuma")
        self.assertEqual(self._sinif("posthog", "GET", "/api/projects/999/insights/"), "red")
        self.assertEqual(self._sinif("sentry", "GET", "/api/0/organizations/example-org/issues/"), "okuma")
        self.assertEqual(self._sinif("sentry", "GET", "/api/0/organizations/baska/issues/"), "red")

    def test_wikidata_yazma_yalniz_kendi_ogemize(self):
        f = "application/x-www-form-urlencoded"
        self.assertEqual(self._sinif("wikidata", "POST", "/w/api.php?action=wbsetreference",
                                     b"statement=Q000000001%24abc&token=t", f), "yazma")
        self.assertEqual(self._sinif("wikidata", "POST", "/w/api.php?action=edit", b"title=User:OrnekBot&token=t", f), "yazma")
        self.assertEqual(self._sinif("wikidata", "POST", "/w/api.php?action=wbeditentity", b"id=Q1&token=t", f), "red")
        self.assertEqual(self._sinif("wikidata", "GET", "/w/api.php?action=wbgetentities&ids=Q1"), "okuma")

    def test_red_ust_sunucuya_gitmez_403(self):
        d, _, g = self.v.isle("POST", "/github/gists", [], b"{}")
        self.assertEqual(d, 403)
        self.assertEqual(self.g.cagri, [])
        self.assertIn("gist", json.loads(g)["vekil"])

    def test_sinif_kacaklari_kapali(self):
        # 3. tur: yorumla/çoklu-işlemle gizlenen GraphQL mutation; D1 WITH…yazma; MCP beyaz liste;
        # çift chat_id; Wikidata parolası yalnız login.
        import json as _j
        SIR_IZIN = {"CLOUDFLARE_ACCOUNT_ID": "acc1", "POSTHOG_PROJECT_ID": "000000", "TELEGRAM_OWNER_ID": "111"}
        f = lambda srv_, yn, yol, g=None, t="application/json": srv.izin(srv_, yn, yol, g, t, SIR_IZIN)[0]
        self.assertEqual(f("github", "POST", "/graphql", _j.dumps({"query": "# c\nmutation{a}"}).encode()), "red")
        self.assertEqual(f("github", "POST", "/graphql",
                           _j.dumps({"query": "query a{x} mutation b{y}", "operationName": "b"}).encode()), "red")
        self.assertEqual(f("github", "POST", "/graphql", _j.dumps({"query": "query{viewer{login}}"}).encode()), "okuma")
        sql = lambda q: ("/client/v4/accounts/acc1/d1/database/d/query", _j.dumps({"sql": q}).encode())
        self.assertEqual(f("cloudflare", "POST", *sql("WITH x AS (SELECT 1) UPDATE t SET a=1")), "yazma")
        self.assertEqual(f("cloudflare", "POST", *sql("SELECT * FROM t -- UPDATE")), "okuma")
        cg = lambda ad, **a: _j.dumps({"method": "tools/call", "params": {"name": ad, "arguments": a}}).encode()
        self.assertEqual(f("github-mcp-yaz", "POST", "/mcp/", cg("run_secret_scanning", owner="baska")), "red")
        self.assertEqual(f("github-mcp-yaz", "POST", "/mcp/", cg("actions_run_trigger", owner="ornek-org")), "yazma")
        self.assertEqual(f("github-mcp", "POST", "/mcp/readonly", cg("list_commits", owner="x")), "okuma")
        self.assertEqual(f("telegram", "POST", "/sendMessage?chat_id=111",
                           b'{"chat_id":"222","text":"x"}'), "red")
        self.assertEqual(f("wikidata", "POST", "/w/api.php?action=edit",
                           b"title=User:OrnekBot&text=__WIKIDATA_BOT_PASS__&token=t",
                           "application/x-www-form-urlencoded"), "red")
        self.assertEqual(f("wikidata", "POST", "/w/api.php?action=login",
                           b"lgname=E&lgpassword=__WIKIDATA_BOT_PASS__",
                           "application/x-www-form-urlencoded"), "yazma")

    def test_ozerk_oturum_yalniz_okuma(self):
        oz = lambda: True  # noqa: E731
        d, _, _ = self.v.isle("GET", "/github/repos/ornek-org/jarvis", [], None, ozerk_mi=oz)
        self.assertEqual(d, 200)
        d, _, _ = self.v.isle("POST", "/posthog/api/projects/000000/query/", [], b"{}", ozerk_mi=oz)
        self.assertEqual(d, 200)
        d, _, _ = self.v.isle("POST", "/telegram/sendMessage", [("Content-Type", "application/x-www-form-urlencoded")],
                              b"chat_id=111&text=x", ozerk_mi=oz)
        self.assertEqual(d, 200)
        n = len(self.g.cagri)
        d, _, _ = self.v.isle("POST", "/github/repos/ornek-org/jarvis/issues", [], b"{}", ozerk_mi=oz)
        self.assertEqual(d, 403)
        d, _, _ = self.v.isle("DELETE", "/cloudflare/client/v4/zones/z/dns_records/r", [], None, ozerk_mi=oz)
        self.assertEqual(d, 403)
        self.assertEqual(len(self.g.cagri), n)

    def test_gozetimli_oturum_yazar_kimlik_bilinemezse_yazma_durur(self):
        d, _, _ = self.v.isle("POST", "/github/repos/ornek-org/jarvis/issues", [], b"{}", ozerk_mi=lambda: False)
        self.assertEqual(d, 200)
        d, _, g = self.v.isle("POST", "/github/repos/ornek-org/jarvis/issues", [], b"{}", ozerk_mi=lambda: None)
        self.assertEqual(d, 503)
        self.assertIn("ghost-kimlik", json.loads(g)["vekil"])


_KYOL = os.path.join(os.path.dirname(_YOL), "kimlik_servisi.py")
_kspec = importlib.util.spec_from_file_location("kimlik_servisi", _KYOL)
kim = importlib.util.module_from_spec(_kspec)
_kspec.loader.exec_module(kim)


class Kimlik(unittest.TestCase):
    """ghost-kimlik: bağlantı → süreç → ata zinciri; işaretli ata varsa özerk. Sahte /proc ağacıyla."""

    def setUp(self):
        import tempfile
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def _surec(self, pid, ppid, sid, env=b"PATH=/x", soket=None):
        k = os.path.join(self.d, str(pid))
        os.makedirs(os.path.join(k, "fd"))
        with open(os.path.join(k, "stat"), "wb") as f:
            f.write(b"%d (a b) S %d 1 %d 0" % (pid, ppid, sid))
        with open(os.path.join(k, "environ"), "wb") as f:
            f.write(env + b"\0")
        if soket:
            os.symlink("socket:[%d]" % soket, os.path.join(k, "fd", "3"))

    def _net(self, port, inode):
        p = os.path.join(self.d, "tcp")
        with open(p, "w") as f:
            f.write("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n")
            f.write("   0: 0100007F:%04X 0100007F:225D 01 00000000:00000000 00:00000000 00000000  1000  0 %d 1\n"
                    % (port, inode))
        return p

    def test_ozerk_ata_isaretlenir_gozetimli_temiz(self):
        net = self._net(40000, 777)
        self._surec(10, 1, 10, env=b"GHOST_OZERK=1")      # özerk claude
        self._surec(11, 10, 10)                              # bash
        self._surec(12, 11, 10, soket=777)                   # curl
        self.assertTrue(kim.cevapla({"port": 40000}, proc=self.d, net=net)["ozerk"])
        os.remove(os.path.join(self.d, "10", "environ"))
        with open(os.path.join(self.d, "10", "environ"), "wb") as f:
            f.write(b"PATH=/x\0")
        r = kim.cevapla({"port": 40000}, proc=self.d, net=net)
        self.assertIs(r["ozerk"], False)
        self.assertEqual(r["pid"], 12)

    def test_yetim_surec_oturum_liderinden_taninir(self):
        net = self._net(40001, 778)
        self._surec(20, 1, 20, env=b"GHOST_OZERK=1")
        self._surec(22, 1, 20, soket=778)                    # ebeveyni ölmüş, oturumu sürüyor
        self.assertTrue(kim.cevapla({"port": 40001}, proc=self.d, net=net)["ozerk"])

    def test_kimligi_okunamayan_soket_gozetimli_sayilmaz(self):
        # 4. tur: TCP'de süreç bulunamayınca sahibi başka uid olsa da None (Mac artık Unix sokette)
        p = os.path.join(self.d, "tcp2")
        with open(p, "w") as f:
            f.write("sl\n   0: 0100007F:9C44 0100007F:225D 01 0:0 0:0 0 0 0 999 1\n")
        self.assertIsNone(kim.cevapla({"port": 40004}, proc=self.d, net=p)["ozerk"])

    def test_baglanti_yoksa_bilinemedi(self):
        net = self._net(40002, 779)
        self.assertIsNone(kim.cevapla({"port": 40003}, proc=self.d, net=net)["ozerk"])


class MacTuneli(unittest.TestCase):
    """Mac vekil tüneli Unix sokete bağlanır; yazma yalnız ssh zinciri (ghost sshd-session → root sshd-session →
    ssh.service dinleyicisi) doğrulanınca serbest. Biçim: izinli zincir geçer, gerisi yazamaz (okuma serbest)."""

    def setUp(self):
        import tempfile
        self.d = tempfile.mkdtemp()
        self.cg = os.path.join(self.d, "ssh.procs")
        with open(self.cg, "w") as f:
            f.write("100\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def _surec(self, pid, ppid, uid, comm):
        k = os.path.join(self.d, str(pid))
        os.makedirs(k)
        with open(os.path.join(k, "stat"), "w") as f:
            f.write("%d (%s) S %d 1 1 0" % (pid, comm, ppid))
        with open(os.path.join(k, "status"), "w") as f:
            f.write("Name:\t%s\nUid:\t%d\t%d\t%d\t%d\n" % (comm, uid, uid, uid, uid))
        with open(os.path.join(k, "comm"), "w") as f:
            f.write(comm + "\n")

    def test_ssh_zinciri_gozetimli_digerleri_bilinemedi(self):
        self._surec(200, 100, 0, "sshd-session")
        self._surec(201, 200, 1000, "sshd-session")      # Mac tüneli
        self._surec(300, 1, 1000, "sshd-session")         # adı taklit, ebeveyni root sshd değil
        self._surec(301, 201, 1000, "python3")            # ssh oturumunun çocuğu ama sshd değil
        self._surec(400, 399, 0, "sshd-session")
        self._surec(401, 400, 1000, "sshd-session")      # dinleyicisi ssh.service'te değil
        m = lambda p: srv.mac_ssh_mi(p, proc=self.d, ssh_cgroup=self.cg)  # noqa: E731
        self.assertIs(m(201), False)
        for p in (300, 301, 401, 200, 999):
            self.assertIsNone(m(p), p)

    def test_unix_soket_ucatan_uca_okuma_serbest_yazma_zincire_bagli(self):
        import http.client
        import socket
        g = Sahte()
        yol = os.path.join(self.d, "mac.sock")
        for dogrula, beklenen in ((lambda pid: None, 503), (lambda pid: False, 200)):
            s = srv.UnixSunucu(yol, srv.Vekil(dict(SIR), gonder=g), dogrula=dogrula)
            threading.Thread(target=s.serve_forever, daemon=True).start()
            try:
                def iste(yontem, p, govde=None):
                    c = http.client.HTTPConnection("localhost", timeout=5)
                    c.sock = socket.socket(socket.AF_UNIX)
                    c.sock.connect(yol)
                    c.request(yontem, p, body=govde)
                    return c.getresponse().status
                self.assertEqual(iste("GET", "/github/repos/ornek-org/jarvis"), 200)
                self.assertEqual(iste("POST", "/github/repos/ornek-org/jarvis/issues", b"{}"), beklenen)
            finally:
                s.shutdown()
                s.server_close()
