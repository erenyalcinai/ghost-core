#!/bin/sh
# Ghost onay servisi kurulumu (Faz 1 madde 10, 2026-10-28) — ROOT ister (YubiKey: ghost_root_sk).
#   Mac'ten: /opt/homebrew/bin/ssh -i ~/.ssh/ghost_root_sk root@203.0.113.10 "sh /home/ghost/jarvis/ops/onay/kur.sh"
# Ne yapar (sırayla, her adım tekrar koşulabilir):
#   1. ghost-onay sistem kullanıcısı, /var/lib/ghost-onay (700), kod /usr/local/lib/ghost-onay (root, 644)
#   2. ayar.json: sahip Telegram id + bot token .env'den (değer ekrana BASILMAZ), izinli uid = ghost
#   3. ghost.db'deki AKTİF Kokpit cihaz anahtarları servise taşınır
#   4. servis açılır, ghost kullanıcısından ping denenir
#   5. ancak ping ve taşıma tuttuysa ghost.db'deki anahtarlar silinir (artık yalnız serviste)
#   6. güvenlik kapısının yeni sürümü kurulur (servise soran sürüm)
set -eu
J=/home/ghost/jarvis
SRC=$J/ops/onay
DURUM=/var/lib/ghost-onay
LIB=/usr/local/lib/ghost-onay
[ "$(id -u)" = 0 ] || { echo "root gerekir" >&2; exit 1; }

echo "== kaynak özetleri (kurulandan sonra /usr/local altında aynı olmalı):"
sha256sum "$SRC/onay_servisi.py" "$SRC/ghost-onay.service" "$J/ops/guvenlik_kapisi.py"

# 1
id ghost-onay >/dev/null 2>&1 || useradd --system --home-dir "$DURUM" --no-create-home --shell /usr/sbin/nologin ghost-onay
install -d -o ghost-onay -g ghost-onay -m 700 "$DURUM"
install -d -o root -g root -m 755 "$LIB"
install -o root -g root -m 644 "$SRC/onay_servisi.py" "$LIB/onay_servisi.py"
install -o root -g root -m 644 "$SRC/ghost-onay.service" /etc/systemd/system/ghost-onay.service

# 2
if [ ! -s "$DURUM/ayar.json" ]; then
    GHOST_UID="$(id -u ghost)" /usr/bin/python3 -I - "$J/.env" "$DURUM/ayar.json" <<'EOF'
import json, os, sys
env = {}
for satir in open(sys.argv[1], encoding="utf-8"):
    satir = satir.strip()
    if satir and not satir.startswith("#") and "=" in satir:
        k, v = satir.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
sahip = env.get("TELEGRAM_OWNER_ID", "")
if not sahip.isdigit():
    sys.exit("TELEGRAM_OWNER_ID .env'de yok — ayar yazılmadı")
ayar = {"sahip_id": int(sahip), "telegram_token": env.get("TELEGRAM_BOT_TOKEN", ""),
        "izinli_uid": [int(os.environ["GHOST_UID"])], "kopru_birimi": "ghost-bridge.service"}
fd = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(ayar, f)
print("ayar yazıldı: sahip var, telegram", "var" if ayar["telegram_token"] else "YOK")
EOF
    chown ghost-onay:ghost-onay "$DURUM/ayar.json"
fi

# 3
AKTIF="$(runuser -u ghost -- /usr/bin/python3 -I -c "
import sqlite3; c = sqlite3.connect('$J/ops/ghost.db')
print(c.execute(\"SELECT COUNT(*) FROM kokpit_devices WHERE durum='aktif' AND length(key_hex)=64\").fetchone()[0])")"
echo "== ghost.db'de anahtarlı aktif cihaz: $AKTIF"
if [ "$AKTIF" -gt 0 ]; then
    runuser -u ghost -- /usr/bin/python3 -I -c "
import json, sqlite3; c = sqlite3.connect('$J/ops/ghost.db')
r = c.execute(\"SELECT id, ad, key_hex, durum, created, last_seen FROM kokpit_devices WHERE durum='aktif'\").fetchall()
print(json.dumps([dict(zip(('id','ad','key_hex','durum','created','last_seen'), x)) for x in r]))" \
    | runuser -u ghost-onay -- /usr/bin/python3 -I "$LIB/onay_servisi.py" ice-aktar
fi

# 4
systemctl daemon-reload
systemctl enable ghost-onay >/dev/null 2>&1
systemctl restart ghost-onay
i=0; while [ ! -S /run/ghost-onay/onay.sock ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done
DURUM_JSON="$(runuser -u ghost -- /usr/bin/python3 -I -c "
import json, socket; s = socket.socket(socket.AF_UNIX); s.settimeout(5); s.connect('/run/ghost-onay/onay.sock')
s.sendall(b'{\"op\": \"durum\"}\n'); print(s.recv(65536).decode())")"
echo "== servis: $DURUM_JSON"
SAYI="$(printf '%s' "$DURUM_JSON" | /usr/bin/python3 -I -c 'import json,sys; print(json.load(sys.stdin).get("aktif_cihaz", -1))')"

# 5
if [ "$AKTIF" -gt 0 ] && [ "$SAYI" -ge "$AKTIF" ]; then
    runuser -u ghost -- /usr/bin/python3 -I -c "
import sqlite3; c = sqlite3.connect('$J/ops/ghost.db')
n = c.execute(\"UPDATE kokpit_devices SET key_hex='' WHERE key_hex != ''\").rowcount; c.commit()
print('ghost.db anahtar silindi:', n)"
elif [ "$AKTIF" -gt 0 ]; then
    echo "!! servis cihaz sayısı ($SAYI) < ghost.db ($AKTIF) — ghost.db anahtarlarına DOKUNULMADI" >&2
fi

# 6
install -o root -g root -m 644 "$J/ops/guvenlik_kapisi.py" /usr/local/lib/ghost-kapi/guvenlik_kapisi.py
echo "== kurulanlar:"
sha256sum "$LIB/onay_servisi.py" /etc/systemd/system/ghost-onay.service /usr/local/lib/ghost-kapi/guvenlik_kapisi.py
ls -la "$DURUM" /run/ghost-onay
systemctl is-active ghost-onay
echo TAMAM
