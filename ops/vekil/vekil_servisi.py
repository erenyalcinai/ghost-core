#!/usr/bin/python3 -I
"""Ghost sır vekili — API anahtarları AYRI KULLANICIDA, çağrıya vekil ekler (Faz 2 madde 16, 2026-10-28).

NEDEN VAR: model ile anahtarlar aynı `ghost` kullanıcısındaydı (ortam + secrets.env). Kapının regex'i
"bu sır yalnız şu adrese" kararını komut METNİNE bakarak veriyordu — delinebilir bir yarış. Vekil yükü
regex'ten alır: anahtar `ghost-vekil` kullanıcısında durur (/var/lib/ghost-vekil 700), model ve Ghost'un
betikleri API'yi yine TAM yetkiyle çağırır ama anahtarı hiç görmez:

    http://127.0.0.1:8797/<servis>/<yol>  →  https://<sabit host>/<yol>  + kimlik başlığı

Yetenek AZALMAZ (Sahip 2026-10-28: "güvenlik artarken güç azalmasın"): her uç, her yöntem, her gövde
geçer; akan cevap (SSE, büyük dosya) parça parça taşınır; istemci KENDİ geçici token'ını getiriyorsa
(wrangler'ın Pages yükleme JWT'si gibi) ona dokunulmaz. Yalnız hedef host servise SABİTTİR (anahtar
başka adrese gidemez) ve cevapta anahtarın düz değeri geçerse maskelenir.

JWT servisleri (2. aşama): App Store Connect isteğine vekil kendi .p8'iyle 10 dk'lık JWT imzalar;
APNs HTTP/2 istediği için `/_jwt/apns` yalnız kısa ömürlü (50 dk) JWT döner, .p8 vekilden çıkmaz.
Bağımlılık: stdlib + PyJWT (sistemde). Repodan import YOK — root sahipli kopya kendi başına koşar.
"""
import http.client
import http.server
import json
import os
import re
import socketserver
import ssl
import sys
import threading
import time

ADRES = ("127.0.0.1", 8797)
DURUM_DIZIN = "/var/lib/ghost-vekil"
GOVDE_TAVAN = 200 * 1024 * 1024
TAMPON_TAVAN = 5 * 1024 * 1024        # bundan büyük ya da boyutsuz cevap akarak taşınır
ZAMAN_ASIMI = 300
YER_TUTUCU = {"", "vekil", "bearer vekil", "token vekil"}   # istemcinin "kimliği vekil eklesin" işareti

# servis → sabit host + kimlik. {AD} = sirlar.env'deki değişken. jwt: vekil imzalar.
SERVISLER = {
    "cloudflare": {"host": "api.cloudflare.com", "baslik": {"Authorization": "Bearer {CLOUDFLARE_API_TOKEN}"}},
    "posthog": {"host_degisken": "POSTHOG_HOST", "host": "eu.posthog.com",
                "baslik": {"Authorization": "Bearer {POSTHOG_API_KEY}"}},
    "github": {"host": "api.github.com", "baslik": {"Authorization": "token {GHOST_GITHUB_TOKEN}"}},
    "telegram": {"host": "api.telegram.org", "on_ek": "/bot{TELEGRAM_BOT_TOKEN}"},
    "telegram-dosya": {"host": "api.telegram.org", "on_ek": "/file/bot{TELEGRAM_BOT_TOKEN}"},
    # 2. aşama
    "elevenlabs": {"host": "api.elevenlabs.io", "baslik": {"xi-api-key": "{ELEVENLABS_API_KEY}"}},
    "sentry": {"host": "sentry.io", "baslik": {"Authorization": "Bearer {SENTRY_ACCESS_TOKEN}"}},
    "asc": {"host": "api.appstoreconnect.apple.com", "jwt": "asc"},
    "github-mcp": {"host": "api.githubcopilot.com", "baslik": {"Authorization": "Bearer {GITHUB_MCP_TOKEN}"}},
    "github-mcp-yaz": {"host": "api.githubcopilot.com", "baslik": {"Authorization": "Bearer {GITHUB_MCP_YAZ_TOKEN}"}},
    # 3. aşama
    "higgsfield": {"host": "api.higgsfield.ai", "baslik": {"Authorization": "Key {HF_API_KEY_ID}:{HF_API_KEY_SECRET}"}},
    "wikidata": {"host": "www.wikidata.org", "govde": {"__WIKIDATA_BOT_PASS__": "WIKIDATA_BOT_PASS"}},
    "github-git": {"host": "github.com", "basic": "x-access-token:{GHOST_GITHUB_TOKEN}"},        # git push/fetch
    "github-git-sahip": {"host": "github.com", "basic": "x-access-token:{GITHUB_MCP_TOKEN}"},     # ghost-core (Sahip)
}
# JWT: (iss değişkeni, kid değişkeni, .p8 dosyası, ömür sn, aud)
JWTLER = {
    "asc": ("ASC_ISSUER_ID", "ASC_KEY_ID", "asc.p8", 1200, "appstoreconnect-v1"),
    "apns": ("APNS_TEAM_ID", "APNS_KEY_ID", "apns.p8", 3000, None),
}
_ATLA = {"host", "accept-encoding", "connection", "keep-alive", "proxy-authorization", "proxy-connection",
         "te", "trailer", "transfer-encoding", "upgrade", "content-length"}
_CEVAP_ATLA = {"connection", "keep-alive", "transfer-encoding", "content-length", "content-encoding"}


def sirlari_oku(dizin):
    """sirlar.env (KEY=değer satırları). Değerler yalnız bellekte."""
    d = {}
    try:
        with open(os.path.join(dizin, "sirlar.env"), encoding="utf-8") as f:
            for satir in f:
                m = re.match(r"\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$", satir.rstrip("\n"))
                if m:
                    d[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    except OSError:
        pass
    return d


def _doldur(sablon, sirlar):
    eksik = []

    def r(m):
        v = sirlar.get(m.group(1))
        if not v:
            eksik.append(m.group(1))
            return ""
        return v
    out = re.sub(r"\{([A-Z_][A-Z0-9_]*)\}", r, sablon)
    if eksik:
        raise KeyError(", ".join(eksik))
    return out


def _cerez_yerel(v):
    """Üst sunucunun çerezi istemcide 127.0.0.1 için saklansın: Domain ve Secure düşer (Wikidata oturumu)."""
    return re.sub(r";\s*(Domain=[^;]*|Secure)(?=;|$)", "", v, flags=re.I)


def _hata(durum, metin):
    return durum, [("Content-Type", "application/json")], json.dumps({"vekil": metin}, ensure_ascii=False).encode()


class Vekil:
    """Yönlendirme + kimlik ekleme + maskeleme. Ağdan bağımsız test edilebilir (gonder enjekte edilir)."""

    def __init__(self, sirlar, gonder=None, dizin=DURUM_DIZIN):
        self.sirlar = sirlar
        self.gonder = gonder or self._https
        self.dizin = dizin
        self.sayac = {}
        self.kilit = threading.Lock()
        self._jwt_onbellek = {}

    # ------------------------------------------------------------ JWT
    def jwt(self, ad):
        iss_ad, kid_ad, dosya, omur, aud = JWTLER[ad]
        with self.kilit:
            tok, son = self._jwt_onbellek.get(ad, (None, 0))
            if tok and time.time() < son - 300:
                return tok
        iss, kid = self.sirlar.get(iss_ad), self.sirlar.get(kid_ad)
        if not (iss and kid):
            raise KeyError("%s, %s" % (iss_ad, kid_ad))
        try:
            with open(os.path.join(self.dizin, dosya), encoding="utf-8") as f:
                anahtar = f.read()
        except OSError:
            raise KeyError(dosya)
        import jwt as pyjwt
        simdi = int(time.time())
        yuk = {"iss": iss, "iat": simdi}
        if aud:
            yuk.update(aud=aud, exp=simdi + omur)
        tok = pyjwt.encode(yuk, anahtar, algorithm="ES256", headers={"kid": kid})
        with self.kilit:
            self._jwt_onbellek[ad] = (tok, simdi + omur)
        return tok

    # ------------------------------------------------------------ yönlendirme
    def hedef(self, yol):
        """'/cloudflare/client/v4/x?y=1' → (servis, host, üst yol, başlıklar) ya da hata."""
        if not yol.startswith("/"):
            raise ValueError("yol")
        yol_, _, sorgu = yol.partition("?")
        parca = yol_[1:].split("/", 1)
        servis = parca[0]
        kalan = "/" + (parca[1] if len(parca) > 1 else "") + ("?" + sorgu if sorgu else "")
        s = SERVISLER.get(servis)
        if not s:
            raise LookupError(servis)
        host = s["host"]
        if s.get("host_degisken") and self.sirlar.get(s["host_degisken"]):
            host = re.sub(r"^https?://", "", self.sirlar[s["host_degisken"]]).split("/")[0]
        if not re.fullmatch(r"[a-z0-9.-]+", host):
            raise ValueError("host")
        ust = _doldur(s.get("on_ek", ""), self.sirlar) + kalan
        bas = {k: _doldur(v, self.sirlar) for k, v in (s.get("baslik") or {}).items()}
        if s.get("jwt"):
            bas["Authorization"] = "Bearer " + self.jwt(s["jwt"])
        if s.get("basic"):
            import base64
            bas["Authorization"] = "Basic " + base64.b64encode(_doldur(s["basic"], self.sirlar).encode()).decode()
        return servis, host, ust, bas

    def govde_doldur(self, servis, govde):
        """Gövdedeki yer tutucu (yalnız o servise tanımlı olan) → sır. Host sabit: sır başka adrese gidemez."""
        tanim = SERVISLER.get(servis, {}).get("govde") or {}
        if not govde or not tanim:
            return govde
        for yer, ad in tanim.items():
            if yer.encode() in govde:
                v = self.sirlar.get(ad)
                if not v:
                    raise KeyError(ad)
                import urllib.parse
                govde = govde.replace(yer.encode(), urllib.parse.quote(v, safe="").encode())
        return govde

    def _gunluk_yaz(self, satir):
        sys.stderr.write("%s %s\n" % (time.strftime("%F %T"), satir))

    def maskele(self, veri):
        for ad, v in self.sirlar.items():
            if len(v) >= 12 and not ad.endswith(("_HOST", "_ID", "_URL", "_ADDRESS")):
                b = v.encode()
                if b in veri:
                    veri = veri.replace(b, b"***")
        return veri

    def _akis_maskele(self, parcalar):
        for p in parcalar:
            yield self.maskele(p)

    @staticmethod
    def _https(host, yontem, yol, basliklar, govde):
        c = http.client.HTTPSConnection(host, 443, timeout=ZAMAN_ASIMI, context=ssl.create_default_context())
        c.request(yontem, yol, body=govde, headers=basliklar)
        r = c.getresponse()
        tur = (r.getheader("Content-Type") or "").lower()
        boy = r.getheader("Content-Length")
        if tur.startswith("text/event-stream") or boy is None or int(boy) > TAMPON_TAVAN:
            def akis():
                try:
                    while True:
                        p = r.read1(65536) if hasattr(r, "read1") else r.read(65536)
                        if not p:
                            break
                        yield p
                finally:
                    c.close()
            return r.status, r.getheaders(), akis()
        veri = r.read()
        c.close()
        return r.status, r.getheaders(), veri

    def isle(self, yontem, yol, gelen_basliklar, govde):
        """→ (durum, başlık listesi, gövde: bytes ya da bytes parçası üreteci)."""
        try:
            servis, host, ust, ekle = self.hedef(yol)
        except KeyError as e:          # (LookupError'ın alt sınıfı — önce o)
            return _hata(503, "sır eksik: %s" % e)
        except LookupError as e:
            d, b, v = _hata(404, "bilinmeyen servis %s" % e)
            return d, b, json.dumps({"vekil": "bilinmeyen servis %s" % e, "servisler": sorted(SERVISLER)}).encode()
        except ValueError as e:
            return _hata(400, "geçersiz %s" % e)
        bas = {}
        for k, v in gelen_basliklar:
            kl = k.lower()
            if kl in _ATLA:
                continue
            if any(kl == e.lower() for e in ekle):
                if v.strip().lower() in YER_TUTUCU:
                    continue          # yer tutucu: kimliği vekil ekler
                ekle = {e: x for e, x in ekle.items() if e.lower() != kl}   # istemcinin kendi geçici token'ı
            bas[k] = v
        bas.update(ekle)
        bas["Host"] = host
        bas.setdefault("User-Agent", "ghost-vekil/2")
        bas["Accept-Encoding"] = "identity"     # maskeleme düz gövde ister
        try:
            govde = self.govde_doldur(servis, govde)
        except KeyError as e:
            return _hata(503, "sır eksik: %s" % e)
        try:
            durum, cevap_bas, veri = self.gonder(host, yontem, ust, bas, govde)
        except Exception as e:
            return _hata(502, "üst sunucu hatası: %s" % type(e).__name__)
        with self.kilit:
            self.sayac[servis] = self.sayac.get(servis, 0) + 1
        cevap_bas = [(k, _cerez_yerel(v) if k.lower() == "set-cookie" else v)
                     for k, v in cevap_bas if k.lower() not in _CEVAP_ATLA]
        if isinstance(veri, (bytes, bytearray)):
            return durum, cevap_bas, self.maskele(bytes(veri))
        return durum, cevap_bas, self._akis_maskele(veri)


class _Isleyici(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ghost-vekil"

    def _kos(self):
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            govde = self._parcali_oku()           # git push büyük paketi parça parça yollar
            if govde is None:
                return self._yaz(*_hata(413, "gövde çok büyük"))
        else:
            n = int(self.headers.get("Content-Length") or 0)
            if n > GOVDE_TAVAN:
                return self._yaz(*_hata(413, "gövde çok büyük"))
            govde = self.rfile.read(n) if n else None
        v = self.server.vekil
        if self.path == "/_durum":
            return self._yaz(200, [("Content-Type", "application/json")], json.dumps(
                {"ok": True, "servisler": sorted(SERVISLER), "sayac": v.sayac,
                 "eksik": sorted(s for s, t in SERVISLER.items() if not _var(t, v.sirlar, v.dizin))}).encode())
        if self.path.startswith("/_jwt/"):
            ad = self.path[6:].split("?")[0]
            if ad != "apns":            # ASC JWT'si dışarı verilmez, yalnız istekte kullanılır
                return self._yaz(*_hata(404, "yalnız /_jwt/apns"))
            try:
                return self._yaz(200, [("Content-Type", "text/plain")], v.jwt("apns").encode())
            except KeyError as e:
                return self._yaz(*_hata(503, "sır eksik: %s" % e))
        durum, bas, veri = v.isle(self.command, self.path, self.headers.items(), govde)
        self._yaz(durum, bas, veri)

    def _parcali_oku(self):
        parca, toplam = [], 0
        while True:
            boy = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
            if boy == 0:
                while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                    pass
                return b"".join(parca)
            toplam += boy
            if toplam > GOVDE_TAVAN:
                return None
            parca.append(self.rfile.read(boy))
            self.rfile.readline()

    def _yaz(self, durum, bas, veri):
        self.send_response(durum)
        for k, v in bas:
            self.send_header(k, v)
        if isinstance(veri, (bytes, bytearray)):
            self.send_header("Content-Length", str(len(veri)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(veri)
            return
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            for p in veri:
                if p:
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(p), p))
                    self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _kos

    def log_message(self, fmt, *a):      # yol günlüğe girer; sorgu dizesi girmez
        sys.stderr.write("%s %s\n" % (time.strftime("%F %T"), (fmt % a).split("?")[0][:200]))


def _var(tanim, sirlar, dizin):
    alanlar = re.findall(r"\{([A-Z_][A-Z0-9_]*)\}", json.dumps(tanim))
    if tanim.get("jwt"):
        iss, kid, dosya, _, _ = JWTLER[tanim["jwt"]]
        alanlar += [iss, kid]
        if not os.path.exists(os.path.join(dizin, dosya)):
            return False
    return all(sirlar.get(a) for a in alanlar)


class Sunucu(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, adres, vekil):
        super().__init__(adres, _Isleyici)
        self.vekil = vekil


# ---------------------------------------------------------------- Gmail (3. aşama)
# SMTP 127.0.0.1:8587 ve IMAP 127.0.0.1:8993 — düz metin YEREL bağlantı, vekil üst sunucuya TLS ile bağlanır
# ve giriş satırına uygulama şifresini KENDİSİ koyar. SMTP'de girişin kullanıcı adı onay kimliğidir: vekil onay
# servisine "bu onay az önce sahibin gerçek kapısından tüketildi mi?" diye sorar; değilse gönderim YOK (mail
# kapısı kod satırı olmaktan çıkıp yapısal olur). 'test-kendine' yalnız kendi adresine, tek alıcı.
SMTP_ADRES = ("127.0.0.1", 8587)
IMAP_ADRES = ("127.0.0.1", 8993)
ONAY_SOKET = "/run/ghost-onay/onay.sock"


def onay_az_once(eid):
    """Onay servisi: eid son 120 sn içinde gerçek kayıttan tüketildi mi? Ulaşılamazsa HAYIR."""
    import socket as _s
    try:
        with _s.socket(_s.AF_UNIX, _s.SOCK_STREAM) as c:
            c.settimeout(3)
            c.connect(ONAY_SOKET)
            c.sendall((json.dumps({"op": "az_once_tuketildi", "eid": eid}) + "\n").encode())
            r = json.loads(c.makefile().readline() or "{}")
        return bool(r.get("evet"))
    except Exception:
        return False


class _Satirlar:
    def __init__(self, sock):
        self.sock, self.f = sock, sock.makefile("rb")

    def oku(self):
        return self.f.readline(65536)

    def yaz(self, b):
        self.sock.sendall(b)


def _smtp_cevap(ust):
    """Çok satırlı SMTP cevabını (250-... 250 ...) topla."""
    out = b""
    while True:
        satir = ust.oku()
        out += satir
        if len(satir) < 4 or satir[3:4] != b"-":
            return out


class _SmtpIsleyici(socketserver.BaseRequestHandler):
    def handle(self):
        import base64
        v = self.server.vekil
        adres, sifre = v.sirlar.get("GMAIL_ADDRESS"), (v.sirlar.get("GMAIL_APP_PASSWORD") or "").replace(" ", "")
        ist = _Satirlar(self.request)
        if not (adres and sifre):
            return ist.yaz(b"421 ghost-vekil: Gmail kimligi yok\r\n")
        try:
            ust = self.server.ust_ac()
        except Exception as e:
            return ist.yaz(("421 ghost-vekil: ust sunucu %s\r\n" % type(e).__name__).encode())
        ist.yaz(b"220 ghost-vekil ESMTP\r\n")
        kip, alicilar, gonderildi = None, [], False
        try:
            while True:
                satir = ist.oku()
                if not satir:
                    return
                k = satir.strip().upper()
                if k.startswith(b"AUTH "):
                    p = satir.strip().split()
                    if len(p) != 3 or p[1].upper() != b"PLAIN":
                        ist.yaz(b"504 ghost-vekil: yalniz AUTH PLAIN\r\n")
                        continue
                    try:
                        kullanici = base64.b64decode(p[2]).split(b"\0")[1].decode()
                    except Exception:
                        ist.yaz(b"501 ghost-vekil: bozuk AUTH\r\n")
                        continue
                    if kullanici == "test-kendine":
                        kip = "test"
                    elif re.fullmatch(r"onay-[0-9a-f]{16}", kullanici) and self.server.onay_sor(kullanici):
                        kip = "onay"
                    else:
                        v._gunluk_yaz("smtp RED %s" % kullanici[:24])
                        ist.yaz(b"535 ghost-vekil: onay yok (sahibin gercek onayi tuketilmemis)\r\n")
                        continue
                    yeni = base64.b64encode(b"\0" + adres.encode() + b"\0" + sifre.encode())
                    ust.yaz(b"AUTH PLAIN " + yeni + b"\r\n")
                    ist.yaz(v.maskele(_smtp_cevap(ust)))
                    continue
                if k.startswith((b"MAIL", b"RCPT", b"DATA")) and not kip:
                    ist.yaz(b"530 ghost-vekil: once AUTH\r\n")
                    continue
                if k.startswith(b"MAIL") and gonderildi:
                    ist.yaz(b"554 ghost-vekil: bir onay = bir mail\r\n")
                    continue
                if k.startswith(b"RCPT"):
                    m = re.search(rb"<([^>]*)>", satir)
                    alici = (m.group(1) if m else b"").decode(errors="replace").lower()
                    if kip == "test" and (alici != adres.lower() or alicilar):
                        ist.yaz(b"550 ghost-vekil: test yalniz kendi adresine\r\n")
                        continue
                    alicilar.append(alici)
                if k == b"DATA":
                    ust.yaz(satir)
                    c = _smtp_cevap(ust)
                    ist.yaz(c)
                    if not c.startswith(b"354"):
                        continue
                    while True:
                        govde = ist.oku()
                        if not govde:
                            return
                        ust.yaz(govde)
                        if govde in (b".\r\n", b".\n"):
                            break
                    c = _smtp_cevap(ust)
                    gonderildi = c.startswith(b"250")
                    v._gunluk_yaz("smtp %s %s alici=%d" % (kip, "GITTI" if gonderildi else "red", len(alicilar)))
                    ist.yaz(c)
                    continue
                ust.yaz(satir)
                ist.yaz(v.maskele(_smtp_cevap(ust)))
                if k == b"QUIT":
                    return
        finally:
            try:
                ust.sock.close()
            except Exception:
                pass


class _ImapIsleyici(socketserver.BaseRequestHandler):
    def handle(self):
        v = self.server.vekil
        adres, sifre = v.sirlar.get("GMAIL_ADDRESS"), (v.sirlar.get("GMAIL_APP_PASSWORD") or "").replace(" ", "")
        ist = _Satirlar(self.request)
        if not (adres and sifre):
            return ist.yaz(b"* BYE ghost-vekil: Gmail kimligi yok\r\n")
        try:
            ust = self.server.ust_ac()
        except Exception as e:
            return ist.yaz(("* BYE ghost-vekil: ust sunucu %s\r\n" % type(e).__name__).encode())
        ist.yaz(ust.oku())                                   # karşılama
        try:
            while True:                                      # giriş öncesi satır kipi
                satir = ist.oku()
                if not satir:
                    return
                p = satir.split(None, 2)
                if len(p) >= 2 and p[1].upper() == b"LOGIN":
                    def q(x):
                        return '"%s"' % x.replace("\\", "\\\\").replace('"', '\\"')
                    ust.yaz(p[0] + (" LOGIN %s %s\r\n" % (q(adres), q(sifre))).encode())
                    break
                if len(p) >= 2 and p[1].upper() == b"AUTHENTICATE":
                    ist.yaz(p[0] + b" NO ghost-vekil: yalniz LOGIN\r\n")
                    continue
                ust.yaz(satir)
                while True:                                  # etiketli cevaba kadar aktar
                    c = ust.oku()
                    ist.yaz(c)
                    if not c or (p and c.startswith(p[0] + b" ")):
                        break
            v._gunluk_yaz("imap giris")
            _cift_yon(self.request, ust.sock, v)
        finally:
            try:
                ust.sock.close()
            except Exception:
                pass


def _cift_yon(a, b, v):
    """Giriş sonrası kör aktarım (iki yön). Üstten gelen veride sır geçerse maskelenir."""
    def tasi(kay, hed, maske):
        try:
            while True:
                d = kay.recv(65536)
                if not d:
                    break
                hed.sendall(v.maskele(d) if maske else d)
        except OSError:
            pass
        finally:
            for x in (kay, hed):
                try:
                    x.shutdown(2)
                except OSError:
                    pass
    t = threading.Thread(target=tasi, args=(b, a, True), daemon=True)
    t.start()
    tasi(a, b, False)
    t.join(5)


def _tls_ac(host, port, starttls=False):
    import socket as _s
    ham = _s.create_connection((host, port), timeout=60)
    bag = ssl.create_default_context()
    if not starttls:
        return _Satirlar(bag.wrap_socket(ham, server_hostname=host))
    d = _Satirlar(ham)
    _smtp_cevap(d)
    d.yaz(b"EHLO ghost-vekil\r\n")
    _smtp_cevap(d)
    d.yaz(b"STARTTLS\r\n")
    if not _smtp_cevap(d).startswith(b"220"):
        raise OSError("STARTTLS")
    t = _Satirlar(bag.wrap_socket(ham, server_hostname=host))
    t.yaz(b"EHLO ghost-vekil\r\n")
    _smtp_cevap(t)
    return t


class AkisSunucu(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, adres, isleyici, vekil, ust_ac, onay_sor=onay_az_once):
        super().__init__(adres, isleyici)
        self.vekil, self.ust_ac, self.onay_sor = vekil, ust_ac, onay_sor


def main(argv):
    os.umask(0o077)
    v = Vekil(sirlari_oku(DURUM_DIZIN))
    if v.sirlar.get("GMAIL_APP_PASSWORD"):
        for adres, isl, ac in ((SMTP_ADRES, _SmtpIsleyici, lambda: _tls_ac("smtp.gmail.com", 587, starttls=True)),
                               (IMAP_ADRES, _ImapIsleyici, lambda: _tls_ac("imap.gmail.com", 993))):
            threading.Thread(target=AkisSunucu(adres, isl, v, ac).serve_forever, daemon=True).start()
    Sunucu(ADRES, v).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
