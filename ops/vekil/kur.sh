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
set -eu
J=/home/ghost/jarvis
SRC=$J/ops/vekil
DURUM=/var/lib/ghost-vekil
LIB=/usr/local/lib/ghost-vekil
ANAHTARLAR="CLOUDFLARE_API_TOKEN POSTHOG_API_KEY POSTHOG_HOST GHOST_GITHUB_TOKEN TELEGRAM_BOT_TOKEN ELEVENLABS_API_KEY SENTRY_ACCESS_TOKEN ASC_ISSUER_ID ASC_KEY_ID APNS_TEAM_ID APNS_KEY_ID GITHUB_MCP_TOKEN GITHUB_MCP_YAZ_TOKEN GMAIL_ADDRESS GMAIL_APP_PASSWORD HF_API_KEY_ID HF_API_KEY_SECRET WIKIDATA_BOT_PASS TELEGRAM_OWNER_ID CLOUDFLARE_ACCOUNT_ID POSTHOG_PROJECT_ID"
[ "$(id -u)" = 0 ] || { echo "root gerekir" >&2; exit 1; }

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
                       ("mcp-sentry.json", "SENTRY_ACCESS_TOKEN", ("env", "SENTRY_ACCESS_TOKEN"))):
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
            m = re.match(r"\s*(ASC_KEY_PATH|APNS_KEY_PATH)\s*=\s*(.*)$", satir.strip())
            if m:
                yollar.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'").replace("~", "/home/ghost", 1))
    except OSError:
        pass
for ad, dosya in (("ASC_KEY_PATH", "asc.p8"), ("APNS_KEY_PATH", "apns.p8")):
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
