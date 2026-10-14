#!/bin/sh
# Köprü kodu/config değişince otomatik yeniler (WatchPaths tetikler).
# Elle "launchctl kickstart" derdini bitirir.
sleep 1  # dosya yazımı bitsin
# SYNTAX KAPISI: bozuk dosya köprüyü crash-loop'a sokmasın (KeepAlive).
# Derlenmiyorsa restart YOK — çalışan eski kod ayakta kalır, uyarı düşer.
if ! /usr/bin/python3 -m py_compile /Users/sahip/jarvis/ops/telegram_bridge.py 2>/tmp/ghost_reload_err; then
    echo "$(date '+%F %T') autoreload reddetti: syntax hatası" >> /Users/sahip/jarvis/ops/bridge.log
    head -c 1000 /tmp/ghost_reload_err | /usr/bin/python3 /Users/sahip/jarvis/ops/send_telegram.py 2>/dev/null
    exit 0
fi
# Mac tarafında artık macbridge (ROLE=mac) çalışıyor; onu yenile.
exec launchctl kickstart -k "gui/$(id -u)/ai.jarvis.macbridge"
