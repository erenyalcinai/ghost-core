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
       "TELEGRAM_BOT_TOKEN": "123456789:" + "AbCdEfGhIjKl" * 3}


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
        self.v.isle("POST", "/telegram/sendMessage", [], b"x")
        self.v.isle("GET", "/telegram-dosya/photos/a.jpg", [], None)
        self.assertEqual(self.g.cagri[0][2], "/bot%s/sendMessage" % SIR["TELEGRAM_BOT_TOKEN"])
        self.assertEqual(self.g.cagri[1][2], "/file/bot%s/photos/a.jpg" % SIR["TELEGRAM_BOT_TOKEN"])

    def test_posthog_host_degiskenden_github_token(self):
        self.v.isle("POST", "/posthog/api/projects/1/query/", [], b"{}")
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
        cls.s = srv.Sunucu(("127.0.0.1", 0), srv.Vekil(dict(SIR), gonder=cls.g))
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
        s = srv.Sunucu(("127.0.0.1", 0), srv.Vekil(self.sir, gonder=akan, dizin=self.d))
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

    def _sunucu(self, isl, onay_sor):
        import socket
        port = self.l.getsockname()[1]
        ac = lambda: srv._Satirlar(socket.create_connection(("127.0.0.1", port)))  # noqa: E731
        s = srv.AkisSunucu(("127.0.0.1", 0), isl, self.v, ac, onay_sor=onay_sor)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        self.addCleanup(s.server_close)
        self.addCleanup(s.shutdown)
        return s.server_address[1]

    def test_smtp_onaysiz_gonderemez_onayli_bir_kez(self):
        import smtplib
        threading.Thread(target=self._sahte_smtp, daemon=True).start()
        onaylar = {"onay-00112233445566aa"}
        port = self._sunucu(srv._SmtpIsleyici, lambda eid: eid in onaylar)
        with smtplib.SMTP("127.0.0.1", port, timeout=5) as s:
            s.ehlo()
            with self.assertRaises(smtplib.SMTPAuthenticationError):
                s.login("onay-ffffffffffffffff", "vekil")          # onay servisi: hayır
            with self.assertRaises(smtplib.SMTPSenderRefused):
                s.sendmail("owner@example.com", ["x@y.z"], "Subject: a\r\n\r\nb")   # AUTH yok
            s.login("onay-00112233445566aa", "vekil")
            s.sendmail("owner@example.com", ["x@y.z"], "Subject: a\r\n\r\nb")
            with self.assertRaises(smtplib.SMTPSenderRefused):
                s.sendmail("owner@example.com", ["x@y.z"], "Subject: 2\r\n\r\nb")   # bir onay = bir mail
        import base64
        auth = [x for x in self.ust_gelen if x.upper().startswith(b"AUTH")]
        self.assertEqual(len(auth), 1)
        self.assertEqual(base64.b64decode(auth[0].split()[2]), b"\0owner@example.com\0abcdefghijklmnop")

    def test_smtp_test_kipi_yalniz_kendine(self):
        import smtplib
        threading.Thread(target=self._sahte_smtp, daemon=True).start()
        port = self._sunucu(srv._SmtpIsleyici, lambda eid: False)
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
        v.isle("POST", "/wikidata/w/api.php", [], b"lgname=E&lgpassword=__WIKIDATA_BOT_PASS__&lgtoken=t")
        self.assertIn(b"lgpassword=p%40ss%20w0rd%26x&", g.cagri[-1][4])
        v.isle("POST", "/github/x", [], b"__WIKIDATA_BOT_PASS__")          # başka servise yer tutucu açılmaz
        self.assertEqual(g.cagri[-1][4], b"__WIKIDATA_BOT_PASS__")
        self.assertEqual(srv._cerez_yerel("a=1; Domain=.wikidata.org; Path=/; Secure; HttpOnly"), "a=1; Path=/; HttpOnly")

    def test_parcali_govde(self):
        g = Sahte()
        s = srv.Sunucu(("127.0.0.1", 0), srv.Vekil(dict(SIR), gonder=g))
        threading.Thread(target=s.serve_forever, daemon=True).start()
        try:
            import http.client
            c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
            c.request("POST", "/github-git/a/b.git/git-receive-pack", body=iter([b"abc", b"defg"]),
                      encode_chunked=True, headers={"Transfer-Encoding": "chunked"})
            self.assertEqual(c.getresponse().status, 200)
            self.assertEqual(g.cagri[-1][4], b"abcdefg")
        finally:
            s.shutdown()
            s.server_close()
