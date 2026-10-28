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

  10. (bağımsız denetim, 2026-10-28 akşam) Her sır yalnız KENDİ servisine gider (Cloudflare anahtarı
     Cloudflare'e, GitHub anahtarı GitHub'a); komutta yabancı bir kimlik (düz Bearer/bot token/
     user:pass@) varken sır kullanılamaz — izinli servisteki saldırgan hesabı (ntfy, başka Telegram
     botu, gist) kanal olmasın. Kalıcı kod bırakmak (crontab, at, systemd-run, ~/.bashrc) onay ister;
     depo dışına yazılan betik de Bash kurallarından geçer; Read sembolik bağı çözer.

Sezgiseldir: kararlı bir saldırgan gizleyerek aşabilir. Amaç, güvenilmez metnin (mail, sayfa,
belge) içindeki düz talimatın tek adımda sır sızdırmasını kesmek. Kapı hata verirse (2026-10-28'den
beri) ağ/yorumlayıcı/korunan dosya içeren komutu REDDEDER, gerisine izin verir (oturum kilitlenmesin);
hata ops/guvenlik_kapisi.log'a yazılır.

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
    r"\.git-credentials|/\.netrc\b|"
    # joker/değişkenle kurulan ad: secret?.env, *.env, $S.env, ${S}.env, .s*crets/
    r"[\w.?*\[\]{}$-]*[?*\[$][\w.?*\[\]{}$-]*\.env(?![\w.-])|\.[\w?*\[\]]*[?*\[][\w?*\[\]]*crets/)", re.I)
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
    r"(^|[;&|(`\n]\s*)printenv\s+\w|/proc/\S*environ|/proc/[^\s/]+/(?:e[\w?*\[\]]*|[?*\[][\w?*\[\]]*)(?![\w/])|"
    r"(print|dumps|write|pprint|console\.log)\([^)\n]*\b(os\.environ|process\.env)\b(?!\s*[.\[])", re.M)

# --- 3. sır değişkeni + izinsiz adres ------------------------------------------------
IZINLI_ALAN = (
    "api.cloudflare.com", "posthog.com", "api.github.com", "github.com", "githubusercontent.com",
    "api.elevenlabs.io", "api.appstoreconnect.apple.com", "appstoreconnect.apple.com",
    "higgsfield.ai", "wikidata.org", "api.telegram.org", "api.anthropic.com",
    "sentry.io", "localhost", "127.0.0.1",
)   # ntfy.sh 2026-10-28'de çıktı: konusu herkese açık, herkes okur (denetim K3)
# Her sır yalnız kendi servisine (denetim K3: Cloudflare anahtarı Telegram'a/GitHub'a gitmesin —
# izinli servislerin hepsinde saldırgan da hesap açabilir). Eşleşmeyen ad: genel IZINLI_ALAN.
YEREL = ("localhost", "127.0.0.1")
SIR_HEDEF = (
    (r"^CLOUDFLARE_", ("api.cloudflare.com",)),
    (r"^ELEVENLABS_", ("api.elevenlabs.io",)),
    (r"GITHUB|^GH_", ("github.com", "api.github.com", "githubusercontent.com")),
    (r"^POSTHOG_", ("posthog.com",)),
    (r"^HF_|HIGGSFIELD", ("higgsfield.ai",)),
    (r"^WIKIDATA_", ("wikidata.org",)),
    (r"^TELEGRAM_", ("api.telegram.org",)),
    (r"^SENTRY_", ("sentry.io",)),
    (r"ANTHROPIC|^CLAUDE_CODE_OAUTH", ("api.anthropic.com",)),
    (r"^ASC_|APPSTORE|^APNS_", ("api.appstoreconnect.apple.com", "appstoreconnect.apple.com", "push.apple.com")),
    (r"^GHOST_SES_SECRET$", ("api.elevenlabs.io", "ghostinprod.com")),
    (r"^(GHOST_MACLINK_TOKEN|GHOST_BACKUP_PASS|APPLE_ID_PASS|GMAIL_APP_PASSWORD|WEBHOOK_SECRET)$", ()),
)
# Komutta yabancı kimlik: sırrımız saldırganın hesabına (aynı izinli serviste) gitmesin.
YABANCI_KIMLIK = re.compile(
    r"(?i)authorization:\s*(?:bearer|token|basic)\s+(?![\"'$]|\$)[A-Za-z0-9._~+/=-]{16,}|"
    r"\b(?:x-api-key|xi-api-key|api-key|x-auth-key):\s*(?![\"'$])[A-Za-z0-9._~+/=-]{16,}|"
    r"https?://[^/\s:@'\"]+:(?!\$)[^/\s@'\"]{6,}@|"
    r"\s-u\s+['\"]?[^\s:'\"]+:(?!\$)[^\s'\"]{8,}")
TELEGRAM_YABANCI_BOT = re.compile(r"api\.telegram\.org/(?:file/)?bot\d{5,}:[A-Za-z0-9_-]{20,}")
KENDI_GITHUB = ("ornek-org", "ghost-bot")
GITHUB_YABANCI_YAZ = re.compile(
    r"\bgit\s+(?:-\S+\s+(?:\S+\s+)?)*(?:push|remote\s+(?:add|set-url)\s+\S+)\s[^;&|\n]*?"
    r"(?:https?://(?:[^/\s@]+@)?|git@)github\.com[/:](?!(?:ornek-org|ghost-bot)/)[\w.-]+/|"
    r"\bgh\s+gist\b|api\.github\.com/gists\b", re.I)
URL = re.compile(r"\b(?:https?|wss?|ftp)://([^/\s'\"`:?#]+)", re.I)
PARCA_AG = re.compile(r"\s*(?:;|&&|\|\||\n)\s*")      # boru (|) bölmez: curl … | jq tek iş
AG_ARACI = re.compile(r"(?<![./~-])\b(curl|wget|nc|ncat|socat|telnet|ssh|scp|sftp|rsync|ftp|"
                      r"urllib|requests|http\.client|fetch\(|axios|websocat|dig|host|nslookup|drill)\b")

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
    # Faz 1 madde 10/2: onay servisi istemcisi ve köprü sahip kilidi — gmail_gonder ve kapı dışı
    # onay okuyucuları bu kodu içe alır; model bunu değiştirip sahte onay/sahip üretmesin.
    os.path.join(KOK, "ghost", "onay.py"),
    os.path.join(KOK, "ghost", "__init__.py"),
    os.path.join(KOK, "ops", "owner.json"),
)
KORUNAN_DIZIN = (os.path.join(KOK, "ops", "gmail_taslak"), os.path.join(KOK, "ops", "githooks"),
                 os.path.join(KOK, "ops", "onay"))
KORUNAN_AD = re.compile(r"(\.claude/settings(\.local)?\.json|guvenlik_kapisi\.py)")
_P = (r"(?:\.claude/settings(?:\.local)?\.json|guvenlik_kapisi\.py|sahne_onaylar\.json|ghost/sahne\.py|"
      r"gmail_gonder\.py|gmail_taslak/|ops/githooks/|ghost/onay\.py|ghost/__init__\.py|ops/owner\.json|ops/onay/)")
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
            with open(yol, encoding="utf-8") as _f:
                _d = _json.load(_f)
            for v in _yuru(_d):
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


def _alan_izinli(host, alanlar=IZINLI_ALAN):
    host = host.lower().rstrip(".")
    return any(host == a or host.endswith("." + a) for a in alanlar)


def _sir_alanlari(ad):
    """Bu sırrın gidebileceği alanlar (yerel her zaman). Eşlenmemiş ad: genel liste (+ kendi alanlar)."""
    for desen, alanlar in SIR_HEDEF:
        if re.search(desen, ad):
            return YEREL + tuple(alanlar) + KENDI_ALAN      # kendi alanlarımız (Pages/Worker) dışarı sayılmaz
    return IZINLI_ALAN + KENDI_ALAN


def _yasak_hostlar(hostlar, kullanilan, kokler):
    """Kullanılan her sırrın (kökleriyle — X=$TOKEN zinciri) kendi alanına uymayan hostlar."""
    adlar = set()
    for a in kullanilan:
        adlar |= kokler.get(a) or {a}
    return sorted({h for h in hostlar for a in adlar if not _alan_izinli(h, _sir_alanlari(a))})


def _github_yabanci_yazma(parca):
    """Sırla birlikte başka sahibin reposuna API yazması (issue/yorum = herkese açık kanal)."""
    for m in re.finditer(r"api\.github\.com/repos/([\w.-]+)/", parca):
        if m.group(1).lower() not in KENDI_GITHUB and re.search(
                r"\s-X\s*(POST|PUT|PATCH)|--request\s+(POST|PUT|PATCH)|\s-d\s|\s--data|\s-F\s|\s--json\s|\s-f\s", parca, re.I):
            return m.group(1)
    return None


_TUKETILEN = set()   # bu karar sürecinde tüketilen onaylar (kanca her araç çağrısında yeni süreç)
_ICERIK_TARAMA = [False]   # dosyaya yazılan metin taranırken onay TÜKETİLMEZ ve geçerli sayılmaz


def _onay_gecerli(metin):
    """Komutta sahibin gerçek kapısından kaydedilmiş, kullanılmamış onay var mı? Varsa tüket.
    Aynı komut birden çok kurala takılırsa (ör. root anahtarı + korunan dosya) tek onay yeter."""
    if _ICERIK_TARAMA[0]:
        return None
    for eid in ONAY.findall(metin or ""):
        if eid in _TUKETILEN:
            return eid
        try:
            if _onay_tuket_kendi(eid):
                _TUKETILEN.add(eid)
                return eid
        except Exception:
            return None
    return None


# Faz 1 madde 10 (bağımsız denetim K1): onay kaydı ayrı `ghost-onay` kullanıcısına taşındıysa dosya YOK;
# kapı tek doğruluk kaynağı olarak Unix soketini kullanır. pwd ile kullanıcı var mı diye bakar (ORTAMDAN
# bağımsız — GHOST_OZERK/GHOST_TEST bunu değiştiremez; testler ONAY_SOKET ve pwd'yi yamalar). İstemci
# kapıya GÖMÜLÜ (repodan import YOK; root sahipli kopya -I ile koşar, ghost/ kodunu içine almaz).
ONAY_SOKET = "/run/ghost-onay/onay.sock"
ONAY_KULLANICI = "ghost-onay"


def _onay_servisi_kurulu():
    try:
        import pwd
        pwd.getpwnam(ONAY_KULLANICI)
        return True
    except Exception:
        return False


def _onay_soket_cagir(op, eid, zaman_asimi=2):
    """Onay soketine tek satır JSON gönder, tek satır JSON cevabı döndür (dict) ya da None (fail-closed)."""
    import socket
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(zaman_asimi)
            s.connect(ONAY_SOKET)
            s.sendall((json.dumps({"op": op, "eid": str(eid or "")}) + "\n").encode("utf-8"))
            parca = []
            while True:
                b = s.recv(65536)
                if not b:
                    break
                parca.append(b)
        cevap = json.loads(b"".join(parca).decode("utf-8"))
        return cevap if isinstance(cevap, dict) else None
    except Exception:
        return None


def _onay_tuket_kendi(eid, omur_sn=24 * 3600):
    """ghost/sahne.py onay_gecerli + onay_tuket'in AYNISI, ama oturumun değiştirebildiği ghost/ kodunu
    İÇE ALMADAN (2026-10-27 B: kapı root sahipli kopyadan koşar; ghost/config.py'yi değiştiren bir
    oturum kapının içinde kod koşturamasın). Kayıt biçimi sahne.py ile ortak: {eid: {ts, tuketildi}}.
    Onay servisi kuruluysa (Faz 1 madde 10) dosya HİÇ kullanılmaz: soketten atomik tüket (fail-closed)."""
    if _onay_servisi_kurulu():
        cevap = _onay_soket_cagir("tuket", eid)
        return bool(cevap and cevap.get("tuketildi"))
    import fcntl
    import tempfile
    ops = os.environ.get("GHOST_OPS") or os.path.join(KOK, "ops")
    yol = os.path.join(ops, "sahne_onaylar.json")
    with open(yol + ".kilit", "a") as k:
        son = time.time() + 2       # kilit tutulursa kanca 10 sn'de düşer ve İZİN olurdu (denetim Y1)
        while True:
            try:
                fcntl.flock(k, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() > son:
                    return False
                time.sleep(0.05)
        try:
            try:
                with open(yol, encoding="utf-8") as f:
                    d = json.load(f)
            except (OSError, ValueError):
                return False
            v = d.get(eid) if isinstance(d, dict) else None
            if not v or v.get("tuketildi") or time.time() - float(v.get("ts") or 0) >= omur_sn:
                return False
            v["tuketildi"], v["tuketim_ts"] = True, time.time()
            fd, tmp = tempfile.mkstemp(prefix=".sahne_onaylar.", dir=ops)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
            os.replace(tmp, yol)
            return True
        finally:
            fcntl.flock(k, fcntl.LOCK_UN)


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
                           r"environ\.(items|values|copy)\(|dict\(os\.environ|process\.env\[\s*[^'\"\s\]]|"
                           # 'CLOUD'+'FLARE_…', f"{x}", '%s' % x, .format: ad parçalı kurulur (denetim K5)
                           r"(?:environ\[|environ\.get\(|getenv\(|process\.env\[)\s*(?:[fbr]?['\"][^'\"\n]*['\"]\s*"
                           r"(?:[+%]|\.format|\.join)|[fF]['\"])")
# Kabukta dolaylı değişken (${!X}), eval ve ortamı topluca aktaran declare: adı kapı göremez.
KABUK_DOLAYLI = re.compile(r"\$\{!\w|\beval\b|\bcompgen\s+-[ve]\b|\bdeclare\s+-[a-z]*[xp]")
# Kalıcı kod bırakmak: hiçbir kancadan geçmeden sonra koşar (denetim Y6) — onay ister.
KALICILIK = re.compile(
    r"(?:^|[;&|(`]\s*|\$\(\s*)(?:sudo\s+)?crontab\b(?!\s+-l\b)|(?:^|[;&|(]\s*)at\s+(?:now|noon|midnight|teatime|\d)|\bbatch\b\s*<<|\bsystemd-run\b(?![^;&|\n]*\s--scope\b)|"
    r"\bsystemctl\s+--user\s+(?:enable|link|edit)\b|(?:>{1,2}|\btee\s+(?:-a\s+)?|\bcp\b[^;&|\n]*|\bmv\b[^;&|\n]*|"
    r"sed\s+-i[^;&|\n]*)\s*['\"]?(?:~|\$HOME|/home/ghost)/(?:\.bashrc|\.bash_profile|\.profile|\.zshrc|\.bash_logout|"
    r"\.config/systemd/|\.config/autostart/|\.ssh/authorized_keys|\.ssh/config|\.gitconfig|\.npmrc|\.pypirc)")
KALICI_YOL = re.compile(r"(?:^|/)(?:\.bashrc|\.bash_profile|\.profile|\.zshrc|\.bash_logout|\.gitconfig|\.npmrc|"
                        r"\.pypirc|\.ssh/authorized_keys|\.ssh/config)$|/\.config/(?:systemd|autostart)/")
FREEZE_SILME = re.compile(r"\b(?:rm|mv|unlink|truncate)\b[^;&|\n]*\bFREEZE\b|(?:remove|unlink|rename|replace)\([^)\n]*FREEZE")
# Mac'e / uzak kabuğa giden komut: iç metin de aynı kurallardan geçer.
IC_KOMUT = re.compile(r"\b(?:ops/mac\.py\s+komut|ops/mac_cmd\.py|ssh\s+(?:-\S+\s+(?:\S+\s+)?)*[\w.@-]+)\s+(?:--\s+)?('([^']*)'|\"((?:[^\"\\]|\\.)*)\")")
GIT_KACIS = re.compile(r"(?i)\bgit\b[^;&|\n]*\s(--no-verify\b|-c\s+core\.hooksPath|config\s+(--\w+\s+)*(core\.hooksPath\s+[^\s;&|]|--unset\S*\s+core\.hooksPath))|"
                       r"\bgit\s+(?:-c\s+\S+\s+)*commit\b[^;&|\n]*\s-n\b")
ROOT_ANAHTAR = re.compile(r"\b(ssh|rsync|scp|sftp|GIT_SSH_COMMAND)\b[^\n]*?(-i\s*|IdentityFile[\s=]+)['\"]?\S*ghost_vps\b")
# ghost_vps artık authorized_keys command= ile KISITLI (2026-10-28): yalnız reboot + ghost-* servisleri +
# ghost-* journalctl. Bu komutlar onaysız geçer (anahtar başka bir şey yapamaz zaten); başka her kullanım
# (rsync, dosya, serbest kabuk) yine onay ister — o çağrı sunucuda reddedilir ama kapı erken ve açık söyler.
GHOST_VPS_IZINLI = re.compile(
    r"^(systemctl\s+reboot|systemctl\s+(restart|status|is-active)\s+ghost-[a-z0-9-]+(\.(service|timer))?( --no-pager)?"
    r"|journalctl\s+-u\s+ghost-[a-z0-9-]+\s+-n\s+[0-9]{1,4}\s+--no-pager)$")


def _ghost_vps_sadece_izinli(k):
    """ghost_vps ssh çağrısındaki uzak komut(lar) tamamen izinli kümedeyse True (onaysız geçer)."""
    bulundu = False
    for m in re.finditer(r"ssh\b[^\n]*?(?:-i\s*|IdentityFile[\s=]+)['\"]?\S*ghost_vps\b[^\n]*?"
                         r"(root@\S+|ghost@\S+)\s+(?:--\s+)?('([^']*)'|\"((?:[^\"\\]|\\.)*)\")", k):
        bulundu = True
        uzak = (m.group(3) if m.group(3) is not None else m.group(4) or "").strip()
        if not GHOST_VPS_IZINLI.match(uzak):
            return False
    return bulundu
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


def _zincir(k, adlar, kokler=None):
    """X=$TOKEN / X="${TOKEN}" → X de sır. Sabit noktaya dek (Y=$X ...). kokler verilirse
    türeyen adın kök sırları oraya yazılır (sır yalnız KÖKÜNÜN servisine gider)."""
    adlar = set(adlar)
    kokler = kokler if kokler is not None else {}
    for _ in range(5):
        yeni = set()
        for m in ATAMA.finditer(k):
            ad, deger = m.group(1), m.group(3)
            kaynak = [a for a in adlar if re.search(r"\$\{?%s\b" % re.escape(a), deger)]
            if ad not in adlar and kaynak:
                yeni.add(ad)
                kokler[ad] = set().union(*[kokler.get(a) or {a} for a in kaynak])
        if not yeni:
            break
        adlar |= yeni
    return adlar


def _kullanilan(parca, adlar):
    if not re.search(r"\b(sh|bash|zsh|eval|xargs|ssh|env|nohup|setsid|timeout|parallel)\b|komut|mac_cmd", parca):
        parca = re.sub(r"'[^']*'", "''", parca)  # tek tırnak içindeki $AD kabukta açılmaz: metin
                                                 # (ama sh -c '…', ssh '…', Mac komutu onu KOŞAR)
    return [a for a in adlar if re.search(ORTAM_OKU % ((re.escape(a),) * 6), parca)]


# --- 8. güvenilmez metin karantinada okunur (2026-10-28, GPT P0; dossier/guvenli-okuma-tasarim-2026-10-28.md) ---
# Tam mail, ham posta kutusu ve dış sayfa ana oturuma ham girmez: ops/karantina.py araçsız/sırsız alt modelle okur.
# Ham metin yalnız Sahip onayıyla (komutta onay-<id>). Başlık listesi (gmail liste), WebSearch, tarayıcı serbest.
KARANTINA_MESAJ = ("Dışarıdan gelen metin ana oturuma ham girmez — karantinada oku: "
                   "python3 ops/karantina.py posta <uid> \"soru\" | spool \"soru\" | web <url> \"soru\" | masa <id>. "
                   "Ham metin gerçekten gerekiyorsa Sahip onayı (onay kartı) + komutta onay kimliği.")
SPOOL_OKUYAN = re.compile(r"^(sudo\s+)?(cat|less|more|head|tail|sed|awk|jq|strings|cut|tr|sort|uniq|tac|nl|"
                          r"xxd|od|base64|python3?\b.*\bopen|cp|rsync|scp)\b")


def _ham_dis_metin(k):
    """Ana oturumun güvenilmez metni HAM okuduğu kalıp var mı? (gmail oku, spool okuma, karantina --ham).
    Faz 1 madde 12: karantinanın eşdeğer yolları da buraya — gmail'i import edip oku()/metin alma,
    runpy ile gmail.py oku, ve gmail.py'nin ham mail içeriğini basan 'oku' alt komutu (kabuk ya da kod)."""
    kabuk = _kod_govdeleri(_kod_metni(k))[0]      # commit mesajı / yazılan heredoc metni komut değildir
    if re.search(r"\bgmail\.py\s+(?:--\S+\s+)*oku\b", kabuk):
        return True
    # python3 -m ops.gmail oku / runpy gmail.py oku: kabukta modül çağrısı
    if re.search(r"\b(?:python3?\s+-m\s+(?:ops\.)?gmail|runpy[\s\S]{0,40}gmail\.py)\b[\s\S]{0,60}\boku\b", kabuk):
        return True
    if re.search(r"\bkarantina\.py\b[^;&|\n]*\s--ham\b", kabuk):
        return True
    for _, g in _kod_govdeleri(k)[1]:
        if "email_spool" in g and re.search(r"open\(|read_text|readlines", g):
            return True
        # gmail modülünü içe alıp ham mail gövdesi okumak (import gmail; gmail.oku(...) / from ops.gmail import oku)
        if re.search(r"(?:import\s+gmail\b|from\s+(?:ops\.)?gmail\s+import|from\s+ops\s+import\s+gmail\b|"
                     r"\bgmail\b)", g) and re.search(r"\boku\s*\(|\.oku\b|\bimport\s+oku\b|\boku\s+as\b", g):
            return True
    for parca in PARCA.split(kabuk):
        parca = parca.strip()
        if "email_spool" not in parca:
            continue
        if re.match(r"^(grep|egrep|rg)\b", parca) and re.search(r"\s-[A-Za-z]*[cql][A-Za-z]*\b", parca):
            continue                                 # sayım/var mı: içerik basmaz
        if SPOOL_OKUYAN.match(parca) or re.match(r"^(grep|egrep|rg)\b", parca) or re.search(r"<\s*\S*email_spool", parca):
            return True
    return False


# --- 9. özerk oturum: geri alınmaz eylem YOK (2026-10-28, dış inceleme GPT: "onay regex'le, tanınmayan iş READ") ---
# Gözetimsiz işler (nöbetçi/zamanlayıcı/hedef) köprüde GHOST_OZERK=1 ile koşar; ön onay metin regex'ine bakıyordu,
# kaçan iş yalnız istemdeki "yapma" ricasıyla korunuyordu. Burada EYLEM seviyesinde: deploy, push/commit, silme,
# mail gönderme, dışarıya yazan HTTP, servis/yeniden başlatma, GitHub yazma. Sınıflandırıcı ne derse desin geçmez.
# Okuma/araştırma/taslak/öneri serbest; iş gerçekten eylem istiyorsa sonuç sahibe öneri olarak döner.
OZERK_YASAK = [
    (r"\bgit\s+(?:-[cC]\s+\S+\s+|--[\w-]+(?:=\S+)?\s+)*(push|commit|commit-tree|update-ref|reset\s+--hard|rebase|tag|merge|am|cherry-pick)\b",
     "git yazma (commit = yayın)"),
    # betik dolaylaması (denetim Y5): içeriği kapıdan geçmemiş dosyayı koşturmak
    (r"(?:^|[;&|(]\s*|\s)(?:bash|sh|zsh|python3?|node|perl|ruby|source|\.)\s+(?:-\S+\s+)*['\"]?(?:/tmp/|/var/tmp/|/dev/shm/)",
     "geçici/yerel betik koşturma (içeriği kapıdan geçmedi)"),
    (r"\bfind\b[^;&|\n]*\s-(?:delete|exec\s+rm)\b|\bshred\b|\brm\s+[^;&|\n]*\.\./", "silme (find -delete / shred / ../)"),
    (r"\bwget\b[^;&|\n]*--(?:post|method|body)|\bhttp(?:ie)?\s+(?:POST|PUT|PATCH|DELETE)\b", "dışarıya yazan HTTP"),
    (r"\bwrangler(@\S+)?\b[^;&|\n]*\b(deploy|publish|delete|secret|d1\s+execute|kv\b|r2\b)", "Cloudflare deploy/yazma"),
    (r"(?:^|[;&|(]|&&)\s*(?:\S+=\S*\s+)*(?:\S*/)?deploy\.sh\b|\bnpm\s+publish\b|\bpip\s+upload\b|\btwine\b", "deploy/yayın"),
    (r"\bgmail_gonder\.py\s+gonder\b", "mail gönderme"),
    (r"\bgh\s+(pr|release|repo|issue|api)\s+(create|merge|delete|edit|close|comment|-X)", "GitHub yazma"),
    (r"\brm\s+(-[a-zA-Z]*[rRf][a-zA-Z]*\s+)+(?!/tmp/|\$TMPDIR|\$\{?TMPDIR)", "özyinelemeli/zorla silme (yalnız /tmp serbest)"),
    (r"\b(systemctl|launchctl|service)\s+(?:--?[\w-]+\s+)*(restart|stop|disable|kill|kickstart|bootout|unload)|self_restart\.sh|reboot_vps\.sh|\breboot\b",
     "servis durdurma/yeniden başlatma"),
    (r"\bghost\.masa\s+(sil|tasi|adlandir|geri)\b|\bghost\.kurallar\s+(ekle|sil|kapat|ac)\b", "Masa/kural değişikliği"),
    (r"\bASC_READ_ONLY=0\b|\basc\b[^;&|\n]*\b(submit|upload|create|update|delete|publish)\b", "App Store Connect yazma"),
]
# Dışarıya YAZAN HTTP: veri gövdesi ya da yazma yöntemi; okuma amaçlı POST uçları istisna.
OZERK_HTTP_YAZ = re.compile(r"\bcurl\b[^;&|\n]*(\s-X\s*(POST|PUT|PATCH|DELETE)|--request\s+(POST|PUT|PATCH|DELETE)|"
                            r"\s-d\s|\s--data(-\w+)?\s|\s-F\s|\s--form\s|\s--json\s|\s-T\s|--upload-file)", re.I)
OZERK_OKUMA_POST = re.compile(r"posthog\.com[^\s'\"]*/query|\$\{?POSTHOG_HOST\}?[^\s'\"]*/query|"
                              r"api\.cloudflare\.com/client/v4/graphql|api\.telegram\.org/bot|127\.0\.0\.1|localhost")
OZERK_MCP_YAZ = re.compile(r"^mcp__github[\w-]*__(create_|push_files|merge_|delete_|update_|add_|fork_|issue_write|"
                           r"sub_issue_write|pull_request_review_write|request_copilot)|^mcp__playwright__browser_"
                           r"(fill_form|type|file_upload|run_code_unsafe|evaluate)$|^(Monitor|CronCreate|RemoteTrigger)$")
# Özerk koddan dışarıya yazan istek (requests.post, httpx.put, urlopen(data=…), method="POST")
OZERK_KOD_YAZ = re.compile(r"\b(?:requests|httpx|session|client)\.(?:post|put|patch|delete)\(|method\s*=\s*['\"]"
                           r"(?:POST|PUT|PATCH|DELETE)|urlopen\([^)\n]*data\s*=|Request\([^)\n]*data\s*=", re.I)
# Faz 1 madde 13: kabuk katmanı zaten /tmp betik koşturmayı, deploy/push/rm -r'ı durduruyor; ama INLINE python/node
# (python3 -c, heredoc, node -e) aynı geri alınmaz işi kütüphaneyle yapabilir. Bu yüzden özerk kod gövdesinde:
#   (a) dosya SİLME (os.remove/unlink, shutil.rmtree, Path.unlink, os.rmdir, fs.unlink/rm)
#   (b) alt süreç başlatıp (subprocess/os.system/os.popen/Popen/check_call/run) yasak bir komut koşturma
#       — gövdedeki dize OZERK_YASAK desenlerinden birine uyuyorsa (git push, deploy, systemctl restart, gh create…)
# Okuma/analiz/dosya YAZMA (açık betiğin ürettiği rapor) serbest kalır; yalnız yıkıcı/dışa dönük eylem durur.
OZERK_KOD_SIL = re.compile(
    r"\b(?:os\.(?:remove|unlink|rmdir|removedirs)|shutil\.rmtree|(?:pathlib\.)?Path\([^)\n]*\)\.(?:unlink|rmdir)|"
    r"\.unlink\(|\.rmtree\(|fs\.(?:unlink|rm|rmSync|rmdir)|fsPromises\.(?:unlink|rm|rmdir))\b", re.I)
OZERK_KOD_ALTSUREC = re.compile(r"\b(?:subprocess\.(?:run|call|check_call|check_output|Popen)|os\.system|os\.popen|"
                                r"child_process|execSync|spawnSync|\.exec\()", re.I)


def _ozerk_kod_govde_engel(govde):
    """Bir inline kod gövdesinde (python -c / heredoc / node -e) geri alınmaz eylem var mı?"""
    if OZERK_KOD_YAZ.search(govde) and not OZERK_OKUMA_POST.search(govde):
        return "Özerk işte koddan dışarıya yazan HTTP isteği yapılamaz. Eylemi sahibe öner."
    if OZERK_KOD_SIL.search(govde):
        return "Özerk işte koddan dosya silme (os.remove/shutil.rmtree/unlink) yapılamaz. Eylemi sahibe öner."
    if OZERK_KOD_ALTSUREC.search(govde):
        # subprocess.run(['git','push',...]) liste biçimini kabuk biçimine düzleştir ki OZERK_YASAK
        # (git push, deploy.sh, systemctl restart…) kabuk desenleri liste argümanında da yakalansın.
        duz = re.sub(r"[\[\]'\",]+", " ", govde)
        for desen, ad in OZERK_YASAK:
            if re.search(desen, govde, re.M) or re.search(desen, duz, re.M):
                return ("Özerk işte kod içinden alt süreçle geri alınmaz eylem (%s) koşturulamaz. "
                        "Eylemi sahibe öner." % ad)
    return None


# Faz 1 madde 13: özerk kipte Write/Edit — rapor/araştırma/not (dossier, intel, memory, docs, /tmp) serbest
# kalır; AMA köprü/selfcheck'in KENDİLİĞİNDEN yüklediği çalışır kodu (repo içi ghost/ ve ops/ .py/.sh) özerk
# oturum DEĞİŞTİREMEZ — commit kapısı zaten özerkte kapalı, ama selfcheck ghost/ kodunu boşta yeniden yüklüyor,
# yani denetlenmemiş kod commit beklemeden koşabilir. Bu yol onaya bağlanır.
_OZERK_KOD_YOL = re.compile(r"(?:^|/)jarvis/(?:ghost|ops)/[\w./-]*\.(?:py|sh|bash|zsh|mjs|cjs|js|ts)$")


def _ozerk_yazma_engel(yol):
    if os.environ.get("GHOST_OZERK") != "1":
        return None
    try:
        gercek = os.path.realpath(os.path.expanduser(yol or ""))
    except Exception:
        gercek = yol or ""
    if _OZERK_KOD_YOL.search(gercek):
        return ("Özerk (gözetimsiz) işte köprü/selfcheck'in otomatik yüklediği çalışır koda (jarvis/ghost, "
                "jarvis/ops altındaki betikler) yazılamaz — denetimsiz kod koşabilir. Rapor/araştırma/not "
                "(dossier, intel, memory, docs, /tmp) serbest; kod değişikliğini sahibe öner.")
    return None


def _ozerk_engel(k):
    if os.environ.get("GHOST_OZERK") != "1":
        return None
    kabuk = ham = _kod_govdeleri(_kod_metni(k))[0]
    if not re.search(r"(^|[\s;&|(])(sh|bash|zsh)\s+-\w*c\b|(^|[\s;&|(])(eval|xargs|ssh|nohup|setsid)\s|komut|mac_cmd", kabuk):
        kabuk = re.sub(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"", "''", kabuk)   # tırnak içi metin komut değildir (grep deseni)
    for desen, ad in OZERK_YASAK:
        if re.search(desen, kabuk, re.M):
            return ("Özerk (gözetimsiz) işte geri alınmaz eylem yapılamaz: %s. Araştır, hazırla, ÖNER — "
                    "eylemi Sahip onaylasın (sonuca 'önerilen eylem' olarak yaz)." % ad)
    for parca in PARCA_AG.split(ham):           # adres çoğu zaman tırnak içinde: ham metinde bak
        if OZERK_HTTP_YAZ.search(parca) and not OZERK_OKUMA_POST.search(parca):
            return ("Özerk işte dışarıya yazan HTTP isteği (POST/PUT/DELETE/veri gövdesi) yapılamaz; "
                    "okuma uçları (PostHog query, Cloudflare GraphQL, Telegram bildirimi) serbest. Eylemi sahibe öner.")
    for _, govde in _kod_govdeleri(_kod_metni(k))[1]:
        sebep = _ozerk_kod_govde_engel(govde)
        if sebep:
            return sebep
    return None


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
    if (ROOT_ANAHTAR.search(_kod_govdeleri(k)[0]) and not _ghost_vps_sadece_izinli(k)
            and not _onay_gecerli(k)):
        return ("VPS root anahtarı (ghost_vps) yalnız reboot ve ghost-* servisleri için onaysız; başka iş için "
                "Sahip onayı gerekir. Dosya eşitlemek için root gerekmez: Mac'te -i ~/.ssh/ghost_runtime ve "
                "ghost@VPS kullan. Root gerçekten gerekiyorsa python3 -m ghost.sahne onay ile kart aç.")
    if GIT_KACIS.search(_kod_metni(k)):
        return "Git kancalarını atlatmak (--no-verify, -n, core.hooksPath) kapalı: sır taraması ve test kapısı orada."
    # Faz 1 madde 14: push sır taraması kaçışı (GHOST_SKIP_SIR=1) yalnız Sahip onayıyla — kaçış, sır
    # tarayıcısını kapatıp sırrı GitHub'a gönderme yolu olur. (GHOST_SKIP_TESTS'ten ayrı: o yalnız testi atlar.)
    if re.search(r"\bGHOST_SKIP_SIR=", _kod_metni(k)) and not _onay_gecerli(k):
        return ("Push sır taraması kaçışı (GHOST_SKIP_SIR) yalnız Sahip onayıyla kullanılır — sır GitHub'a "
                "sızabilir. Önce python3 -m ghost.sahne onay ile kart aç, komuta onay kimliğini ekle.")
    if TELEGRAM_YABANCI_BOT.search(k):
        return ("Komutta düz bir Telegram bot kimliği var — Ghost'un botu yalnız $TELEGRAM_BOT_TOKEN ile kullanılır; "
                "başka bir bota veri gönderilemez.")
    kod_disi = _kod_metni(k)
    if GITHUB_YABANCI_YAZ.search(kod_disi) and not _onay_gecerli(k):
        return ("GitHub'da yalnız ornek-org/ghost-bot reposuna yazılır; başka sahibin reposuna push ya da "
                "gist (herkese açık kanal) Sahip onayı ister.")
    kabuk_kal = re.sub(r"'[^']*'|\"[^\"$]*\"", "''", _kod_govdeleri(kod_disi)[0])   # echo "== crontab ==" metindir
    if KALICILIK.search(kabuk_kal) and not _onay_gecerli(k):
        return ("Kalıcı kod bırakmak (crontab, at, systemd-run, ~/.bashrc/.profile, ~/.ssh, ~/.config/systemd) "
                "güvenlik kancasından geçmeden sonra koşar — Sahip onayı (onay kartı + komutta onay kimliği) ister.")
    if FREEZE_SILME.search(kod_disi) and not _onay_gecerli(k):
        return "FREEZE (acil durdurma) bayrağı yalnız Sahip kaldırır: /unfreeze ya da onay kartı + onay kimliği."
    kabuk0 = _kod_govdeleri(kod_disi)[0]
    if KABUK_DOLAYLI.search(re.sub(r"'[^']*'", "''", kabuk0)) and AG_ARACI.search(k):
        return ("Dolaylı değişken/eval (${!X}, eval, declare -x) ağ komutuyla aynı çağrıda kullanılamaz — "
                "kapı hangi değerin gittiğini göremez. Değişkeni adıyla ($AD) kullan.")
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
    kokler = {}
    adlar = _zincir(k, adlar, kokler)
    kabuk, govdeler = _kod_govdeleri(k)
    birimler = [(p, False, []) for p in PARCA_AG.split(kabuk)]
    for cagiran, govde in govdeler:     # kod gövdesi tek birim; çağıran satırdaki $SIR argümanı da sayılır
        ym = YORUMLAYICI.search(cagiran)       # yalnız yorumlayıcıya giden argüman (borunun önceki aşaması değil)
        cagiran = cagiran[ym.start():] if ym else cagiran
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
        if YABANCI_KIMLIK.search(parca):
            return ("Sır (%s) yabancı bir kimlikle (düz Bearer/API anahtarı/user:parola@) aynı istekte — izinli "
                    "serviste başkasının hesabına gidebilir. Kendi anahtarını $AD ile kullan."
                    % ", ".join(sorted(kullanilan)))
        yab = _github_yabanci_yazma(parca)
        if yab:
            return ("Sır (%s) ile başka sahibin reposuna (%s) yazılamaz — issue/yorum herkese açık kanaldır."
                    % (", ".join(sorted(kullanilan)), yab))
        for ad, host in HOST_DEGISKEN.items():
            parca = re.sub(r"\$\{?%s\}?" % ad, "https://" + host, parca)
            parca = re.sub(ORTAM_OKU.replace("%s", ad) + r"[\]\)]?", "'https://%s'" % host, parca)
        if kod:
            hostlar = URL.findall(parca)
            if not hostlar:
                return ("Sır (%s) koddan ağa gidiyor ama hedef adres kodda açık değil. URL'yi koda açıkça yaz."
                        % ", ".join(sorted(kullanilan)))
            yasak = _yasak_hostlar(hostlar, kullanilan, kokler)
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
        yasak = _yasak_hostlar(hostlar, kullanilan, kokler)
        if yasak:
            return ("Sır değişkeni (%s) izinsiz adrese gidemez: %s. Her sır yalnız kendi servisine gider "
                    "(ops/guvenlik_kapisi.py SIR_HEDEF / IZINLI_ALAN); yeni alan Sahip onayıyla eklenir."
                    % (", ".join(sorted(kullanilan)), ", ".join(sorted(set(yasak)))))
    oz = _ozerk_engel(k)
    if oz:
        return oz
    kc = _kisisel_cikis(k)
    if kc:
        return kc
    if _ham_dis_metin(k) and not _onay_gecerli(k):
        return KARANTINA_MESAJ
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
    # Kum havuzu oturumu (sabah brifingi: araçları yalnız WebSearch+WebFetch, sırsız, boş dizin) WebFetch kullanabilir.
    # İşaret Claude SÜRECİNİN ortamındadır (env -i GHOST_KUM=1); ana oturumun Bash'i onu ayarlayamaz — kanca
    # oturumun değil, onu başlatan claude sürecinin çocuğudur.
    if arac == "WebFetch" and os.environ.get("GHOST_KUM") != "1":
        return KARANTINA_MESAJ + " (WebFetch yerine: python3 ops/karantina.py web <url> \"soru\")"
    # sosumi fetchExternalDocumentation keyfi dış URL çeker (Apple dışı) → WebFetch'le aynı tehdit
    # (Faz 1 madde 12). fetchAppleDocumentation / searchAppleDocumentation / fetchAppleVideoTranscript
    # Apple'ın kendi dokümanı, güvenli. Kum havuzunda (GHOST_KUM=1) serbest.
    if arac == "mcp__sosumi__fetchExternalDocumentation" and os.environ.get("GHOST_KUM") != "1":
        return (KARANTINA_MESAJ + " (sosumi fetchExternalDocumentation dış URL çeker; URL'yi karantinada "
                "oku: python3 ops/karantina.py web <url> \"soru\")")
    if os.environ.get("GHOST_OZERK") == "1" and OZERK_MCP_YAZ.match(arac):
        return "Özerk (gözetimsiz) işte GitHub'a yazılamaz (%s). Hazırla ve sahibe öner." % arac
    if arac in ("Bash", "Monitor"):     # Monitor da kabuk komutu koşturur (denetim K4)
        return karar_bash(str(gi.get("command") or gi.get("cmd") or ""))
    if arac in ("Read", "NotebookRead", "Grep", "Glob"):
        yol = str(gi.get("file_path") or gi.get("path") or gi.get("notebook_path") or "")
        desen = str(gi.get("pattern") or gi.get("glob") or "")
        try:
            gercek = os.path.realpath(os.path.expanduser(yol)) if yol else ""
        except Exception:
            gercek = yol
        if SIR_DOSYA.search(yol) or SIR_DOSYA.search(gercek) or (
                arac in ("Grep", "Glob") and SIR_DOSYA.search(str(gi.get("glob") or ("" if arac == "Grep" else desen)))):
            return "Sır dosyası okunamaz. Anahtarı kullanmak için ortamdaki $DEGISKEN'i kullan."
        if arac in ("Read", "Grep") and ("email_spool" in yol or "email_spool" in gercek):
            return KARANTINA_MESAJ
        if arac == "Grep" and not yol and SIR_DOSYA.search(desen):
            return None
    if arac in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        yol = str(gi.get("file_path") or gi.get("notebook_path") or "")
        if _yol_korunan(yol):
            return "Güvenlik kapısı ve Claude ayar dosyaları yalnız sahibin gerçek onayıyla (Bash + onay kimliği) değişir."
        oz = _ozerk_yazma_engel(yol)
        if oz:
            return oz
        if re.search(r"secrets\.env$|(^|/)\.env$|\.secrets/", yol):
            return "Sır dosyası araçla düzenlenemez; yeni anahtar eklemek için: echo 'AD=değer' >> secrets.env"
        if KALICI_YOL.search(os.path.expanduser(yol)):
            return ("Kabuk açılış/ssh/systemd dosyasına yazmak kalıcı kod bırakır — Sahip onayı ister "
                    "(Bash + onay kimliği).")
        icerik = _yazilan_icerik(gi)
        if icerik and not _depo_icinde(yol) and _calisir_dosya(yol, icerik):
            # depo dışına (çoğu /tmp) yazılan betik sonra koşar: içeriği Bash kurallarından geçsin (denetim Y6)
            _ICERIK_TARAMA[0] = True
            kod = re.search(r"\.(py|js|mjs|cjs|ts)$", yol) or re.match(r"#!\S*(python|node)", icerik)
            sebep = (karar_bash("python3 - <<'GHOSTKAPI'\n%s\nGHOSTKAPI" % icerik) if kod
                     else karar_bash(icerik) or karar_bash("python3 - <<'GHOSTKAPI'\n%s\nGHOSTKAPI" % icerik))
            _ICERIK_TARAMA[0] = False
            if sebep:
                return "Depo dışına yazılan dosya: " + sebep
    return None


def _yazilan_icerik(gi):
    parcalar = [gi.get("content"), gi.get("new_string"), gi.get("new_source")]
    for e in gi.get("edits") or []:
        if isinstance(e, dict):
            parcalar.append(e.get("new_string"))
    return "\n".join(str(p) for p in parcalar if p)


def _calisir_dosya(yol, icerik):
    """Sonradan koşturulabilecek dosya mı? (betik uzantısı ya da #!). Not/rapor (.md/.txt/.json) değil."""
    return bool(re.search(r"\.(py|js|mjs|cjs|ts|sh|bash|zsh|pl|rb|command)$", yol) or icerik.lstrip().startswith("#!")
                or (not os.path.splitext(yol)[1] and "/tmp/" in yol))


def _depo_icinde(yol):
    """Yol, ev dizini altındaki bir git deposunun içinde mi? (Depo kodu commit'le izlenir; /tmp izlenmez.)"""
    try:
        d = os.path.dirname(os.path.realpath(os.path.expanduser(yol)))
    except Exception:
        return False
    while d.startswith(EV + os.sep):
        if os.path.exists(os.path.join(d, ".git")):
            return True
        d = os.path.dirname(d)
    return False


def _log(satir):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%F %T"), satir[:500]))
    except OSError:
        pass


RISKLI_HATA = re.compile(r"\b(curl|wget|nc|ncat|socat|ssh|scp|rsync|python3?|node|perl|ruby|bash|sh|eval|"
                         r"crontab|gmail|smtp|onay-|secrets|environ|FREEZE|sahne|kokpit)\b|\.env\b|https?://|mcp__", re.I)


def _hata_karari(ham):
    """Kapı bozulursa (2026-10-28, denetim Y1: eskiden her hata İZİN'di): ağ/yorumlayıcı/sır/onay
    içeren girdi reddedilir; düz okuma (ls, git status, Read) geçer ki oturum kilitlenmesin."""
    try:
        d = json.loads(ham)
        ham = "%s %s" % (d.get("tool_name") if str(d.get("tool_name", "")).startswith("mcp__") else "",
                         json.dumps(d.get("tool_input"), ensure_ascii=False))
    except Exception:
        pass
    return bool(RISKLI_HATA.search(ham or ""))


def _ret(sebep):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "Ghost güvenlik kapısı: " + sebep}}, ensure_ascii=False))
    return 0


def main():
    ham = sys.stdin.read()
    try:
        girdi = json.loads(ham)
        if not isinstance(girdi, dict):
            raise ValueError("girdi nesne değil")
        sebep = karar(girdi)
    except Exception as e:
        _log("HATA %r" % e)
        if _hata_karari(ham):
            return _ret("kapı bu girdiyi değerlendiremedi (hata günlükte) — riskli komut güvenli tarafta reddedildi. "
                        "Komutu sadeleştir ya da sahibe bildir.")
        return 0
    if not sebep:
        return 0
    gi = girdi.get("tool_input") or {}
    ozet = str(gi.get("command") or gi.get("file_path") or gi.get("url") or "")[:200]
    if sebep.startswith("Araç girdisinde bir sırrın"):
        ozet = "(girdi sır içerdiği için günlüğe yazılmadı)"
    _log("ENGEL %s | %s | %s" % (girdi.get("tool_name"), sebep[:80], ozet))
    return _ret(sebep)


if __name__ == "__main__":
    sys.exit(main())
