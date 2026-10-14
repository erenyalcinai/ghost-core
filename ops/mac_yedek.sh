#!/bin/bash
# Ghost — iPhone yedeği (Mac'te koşar; launchd ai.jarvis.yedek, günde bir + elle).
# Sahip kararı 2026-09-28 ("finderı bağla"): sahibin iPhone'unun yedeği ~/.ghost/yedek/<udid>/
# altına idevicebackup2 ile alınır; artımlı (ilk tam, sonrakiler yalnız değişen). Okuyan taraf
# ops/mac_veri_okuyucu.py (/yedek, /aramalar, /rehber, /safari, mesajlar|whatsapp?kaynak=yedek).
# Telefon kabloda değilse -n (Wi-Fi) denenir; o da yoksa sessizce çıkar (telefon yanında değil).
# Kullanım: mac_yedek.sh [udid] [--tam]
set -u
UDID="${1:-00008150-000000000000000B}"   # Sahip · iPhone 17
KOK="$HOME/.ghost/yedek"
LOG="$KOK/yedek.log"
IB=/opt/homebrew/bin/idevicebackup2
mkdir -p "$KOK"
if pgrep -f "idevicebackup2 .*backup" >/dev/null; then
  echo "$(date '+%F %T') zaten koşuyor, atlandı" >> "$LOG"; exit 0
fi
TAM=""; [ "${2:-}" = "--tam" ] && TAM="--full"
if /opt/homebrew/bin/idevice_id -l 2>/dev/null | grep -q "$UDID"; then AG=""
elif /opt/homebrew/bin/idevice_id -n 2>/dev/null | grep -q "$UDID"; then AG="-n"
else echo "$(date '+%F %T') telefon görünmüyor (USB yok, Wi-Fi yok)" >> "$LOG"; exit 0; fi
echo "$(date '+%F %T') başladı ($([ -n "$AG" ] && echo wifi || echo usb)$([ -n "$TAM" ] && echo ', tam'))" >> "$LOG"
"$IB" $AG -u "$UDID" backup $TAM "$KOK" 2>&1 | tr '\r' '\n' | grep -v '^\[=' >> "$LOG"
echo "$(date '+%F %T') bitti rc=${PIPESTATUS[0]}" >> "$LOG"
