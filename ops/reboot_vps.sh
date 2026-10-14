#!/bin/bash
# VPS'i yeniden başlat — AYRIK ve BOŞTA. Ghost'un sohbet oturumu köprünün çocuğu:
# turun ortasında reboot cevabı öldürür ve kesik-mesaj mekanizması aynı "reboot et"
# emrini açılışta BİR kez daha koşturur (ikinci reboot!). O yüzden: cevap teslim
# edilsin, sohbet boşalsın, SONRA reboot. Root gerek: VPS'te sudo yalnız restart'a
# izinli; reboot Mac'teki ghost_vps root anahtarıyla, hızlı hat üzerinden gider.
# Kullanım: setsid nohup ~/jarvis/ops/reboot_vps.sh "neden" >/dev/null 2>&1 &
set -u
NOTE="${1:-planlı reboot}"
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$BASE/ops/reboot.log"
sleep "${GHOST_REBOOT_DELAY:-30}"
t0=$(date +%s)
while [ $(( $(date +%s) - t0 )) -lt "${GHOST_REBOOT_BOSTA_TAVAN:-900}" ]; do
  if (cd "$BASE" && python3 -c 'import sys
from ghost import akis, kalici_oturum
m = kalici_oturum.durum_oku().get("mesgul") or akis.sohbet_durumu().get("aktif")
sys.exit(1 if m else 0)' 2>/dev/null) && [ ! -f "$BASE/ops/sohbet_kesik.json" ]; then break; fi
  sleep 5
done
echo "$(date '+%F %T') reboot tetikleniyor — $NOTE" >> "$LOG"
echo "♻️ VPS'i yeniden başlatıyorum ($NOTE). ~2 dk sessizlik; dönüşte kendim rapor vereceğim." \
  | /usr/bin/env python3 "$BASE/ops/send_telegram.py" 2>>"$LOG" || true
sleep 3
cd "$BASE" && timeout 40 python3 ops/mac.py komut \
  'ssh -o BatchMode=yes -o ConnectTimeout=8 -i $HOME/.ssh/ghost_vps root@203.0.113.10 "systemctl reboot"' >>"$LOG" 2>&1
echo "$(date '+%F %T') reboot komutu gönderildi (rc=$?)" >> "$LOG"
