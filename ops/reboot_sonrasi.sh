#!/bin/bash
# VPS yeniden başladıktan sonra (@reboot cron, ghost kullanıcısı) organları sayar ve
# sahibe haber verir. Köprüden BAĞIMSIZ: köprü kalkmasa bile Telegram'a bu betik yazar.
# Kuruldu 2026-09-26 (ilk bilinçli reboot: çekirdek 7.0.0-29 → 7.0.0-34).
set -u
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
sleep "${GHOST_REBOOT_RAPOR_BEKLE:-75}"   # ağ + systemd birimleri + Mac ters tüneli için pay
durum() { systemctl is-active "$1" 2>/dev/null || echo "?"; }
udurum() { systemctl --user is-active "$1" 2>/dev/null || echo "?"; }
KOPRU=$(durum ghost-bridge); HOOK=$(durum ghost-hook); API=$(udurum ghost-api)
UFW=$(durum ufw); SSHS=$(durum ssh.socket)
TUNEL=$(ss -ltn 2>/dev/null | grep -q "127.0.0.1:8765 " && echo "açık" || echo "YOK")
WDA=$(ss -ltn 2>/dev/null | grep -q "127.0.0.1:8101 " && echo "açık" || echo "yok")
TIMERS=$(systemctl list-timers --all --no-pager 2>/dev/null | grep -c "ghost-")
SORUN=""
[ "$KOPRU" = active ] || SORUN="$SORUN köprü=$KOPRU"
[ "$HOOK" = active ] || SORUN="$SORUN hook=$HOOK"
[ "$API" = active ] || SORUN="$SORUN api=$API"
[ "$UFW" = active ] || SORUN="$SORUN ufw=$UFW"
[ "$TUNEL" = "açık" ] || SORUN="$SORUN mac-tünel=$TUNEL"
BASLIK="✅ VPS yeniden başladı"; [ -z "$SORUN" ] || BASLIK="🔴 VPS yeniden başladı — SORUN:$SORUN"
[ -z "${GHOST_REBOOT_TEST:-}" ] || BASLIK="(deneme, gerçek reboot değil) $BASLIK"
MSG="$BASLIK
çekirdek $(uname -r) · uptime $(uptime -p | sed 's/^up //')
köprü $KOPRU · hook $HOOK · kokpit api $API · ufw $UFW · ssh $SSHS
Mac tüneli $TUNEL · WDA rölesi $WDA · zamanlayıcı $TIMERS"
echo "$(date '+%F %T') $MSG" >> "$BASE/ops/reboot.log"
echo "$MSG" | /usr/bin/env python3 "$BASE/ops/send_telegram.py" 2>>"$BASE/ops/reboot.log" || true
