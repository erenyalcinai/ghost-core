#!/bin/bash
# Ghost'un KENDİ köprüsünü yeniden başlatması — ayrık (detached).
#
# KÖK SEBEP (2026-08-18): Ghost'un sohbet oturumu telegram_bridge.py'nin ÇOCUK
# sürecidir (ps ile doğrulandı: claude → telegram_bridge.py → systemd). Yani
# oturumun İÇİNDEN `systemctl restart ghost-bridge` çalıştırmak kendi ebeveynini
# öldürür: süreç ölür, üretilen cevap Telegram'a HİÇ ulaşmaz, restart'ın sonucu
# da öğrenilemez. Komut yanlış değildi — senkron çalıştırılması yapısal olarak
# imkânsızdı.
#
# Çözüm: setsid ile süreç ağacından kopar, cevabın teslimi için biraz bekle,
# sonra restart et ve sonucu Telegram'a KENDİN bildir (çünkü bildirecek oturum
# o sırada ölmüş olacak). Ölürse log'u diske bırak ki geri alınabilsin.
#
# Kullanım (oturumun içinden):
#   setsid nohup ~/jarvis/ops/self_restart.sh "ne değişti" >/dev/null 2>&1 &
# Doğrudan çalıştırmak da çalışır ama beklemek zorunda kalırsın.
set -u
NOTE="${1:-köprü yenilendi}"
DELAY="${GHOST_RESTART_DELAY:-30}"     # cevabın teslim edilmesi için
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILLOG="$BASE/ops/restart_fail.log"

sleep "$DELAY"

# BOŞTA BEKLE (2026-09-26): sohbet turu ya da kalıcı süreç meşgulken restart
# cevabı öldürür. Kesik mesaj artık açılışta yeniden kuyruğa alınıyor
# (ghost/kesik_mesaj.py) ama baştan koşar — beklemek daha ucuz. Tavan 10 dk.
BEKLE_TAVAN="${GHOST_RESTART_BOSTA_TAVAN:-600}"
t0=$(date +%s)
while [ $(( $(date +%s) - t0 )) -lt "$BEKLE_TAVAN" ]; do
  if (cd "$BASE" && python3 -c 'import sys
from ghost import akis, kalici_oturum
m = kalici_oturum.durum_oku().get("mesgul") or akis.sohbet_durumu().get("aktif")
sys.exit(1 if m else 0)' 2>/dev/null); then break; fi
  sleep 5
done

if [ "$(uname)" = "Darwin" ]; then     # Mac köprüsü launchd altında
  launchctl kickstart -k "gui/$(id -u)/ai.jarvis.macbridge"
  sleep 6
  launchctl list | grep -q "ai.jarvis.macbridge" && OK=1 || OK=0
else                                   # VPS: sudoers'ta yalnız bu komuta izin var
  sudo -n /usr/bin/systemctl restart ghost-bridge
  sleep 6
  systemctl is-active --quiet ghost-bridge && OK=1 || OK=0
fi

if [ "$OK" = "1" ]; then
  echo "✅ Köprü yeniden başlatıldı — $NOTE" \
    | /usr/bin/env python3 "$BASE/ops/send_telegram.py"
else
  # Bildiremeyebiliriz (köprü ölü); en azından diske delil bırak.
  {
    echo "=== $(date '+%F %T') KÖPRÜ AYAĞA KALKMADI — $NOTE ==="
    journalctl -u ghost-bridge -n 30 --no-pager 2>&1 || true
  } >> "$FAILLOG"
  echo "🔴 Köprü restart sonrası ölü — $NOTE. Log: ops/restart_fail.log" \
    | /usr/bin/env python3 "$BASE/ops/send_telegram.py" 2>/dev/null || true
fi
