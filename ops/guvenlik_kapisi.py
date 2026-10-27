#!/usr/bin/env python3
"""Ghost güvenlik kapısı — Claude Code PreToolUse kancası (2026-10-27 güvenlik denetimi, madde 1).

Sohbet ve iş oturumları secrets.env'in tamamını ortamda görüyor; anahtarları oturumdan tamamen
ayırmak Ghost'un günlük işini (curl+token) değiştirirdi. Bu kanca onun yerine HARNESS
katmanında, modelin kapatamayacağı bir filtre koyar. Engellediği şeyler:

  1. Sır dosyalarını okumak/basmak/kopyalamak (secrets.env, .env, ~/.secrets/, *.p8, ssh anahtarı).
     Eklemek (>>) serbest; anahtarı kullanmak için $DEGISKEN zaten ortamda.
  2. Ortamı topluca dökmek (env, printenv, export -p, /proc/*/environ ...).
  3. Bir sır değişkenini ($CLOUDFLARE_API_TOKEN gibi) izinli listede olmayan bir adrese göndermek.
  4. Gmail şifresini ya da SMTP'yi ops/gmail_gonder.py dışında kullanmak (onay kapısını atlamak).
  5. Bu kancayı ya da ayar dosyalarını değiştirmek — yalnız sahibin GERÇEK onayıyla
     (komutta geçerli bir 'onay-<16 hex>' kimliği; ghost/sahne.py GERÇEK ONAY KAYDI).
  7. (2. tur, 2026-10-27 gece) Bir sırrın DÜZ DEĞERİ hiçbir araç girdisinde (Bash, Write/Edit,
     MCP — github-yaz, playwright —, WebFetch) geçemez: ham, URL-kodlu, base64, hex, ters hâli.
     Değişken zinciri (X=$TOKEN), python/node'un ortamdan okuduğu sır, /proc/*/environ, Mac'e
     ya da uzak kabuğa giden komutun içi, git kapı atlatma (--no-verify, hooksPath, add -f sır)
     ve VPS root anahtarı (ghost_vps) da izlenir.

Sezgiseldir: kararlı bir saldırgan gizleyerek aşabilir. Amaç, güvenilmez metnin (mail, sayfa,
belge) içindeki düz talimatın tek adımda sır sızdırmasını kesmek. Kapı hata verirse İZİN VERİR
(oturum kilitlenmesin) ve hatayı ops/guvenlik_kapisi.log'a yazar.

Girdi: stdin JSON {tool_name, tool_input}. Çıktı: engelde permissionDecision=deny JSON.
"""
import json
import os
import re
import sys
import time

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if not os.path.isdir(os.path.join(KOK, "ghost")):     # kök sahipli kopya (/usr/local/lib/ghost)
    KOK = os.path.join(os.path.expanduser("~"), "jarvis")
EV = os.path.expanduser("~")
LOG = os.path.join(os.environ.get("GHOST_OPS") or os.path.join(KOK, "ops"), "guvenlik_kapisi.log")

# --- 1. sır dosyaları -------------------------------------------------------------
# Komut parçalara (; && || | satır) bölünür; bir parçada sır yolu, OKUYAN bir fiilin argümanıysa
# engellenir. Serbest: dosyayı ortama yüklemek (. secrets.env / source — değerler zaten ortamda),
# sona eklemek (>>), ssh/rsync/scp'ye kimlik olarak vermek (-i ~/.ssh/anahtar).
SIR_DOSYA = re.compile(
    r"(secrets\.env|(^|[\s/'\"=])\.env(?![\w.-])|\.secrets/|\.p8\b|\.ssh/(id_|ghost_)[\w.-]*|"
    r"\.git-credentials|/\.netrc\b)", re.I)
OKUYAN = re.compile(
    r"^(sudo\s+)?(cat|less|more|head|tail|grep|egrep|fgrep|rg|sed|awk|cut|strings|base64|xxd|od|"
    r"hexdump|cp|tar|zip|gzip|bzip2|curl|wget|nc|tee|diff|cmp|openssl|gpg|sort|uniq|tr|jq|vi|vim|"
    r"nano|view|bat|nl|rev|fold|paste|column|split|dd|install|mail|ncat|socat)\b")
YUKLEME = re.compile(r"^(set\s+-a\s*)?(\.|source)\s+\S*(secrets\.env|\.env)(\s+2>\s*/dev/null)?\s*$")
PY_OKUMA = re.compile(r"(open\(|read_text|read_bytes|readlines|load_dotenv|dotenv_values|"
                      r"fs\.readFile|File\.read)[^\n]{0,80}(secrets\.env|\.secrets/|\.p8)", re.I)
PARCA = re.compile(r"\s*(?:;|&&|\|\||\||\n|\$\(|`)\s*")


YAKALAMA = re.compile(r"\b([A-Za-z_]\w*)=[\"']?\$\(((?:[^()]|\([^()]*\))*)\)[\"']?")


def _yakalananlar(komut):
    """VAR=$(… secrets.env …) — değer ekrana basılmaz, değişkene alınır. Serbest; ama VAR artık
    sır sayılır (ağ kuralı onu da izler). Döndürür: (yakalama gövdeleri çıkarılmış komut, {VAR})."""
    adlar = set()

    def degistir(m):
        if SIR_DOSYA.search(m.group(2)) and not PY_OKUMA.search(m.group(2)):
            adlar.add(m.group(1))
            return "%s=__SIR__" % m.group(1)
        return m.group(0)
    return YAKALAMA.sub(degistir, komut), adlar


def _sir_okuma(komut):
    komut, _ = _yakalananlar(komut)
    if PY_OKUMA.search(komut):
        return True
    for parca in PARCA.split(komut):
        parca = re.sub(r"^(\w+=\S*\s+)+", "", parca.strip())       # VAR=x komut
        parca = re.sub(r"^(cd\s+\S+|exec|time|nohup|env\s+-i)\s*", "", parca)
        if re.match(r"^(grep|egrep|fgrep|rg)\b", parca):
            parca = re.sub(r"('[^']*'|\"[^\"]*\")", "", parca, count=1)      # desen
        parca = re.sub(r"\S+\.pub\b", "", parca)                               # açık anahtar
        if not parca or not SIR_DOSYA.search(parca) or YUKLEME.match(parca):
            continue
        if re.match(r"^(echo|printf)\b[^<]*>>\s*\S*secrets\.env\s*$", parca):
            continue
        if re.match(r"^grep\s+(-[A-Za-z]*[cqlL][A-Za-z]*\s+)", parca) and not re.search(r"\s-[A-Za-z]*[oA-CN]", parca):
            continue                                   # grep -c/-q/-l: değer basmaz (anahtar var mı?)
        if OKUYAN.match(parca):
            return True
        if re.search(r"<\s*\S*(secrets\.env|\.secrets/|\.p8)", parca):          # cmd < secrets.env
            return True
        if re.match(r"^(scp|rsync)\b", parca) and re.search(r"(secrets\.env|\.secrets/|\.p8)", parca):
            return True
    return False


# --- 2. ortam dökümü ---------------------------------------------------------------
ORTAM_DOKUM = re.compile(
    r"(^|[;&|(`\n]\s*|\$\(\s*)(env|printenv|export\s+-p|declare\s+-[xp]|set)\s*($|[;&|)>`\n])|"
    r"(^|[;&|(`\n]\s*)printenv\s+\w|/proc/\S*environ|"
    r"(print|dumps|write|pprint|console\.log)\([^)\n]*\b(os\.environ|process\.env)\b(?!\s*[.\[])", re.M)

# --- 3. sır değişkeni + izinsiz adres ------------------------------------------------
IZINLI_ALAN = (
    "api.cloudflare.com", "posthog.com", "api.github.com", "github.com", "githubusercontent.com",
    "api.elevenlabs.io", "api.appstoreconnect.apple.com", "appstoreconnect.apple.com",
    "higgsfield.ai", "wikidata.org", "api.telegram.org", "ntfy.sh", "api.anthropic.com",
    "sentry.io", "localhost", "127.0.0.1",
)
URL = re.compile(r"\b(?:https?|wss?|ftp)://([^/\s'\"`:?#]+)", re.I)
PARCA_AG = re.compile(r"\s*(?:;|&&|\|\||\n)\s*")      # boru (|) bölmez: curl … | jq tek iş
AG_ARACI = re.compile(r"(?<![./~-])\b(curl|wget|nc|ncat|socat|telnet|ssh|scp|sftp|rsync|ftp|"
                      r"urllib|requests|http\.client|fetch\(|axios|websocat)\b")

# --- 4. SMTP / Gmail şifresi -----------------------------------------------------------
SMTP_KULLANIM = re.compile(r"\$\{?GMAIL_APP_PASSWORD\b|smtplib[\s\S]{0,800}(\.login\(|send_message|sendmail\()|"
                           r"\b(swaks|msmtp|sendmail)\s+-", re.I)

# --- 6. kişisel veri dışarı gitmez (2026-10-27, Sahip: "kişisel veri tarafında da güvenli mi") ----
# Kişisel kaynak (Mac veri okuyucusu, Gmail, arşiv arama, ChatGPT geçmişi, para/sağlık/tüketim,
# ~/.ghost altındaki ham veri, e-posta spool'u, hafıza DB'si) ile izinsiz adrese giden ağ komutu
# AYNI komutta olamaz. Ayrıca dosya YÜKLEME (-F/-T/--data-binary @/--upload-file, nc/scp/rsync uzak)
# izinsiz adrese yapılamaz — kişisel veri önce dosyaya yazılıp sonra ayrı komutla gönderilmesin.
# sahibe iletmek (send_telegram/send_photo/send_document, Kokpit, Masa) serbest: o betikler URL taşımaz.
KISISEL_KAYNAK = re.compile(
    r"ops/(mac_veri|gmail|arsiv_ara|chatgpt_export|kisisel_ozet|para_defteri|saglik|tuketim)\.py|"
    r"\.ghost/(para|ozet|chatgpt|saglik|tuketim|yedek|instagram)|email_spool\.jsonl|sms_spool\.jsonl|"
    r"ops/ghost\.db|ghost\.masa\s+(oku|yeni)|\.claude/projects/[^\s]*/memory", re.I)
YUKLEME_ARGS = re.compile(r"(\s-F\s|\s--form\s|\s-T\s|--upload-file|--data-binary\s+@|\s-d\s+@|--data\s+@|"
                          r"\bnc\b|\bncat\b|\bsocat\b|\btelnet\b)")


def _izinsiz_hostlar(metin):
    hedef = re.sub(r"(\s-d|\s--data[\w-]*|\s-F|\s--form|\s--json)\s+('[^']*'|\"[^\"]*\"|\S+)", " ", metin)
    hostlar = URL.findall(hedef)
    # nc/ncat/telnet/socat: URL'siz host argümanı
    hostlar += re.findall(r"\b(?:nc|ncat|telnet)\s+(?:-\S+\s+)*([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b", metin)
    hostlar += re.findall(r"\b(?:scp|rsync)\b[^;&|\n]*\s[\w.-]+@([A-Za-z0-9.-]+):", metin)
    return [h for h in hostlar if not _alan_izinli(h)]


# Kişisel verinin gidebileceği yerler: izinli API'ler + sahibin kendi alanları ve makineleri.
KENDI_ALAN = ("203.0.113.10", "ghostinprod.com", "owner.example", "example.com", "sahip.kim", "owner-card.example",
              "app-a.example.com", "app-b.example.com")
YUKLEME = re.compile(r"(\s-F\b|\s--form\b|\s-T\b|--upload-file|@-|@/|@~|@\$|--data-binary\s+@|"
                     r"\s-d\s+@|--data\s+@|--post-file|--body-file)")


def _kisisel_cikis(k):
    """Komutta kişisel kaynak varsa: izinsiz adrese giden bir parça (veri gövdesi/yükleme, kişisel
    komut çıktısı, boru, ham soket, uzak kopya) engellenir. Düz GET ve genel POST'a dokunmaz."""
    if not KISISEL_KAYNAK.search(k):
        return None
    for parca in PARCA_AG.split(k):
        if not re.search(r"\b(curl|wget|nc|ncat|socat|telnet|scp|rsync|sftp|ftp|urllib|requests)\b", parca):
            continue
        yasak = [h for h in _izinsiz_hostlar(parca)
                 if not any(h == a or h.endswith("." + a) for a in KENDI_ALAN)]
        if not yasak:
            continue
        gonderim = (YUKLEME.search(" " + parca)
                    or re.search(r"\|\s*(curl|wget|nc|ncat|socat|telnet)\b", parca)
                    or re.search(r"\b(nc|ncat|socat|telnet|scp|rsync|sftp)\b", parca)
                    or (KISISEL_KAYNAK.search(parca) and re.search(r"\$\(|`|-d\b|--data", parca)))
        if gonderim:
            return ("Kişisel veri (Mac/Gmail/arşiv/ChatGPT/para/sağlık/hafıza) dışarıdaki bir adrese gönderilemez: %s. "
                    "sahibe iletmek için send_telegram/send_document, Kokpit ya da Masa kullan."
                    % ", ".join(sorted(set(yasak))))
    return None


# --- 5. korunan dosyalar ----------------------------------------------------------------
KORUNAN = (
    os.path.join(EV, ".claude", "settings.json"),
    os.path.join(EV, ".claude", "settings.local.json"),
    os.path.join(KOK, ".claude", "settings.json"),
    os.path.join(KOK, ".claude", "settings.local.json"),
    os.path.join(KOK, "ops", "guvenlik_kapisi.py"),
    os.path.join(KOK, "ops", "sahne_onaylar.json"),
    os.path.join(KOK, "ghost", "sahne.py"),
    os.path.join(KOK, "ops", "gmail_gonder.py"),
)
KORUNAN_DIZIN = (os.path.join(KOK, "ops", "gmail_taslak"), os.path.join(KOK, "ops", "githooks"))
KORUNAN_AD = re.compile(r"(\.claude/settings(\.local)?\.json|guvenlik_kapisi\.py)")
_P = (r"(?:\.claude/settings(?:\.local)?\.json|guvenlik_kapisi\.py|sahne_onaylar\.json|ghost/sahne\.py|"
      r"gmail_gonder\.py|gmail_taslak/|ops/githooks/)")
DEGISTIRME = re.compile(
    r"(?<![0-9&])>{1,2}\s*['\"]?\S*" + _P + "|"
    r"\b(?:sed\s+-i|perl\s+-[a-z]*i|mv|rm|ln|truncate|chmod|tee|git\s+(?:checkout|restore|apply))\b[^;&|\n]*" + _P + "|"
    r"\b(?:cp|install|rsync|dd)\b[^;&|\n#]*\s['\"]?\S*" + _P + r"['\"]?\s*(?:$|[;&|\n#])|"
    r"open\([^)\n]*" + _P + r"[^)\n]*,\s*['\"][wax+]|" + _P + r"[^\n]{0,40}\)\.write_(?:text|bytes)|"
    r"disableAllHooks|"
    # onay kaydını yazan/tüketen ve SMTP alt fonksiyonunu doğrudan çağırmak (yalnız kendi kapıları çağırır)
    r"sahip_onayi_kaydet\s*\(|onay_tuket\s*\(|sahne\.eylem\(|_smtp_gonder\s*\(|import\s+gmail_gonder|gmail_gonder\s+as\b|"
    r"from\s+gmail_gonder\s+import", re.M)
ONAY = re.compile(r"\bonay-[0-9a-f]{16}\b")


# Gizli olmayan yapılandırma (kimlik/host/yol): ağ komutunda geçmesi sızıntı değil.
GIZLI_DEGIL = {"NTFY_TOPIC", "CLOUDFLARE_ACCOUNT_ID", "POSTHOG_HOST", "POSTHOG_PROJECT_ID", "ASC_ISSUER_ID",
               "ASC_KEY_ID", "ASC_KEY_PATH", "APNS_KEY_ID", "APNS_TEAM_ID", "APNS_KEY_PATH", "APNS_TOPIC",
               "ELEVENLABS_VOICE_ID", "GMAIL_ADDRESS", "WIKIDATA_USER", "APPLE_ID", "HF_API_KEY_ID",
               "JARVIS_CLAUDE_ARGS", "CLAUDE_BIN", "TELEGRAM_CHAT_ID"}
SIR_AD = re.compile(r"(TOKEN|SECRET|PASSWORD|PASS$|API_KEY|PRIVATE|CREDENTIAL)")
# Değeri bilinen host değişkenleri: URL'de değişken olarak geçse de hedef bellidir.
HOST_DEGISKEN = {"POSTHOG_HOST": "eu.posthog.com"}


def _yurutulen(komut):
    """Dosyaya YAZILAN heredoc gövdelerini (cat > f <<'EOF' … EOF) çıkar — yazılan metin koşmaz."""
    satirlar, out, bitis = komut.split("\n"), [], None
    for satir in satirlar:
        if bitis is not None:
            if satir.strip() == bitis:
                bitis = None
            continue
        out.append(satir)
        m = re.search(r"\b(cat|tee)\b[^<\n]*>[^<\n]*<<-?\s*['\"]?(\w+)['\"]?", satir) or \
            re.search(r"\btee\b[^<\n]*<<-?\s*['\"]?(\w+)['\"]?", satir)
        if m:
            bitis = m.groups()[-1]
    return "\n".join(out)


YORUMLAYICI = re.compile(r"\b(python3?|node|bash|sh|zsh|perl|ruby|osascript)\b")


def _kod_metni(komut):
    """Koşacak kod: yorumlayıcıya gitmeyen heredoc gövdeleri ve git commit -m metni çıkarılır."""
    satirlar, out, bitis = komut.split("\n"), [], None
    for satir in satirlar:
        if bitis is not None:
            if satir.strip() == bitis:
                bitis = None
            continue
        out.append(satir)
        m = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", satir)
        if m and not YORUMLAYICI.search(satir):
            bitis = m.group(1)
    metin = "\n".join(out)
    return re.sub(r"(\bgit\s+commit\b[^;&|\n]*?\s-m\s+)('[^']*'|\"(?:[^\"\\]|\\.)*\")", r"\1''", metin)


def _sir_adlari():
    """secrets.env'deki ANAHTAR ADLARI (değerler okunmaz)."""
    adlar = set()
    try:
        with open(os.path.join(KOK, "secrets.env"), encoding="utf-8") as f:
            for satir in f:
                m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=", satir)
                if m:
                    adlar.add(m.group(1))
    except OSError:
        pass
    return adlar - GIZLI_DEGIL


def _sir_degerleri():
    """secrets.env (+ .env) DEĞERLERİ — yalnız bu süreçte, karşılaştırma için; hiçbir yere yazılmaz.
    Kısa ya da gizli olmayan değerler (kimlik/host/yol) hariç: yanlış alarm üretmesinler."""
    degerler = set()
    for ad in ("secrets.env", ".env"):
        try:
            with open(os.path.join(KOK, ad), encoding="utf-8") as f:
                for satir in f:
                    m = re.match(r"\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$", satir.rstrip("\n"))
                    if not m or m.group(1) in GIZLI_DEGIL:
                        continue
                    v = m.group(2).strip().strip("'\"")
                    if len(v) >= 12 and not v.startswith(("/", "~", "http", "-")):
                        degerler.add(v)
        except OSError:
            pass

    import glob as _glob
    import json as _json

    def _yuru(o, anahtar=""):
        if isinstance(o, dict):
            for a, b in o.items():
                yield from _yuru(b, a)
        elif isinstance(o, list):
            for b in o:
                yield from _yuru(b, anahtar)
        elif isinstance(o, str) and re.search(r"(?i)auth|token|key|secret|pass", anahtar):
            yield re.sub(r"^(Bearer|token|Basic)\s+", "", o.strip())
    for yol in _glob.glob(os.path.join(KOK, "mcp*.json")):      # MCP uç başlıkları (GitHub PAT'leri)
        if yol.endswith(".example.json"):
            continue
        try:
            for v in _yuru(_json.load(open(yol, encoding="utf-8"))):
                if len(v) >= 12 and not v.startswith(("/", "~", "http", "-", "$")):
                    degerler.add(v)
        except (OSError, ValueError):
            pass
    return degerler


def _bicimler(v):
    """Değerin, bir sızdırma girişiminde görülebilecek hâlleri (her biri ≥10 karakter parça)."""
    import base64
    import binascii
    from urllib.parse import quote
    out = {v, quote(v, safe=""), v[::-1], binascii.hexlify(v.encode()).decode()}
    for on in (b"", b"\0", b"\0\0"):                 # base64 hizalaması: üç kaydırma
        b = base64.b64encode(on + v.encode()).decode()
        kes = {b"": 0, b"\0": 2, b"\0\0": 3}[on]
        govde = b[kes:].rstrip("=")
        out.add(govde[:-2] if len(govde) > 12 else govde)
        out.add(govde.replace("+", "-").replace("/", "_")[:-2] if len(govde) > 12 else govde)
    return {x for x in out if len(x) >= 10}


def _duz_deger(metin):
    """Araç girdisinde bir sırrın düz değeri (ya da kodlanmış hâli) var mı? Varsa sırrın ADI değil,
    yalnız 'var' bilgisi döner — değer hiçbir yere yazılmaz."""
    if not metin:
        return False
    for v in _sir_degerleri():
        if any(b in metin for b in _bicimler(v)):
            return True
    return False


def _alan_izinli(host):
    host = host.lower().rstrip(".")
    return any(host == a or host.endswith("." + a) for a in IZINLI_ALAN)


_TUKETILEN = set()   # bu karar sürecinde tüketilen onaylar (kanca her araç çağrısında yeni süreç)


def _onay_gecerli(metin):
    """Komutta sahibin gerçek kapısından kaydedilmiş, kullanılmamış onay var mı? Varsa tüket.
    Aynı komut birden çok kurala takılırsa (ör. root anahtarı + korunan dosya) tek onay yeter."""
    for eid in ONAY.findall(metin or ""):
        if eid in _TUKETILEN:
            return eid
        try:
            sys.path.insert(0, KOK)
            from ghost import sahne
            if sahne.onay_gecerli(eid):
                sahne.onay_tuket(eid)
                _TUKETILEN.add(eid)
                return eid
        except Exception:
            return None
    return None


def _yol_korunan(yol):
    try:
        y = os.path.realpath(os.path.expanduser(yol or ""))
    except Exception:
        return False
    return y in KORUNAN or any(y == d or y.startswith(d + os.sep) for d in KORUNAN_DIZIN)


ATAMA = re.compile(r"(?:^|[\s;&|(])(?:export\s+|local\s+|declare\s+(?:-\w+\s+)?)?([A-Za-z_]\w*)=([\"']?)([^\s;&|]*)")
ORTAM_OKU = r"(?:\$\{?%s\b|environ\[\s*['\"]%s['\"]|environ\.get\(\s*['\"]%s['\"]|getenv\(\s*['\"]%s['\"]|process\.env\.%s\b|process\.env\[['\"]%s['\"])"
ORTAM_KOD = r"(?:environ\[\s*['\"]%s['\"]|environ\.get\(\s*['\"]%s['\"]|getenv\(\s*['\"]%s['\"]|process\.env\.%s\b|process\.env\[['\"]%s['\"])"
DINAMIK_ORTAM = re.compile(r"environ\[\s*[^'\"\s\]]|environ\.get\(\s*[^'\"\s)]|getenv\(\s*[^'\"\s)]|"
                           r"environ\.(items|values|copy)\(|dict\(os\.environ|process\.env\[\s*[^'\"\s\]]")
# Mac'e / uzak kabuğa giden komut: iç metin de aynı kurallardan geçer.
IC_KOMUT = re.compile(r"\b(?:ops/mac\.py\s+komut|ops/mac_cmd\.py|ssh\s+(?:-\S+\s+(?:\S+\s+)?)*[\w.@-]+)\s+(?:--\s+)?('([^']*)'|\"((?:[^\"\\]|\\.)*)\")")
GIT_KACIS = re.compile(r"\bgit\b[^;&|\n]*\s(--no-verify\b|-c\s+core\.hooksPath|config\s+(--\w+\s+)*(core\.hooksPath\s+[^\s;&|]|--unset\S*\s+core\.hooksPath))|"
                       r"\bgit\s+(?:-c\s+\S+\s+)*commit\b[^;&|\n]*\s-n\b")
ROOT_ANAHTAR = re.compile(r"\b(ssh|rsync|scp|sftp|GIT_SSH_COMMAND)\b[^\n]*?(-i\s*|IdentityFile[\s=]+)['\"]?\S*ghost_vps\b")
KOD_AG = re.compile(r"\b(urllib|requests|http\.client|httpx|aiohttp|socket|websocket|fetch\(|axios|"
                    r"subprocess[\s\S]{0,200}\b(curl|wget|nc|ssh|scp|rsync))\b")
KOD_C = re.compile(r"\b(?:python3?|node)\s+(?:-[A-Za-z]+\s+)*?-[ce]\s+('([^']*)'|\"((?:[^\"\\]|\\.)*)\")", re.S)


def _kod_govdeleri(k):
    """Kabuk metni ile yorumlayıcıya giden kod gövdelerini ayır: (kabuk, [(çağıran satır, gövde)]).
    Gövde (python heredoc'u, python -c / node -e) TEK birim olarak denetlenir — ortamdan okunan sır
    ile ağ çağrısı farklı satırlarda olabilir; kabuk satırlarıyla da karışmaz (yanlış alarm)."""
    govdeler = []

    def c_al(m):
        govdeler.append((m.group(0)[:80], m.group(2) if m.group(2) is not None else m.group(3)))
        return m.group(0)[:m.start(1) - m.start(0)] + "''"
    k = KOD_C.sub(c_al, k)
    satirlar, kabuk, i = k.split("\n"), [], 0
    while i < len(satirlar):
        satir = satirlar[i]
        kabuk.append(satir)
        m = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", satir)
        if m and YORUMLAYICI.search(satir):
            j, govde = i + 1, []
            while j < len(satirlar) and satirlar[j].strip() != m.group(1):
                govde.append(satirlar[j])
                j += 1
            govdeler.append((satir, "\n".join(govde)))
            i = j + 1
            continue
        i += 1
    return "\n".join(kabuk), govdeler


def _zincir(k, adlar):
    """X=$TOKEN / X="${TOKEN}" → X de sır. Sabit noktaya dek (Y=$X ...)."""
    adlar = set(adlar)
    for _ in range(5):
        yeni = set()
        for m in ATAMA.finditer(k):
            ad, deger = m.group(1), m.group(3)
            if ad not in adlar and any(re.search(r"\$\{?%s\b" % re.escape(a), deger) for a in adlar):
                yeni.add(ad)
        if not yeni:
            break
        adlar |= yeni
    return adlar


def _kullanilan(parca, adlar):
    if not re.search(r"\b(sh|bash|zsh|eval|xargs|ssh|env|nohup|setsid|timeout|parallel)\b|komut|mac_cmd", parca):
        parca = re.sub(r"'[^']*'", "''", parca)  # tek tırnak içindeki $AD kabukta açılmaz: metin
                                                 # (ama sh -c '…', ssh '…', Mac komutu onu KOŞAR)
    return [a for a in adlar if re.search(ORTAM_OKU % ((re.escape(a),) * 6), parca)]


def karar_bash(komut, _derinlik=0):
    """Engel sebebi (str) ya da None."""
    k = re.sub(r"\\[ \t]*\n", " ", _yurutulen(komut or ""))     # satır devamı (\) tek komut
    if _derinlik < 2:
        for m in IC_KOMUT.finditer(k):
            ic = m.group(2) if m.group(2) is not None else m.group(3)
            sebep = karar_bash(ic, _derinlik + 1)
            if sebep:
                return "Uzak/Mac komutunun içi: " + sebep
    if _derinlik:
        if _sir_okuma(k):
            return "Sır dosyası okunamaz/kopyalanamaz (Mac/uzak makinede de)."
        if ORTAM_DOKUM.search(k):
            return "Ortam topluca dökülemez (Mac/uzak makinede de)."
        return None
    if ROOT_ANAHTAR.search(_kod_govdeleri(k)[0]) and not _onay_gecerli(k):
        return ("VPS root anahtarı (ghost_vps) yalnız sahibin gerçek onayıyla kullanılır. Dosya eşitlemek için "
                "root gerekmez: Mac'te -i ~/.ssh/ghost_runtime ve ghost@VPS kullan. Root gerçekten gerekiyorsa "
                "python3 -m ghost.sahne onay ile kart aç, onaylanınca komuta onay kimliğini ekle.")
    if GIT_KACIS.search(_kod_metni(k)):
        return "Git kancalarını atlatmak (--no-verify, -n, core.hooksPath) kapalı: sır taraması ve test kapısı orada."
    if re.search(r"\bgit\s+add\b[^;&|\n]*\s(-f|--force)\b", k) and SIR_DOSYA.search(k):
        return "Sır dosyası git'e eklenemez (main 15 dk'da bir GitHub'a gider)."
    if _sir_okuma(k):
        return ("Sır dosyası okunamaz/kopyalanamaz (secrets.env, .env, ~/.secrets, *.p8, ssh anahtarı). "
                "Anahtarı kullanmak için ortamdaki $DEGISKEN'i komutta doğrudan kullan; değeri ekrana basma.")
    # Kabuk komutu (env/printenv/set) yalnız kabuk metninde aranır — python kodundaki '|env|' metni
    # komut değildir; kodun ortamı basması (print(os.environ)) ve /proc/*/environ her yerde aranır.
    kabuk_metni, govdeler_ = _kod_govdeleri(k)
    if ORTAM_DOKUM.search(kabuk_metni) or any(ORTAM_DOKUM.search(re.sub(r"(?m)^.*$", lambda m: m.group(0) if re.search(
            r"environ|process\.env", m.group(0)) else "", g)) for _, g in govdeler_):
        return "Ortam topluca dökülemez (env/printenv/export -p/environ). Gereken tek değişkeni $AD ile kullan."
    tek_cli = re.fullmatch(r"\s*(cd\s+\S+\s*&&\s*)?python3?\s+(\S*/)?ops/gmail_gonder\.py\b[^;&|\n`$]*", k)
    if SMTP_KULLANIM.search(k) and not tek_cli:
        return ("Mail yalnız ops/gmail_gonder.py hazirla → Sahip onayı → gonder --onay yoluyla gider; "
                "SMTP/Gmail şifresi doğrudan kullanılamaz.")
    adlar = _sir_adlari() | _yakalananlar(k)[1] | {
        x for t in re.findall(r"\$\{?([A-Z][A-Z0-9_]*)|(?:environ\[\s*['\"]|getenv\(\s*['\"]|environ\.get\(\s*['\"]|process\.env\.)([A-Z][A-Z0-9_]*)", k)
        for x in t if x and SIR_AD.search(x)} - GIZLI_DEGIL
    adlar = _zincir(k, adlar)
    kabuk, govdeler = _kod_govdeleri(k)
    birimler = [(p, False, []) for p in PARCA_AG.split(kabuk)]
    for cagiran, govde in govdeler:     # kod gövdesi tek birim; çağıran satırdaki $SIR argümanı da sayılır
        birimler.append((govde, True, [a for a in adlar if re.search(r"\$\{?%s\b" % re.escape(a), cagiran)]))
    for parca, kod, miras in birimler:
        if kod:   # kodda '$AD' düz metindir (kabuk genişletmez); ağ = kütüphane çağrısı, '.ssh/' yolu değil
            kullanilan = [a for a in adlar if re.search(ORTAM_KOD % ((re.escape(a),) * 5), parca)] + miras
            ag = KOD_AG.search(parca)
            if DINAMIK_ORTAM.search(parca) and ag:
                kullanilan = kullanilan or ["(ortamdan dinamik okuma)"]
        else:
            kullanilan, ag = _kullanilan(parca, adlar), AG_ARACI.search(parca)
        if not kullanilan or not ag:
            continue
        for ad, host in HOST_DEGISKEN.items():
            parca = re.sub(r"\$\{?%s\}?" % ad, "https://" + host, parca)
            parca = re.sub(ORTAM_OKU.replace("%s", ad) + r"[\]\)]?", "'https://%s'" % host, parca)
        if kod:
            hostlar = URL.findall(parca)
            if not hostlar:
                return ("Sır (%s) koddan ağa gidiyor ama hedef adres kodda açık değil. URL'yi koda açıkça yaz."
                        % ", ".join(sorted(kullanilan)))
            yasak = [h for h in hostlar if not _alan_izinli(h)
                     and not any(h == a or h.endswith("." + a) for a in KENDI_ALAN)]
            if yasak:
                return ("Sır (%s) içeren kod izinsiz adrese gidebilir: %s. İzinli alan listesi "
                        "ops/guvenlik_kapisi.py IZINLI_ALAN; yeni alan Sahip onayıyla eklenir."
                        % (", ".join(sorted(kullanilan)), ", ".join(sorted(set(yasak)))))
            continue
        # -d/--data/-F gövdesindeki URL hedef değildir (ör. Page Rule yönlendirme adresi)
        hedef = re.sub(r"(\s-d|\s--data[\w-]*|\s-F|\s--form|\s--json)\s+('[^']*'|\"[^\"]*\"|\S+)", " ", parca)
        hostlar = URL.findall(hedef)
        if not hostlar:
            return ("Sır değişkeni (%s) ağ komutunda ama hedef adres komutta açık değil. "
                    "URL'yi komuta açıkça yaz (izinli alanlar: Cloudflare, PostHog, GitHub, ElevenLabs, "
                    "App Store Connect, Higgsfield, Wikidata, Telegram)." % ", ".join(sorted(kullanilan)))
        yasak = [h for h in hostlar if not _alan_izinli(h)]
        if yasak:
            return ("Sır değişkeni (%s) izinsiz adrese gidemez: %s. İzinli alan listesi "
                    "ops/guvenlik_kapisi.py IZINLI_ALAN; yeni alan Sahip onayıyla eklenir."
                    % (", ".join(sorted(kullanilan)), ", ".join(sorted(set(yasak)))))
    kc = _kisisel_cikis(k)
    if kc:
        return kc
    if DEGISTIRME.search(_kod_metni(k)):
        if _onay_gecerli(k):
            return None
        return ("Güvenlik kapısı ve Claude ayar dosyaları yalnız sahibin gerçek onayıyla değişir: "
                "önce python3 -m ghost.sahne onay ile kart aç, Sahip onaylayınca komuta onay kimliğini ekle.")
    return None


def karar(girdi):
    arac = girdi.get("tool_name") or ""
    gi = girdi.get("tool_input") or {}
    duz = json.dumps(gi, ensure_ascii=False)
    if _duz_deger(duz) or _duz_deger(str(gi.get("command") or "")):
        if not (arac == "Bash" and _onay_gecerli(str(gi.get("command") or ""))):
            return ("Araç girdisinde bir sırrın düz değeri (ya da kodlanmış hâli) var. Değeri komuta/dosyaya/"
                    "isteğe yazma; Bash'te $DEGISKEN kullan. Gerçekten gerekiyorsa Sahip onayı + onay kimliği.")
    if arac == "Bash":
        return karar_bash(str(gi.get("command") or ""))
    if arac in ("Read", "NotebookRead", "Grep", "Glob"):
        yol = str(gi.get("file_path") or gi.get("path") or gi.get("notebook_path") or "")
        desen = str(gi.get("pattern") or gi.get("glob") or "")
        if SIR_DOSYA.search(yol) or (arac == "Grep" and SIR_DOSYA.search(gi.get("glob") or "")):
            return "Sır dosyası okunamaz. Anahtarı kullanmak için ortamdaki $DEGISKEN'i kullan."
        if arac == "Grep" and not yol and SIR_DOSYA.search(desen):
            return None
    if arac in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        yol = str(gi.get("file_path") or gi.get("notebook_path") or "")
        if _yol_korunan(yol):
            return "Güvenlik kapısı ve Claude ayar dosyaları yalnız sahibin gerçek onayıyla (Bash + onay kimliği) değişir."
        if re.search(r"secrets\.env$|(^|/)\.env$|\.secrets/", yol):
            return "Sır dosyası araçla düzenlenemez; yeni anahtar eklemek için: echo 'AD=değer' >> secrets.env"
    return None


def _log(satir):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%F %T"), satir[:500]))
    except OSError:
        pass


def main():
    try:
        girdi = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        sebep = karar(girdi)
    except Exception as e:  # kapı bozulursa oturumu kilitleme
        _log("HATA %r" % e)
        return 0
    if not sebep:
        return 0
    gi = girdi.get("tool_input") or {}
    ozet = str(gi.get("command") or gi.get("file_path") or gi.get("url") or "")[:200]
    if sebep.startswith("Araç girdisinde bir sırrın"):
        ozet = "(girdi sır içerdiği için günlüğe yazılmadı)"
    _log("ENGEL %s | %s | %s" % (girdi.get("tool_name"), sebep[:80], ozet))
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "Ghost güvenlik kapısı: " + sebep}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
