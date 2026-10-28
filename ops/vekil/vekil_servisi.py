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
OKUMA_ZAMAN_ASIMI = 60                # S9 (Codex, 2026-10-28): istemciden okuma (başlık/gövde) zaman aşımı
ES_ZAMANLI_TAVAN = 48                 # S9: aynı anda işlenen bağlantı tavanı; üstü 503 (yerel istemci yeniden dener)
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
# ---------------------------------------------------------------- izin listesi (Faz 2 devamı, 2026-10-28)
# Vekil kimliği BİZİM: anahtar başka adrese gidemese de aynı servisin başka bir hesabına/sohbetine yazılabilirdi.
# Süzgeç hedefi kendi hesaplarımıza sabitler. OKUMA her yerde serbest (okuma dışarı veri taşımaz); YAZMA yalnız
# bizim hesaplarımızda. Gözetimsiz (özerk) süreçten gelen istek yalnız okuma sınıfına girer.
BIZIM_GITHUB = {"ornek-org", "ghost-bot"}
SENTRY_ORG = "example-org"
WIKIDATA_OGELER = {"Q000000001"}            # bizim öğemiz (sahip.kim SEO)
WIKIDATA_KULLANICI = "User:OrnekBot"
TELEGRAM_SAHIPSIZ = {"getMe", "getUpdates", "getFile", "answerCallbackQuery", "setMyCommands", "getMyCommands",
                     "getWebhookInfo"}
TELEGRAM_BILDIRIM = re.compile(r"^(send[A-Z]\w*|edit[A-Z]\w*|answerCallbackQuery|getMe|getFile|getChat)$")
# 5. tur (N3): chat_id'siz yönteme sahte chat_id=<sahip> eklenince geçiyordu → setWebhook (botun TÜM güncellemeleri dış
# URL'e), logOut/close (botu düşürür). Yöntem BEYAZ listesi: Ghost'un kullandığı mesaj/komut/dosya uçları; kara liste kesin red.
TELEGRAM_RED = re.compile(r"^(setWebhook|logOut|close|leaveChat|promoteChatMember|setChatAdministratorCustomTitle|"
                          r"setMyDefaultAdministratorRights|createChatInviteLink|exportChatInviteLink|editChatInviteLink|"
                          r"revokeChatInviteLink|setChatPermissions|banChatMember|restrictChatMember|setPassportDataErrors)$")
TELEGRAM_IZINLI = re.compile(r"^(send[A-Z]\w*|edit[A-Z]\w*|delete(Message|Messages|Webhook|MyCommands)|forward\w*|copy\w*|"
                             r"pinChatMessage|unpinChatMessage|unpinAllChatMessages|answerCallbackQuery|answerInlineQuery|"
                             r"setMyCommands|getMyCommands|setMessageReaction|setChatMenuButton|getChatMenuButton|setMyName|"
                             r"setMyDescription|setMyShortDescription|stopPoll|stopMessageLiveLocation|get[A-Z]\w*)$")
# 5. tur: yeni kimlik/anahtar ÜRETEN uçlar (yazma sınıfında geçiyordu; Ghost hiçbirini kullanmaz) — sızan bir token
# kendine kalıcı yedek üretemesin
_GH_KIMLIK_URET = re.compile(r"^/(authorizations|applications|user/(keys|gpg_keys|ssh_signing_keys|installations))(/|$)")
_SENTRY_KIMLIK_URET = re.compile(r"/(api-tokens|api-keys|auth-tokens|sentry-apps)(/|$)")
_CF_KIMLIK_URET = re.compile(r"^/client/v4/(user/tokens(?!/verify)|accounts/[^/]+/tokens)(/|$)")
_POSTHOG_KIMLIK_URET = re.compile(r"/(personal_api_keys|organizations/[^/]+/(members|invites))(/|$)")
_GH_OKUMA_POST = re.compile(r"^/(graphql|markdown(/raw)?)$")
_GH_YASAK_ALT = re.compile(r"^/repos/[^/]+/[^/]+/(hooks|keys|transfer|actions/secrets|environments/[^/]+/secrets)(/|$)")
# MCP: beyaz liste — yalnız bu önekler/kalıplar OKUMA; geri kalan HER araç yazma sayılır (kara liste
# run_secret/actions_run_trigger/label_write/projects_write gibi araçları kaçırıyordu, 3. tur bulgu).
_MCP_OKUMA_ARAC = re.compile(r"^(get_|list_|search_|download_|read_|me$)|_read$|^(dependabot_alerts|"
                             r"code_scanning_alert|secret_scanning_alert|notifications|discussions?|"
                             r"issue_read|pull_request_read)\b")
_D1_OKUMA = re.compile(r"^\s*(select|with|explain|pragma\s+table_info)\b", re.I)


def _form(govde, tur, sorgu):
    """Sorgu dizesi + gövdedeki alanlar (form, JSON, multipart) → dict (ilk değer)."""
    import urllib.parse
    alan = {k: v[0] for k, v in urllib.parse.parse_qs(sorgu or "").items()}
    if not govde:
        return alan
    tur = (tur or "").lower()
    metin = govde[:2_000_000].decode("utf-8", "replace")
    if "multipart/" in tur:
        for m in re.finditer(r'name="([^"]+)"[^\r\n]*\r?\n(?:[^\r\n]+\r?\n)*\r?\n([^\r\n]{0,200})', metin):
            alan.setdefault(m.group(1), m.group(2))
        return alan
    if metin.lstrip().startswith(("{", "[")):
        try:
            d = json.loads(metin)
            if isinstance(d, dict):
                for k, v in d.items():
                    alan.setdefault(k, v)
            elif isinstance(d, list):
                alan["_liste"] = d
        except ValueError:
            pass
        return alan
    for k, v in urllib.parse.parse_qs(metin).items():
        alan.setdefault(k, v[0])
    return alan


def _form_hepsi(govde, tur, sorgu, anahtarlar):
    """Verilen anahtarların sorgu+gövdedeki TÜM değerleri (liste). chat_id iki yere konup biri sahip biri yabancı olmasın."""
    import urllib.parse
    out = []
    for kaynak in (urllib.parse.parse_qs(sorgu or ""),):
        for k in anahtarlar:
            out += [str(x) for x in kaynak.get(k, [])]
    if govde:
        metin = govde[:2_000_000].decode("utf-8", "replace")
        if "multipart/" in (tur or "").lower():
            for m in re.finditer(r'name="([^"]+)"[^\r\n]*\r?\n(?:[^\r\n]+\r?\n)*\r?\n([^\r\n]{0,200})', metin):
                if m.group(1) in anahtarlar:
                    out.append(m.group(2))
        elif (tur or "").lower().startswith("application/json") or metin.lstrip().startswith("{"):
            try:
                d = json.loads(metin)
                if isinstance(d, dict):
                    out += [str(d[k]) for k in anahtarlar if k in d and d[k] not in (None, "")]
            except ValueError:
                pass
        else:
            q = urllib.parse.parse_qs(metin)
            for k in anahtarlar:
                out += [str(x) for x in q.get(k, [])]
    return [x for x in out if x not in ("", "None")]


# Mutation/yazma: SQL/GraphQL metninde dize sabitleri ve yorumlar atıldıktan sonra yazma sözcüğü var mı?
_DIZE = re.compile(r'"(?:[^"\\]|\\.)*"' + r"|'(?:[^'\\]|\\.)*'")
_YORUM_GQL = re.compile(r"#[^\n]*")
_YORUM_SQL = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)


def _dize_yorum_at(q, graphql=False):
    """Dize literal'lerini ve yorumları TEK geçişte at (5. tur N2): eski iki aşamalı sürümde `# "` … `# "` arasına yazılan
    mutation dize sanılıp siliniyor, D1'de `-- '` … `-- '` arasındaki DELETE 'okuma' sayılıyordu. Soldan sağa tarama:
    yorum başladıysa satır sonuna kadar her şey yorumdur (içindeki tırnak dize açmaz)."""
    out, i, n = [], 0, len(q)
    while i < n:
        c = q[i]
        if c in "\"'":
            j = i + 1
            while j < n and q[j] != c:
                j += 2 if (q[j] == "\\" and graphql) else 1
            out.append(" ")
            i = j + 1
        elif (graphql and c == "#") or (not graphql and q.startswith("--", i)):
            j = q.find("\n", i)
            i = n if j < 0 else j
        elif not graphql and q.startswith("/*", i):
            j = q.find("*/", i + 2)
            out.append(" ")
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _wikidata_bizim(h):
    h = h.strip()
    oge = re.split(r"[$-]", h, maxsplit=1)[0].upper()          # açıklama GUID'i: Q000000001$...
    return oge in WIKIDATA_OGELER or h == WIKIDATA_KULLANICI or h.startswith(WIKIDATA_KULLANICI + "/")


def _gh_repo(yol):
    m = re.match(r"^/repos/([^/]+)/([^/?]+)", yol)
    return (m.group(1).lower(), m.group(2)) if m else (None, None)


def izin(servis, yontem, yol, govde=None, tur="", sirlar=None):
    """→ (sınıf, neden). sınıf: 'okuma' | 'yazma' | 'red'. yol = vekil sonrası servis yolu ('/repos/...').
    Tanınmayan servis yolu 'yazma' sayılır (o servisin hesabı zaten bizim); red yalnız başka hesaba giden yazma."""
    sirlar = sirlar or {}
    yontem = (yontem or "GET").upper()
    yol_, _, sorgu = (yol or "/").partition("?")
    yol_ = re.sub(r"/{2,}", "/", yol_)
    if ".." in yol_.split("/"):
        return "red", "yol '..' içeremez"
    oku = yontem in ("GET", "HEAD", "OPTIONS")
    if servis == "github":
        if yol_.startswith("/gists"):
            return "red", "gist kapalı (vekil izin listesi)"
        if oku:
            return "okuma", ""
        if _GH_OKUMA_POST.match(yol_):
            q = str(_form(govde, "application/json", "").get("query") or "")
            if yol_ == "/graphql" and re.search(r"\bmutation\b", _dize_yorum_at(q, graphql=True)):
                return "red", "GraphQL mutation kapalı; REST yazma uçlarını kullan"
            return "okuma", ""
        if _GH_KIMLIK_URET.match(yol_):
            return "red", "GitHub kimlik/anahtar üreten uç (token, ssh/gpg anahtarı, uygulama) vekilden kapalı"
        sahip, _ = _gh_repo(yol_)
        if sahip:
            if sahip not in BIZIM_GITHUB:
                return "red", "GitHub yazma yalnız ornek-org/* ve ghost-bot/* repolarına"
            if _GH_YASAK_ALT.match(yol_):
                return "red", "repo hook/deploy key/transfer/secret yazma vekilden kapalı (panelden)"
            m = re.match(r"^/repos/[^/]+/[^/]+/collaborators/([^/]+)", yol_)
            if m and m.group(1).lower() not in BIZIM_GITHUB:
                return "red", "repoya yalnız ornek-org/ghost-bot ortak eklenir"
            if re.fullmatch(r"/repos/[^/]+/[^/]+", yol_) and yontem == "PATCH":
                g = _form(govde, "application/json", "")
                if g.get("private") is False or str(g.get("visibility") or "").lower() == "public":
                    return "red", "repoyu herkese açmak vekilden kapalı (Sahip panelden yapar)"
            return "yazma", ""
        if re.fullmatch(r"/user(/repos|/repository_invitations(/\d+)?)?", yol_):
            return "yazma", ""
        return "red", "GitHub yazma yalnız kendi repolarımıza, /user ve davet kabulüne"
    if servis in ("github-git", "github-git-sahip"):
        m = re.match(r"^/([^/]+)/[^/]+?(?:\.git)?/(info/refs|git-upload-pack|git-receive-pack)$", yol_)
        if not m:
            return ("okuma", "") if oku else ("red", "git: tanınmayan yol")
        alim = m.group(2) == "git-receive-pack" or "service=git-receive-pack" in sorgu
        if not alim:
            return "okuma", ""
        if m.group(1).lower() not in BIZIM_GITHUB:
            return "red", "git push yalnız ornek-org/* ve ghost-bot/* repolarına"
        if servis == "github-git-sahip" and not re.match(r"^/ornek-org/ghost-core(?:\.git)?/", yol_, re.I):
            return "red", "sahibin token'ı yalnız ornek-org/ghost-core push'u için (5. tur)"
        return "yazma", ""
    if servis in ("github-mcp", "github-mcp-yaz"):
        if oku:
            return "okuma", ""
        g = _form(govde, "application/json", "")
        istekler = g.get("_liste") or [g]
        sinif = "okuma"
        for r in istekler:
            if not isinstance(r, dict) or r.get("method") != "tools/call":
                continue
            p = r.get("params") or {}
            ad, arg = str(p.get("name") or ""), p.get("arguments") or {}
            if "gist" in ad:
                return "red", "gist kapalı (vekil izin listesi)"
            if ad and not _MCP_OKUMA_ARAC.match(ad):      # beyaz liste dışı HER araç yazmadır (kara liste kaçıyordu)
                sahip = str(arg.get("owner") or arg.get("organization") or "").lower()
                if ad.startswith("fork_"):
                    sahip = str(arg.get("organization") or "ghost-bot").lower()
                if ad == "create_repository" and not sahip:
                    sahip = "ghost-bot"
                if sahip not in BIZIM_GITHUB:
                    return "red", "GitHub yazma yalnız ornek-org/* ve ghost-bot/* repolarına"
                sinif = "yazma"
        if sinif == "okuma" and servis == "github-mcp-yaz" and yol_.rstrip("/") != "/mcp":
            sinif = "yazma"
        return sinif, ""
    if servis in ("telegram", "telegram-dosya"):
        if servis == "telegram-dosya":
            return ("okuma", "") if oku else ("red", "telegram dosya ucu yalnız indirme")
        yontem_ad = yol_.strip("/").split("/")[-1]
        if TELEGRAM_RED.match(yontem_ad):
            return "red", "Telegram: %s vekilden kapalı (bot yönlendirmesi/oturum/yetki yöntemi)" % yontem_ad
        if not TELEGRAM_IZINLI.match(yontem_ad):
            return "red", "Telegram: '%s' vekil beyaz listesinde değil" % yontem_ad
        sahip = str(sirlar.get("TELEGRAM_OWNER_ID") or "")
        hedefler = _form_hepsi(govde, tur, sorgu, ("chat_id", "from_chat_id", "user_id"))
        if hedefler:
            if not sahip or any(h != sahip for h in hedefler):
                return "red", "Telegram yalnız sahibin sohbetine"
        elif yontem_ad and yontem_ad not in TELEGRAM_SAHIPSIZ:
            return "red", "Telegram: chat_id'siz bu yöntem kapalı"
        if yontem_ad == "getUpdates":
            return "yazma", ""          # güncellemeyi tüketir (köprünün mesajını çalar) — özerkte kapalı
        if TELEGRAM_BILDIRIM.match(yontem_ad):
            return "okuma", ""          # sahibe bildirim: özerkte de serbest (kapıyla aynı)
        return "yazma", ""
    if servis == "cloudflare":
        hesap = str(sirlar.get("CLOUDFLARE_ACCOUNT_ID") or "")
        m = re.match(r"^/client/v4/accounts/([^/]+)", yol_)
        if m and not oku and (not hesap or m.group(1) != hesap):
            return "red", "Cloudflare yazma yalnız kendi hesabımıza"
        if not oku and _CF_KIMLIK_URET.match(yol_):
            return "red", "Cloudflare API token üretme/değiştirme vekilden kapalı (panelden)"
        if oku or yol_ in ("/client/v4/graphql", "/client/v4/user/tokens/verify"):
            return "okuma", ""
        if re.match(r"^/client/v4/accounts/[^/]+/d1/database/[^/]+/(query|raw)$", yol_):
            g = _form(govde, "application/json", "")
            sql = str(g.get("sql") or "")
            temiz = _dize_yorum_at(sql)
            parca = [p for p in temiz.split(";") if p.strip()]
            yazan = re.compile(r"\b(insert|update|delete|replace|drop|alter|create|attach|detach|reindex|vacuum)\b", re.I)
            if parca and all(_D1_OKUMA.match(p) and not yazan.search(p) for p in parca):
                return "okuma", ""
        return "yazma", ""
    if servis == "posthog":
        proje = str(sirlar.get("POSTHOG_PROJECT_ID") or "")
        m = re.match(r"^/api/(?:projects|environments)/([^/]+)", yol_)
        if m and m.group(1) not in ("@current",) and proje and m.group(1) != proje:
            return "red", "PostHog yalnız kendi projemiz"
        if oku or re.match(r"^/api/(?:projects|environments)/[^/]+/query/?$", yol_):
            return "okuma", ""
        if _POSTHOG_KIMLIK_URET.search(yol_):
            return "red", "PostHog anahtar/üye üretme vekilden kapalı"
        return "yazma", ""
    if servis == "sentry":
        m = re.match(r"^/api/0/(?:organizations|projects|teams)/([^/]+)", yol_)
        if m and m.group(1) != SENTRY_ORG:
            return "red", "Sentry yalnız %s kuruluşu" % SENTRY_ORG
        if not oku and _SENTRY_KIMLIK_URET.search(yol_):
            return "red", "Sentry token/anahtar üretme vekilden kapalı"
        return ("okuma", "") if oku else ("yazma", "")
    if servis == "wikidata":
        a = _form(govde, tur, sorgu)
        eylem = str(a.get("action") or "")
        if oku or eylem in ("query", "wbgetentities", "wbsearchentities", "parse", "login", "clientlogin"):
            return ("okuma", "") if eylem not in ("login", "clientlogin") else ("yazma", "")
        if "__WIKIDATA_BOT_PASS__" in (govde or b"").decode("utf-8", "replace") and eylem not in ("login", "clientlogin"):
            return "red", "Wikidata parolası yalnız giriş (login) isteğinde kullanılır"
        hedef = [x for k in ("id", "entity", "title", "ids", "statement", "claim", "guid") if a.get(k)
                 for x in str(a.get(k)).split("|")]
        if not hedef or not all(_wikidata_bizim(h) for h in hedef):
            return "red", "Wikidata yazma yalnız kendi öğemize (%s) ve kullanıcı sayfamıza" % ", ".join(sorted(WIKIDATA_OGELER))
        return "yazma", ""
    return ("okuma", "") if oku else ("yazma", "")    # elevenlabs, asc, higgsfield: anahtar zaten bizim hesap


_ATLA = {"host", "accept-encoding", "connection", "keep-alive", "proxy-authorization", "proxy-connection",
         "te", "trailer", "transfer-encoding", "upgrade", "content-length",
         "x-http-method-override", "x-http-method", "x-method-override"}     # 5. tur: yöntem taklidi üst sunucuya gitmez
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
        # S6 (Codex, 2026-10-28): akan cevapta parça sınırına düşen sır kaçıyordu — en uzun sır eksi bir bayt
        # bir sonraki parçayla birlikte taranır (bkz. _akis_maskele).
        self._kuyruk_boyu = max([len(v.encode()) - 1 for a, v in sirlar.items()
                                 if len(v) >= 12 and not a.endswith(("_HOST", "_ID", "_URL", "_ADDRESS"))] or [0])
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
        """Parça başına maskeleme, iki parçaya bölünen sırrı kaçırıyordu (Codex S6). Son _kuyruk_boyu bayt bir
        sonraki parçayla birlikte taranır; sır satır sonu içermediği için son satır sonuna kadar olan kısım hemen
        verilir (SSE olayları '\n\n' ile biter → gecikmez). maskele() yer tutucuya ('***') tekrar dokunmaz."""
        kuyruk = b""
        for p in parcalar:
            m = self.maskele(kuyruk + p)
            kes = max(m.rfind(b"\n") + 1, len(m) - self._kuyruk_boyu)
            if kes > 0:
                yield m[:kes]
            kuyruk = m[kes:]
        if kuyruk:
            yield kuyruk

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

    def isle(self, yontem, yol, gelen_basliklar, govde, ozerk_mi=None):
        """→ (durum, başlık listesi, gövde: bytes ya da bytes parçası üreteci).
        ozerk_mi: () → True/False/None — bu bağlantı gözetimsiz bir oturumdan mı (None = bilinemedi)."""
        try:
            servis, host, ust, ekle = self.hedef(yol)
        except KeyError as e:          # (LookupError'ın alt sınıfı — önce o)
            return _hata(503, "sır eksik: %s" % e)
        except LookupError as e:
            d, b, v = _hata(404, "bilinmeyen servis %s" % e)
            return d, b, json.dumps({"vekil": "bilinmeyen servis %s" % e, "servisler": sorted(SERVISLER)}).encode()
        except ValueError as e:
            return _hata(400, "geçersiz %s" % e)
        tur = next((v for k, v in gelen_basliklar if k.lower() == "content-type"), "")
        sinif, neden = izin(servis, yontem, "/" + yol.split("/", 2)[2] if yol.count("/") >= 2 else "/",
                            govde, tur, self.sirlar)
        if sinif == "red":
            self._gunluk_yaz("RED %s %s %s: %s" % (servis, yontem, yol.split("?")[0][:120], neden))
            return _hata(403, "izin listesi: %s" % neden)
        if sinif == "yazma" and ozerk_mi is not None:
            oz = ozerk_mi()
            if oz is None:
                self._gunluk_yaz("RED %s %s kimlik bilinemedi" % (servis, yontem))
                return _hata(503, "yazma için oturum kimliği doğrulanamadı (ghost-kimlik düşük mü? "
                                  "systemctl restart ghost-kimlik)")
            if oz:
                self._gunluk_yaz("RED-OZERK %s %s %s" % (servis, yontem, yol.split("?")[0][:120]))
                return _hata(403, "özerk (gözetimsiz) oturumda vekil yalnız okuma uçlarını geçirir; "
                                  "eylemi sahibe öner")
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
    timeout = OKUMA_ZAMAN_ASIMI       # S9: başlık/gövde gelmeyen bağlantı iş parçacığını sonsuza dek tutamaz

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
        uid = self.server.peer_uid(self) if hasattr(self.server, "peer_uid") else None
        if uid is None or uid not in IZINLI_UID:     # 5. tur N8: fail-closed — kimliği okunamayan/yabancı kullanıcıya hiç cevap yok
            v._gunluk_yaz("RED uid=%s %s %s" % (uid, self.command, self.path.split("?")[0][:80]))
            return self._yaz(*_hata(403, "vekil yalnız ghost kullanıcısına"))
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
        durum, bas, veri = v.isle(self.command, self.path, self.headers.items(), govde,
                                  ozerk_mi=lambda: self.server.ozerk_mi_baglanti(self))
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

    def address_string(self):            # Unix soketinde client_address '' — günlük satırı kırılmasın
        return self.client_address[0] if self.client_address else "unix"


def _var(tanim, sirlar, dizin):
    alanlar = re.findall(r"\{([A-Z_][A-Z0-9_]*)\}", json.dumps(tanim))
    if tanim.get("jwt"):
        iss, kid, dosya, _, _ = JWTLER[tanim["jwt"]]
        alanlar += [iss, kid]
        if not os.path.exists(os.path.join(dizin, dosya)):
            return False
    return all(sirlar.get(a) for a in alanlar)


def _izinli_uidler():
    """Vekile bağlanabilen kullanıcılar: ghost (+root). ghost yoksa (CI) bu sürecin kullanıcısı."""
    import pwd
    try:
        return {pwd.getpwnam("ghost").pw_uid, 0}
    except KeyError:
        return {os.getuid(), 0}


IZINLI_UID = _izinli_uidler()


def _tcp_peer_uid(port, hedef, metin=None):
    """127.0.0.1:<port> → 127.0.0.1:<hedef> bağlantısının sahibi (uid) — /proc/net/tcp herkese okunur, kimlik servisi
    gerekmez. Bulunamazsa None (5. tur N8: okuma yolu ve IMAP her yerel kullanıcıya açıktı; nginx/www-data ele geçirilse
    Gmail + okuma API'leri açılırdı)."""
    try:
        if metin is None:
            with open("/proc/net/tcp", encoding="ascii") as f:
                metin = f.read()
    except OSError:
        return None
    for satir in metin.splitlines()[1:]:
        p = satir.split()
        if len(p) < 8:
            continue
        try:
            yerel_port, uzak_port = int(p[1].split(":")[1], 16), int(p[2].split(":")[1], 16)
        except (IndexError, ValueError):
            continue
        if yerel_port == port and uzak_port == hedef:
            try:
                return int(p[7])
            except ValueError:
                return None
    return None


class Sunucu(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, adres, vekil, kimlik=None, uid_bul=None, tavan=ES_ZAMANLI_TAVAN):
        super().__init__(adres, _Isleyici)
        self.vekil = vekil
        self.kimlik = kimlik or kimlik_sor
        self.uid_bul = uid_bul or _tcp_peer_uid
        self._es = threading.BoundedSemaphore(max(1, int(tavan)))

    def process_request(self, request, client_address):
        # S9: sınırsız iş parçacığı yerine tavan — dolunca 503, bağlantı kapanır (okuma/yazma yolu etkilenmez)
        if not self._es.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._es.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._es.release()

    def ozerk_mi(self, port):
        return self.kimlik(port, self.server_address[1])

    def ozerk_mi_baglanti(self, isleyici):
        return self.ozerk_mi(isleyici.client_address[1])

    def peer_uid(self, isleyici):
        return self.uid_bul(isleyici.client_address[1], self.server_address[1])


# ---------------------------------------------------------------- Mac vekil tüneli (Unix soketi)
# Mac'teki ai.jarvis.vekiltunel `ssh -L 127.0.0.1:8797:/run/ghost-vekil/mac.sock` ile buraya bağlanır. TCP'de
# sshd-session süreci kendini dökülemez (non-dumpable) yaptığı için kimlik servisi bağlantının sahibini göremiyordu
# (yazma 503). Unix sokette karşı ucun PID'i SO_PEERCRED ile doğrudan gelir ve ssh zinciri doğrulanır:
#   P (ghost, sshd-session) → Q (root, sshd-session, ayrıcalık ayırma) → ssh.service dinleyicisi.
# Bu zinciri ghost kullanıcısındaki bir süreç (model) taklit edemez: ebeveyni root bir sshd-session olamaz.
# Zincir tutmazsa yazma reddedilir (okuma her zaman serbest) — fail-closed.
MAC_SOKET = "/run/ghost-vekil/mac.sock"
SSH_CGROUP = "/sys/fs/cgroup/system.slice/ssh.service/cgroup.procs"


def _proc_bilgi(pid, proc="/proc"):
    """→ (ppid, uid, comm) ya da None."""
    try:
        with open("%s/%d/stat" % (proc, pid), "rb") as f:
            st = f.read()
        ppid = int(st[st.rindex(b")") + 2:].split()[1])
        with open("%s/%d/status" % (proc, pid), encoding="ascii", errors="replace") as f:
            uid = next(int(x.split()[1]) for x in f if x.startswith("Uid:"))
        with open("%s/%d/comm" % (proc, pid), encoding="utf-8", errors="replace") as f:
            comm = f.read().strip()
        return ppid, uid, comm
    except (OSError, ValueError, StopIteration, IndexError):
        return None


def mac_ssh_mi(pid, proc="/proc", ssh_cgroup=SSH_CGROUP):
    """Bağlantı Mac'in ssh oturumundan mı? → False (gözetimli, yazma serbest) ya da None (bilinemedi, yazma yok)."""
    p = _proc_bilgi(pid, proc)
    if not p or p[1] == 0 or p[2] not in ("sshd-session", "sshd"):
        return None
    q = _proc_bilgi(p[0], proc)
    if not q or q[1] != 0 or q[2] not in ("sshd-session", "sshd"):
        return None
    try:
        with open(ssh_cgroup, encoding="ascii") as f:
            dinleyici = set(f.read().split())
    except OSError:
        return None
    return False if str(q[0]) in dinleyici else None


class UnixSunucu(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """Aynı HTTP işleyicisi; kimlik sorusu SO_PEERCRED + ssh zinciriyle (mac_ssh_mi) cevaplanır."""
    daemon_threads = True

    def __init__(self, yol, vekil, dogrula=None):
        try:
            os.unlink(yol)
        except FileNotFoundError:
            pass
        super().__init__(yol, _Isleyici)
        os.chmod(yol, 0o666)              # bağlanan herkes; yazma yetkisi PID zincirinden
        self.vekil = vekil
        self.dogrula = dogrula or mac_ssh_mi

    def ozerk_mi_baglanti(self, isleyici):
        import socket as _s
        import struct as _st
        try:
            pid = _st.unpack("3i", isleyici.request.getsockopt(_s.SOL_SOCKET, _s.SO_PEERCRED,
                                                               _st.calcsize("3i")))[0]
        except OSError:
            return None
        return self.dogrula(pid)

    def peer_uid(self, isleyici):
        import socket as _s
        import struct as _st
        try:
            return _st.unpack("3i", isleyici.request.getsockopt(_s.SOL_SOCKET, _s.SO_PEERCRED, _st.calcsize("3i")))[1]
        except OSError:
            return None


KIMLIK_SOKET = "/run/ghost-kimlik/kimlik.sock"
KIMLIK_CGROUP = "/sys/fs/cgroup/system.slice/ghost-kimlik.service/cgroup.procs"


def _kimlik_servisi_mi(pid):
    """Karşı uç gerçekten ghost-kimlik.service mi? Sistem biriminin cgroup'u root'undur; ghost oraya süreç koyamaz."""
    try:
        with open(KIMLIK_CGROUP, encoding="ascii") as f:
            return str(pid) in f.read().split()
    except OSError:
        return False


def kimlik_sor(port, hedef=8797):
    """ghost-kimlik'e sor: bu bağlantı özerk oturumdan mı? → True/False/None (bilinemedi = yazma reddedilir).
    Soket ghost'un dizininde durduğu için karşı tarafın gerçekten servis olduğu SO_PEERCRED pid ile doğrulanır."""
    import socket as _s
    import struct as _st
    try:
        with _s.socket(_s.AF_UNIX, _s.SOCK_STREAM) as c:
            c.settimeout(3)
            c.connect(KIMLIK_SOKET)
            pid = _st.unpack("3i", c.getsockopt(_s.SOL_SOCKET, _s.SO_PEERCRED, _st.calcsize("3i")))[0]
            if not _kimlik_servisi_mi(pid):
                return None
            c.sendall((json.dumps({"port": port, "hedef": hedef}) + "\n").encode())
            r = json.loads(c.makefile().readline() or "{}")
            return r.get("ozerk") if r.get("ok") else None
    except Exception:
        return None


# ---------------------------------------------------------------- Gmail (3. aşama)
# SMTP 127.0.0.1:8587 ve IMAP 127.0.0.1:8993 — düz metin YEREL bağlantı, vekil üst sunucuya TLS ile bağlanır
# ve giriş satırına uygulama şifresini KENDİSİ koyar. SMTP'de girişin kullanıcı adı onay kimliğidir: vekil onay
# servisinden kartı alır, gelen maili kartla karşılaştırır ve onayı KENDİSİ tüketir (_SmtpIsleyici notu); değilse
# gönderim YOK. 'test-kendine' yalnız kendi adresine, tek alıcı.
SMTP_ADRES = ("127.0.0.1", 8587)
IMAP_ADRES = ("127.0.0.1", 8993)
ONAY_SOKET = "/run/ghost-onay/onay.sock"


def onay_cagir(op, **kw):
    """Onay servisine tek satır istek. Ulaşılamazsa {} (fail-closed: onay yok sayılır)."""
    import socket as _s
    try:
        with _s.socket(_s.AF_UNIX, _s.SOCK_STREAM) as c:
            c.settimeout(3)
            c.connect(ONAY_SOKET)
            c.sendall((json.dumps(dict(kw, op=op)) + "\n").encode())
            r = json.loads(c.makefile().readline() or "{}")
        return r if isinstance(r, dict) else {}
    except Exception:
        return {}


SMTP_GUNLUK_TAVAN = 5        # gmail_gonder GUNLUK_TAVAN ile aynı; burada atlanamaz
SMTP_DATA_TAVAN = 35 * 1024 * 1024


def mail_karti_coz(onizleme):
    """gmail_gonder._onizleme biçimi → dict (kime, cc, konu, ekler[(ad, bayt, sha12)], govde) ya da None."""
    m = re.match(r"Kime: ([^\n]*)\n(?:Cc: ([^\n]*)\n)?Konu: ([^\n]*)\nEkler:\n((?:  [^\n]*\n)*?)\n(.*)\Z",
                 onizleme or "", re.S)
    if not m:
        return None
    ekler = []
    for satir in m.group(4).splitlines():
        e = re.match(r"  • (.+) \((\d+) bayt, sha256 ([0-9a-f]{12})\)$", satir)
        if e:
            ekler.append((e.group(1), int(e.group(2)), e.group(3)))
        elif satir.strip() != "(yok)":
            return None
    return {"kime": m.group(1).strip(), "cc": (m.group(2) or "").strip() or None, "konu": m.group(3),
            "ekler": ekler, "govde": m.group(5)}


def mail_kartla_eslesir(kart, ham, alicilar, gonderen):
    """Gönderilmek üzere gelen mail sahibin onay kartında gördüğüyle AYNI mı? → (bool, neden)."""
    import email
    import email.policy
    import hashlib
    try:
        msg = email.message_from_bytes(ham, policy=email.policy.default)
    except Exception:
        return False, "mail çözülemedi"
    adr = lambda h: [a.addr_spec.lower() for a in (msg[h].addresses if msg[h] else ())]  # noqa: E731
    if adr("To") != [kart["kime"].lower()] or adr("Cc") != ([kart["cc"].lower()] if kart["cc"] else []) or adr("Bcc"):
        return False, "alıcı başlığı kartla aynı değil"
    if sorted(alicilar) != sorted(x.lower() for x in [kart["kime"]] + ([kart["cc"]] if kart["cc"] else [])):
        return False, "zarf alıcıları kartla aynı değil"
    if adr("From") != [gonderen.lower()]:
        return False, "gönderen sahibin adresi değil"
    if str(msg["Subject"] or "") != kart["konu"]:
        return False, "konu kartla aynı değil"
    govde = msg.get_body(preferencelist=("plain",))
    metin = govde.get_content() if govde is not None else ""
    if metin.replace("\r\n", "\n").rstrip("\n") != kart["govde"].replace("\r\n", "\n").rstrip("\n"):
        return False, "gövde kartla aynı değil"
    ekler, ek_parcalar = [], []
    for p in msg.iter_attachments():
        veri = p.get_payload(decode=True) or b""
        ekler.append((p.get_filename() or "", len(veri), hashlib.sha256(veri).hexdigest()[:12]))
        ek_parcalar.append(p)
    if ekler != kart["ekler"]:
        return False, "ekler kartla aynı değil"
    # 2./4. tur: gövde ve kartta sayılan ekler dışında HİÇBİR parça olmasın — dosya adı taşıyan ama ek kümesinde
    # olmayan text/html (alternative içinde) dahil. Parça kimliğiyle (is) karşılaştırılır, dosya adıyla değil.
    for p in msg.walk():
        if p is msg or p.get_content_maintype() == "multipart" or p is govde:
            continue
        if any(p is e for e in ek_parcalar):
            continue
        return False, "kartta olmayan gövde parçası (ör. html alternatifi)"
    # Tekil başlıklar: ikinci bir Subject/To/From başlığı alıcı tarafında farklı görünür.
    for ad in ("From", "To", "Cc", "Subject", "Reply-To"):
        if len(msg.get_all(ad, [])) > 1:
            return False, "başlık birden çok: %s" % ad
    # From görünen adı: yalnız sahibin adı ya da adsız (ör. "Apple Destek <adres>" taklidi olmasın)
    gorunen = (msg["From"].addresses[0].display_name if msg["From"] and msg["From"].addresses else "").strip()
    if gorunen not in ("", "Sahip"):
        return False, "gönderen görünen adı kartla aynı değil"
    # Başlıklar: yalnız bilinen küme. gmail_gonder._mesaj'ın ürettiği başlıklar + MIME zorunluları.
    izinli_bas = {"from", "to", "cc", "subject", "reply-to", "mime-version", "content-type",
                  "content-transfer-encoding", "content-disposition", "date", "message-id", "user-agent"}
    for ad in msg.keys():
        if ad.lower() not in izinli_bas:
            return False, "kartta olmayan başlık: %s" % ad[:40]
    if [h.strip().lower() for h in msg.get_all("Reply-To", [])] not in ([], ["sahip@example.com"]):
        return False, "Reply-To kartla aynı değil"
    return True, ""


def _smtp_sayac(dizin, artir=False):
    yol = os.path.join(dizin, "smtp_gunluk.json")
    gun = time.strftime("%F")
    try:
        with open(yol, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    n = d.get("n", 0) if d.get("gun") == gun else 0
    if artir:
        n += 1
        try:
            with open(yol + ".yeni", "w", encoding="utf-8") as f:
                json.dump({"gun": gun, "n": n}, f)
            os.replace(yol + ".yeni", yol)
        except OSError:
            pass
    return n


def _sahibe_yaz(v, metin):
    import urllib.parse
    sahip = v.sirlar.get("TELEGRAM_OWNER_ID")
    if not sahip:
        return
    try:
        v.isle("POST", "/telegram/sendMessage", [("Content-Type", "application/x-www-form-urlencoded")],
               urllib.parse.urlencode({"chat_id": sahip, "text": metin}).encode())
    except Exception:
        pass


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
    """Onay İŞE bağlı (2026-10-28, 2. tur bulgu 1): AUTH kullanıcı adı onay kimliğidir; vekil kartı onay servisinden
    alır (tür 'mail', geçerli), gelen maili (zarf + başlık + gövde + ekler) sahibin kartta GÖRDÜĞÜ önizlemeyle
    kendisi karşılaştırır, eşleşirse onayı KENDİSİ tüketir ve ancak o zaman üst sunucuya verir. Günlük tavan ve
    "mail gitti" bildirimi de burada — istemci atlayamaz."""

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
        kip, eid, kart, alicilar, gonderildi = None, None, None, [], False
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
                    elif re.fullmatch(r"onay-[0-9a-f]{16}", kullanici):
                        r = self.server.onay("onizleme", eid=kullanici)
                        kart = mail_karti_coz(r.get("onizleme")) if (
                            r.get("gecerli") and r.get("tur") == "mail" and not r.get("kirpildi")) else None
                        if not kart:
                            v._gunluk_yaz("smtp RED %s (geçerli mail kartı yok)" % kullanici[:24])
                            ist.yaz(b"535 ghost-vekil: gecerli mail onayi yok (sahibin onayi bu maile ait degil)\r\n")
                            continue
                        kip, eid, kart["sha"] = "onay", kullanici, r.get("sha")
                    else:
                        v._gunluk_yaz("smtp RED %s" % kullanici[:24])
                        ist.yaz(b"535 ghost-vekil: onay yok\r\n")
                        continue
                    yeni = base64.b64encode(b"\0" + adres.encode() + b"\0" + sifre.encode())
                    ust.yaz(b"AUTH PLAIN " + yeni + b"\r\n")
                    ist.yaz(v.maskele(_smtp_cevap(ust)))
                    continue
                if k.startswith((b"MAIL", b"RCPT", b"DATA", b"BDAT")) and not kip:
                    ist.yaz(b"530 ghost-vekil: once AUTH\r\n")
                    continue
                if k.startswith(b"BDAT"):
                    ist.yaz(b"502 ghost-vekil: BDAT yok, DATA kullan\r\n")
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
                    ist.yaz(b"354 ghost-vekil: govdeyi gonder, nokta ile bitir\r\n")
                    parca, boy = [], 0
                    while True:
                        govde = ist.oku()
                        if not govde:
                            return
                        if govde in (b".\r\n", b".\n"):
                            break
                        boy += len(govde)
                        if boy > SMTP_DATA_TAVAN:
                            ist.yaz(b"552 ghost-vekil: mail cok buyuk\r\n")
                            return
                        parca.append(govde)
                    ham = b"".join(x[1:] if x.startswith(b"..") else x for x in parca)
                    if kip == "onay":
                        ok, neden = mail_kartla_eslesir(kart, ham, alicilar, adres)
                        if not ok:
                            v._gunluk_yaz("smtp RED %s: %s" % (eid, neden))
                            ist.yaz(("554 ghost-vekil: %s - gonderilmedi\r\n" % neden).encode())
                            continue
                        if _smtp_sayac(v.dizin) >= SMTP_GUNLUK_TAVAN:
                            _sahibe_yaz(v, "⚠️ Mail gönderimi DURDU: bugünkü tavan (%d) doldu. Alıcı: %s | Konu: %s"
                                        % (SMTP_GUNLUK_TAVAN, kart["kime"], kart["konu"][:80]))
                            ist.yaz(b"554 ghost-vekil: gunluk tavan doldu\r\n")
                            continue
                        r = self.server.onay("tuket", eid=eid, onizleme_sha=kart["sha"], tur="mail")
                        if not r.get("tuketildi"):
                            v._gunluk_yaz("smtp RED %s: tüketilemedi (%s)" % (eid, r.get("neden")))
                            ist.yaz(b"554 ghost-vekil: onay tuketilemedi\r\n")
                            continue
                    ust.yaz(b"DATA\r\n")
                    c = _smtp_cevap(ust)
                    if not c.startswith(b"354"):
                        ist.yaz(c)
                        continue
                    for x in parca:
                        ust.yaz(x)
                    ust.yaz(b".\r\n")
                    c = _smtp_cevap(ust)
                    gonderildi = c.startswith(b"250")
                    v._gunluk_yaz("smtp %s %s alici=%d" % (kip, "GITTI" if gonderildi else "red", len(alicilar)))
                    if gonderildi and kip == "onay":
                        _smtp_sayac(v.dizin, artir=True)
                        _sahibe_yaz(v, "📧 Gmail'inden mail GİTTİ → %s%s | Konu: %s | ek: %d | onay: %s\n"
                                       "Sen onaylamadıysan hemen söyle." % (
                                           kart["kime"], (" (cc %s)" % kart["cc"]) if kart["cc"] else "",
                                           kart["konu"][:80], len(kart["ekler"]), eid))
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
        uid = _tcp_peer_uid(self.client_address[1], self.server.server_address[1]) if self.server.server_address[0] else None
        if self.server.server_address[0] and (uid is None or uid not in IZINLI_UID):        # 5. tur N8
            v._gunluk_yaz("imap RED uid=%s" % uid)
            return ist.yaz(b"* BYE ghost-vekil: yalniz ghost kullanicisi\r\n")
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
            _imap_aktar(self.request, ust.sock, v)
        finally:
            try:
                ust.sock.close()
            except Exception:
                pass


# 5. tur N9: giriş sonrası aktarım KÖR'dü — STORE/EXPUNGE/APPEND/COPY/MOVE/CREATE/DELETE üst sunucuya gidiyordu;
# "salt-okunur" yalnız gmail.py'nin readonly/PEEK iyi niyetine bağlıydı. İstemci→üst yönünde yalnız bu komutlar:
IMAP_SALT_OKUNUR = re.compile(rb"^(?:CAPABILITY|NOOP|LOGOUT|SELECT|EXAMINE|LIST|LSUB|XLIST|STATUS|FETCH|SEARCH|ESEARCH|THREAD|"
                              rb"SORT|IDLE|ID|ENABLE|NAMESPACE|CHECK|CLOSE|UNSELECT|GETQUOTA|GETQUOTAROOT|MYRIGHTS|"
                              rb"UID\s+(?:FETCH|SEARCH|THREAD|SORT))(?:\s|\r|\n|$)", re.I)
_IMAP_LITERAL = re.compile(rb"\{(\d+)(\+?)\}\r?\n$")


def _imap_aktar(ist, ust, v):
    """Giriş sonrası: üst→istemci ham (maskeli); istemci→üst yalnız salt-okunur komutlar. Yazma komutuna `tag NO`
    verilir, üst sunucu onu hiç görmez. Literal ({n}) gövdesi ve etiketsiz devam satırı komutun sınıfını taşır."""
    def ustten():
        try:
            while True:
                d = ust.recv(65536)
                if not d:
                    break
                ist.sendall(v.maskele(d))
        except OSError:
            pass
        finally:
            for x in (ist, ust):
                try:
                    x.shutdown(2)
                except OSError:
                    pass
    t = threading.Thread(target=ustten, daemon=True)
    t.start()
    tampon, kalan, aktar, devam = b"", 0, True, False
    try:
        while True:
            d = ist.recv(65536)
            if not d:
                break
            tampon += d
            while tampon:
                if kalan:                                   # literal gövdesi: izinli komutunsa üste, değilse çöpe
                    n = min(kalan, len(tampon))
                    parca, tampon, kalan = tampon[:n], tampon[n:], kalan - n
                    if aktar:
                        ust.sendall(parca)
                    if not kalan:
                        devam = True
                    continue
                i = tampon.find(b"\n")
                if i < 0:
                    break
                satir, tampon = tampon[:i + 1], tampon[i + 1:]
                m = _IMAP_LITERAL.search(satir)
                if devam:                                   # literal sonrası etiketsiz devam satırı
                    devam = False
                    if aktar:
                        ust.sendall(satir)
                    if m:
                        kalan = int(m.group(1))
                    continue
                p = satir.split(None, 1)
                etiket, komut = (p[0] if p else b""), (p[1] if len(p) > 1 else b"")
                if satir.strip().upper() == b"DONE" or IMAP_SALT_OKUNUR.match(komut):
                    aktar = True
                    ust.sendall(satir)
                else:
                    aktar = False
                    v._gunluk_yaz("imap RED %s" % komut.split()[:1])
                    ist.sendall(etiket + b" NO ghost-vekil: salt-okunur aktarici, bu komut kapali\r\n")
                if m:
                    kalan = int(m.group(1))
                    if not aktar and not m.group(2):        # eşzamanlı literal: istemci '+' bekliyordu, NO aldı → göndermez
                        kalan = 0
    except OSError:
        pass
    finally:
        for x in (ist, ust):
            try:
                x.shutdown(2)
            except OSError:
                pass
    t.join(5)


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

    def __init__(self, adres, isleyici, vekil, ust_ac, onay=onay_cagir):
        super().__init__(adres, isleyici)
        self.vekil, self.ust_ac, self.onay = vekil, ust_ac, onay


def main(argv):
    os.umask(0o077)
    v = Vekil(sirlari_oku(DURUM_DIZIN))
    if v.sirlar.get("GMAIL_APP_PASSWORD"):
        for adres, isl, ac in ((SMTP_ADRES, _SmtpIsleyici, lambda: _tls_ac("smtp.gmail.com", 587, starttls=True)),
                               (IMAP_ADRES, _ImapIsleyici, lambda: _tls_ac("imap.gmail.com", 993))):
            threading.Thread(target=AkisSunucu(adres, isl, v, ac).serve_forever, daemon=True).start()
    try:
        threading.Thread(target=UnixSunucu(MAC_SOKET, v).serve_forever, daemon=True).start()
    except OSError as e:
        sys.stderr.write("mac soketi açılamadı: %s\n" % e)
    Sunucu(ADRES, v).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
