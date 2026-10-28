"""Sır vekili — 5. bağımsız denetim turu (2026-10-28 akşam): izin listesi ayrıştırma kaçakları (GraphQL/D1 yorum-tırnak
hilesi, Telegram sahte chat_id ile setWebhook), kimlik üreten uçlar, Sahip token'ı repo sabiti, IMAP kör aktarım,
bağlanan kullanıcı kimliği."""
import importlib.util
import json
import os
import socket
import threading
import unittest
import urllib.error
import urllib.request

import tests.common  # noqa: F401

_YOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops", "vekil", "vekil_servisi.py")
_spec = importlib.util.spec_from_file_location("vekil_servisi_5", _YOL)
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)

SIR = {"TELEGRAM_OWNER_ID": "111", "CLOUDFLARE_ACCOUNT_ID": "acc1", "POSTHOG_PROJECT_ID": "000000",
       "GMAIL_ADDRESS": "owner@example.com", "GMAIL_APP_PASSWORD": "abcd efgh ijkl mnop"}
J = "application/json"


def izin(servis, yontem, yol, govde=None, tur=J):
    g = json.dumps(govde).encode() if isinstance(govde, dict) else govde
    return srv.izin(servis, yontem, yol, g, tur, SIR)


class Lexer(unittest.TestCase):
    def test_graphql_yorum_icinde_tirnak_mutationu_gizleyemez(self):
        q = 'query{viewer{login}} # "\nmutation{deleteIssue(input:{issueId:"x"}){clientMutationId}} # "'
        self.assertEqual(izin("github", "POST", "/graphql", {"query": q})[0], "red")
        self.assertEqual(izin("github", "POST", "/graphql", {"query": "mutation{x}"})[0], "red")
        self.assertEqual(izin("github", "POST", "/graphql", {"query": 'query{repository(name:"mutation"){id}} # mutation yorum'})[0], "okuma")

    def test_d1_yorum_icinde_tirnak_deleteyi_gizleyemez(self):
        yol = "/client/v4/accounts/acc1/d1/database/db/query"
        self.assertEqual(izin("cloudflare", "POST", yol, {"sql": "SELECT 1; -- '\nDELETE FROM users; -- '"})[0], "yazma")
        self.assertEqual(izin("cloudflare", "POST", yol, {"sql": "SELECT 1; /* ' */ DELETE FROM users /* ' */"})[0], "yazma")
        self.assertEqual(izin("cloudflare", "POST", yol, {"sql": "SELECT * FROM users WHERE ad = 'delete me' -- yorum"})[0], "okuma")
        self.assertEqual(izin("cloudflare", "POST", yol, {"sql": "SELECT 'it''s' FROM x"})[0], "okuma")

    def test_lexer_birimi(self):
        self.assertNotIn("mutation", srv._dize_yorum_at('"mutation" # mutation', graphql=True).replace("mutation", "", 0))
        self.assertEqual(srv._dize_yorum_at("a 'x' b", graphql=False).split(), ["a", "b"])
        self.assertEqual(srv._dize_yorum_at("a -- 'x\nb", graphql=False).split(), ["a", "b"])


class Telegram(unittest.TestCase):
    def test_sahte_chat_id_ile_webhook_kurulamaz(self):
        for yontem in ("setWebhook", "logOut", "close", "leaveChat"):
            s, n = izin("telegram", "POST", "/" + yontem, {"url": "https://evil.example/x", "chat_id": "111"})
            self.assertEqual(s, "red", yontem)
        self.assertEqual(izin("telegram", "POST", "/setWebhook", {"url": "https://evil.example/x"})[0], "red")

    def test_gunluk_yontemler_gecer(self):
        self.assertEqual(izin("telegram", "POST", "/sendMessage", {"chat_id": "111", "text": "a"})[0], "okuma")
        self.assertEqual(izin("telegram", "POST", "/sendPhoto", {"chat_id": "111"})[0], "okuma")
        self.assertEqual(izin("telegram", "POST", "/editMessageText", {"chat_id": "111", "message_id": 1, "text": "b"})[0], "okuma")
        self.assertEqual(izin("telegram", "POST", "/deleteMessage", {"chat_id": "111", "message_id": 1})[0], "yazma")
        self.assertEqual(izin("telegram", "POST", "/setMyCommands", {"commands": []})[0], "yazma")
        self.assertEqual(izin("telegram", "POST", "/getUpdates", {"offset": 1})[0], "yazma")
        self.assertEqual(izin("telegram", "GET", "/getMe")[0], "okuma")
        self.assertIn(izin("telegram", "GET", "/getWebhookInfo")[0], ("okuma", "yazma"))   # beyaz listede, sınıfı eski davranış
        self.assertEqual(izin("telegram", "POST", "/sendMessage", {"chat_id": "222", "text": "a"})[0], "red")
        self.assertEqual(izin("telegram", "POST", "/uydurmaYontem", {"chat_id": "111"})[0], "red")


class KimlikUreten(unittest.TestCase):
    def test_token_anahtar_ureten_uclar_red(self):
        self.assertEqual(izin("github", "POST", "/user/keys", {"key": "ssh-ed25519 AAAA"})[0], "red")
        self.assertEqual(izin("github", "POST", "/authorizations", {})[0], "red")
        self.assertEqual(izin("github", "GET", "/user/keys")[0], "okuma")
        self.assertEqual(izin("sentry", "POST", "/api/0/api-tokens/", {})[0], "red")
        self.assertEqual(izin("sentry", "POST", "/api/0/organizations/example-org/api-keys/", {})[0], "red")
        self.assertEqual(izin("sentry", "PUT", "/api/0/issues/1/", {"status": "resolved"})[0], "yazma")
        self.assertEqual(izin("cloudflare", "POST", "/client/v4/user/tokens", {})[0], "red")
        self.assertEqual(izin("cloudflare", "GET", "/client/v4/user/tokens/verify")[0], "okuma")
        self.assertEqual(izin("cloudflare", "POST", "/client/v4/accounts/acc1/tokens", {})[0], "red")
        self.assertEqual(izin("posthog", "POST", "/api/personal_api_keys/", {})[0], "red")
        self.assertEqual(izin("posthog", "POST", "/api/projects/000000/query/", {"query": {}})[0], "okuma")

    def test_sahip_tokeni_yalniz_ghost_core(self):
        self.assertEqual(izin("github-git-sahip", "POST", "/ornek-org/ghost-core.git/git-receive-pack")[0], "yazma")
        self.assertEqual(izin("github-git-sahip", "POST", "/ornek-org/jarvis.git/git-receive-pack")[0], "red")
        self.assertEqual(izin("github-git-sahip", "GET", "/ornek-org/jarvis.git/info/refs?service=git-upload-pack")[0], "okuma")
        self.assertEqual(izin("github-git", "POST", "/ornek-org/jarvis.git/git-receive-pack")[0], "yazma")

    def test_yontem_taklidi_basligi_atilir(self):
        self.assertIn("x-http-method-override", srv._ATLA)


class PeerUid(unittest.TestCase):
    TCP = ("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
           "   0: 0100007F:B1A5 0100007F:225D 01 00000000:00000000 00:00000000 00000000  1000        0 12345 1 0 20 4 30 10 -1\n"
           "   1: 0100007F:B1A6 0100007F:225D 01 00000000:00000000 00:00000000 00000000    33        0 12346 1 0 20 4 30 10 -1\n")

    def test_proc_net_tcp_ayristirma(self):
        self.assertEqual(srv._tcp_peer_uid(0xB1A5, 0x225D, self.TCP), 1000)
        self.assertEqual(srv._tcp_peer_uid(0xB1A6, 0x225D, self.TCP), 33)
        self.assertIsNone(srv._tcp_peer_uid(0xB1A7, 0x225D, self.TCP))
        self.assertIsNone(srv._tcp_peer_uid(0xB1A5, 0x225D, "bozuk"))

    def _sunucu(self, uid_bul):
        v = srv.Vekil(dict(SIR, CLOUDFLARE_API_TOKEN="cf-" + "a1b2c3d4e5f6" * 3),
                      gonder=lambda *a: (200, [("Content-Type", J)], b'{"ok":true}'))
        s = srv.Sunucu(("127.0.0.1", 0), v, kimlik=lambda port, hedef: False, uid_bul=uid_bul)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        self.addCleanup(s.shutdown)
        return s.server_address[1]

    def test_yabanci_uid_403_kendi_uid_gecer_bilinmeyen_403(self):
        port = self._sunucu(lambda p, h: 33)
        with self.assertRaises(urllib.error.HTTPError) as c:
            urllib.request.urlopen("http://127.0.0.1:%d/cloudflare/client/v4/zones" % port, timeout=5)
        self.assertEqual(c.exception.code, 403)
        port = self._sunucu(lambda p, h: None)
        with self.assertRaises(urllib.error.HTTPError) as c:
            urllib.request.urlopen("http://127.0.0.1:%d/_durum" % port, timeout=5)
        self.assertEqual(c.exception.code, 403)
        port = self._sunucu(lambda p, h: sorted(srv.IZINLI_UID)[-1])
        self.assertEqual(urllib.request.urlopen("http://127.0.0.1:%d/cloudflare/client/v4/zones" % port, timeout=5).status, 200)

    def test_gercek_proc_net_tcp_kendi_baglantimizi_bulur(self):
        """Gerçek /proc/net/tcp: bu süreçten kurulan loopback bağlantının sahibi bizim uid'imiz."""
        l = socket.socket()
        l.bind(("127.0.0.1", 0))
        l.listen(1)
        c = socket.create_connection(l.getsockname())
        self.addCleanup(l.close)
        self.addCleanup(c.close)
        self.assertEqual(srv._tcp_peer_uid(c.getsockname()[1], l.getsockname()[1]), os.getuid())


class ImapSuzgec(unittest.TestCase):
    def setUp(self):
        self.ust_gelen = []
        self.l = socket.socket()
        self.l.bind(("127.0.0.1", 0))
        self.l.listen(1)
        self.addCleanup(self.l.close)

    def _sahte_ust(self):
        c, _ = self.l.accept()
        c.sendall(b"* OK IMAP hazir\r\n")
        f = c.makefile("rb")
        while True:
            satir = f.readline()
            if not satir:
                break
            self.ust_gelen.append(satir)
            etiket = satir.split()[0]
            u = satir.upper()
            if b" CAPABILITY" in u:
                c.sendall(b"* CAPABILITY IMAP4rev1\r\n" + etiket + b" OK tamam\r\n")
            elif b" LOGOUT" in u:
                c.sendall(b"* BYE\r\n" + etiket + b" OK tamam\r\n")
                break
            elif satir.rstrip().endswith(b"}"):          # literal: devam iste, gövdeyi ve devam satırını oku
                n = int(satir.rstrip()[satir.rstrip().rindex(b"{") + 1:-1].rstrip(b"+"))
                c.sendall(b"+ go\r\n")
                self.ust_gelen.append(f.read(n))
                self.ust_gelen.append(f.readline())
                c.sendall(etiket + b" OK tamam\r\n")
            else:
                c.sendall(etiket + b" OK tamam\r\n")
        c.close()

    def _vekil(self):
        import ssl  # noqa: F401
        v = srv.Vekil(dict(SIR))
        host, port = self.l.getsockname()

        class _Ust:
            def __init__(s2):
                s2.sock = socket.create_connection((host, port))
                s2.f = s2.sock.makefile("rb")

            def oku(s2):
                return s2.f.readline()

            def yaz(s2, b):
                s2.sock.sendall(b)
        s = srv.AkisSunucu(("127.0.0.1", 0), srv._ImapIsleyici, v, _Ust)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        self.addCleanup(s.shutdown)
        return s.server_address[1]

    def test_yazma_komutlari_ust_sunucuya_gitmez_okuma_gecer(self):
        import imaplib
        threading.Thread(target=self._sahte_ust, daemon=True).start()
        port = self._vekil()
        m = imaplib.IMAP4("127.0.0.1", port)
        m.login("vekil", "vekil")
        self.assertEqual(m.select("INBOX", readonly=True)[0], "OK")
        self.assertEqual(m.uid("SEARCH", None, "ALL")[0], "OK")
        self.assertEqual(m.uid("FETCH", "1", "(BODY.PEEK[HEADER])")[0], "OK")
        for komut in (lambda: m.uid("STORE", "1", "+FLAGS", r"(\Deleted)"), lambda: m.expunge(),
                      lambda: m.uid("COPY", "1", "Spam"), lambda: m.create("Yeni"), lambda: m.delete("Yeni"),
                      lambda: m.append("INBOX", None, None, b"From: a\r\n\r\nx")):
            typ, _ = komut()
            self.assertEqual(typ, "NO")
        m.logout()
        ust = b"".join(self.ust_gelen).upper()
        for yasak in (b"STORE", b"EXPUNGE", b"COPY", b"CREATE", b"DELETE", b"APPEND"):
            self.assertNotIn(b" " + yasak, ust, yasak)
        self.assertIn(b"UID FETCH", ust)
        self.assertIn(b"UID SEARCH", ust)

    def test_literal_argumanli_arama_gecer(self):
        import imaplib
        threading.Thread(target=self._sahte_ust, daemon=True).start()
        port = self._vekil()
        m = imaplib.IMAP4("127.0.0.1", port)
        m.login("vekil", "vekil")
        m.select("INBOX", readonly=True)
        m.literal = "fatura ödeme".encode("utf-8")
        typ, _ = m.uid("SEARCH", "CHARSET", "UTF-8", "TEXT")
        self.assertEqual(typ, "OK")
        m.logout()
        self.assertIn("fatura ödeme".encode("utf-8"), b"".join(self.ust_gelen))


if __name__ == "__main__":
    unittest.main()
