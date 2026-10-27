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
    r"(^|[;&|(`\n]\s*)printenv\s+\w|/proc/[\w$]+/environ|"
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
AG_ARACI = re.compile(r"\b(curl|wget|nc|ncat|socat|telnet|ssh|scp|sftp|rsync|ftp|"
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
)
KORUNAN_AD = re.compile(r"(\.claude/settings(\.local)?\.json|guvenlik_kapisi\.py)")
_P = r"(?:\.claude/settings(?:\.local)?\.json|guvenlik_kapisi\.py)"
DEGISTIRME = re.compile(
    r"(?<![0-9&])>{1,2}\s*['\"]?\S*" + _P + "|"
    r"\b(?:sed\s+-i|perl\s+-[a-z]*i|mv|rm|ln|truncate|chmod|tee|git\s+(?:checkout|restore|apply))\b[^;&|\n]*" + _P + "|"
    r"\b(?:cp|install|rsync|dd)\b[^;&|\n#]*\s['\"]?\S*" + _P + r"['\"]?\s*(?:$|[;&|\n#])|"
    r"open\([^)\n]*" + _P + r"[^)\n]*,\s*['\"][wax+]|" + _P + r"[^\n]{0,40}\)\.write_(?:text|bytes)|"
    r"disableAllHooks", re.M)
ONAY = re.compile(r"\bonay-[0-9a-f]{16}\b")


# Gizli olmayan yapılandırma (kimlik/host/yol): ağ komutunda geçmesi sızıntı değil.
GIZLI_DEGIL = {"NTFY_TOPIC", "CLOUDFLARE_ACCOUNT_ID", "POSTHOG_HOST", "POSTHOG_PROJECT_ID", "ASC_ISSUER_ID",
               "ASC_KEY_ID", "ASC_KEY_PATH", "APNS_KEY_ID", "APNS_TEAM_ID", "APNS_KEY_PATH", "APNS_TOPIC",
               "ELEVENLABS_VOICE_ID", "GMAIL_ADDRESS", "WIKIDATA_USER", "APPLE_ID", "HF_API_KEY_ID"}
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


def _alan_izinli(host):
    host = host.lower().rstrip(".")
    return any(host == a or host.endswith("." + a) for a in IZINLI_ALAN)


def _onay_gecerli(metin):
    """Komutta sahibin gerçek kapısından kaydedilmiş, kullanılmamış onay var mı? Varsa tüket."""
    for eid in ONAY.findall(metin or ""):
        try:
            sys.path.insert(0, KOK)
            from ghost import sahne
            if sahne.onay_gecerli(eid):
                sahne.onay_tuket(eid)
                return eid
        except Exception:
            return None
    return None


def _yol_korunan(yol):
    try:
        y = os.path.realpath(os.path.expanduser(yol or ""))
    except Exception:
        return False
    return y in KORUNAN


def karar_bash(komut):
    """Engel sebebi (str) ya da None."""
    k = re.sub(r"\\[ \t]*\n", " ", _yurutulen(komut or ""))     # satır devamı (\) tek komut
    if _sir_okuma(k):
        return ("Sır dosyası okunamaz/kopyalanamaz (secrets.env, .env, ~/.secrets, *.p8, ssh anahtarı). "
                "Anahtarı kullanmak için ortamdaki $DEGISKEN'i komutta doğrudan kullan; değeri ekrana basma.")
    if ORTAM_DOKUM.search(k):
        return "Ortam topluca dökülemez (env/printenv/export -p/environ). Gereken tek değişkeni $AD ile kullan."
    if SMTP_KULLANIM.search(k) and not re.match(r"^\s*(cd\s+\S+\s*&&\s*)?python3?\s+(\S*/)?ops/gmail_gonder\.py\b", k):
        return ("Mail yalnız ops/gmail_gonder.py hazirla → Sahip onayı → gonder --onay yoluyla gider; "
                "SMTP/Gmail şifresi doğrudan kullanılamaz.")
    adlar = _sir_adlari() | _yakalananlar(k)[1] | {
        m for m in re.findall(r"\$\{?([A-Z][A-Z0-9_]*)", k) if SIR_AD.search(m)} - GIZLI_DEGIL
    for parca in PARCA_AG.split(k):
        kullanilan = [a for a in adlar if re.search(r"\$\{?%s\b" % re.escape(a), parca)]
        if not kullanilan or not AG_ARACI.search(parca):
            continue
        for ad, host in HOST_DEGISKEN.items():
            parca = re.sub(r"\$\{?%s\}?" % ad, "https://" + host, parca)
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
    if DEGISTIRME.search(k):
        if _onay_gecerli(k):
            return None
        return ("Güvenlik kapısı ve Claude ayar dosyaları yalnız sahibin gerçek onayıyla değişir: "
                "önce python3 -m ghost.sahne onay ile kart aç, Sahip onaylayınca komuta onay kimliğini ekle.")
    return None


def karar(girdi):
    arac = girdi.get("tool_name") or ""
    gi = girdi.get("tool_input") or {}
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
    _log("ENGEL %s | %s | %s" % (girdi.get("tool_name"), sebep[:80],
                                 str(gi.get("command") or gi.get("file_path") or "")[:200]))
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "Ghost güvenlik kapısı: " + sebep}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
