#!/bin/sh
# Ghost sır vekili kurulumu (Faz 2 madde 16, 2026-10-28) — ROOT ister (YubiKey: ghost_root_sk).
#   Mac'ten: /opt/homebrew/bin/ssh -i ~/.ssh/ghost_root_sk root@203.0.113.10 "sh /home/ghost/jarvis/ops/vekil/kur.sh"
# Ne yapar (tekrar koşulabilir; her koşuda sır kopyası tazelenir):
#   1. ghost-vekil sistem kullanıcısı, /var/lib/ghost-vekil (700), kod /usr/local/lib/ghost-vekil (root, 644)
#   2. sirlar.env: vekilin taşıdığı anahtarlar secrets.env/.env'den (değer ekrana BASILMAZ)
#   3. servis açılır, ghost kullanıcısından _durum denenir
# 1. aşama: Cloudflare, PostHog, GitHub REST, Telegram. 2. aşama: ElevenLabs, Sentry, ASC (JWT), APNs JWT, GitHub MCP.
# 3. aşama: Gmail SMTP (onay kapılı) + IMAP, Higgsfield, Wikidata, git (github-git). Anahtarlar ghost'ta DA kalır (4. aşamada silinir).
# Faz 2 devamı: izin listesi (hedef = kendi hesaplarımız) + ghost-kimlik (özerk oturum → yalnız okuma).
# 6. tur (2026-10-29): PostHog MCP anahtarı (POSTHOG_MCP_KEY, mcp-posthog.json başlığından), FREEZE ortak dizini
#   (/run/ghost-freeze, tmpfiles.d; köprü 1/0 yazar, vekil+onay okur), DON=1 anahtar yenileme kipi:
#   Mac'ten: ssh -i ~/.ssh/ghost_root_sk root@VPS "DON=1 sh /home/ghost/jarvis/ops/vekil/kur.sh"  → AD=DEĞER satırlarını
#   yapıştır, Ctrl-D. Değer ekrana basılmaz, kabuk geçmişine girmez; yalnız ANAHTARLAR'daki adlar; TELEGRAM_BOT_TOKEN onay
#   servisinin ayar.json'una, SENTRY_ACCESS_TOKEN ghost'un mcp-sentry.json'una da yazılır; servisler yeniden başlar,
#   her yenilenen servis vekilden sınanır (tokens/verify, getMe, /user, …).
#   Root yedek (R4): Mac'te `bash ops/vekil/mac_root_yedek.sh` — /var/lib/ghost-onay + ghost-vekil tar akışı Mac'te
#   Keychain parolasıyla gpg'lenir, diske düz metin düşmez.
set -eu
J=/home/ghost/jarvis
SRC=$J/ops/vekil
DURUM=/var/lib/ghost-vekil
LIB=/usr/local/lib/ghost-vekil
ANAHTARLAR="CLOUDFLARE_API_TOKEN POSTHOG_API_KEY POSTHOG_HOST GHOST_GITHUB_TOKEN TELEGRAM_BOT_TOKEN ELEVENLABS_API_KEY SENTRY_ACCESS_TOKEN ASC_ISSUER_ID ASC_KEY_ID ASC_FIN_KEY_ID APNS_TEAM_ID APNS_KEY_ID GITHUB_MCP_TOKEN GITHUB_MCP_YAZ_TOKEN POSTHOG_MCP_KEY GMAIL_ADDRESS GMAIL_APP_PASSWORD HF_API_KEY_ID HF_API_KEY_SECRET WIKIDATA_BOT_PASS TELEGRAM_OWNER_ID CLOUDFLARE_ACCOUNT_ID POSTHOG_PROJECT_ID"
[ "$(id -u)" = 0 ] || { echo "root gerekir" >&2; exit 1; }

# ---------------------------------------------------------------- DON=1: anahtar yenileme (stdin)
if [ "${DON:-}" = 1 ]; then
    [ -s "$DURUM/sirlar.env" ] || { echo "sirlar.env yok — önce kurulum" >&2; exit 1; }
    echo "== anahtar yenileme: AD=DEĞER satırlarını yapıştır, bitince Ctrl-D (değer ekrana basılmaz)"
    # 2026-10-29 (GPT kod incelemesi, Sahip iletti): `python3 -` kaynağı stdin'den okuyordu → yapıştırılan AD=DEĞER satırları
    # python'a hiç ulaşmıyor, her seferinde "yeni değer yok" çıkardı. Betik fd 3'ten, stdin terminal/boru olarak kalır.
    SONUC="$(ANAHTARLAR="$ANAHTARLAR" /usr/bin/python3 -I /dev/fd/3 "$DURUM/sirlar.env" "$J/mcp-sentry.json" /var/lib/ghost-onay/ayar.json 3<<'EOF'
import json, os, re, sys
hedef, sentry_mcp, onay_ayar = sys.argv[1:4]
izinli = set(os.environ["ANAHTARLAR"].split())
yeni = {}
for satir in sys.stdin:
    satir = satir.strip()
    if not satir or satir.startswith("#"):
        continue
    m = re.match(r"(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$", satir)
    if not m:
        print("atlandı (AD=DEĞER değil): %s…" % satir[:12])
        continue
    k, v = m.group(1), m.group(2).strip().strip('"').strip("'")
    if k not in izinli:
        print("atlandı (vekil anahtarı değil): %s" % k)
        continue
    if v:
        yeni[k] = v
if not yeni:
    sys.exit("yeni değer yok")
mevcut, sira = {}, []
for satir in open(hedef, encoding="utf-8"):
    m = re.match(r"([A-Z_][A-Z0-9_]*)=(.*)$", satir.rstrip("\n"))
    if m:
        mevcut[m.group(1)] = m.group(2); sira.append(m.group(1))
degisen = sorted(k for k in yeni if mevcut.get(k) != yeni[k])
mevcut.update(yeni)
sira += [k for k in yeni if k not in sira]
fd = os.open(hedef + ".yeni", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    for k in sira:
        f.write("%s=%s\n" % (k, mevcut[k]))
os.replace(hedef + ".yeni", hedef)
def json_yaz(yol, d):
    st = os.stat(yol)
    fd = os.open(yol + ".yeni", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.chown(yol + ".yeni", st.st_uid, st.st_gid); os.chmod(yol + ".yeni", st.st_mode & 0o777)
    os.replace(yol + ".yeni", yol)
yan = []
if "TELEGRAM_BOT_TOKEN" in degisen and os.path.exists(onay_ayar):
    d = json.load(open(onay_ayar, encoding="utf-8")); d["telegram_token"] = yeni["TELEGRAM_BOT_TOKEN"]; json_yaz(onay_ayar, d); yan.append("onay-ayar")
if "SENTRY_ACCESS_TOKEN" in degisen and os.path.exists(sentry_mcp):
    cfg = json.load(open(sentry_mcp, encoding="utf-8"))
    for srv in cfg.get("mcpServers", {}).values():
        if isinstance(srv.get("env"), dict) and "SENTRY_ACCESS_TOKEN" in srv["env"]:
            srv["env"]["SENTRY_ACCESS_TOKEN"] = yeni["SENTRY_ACCESS_TOKEN"]
    json_yaz(sentry_mcp, cfg); yan.append("mcp-sentry")
print("DEGISEN=%s" % ",".join(degisen))
print("YAN=%s" % ",".join(yan))
print("yenilenen %d anahtar: %s (değişmeyen %d)" % (len(degisen), ", ".join(degisen) or "yok", len(yeni) - len(degisen)))
EOF
)"
    echo "$SONUC" | grep -v '^\(DEGISEN\|YAN\)='
    DEGISEN="$(echo "$SONUC" | sed -n 's/^DEGISEN=//p')"
    chown ghost-vekil:ghost-vekil "$DURUM/sirlar.env"
    systemctl restart ghost-vekil
    echo "$SONUC" | grep -q '^YAN=.*onay-ayar' && systemctl restart ghost-onay
    i=0; until runuser -u ghost -- /usr/bin/python3 -I -c "import socket; socket.create_connection(('127.0.0.1', 8797), 1)" 2>/dev/null || [ $i -ge 50 ]; do sleep 0.1; i=$((i+1)); done
    echo "== sınama (vekilden, ghost olarak):"
    DEGISEN="$DEGISEN" runuser -u ghost -- /usr/bin/python3 -I - <<'EOF'
import json, os, urllib.request
T = "http://127.0.0.1:8797"
SINAMA = {"CLOUDFLARE_API_TOKEN": ("/cloudflare/client/v4/user/tokens/verify", '"success":true'),
          "TELEGRAM_BOT_TOKEN": ("/telegram/getMe", '"ok":true'),
          "GHOST_GITHUB_TOKEN": ("/github/user", '"login"'),
          "POSTHOG_API_KEY": ("/posthog/api/projects/@current/", '"id"'),
          "ELEVENLABS_API_KEY": ("/elevenlabs/v1/user", '"subscription"'),
          "SENTRY_ACCESS_TOKEN": ("/sentry/api/0/organizations/", '"slug"'),
          "GITHUB_MCP_TOKEN": ("/github-mcp/mcp/readonly", None),
          "POSTHOG_MCP_KEY": ("/posthog-mcp/mcp", None)}
d = json.loads(urllib.request.urlopen(T + "/_durum", timeout=5).read())
print("vekil: ok=%s eksik=%s" % (d.get("ok"), d.get("eksik")))
for ad in [a for a in os.environ.get("DEGISEN", "").split(",") if a]:
    yol, bekle = SINAMA.get(ad, (None, None))
    if not yol:
        print("  %s: elle sına (otomatik sınama yok)" % ad); continue
    try:
        r = urllib.request.urlopen(urllib.request.Request(T + yol, headers={"Authorization": "Bearer vekil", "Accept": "application/json, text/event-stream"}), timeout=20)
        g = r.read(4000).decode("utf-8", "replace")
        ok = (bekle in g) if bekle else (r.status < 400)
    except Exception as e:
        kod = getattr(e, "code", None)
        ok = bekle is None and kod in (405, 406)      # MCP uçları GET'e 405/406 döner ama kimlik 401 dönerdi
        g = "%s %s" % (type(e).__name__, kod or e)
    print("  %s: %s" % (ad, "TAMAM" if ok else "HATA %s" % g[:80].replace("\n", " ")))
EOF
    echo "== eski anahtarları panelden İPTAL et (yeni çalışıyorsa). Tamam."
    exit 0
fi

echo "== kaynak özetleri:"
sha256sum "$SRC/vekil_servisi.py" "$SRC/ghost-vekil.service" "$SRC/kimlik_servisi.py" "$SRC/ghost-kimlik.service"

id ghost-vekil >/dev/null 2>&1 || useradd --system --home-dir "$DURUM" --no-create-home --shell /usr/sbin/nologin ghost-vekil
install -d -o ghost-vekil -g ghost-vekil -m 700 "$DURUM"
install -d -o root -g root -m 755 "$LIB"
install -o root -g root -m 644 "$SRC/vekil_servisi.py" "$LIB/vekil_servisi.py"
install -o root -g root -m 644 "$SRC/ghost-vekil.service" /etc/systemd/system/ghost-vekil.service
# Faz 2 devamı: izin listesi + özerk kimliği (vekil yazma isteğinde ghost-kimlik'e sorar)
install -o root -g root -m 644 "$SRC/kimlik_servisi.py" "$LIB/kimlik_servisi.py"
install -o root -g root -m 644 "$SRC/ghost-kimlik.service" /etc/systemd/system/ghost-kimlik.service

/usr/bin/python3 -I - "$DURUM/sirlar.env" "$J/secrets.env" "$J/.env" $ANAHTARLAR <<'EOF'
import json, os, re, shutil, sys
hedef, kaynaklar, istenen = sys.argv[1], sys.argv[2:4], sys.argv[4:]
# 4. tur Y4: ghost'un yazabildiği .env/secrets.env, vekildeki MEVCUT anahtarı (izin listesi çapaları dahil:
# TELEGRAM_OWNER_ID, CLOUDFLARE_ACCOUNT_ID, POSTHOG_PROJECT_ID) sessizce ezemez. Varsayılan: yalnız EKSİK anahtar
# eklenir; mevcut bir değeri değiştirmek için kurulumu GUNCELLE="AD1 AD2" ile koş (bilinçli, root).
guncelle = set(os.environ.get("GUNCELLE", "").split())
# 2026-10-28 Faz 2 devamı: BİRLEŞTİR, sıfırdan yazma. 4. aşamada anahtarlar ghost'tan silindi; kaynakta olmayan ya da
# yer tutucu ("vekil") olan değer mevcut sirlar.env'deki gerçeği EZMEZ. Kaynakta gerçek yeni değer varsa günceller.
YER_TUTUCU = {"", "vekil", "bearer vekil", "token vekil"}
mevcut = {}
try:
    for satir in open(hedef, encoding="utf-8"):
        m = re.match(r"([A-Z_][A-Z0-9_]*)=(.*)$", satir.rstrip("\n"))
        if m:
            mevcut[m.group(1)] = m.group(2)
except OSError:
    pass
d = {}
# MCP ayarlarındaki token'lar (2. aşama): github uzak MCP başlığı, sentry yerel MCP ortamı
J = os.path.dirname(kaynaklar[0])
for dosya, ad, yol in (("mcp-github.json", "GITHUB_MCP_TOKEN", ("headers", "Authorization")),
                       ("mcp-github-yaz.json", "GITHUB_MCP_YAZ_TOKEN", ("headers", "Authorization")),
                       ("mcp-sentry.json", "SENTRY_ACCESS_TOKEN", ("env", "SENTRY_ACCESS_TOKEN")),
                       ("mcp-posthog.json", "POSTHOG_MCP_KEY", ("headers", "Authorization"))):
    try:
        v = list(json.load(open(os.path.join(J, dosya), encoding="utf-8"))["mcpServers"].values())[0]
        for k in yol:
            v = v[k]
        d[ad] = v.split()[-1]
    except Exception:
        pass
for k in kaynaklar:
    try:
        for satir in open(k, encoding="utf-8"):
            m = re.match(r"\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$", satir.rstrip("\n"))
            if m and m.group(1) in istenen and m.group(2).strip():
                d.setdefault(m.group(1), m.group(2).strip())
    except OSError:
        pass
d = {k: v for k, v in d.items() if v.strip().lower() not in YER_TUTUCU}
atlanan = sorted(k for k, v in d.items() if k in mevcut and mevcut[k] != v and k not in guncelle)
d = {k: v for k, v in d.items() if k not in mevcut or k in guncelle}
if atlanan:
    print("UYARI kaynakta farklı değer var, EZİLMEDİ (GUNCELLE=\"%s\" ile bilinçli güncelle): %s" % (" ".join(atlanan), ", ".join(atlanan)))
yeni = {k: v for k, v in d.items() if mevcut.get(k) != v}
d = dict(mevcut, **d)
fd = os.open(hedef + ".yeni", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    for k in list(istenen) + sorted(set(mevcut) - set(istenen)):
        if k in d:
            f.write("%s=%s\n" % (k, d[k]))
print("sirlar.env: korunan %d, eklenen/güncellenen %d (%s)" % (len(mevcut), len(yeni), ", ".join(sorted(yeni)) or "yok"))
os.replace(hedef + ".yeni", hedef)
# .p8 anahtarları (JWT'yi vekil imzalar): ASC_KEY_PATH / APNS_KEY_PATH'in gösterdiği dosya
yollar = {}
for k in kaynaklar:
    try:
        for satir in open(k, encoding="utf-8"):
            m = re.match(r"\s*(ASC_KEY_PATH|APNS_KEY_PATH|ASC_FIN_KEY_PATH)\s*=\s*(.*)$", satir.strip())
            if m:
                yollar.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'").replace("~", "/home/ghost", 1))
    except OSError:
        pass
for ad, dosya in (("ASC_KEY_PATH", "asc.p8"), ("APNS_KEY_PATH", "apns.p8"), ("ASC_FIN_KEY_PATH", "asc-finans.p8")):
    y = yollar.get(ad)
    if y and os.path.exists(y):
        shutil.copyfile(y, os.path.join(os.path.dirname(hedef), dosya))
        os.chmod(os.path.join(os.path.dirname(hedef), dosya), 0o600)
        print("%s: kopyalandı" % dosya)
    else:
        print("%s: kaynak yok (%s)" % (dosya, ad))
print("sirlar.env: %d/%d anahtar (%s)" % (len(d), len(istenen), ", ".join(k for k in istenen if k not in d) or "eksik yok"))
EOF
chown ghost-vekil:ghost-vekil "$DURUM/sirlar.env"
for p8 in "$DURUM"/*.p8; do [ -f "$p8" ] && chown ghost-vekil:ghost-vekil "$p8" || true; done

# 3. aşama: vekilin SMTP aktarıcısı onay servisine "bu onay az önce tüketildi mi?" diye sorar → izinli uid
if [ -f /var/lib/ghost-onay/ayar.json ]; then
    VUID="$(id -u ghost-vekil)" /usr/bin/python3 -I - <<'EOF'
import json, os
p = "/var/lib/ghost-onay/ayar.json"
d = json.load(open(p, encoding="utf-8"))
u = int(os.environ["VUID"])
if u not in d.get("izinli_uid", []):
    d.setdefault("izinli_uid", []).append(u)
    tmp = p + ".yeni"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f)
    st = os.stat(p)
    os.chown(tmp, st.st_uid, st.st_gid)
    os.replace(tmp, p)
    print("onay servisi: ghost-vekil izinli uid eklendi")
EOF
    systemctl restart ghost-onay
fi

# 6. tur: FREEZE ortak dizini — köprü (ghost) 1/0 yazar, vekil + onay (ProtectHome) /run'dan okur
printf 'd /run/ghost-freeze 0755 ghost ghost -\n' > /etc/tmpfiles.d/ghost-freeze.conf
systemd-tmpfiles --create /etc/tmpfiles.d/ghost-freeze.conf
runuser -u ghost -- sh -c '[ -f /run/ghost-freeze/durum ] || printf "0\n" > /run/ghost-freeze/durum'
echo "== freeze dizini: $(ls -ld /run/ghost-freeze | cut -c1-30) durum=$(cat /run/ghost-freeze/durum)"

systemctl daemon-reload
systemctl enable ghost-kimlik ghost-vekil >/dev/null 2>&1
systemctl restart ghost-kimlik
systemctl restart ghost-vekil
i=0; until runuser -u ghost -- /usr/bin/python3 -I -c "import socket; socket.create_connection(('127.0.0.1', 8797), 1)" 2>/dev/null || [ $i -ge 50 ]; do sleep 0.1; i=$((i+1)); done
echo "== vekil: $(runuser -u ghost -- /usr/bin/python3 -I -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8797/_durum', timeout=5).read().decode())")"
echo "== kurulan:"; sha256sum "$LIB/vekil_servisi.py" "$LIB/kimlik_servisi.py"
systemctl is-active ghost-kimlik
ls -la "$DURUM"
systemctl is-active ghost-vekil
echo TAMAM
