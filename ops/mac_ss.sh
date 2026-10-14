#!/bin/bash
# Mac ekranını cua üzerinden al (maclink /ekran screencapture yolu TCC yüzünden
# düşerse yedek budur). cua sandbox'ı /tmp'yi paylaşmıyor → ev dizinine yaz.
OUT="${1:-/tmp/mac_ss.png}"
M=/home/ghost/jarvis/ops/mac.py
R=/Users/sahip/jarvis/ops/ghost_ss.png
python3 "$M" cua get_appbop_state "{\"screenshot_out_file\":\"$R\"}" >/dev/null || exit 1
python3 "$M" komut "sips -Z 1512 $R --out ${R%.png}_s.png >/dev/null 2>&1; base64 -i ${R%.png}_s.png | tr -d '\n'" \
  | tail -1 | base64 -d > "$OUT"
ls -la "$OUT"
