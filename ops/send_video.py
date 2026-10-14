#!/usr/bin/env python3
"""Bir video dosyasını sahibe Telegram'dan yollar (sendVideo).

Kullanım: python3 ops/send_video.py <video_yolu> ["opsiyonel açıklama"]
Ghost ürettiği videoları (ör. sosyal medya postları) böyle gösterir.
"""
import json
import os
import sys
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ENV = {}
with (open(os.path.join(BASE, ".env"), encoding="utf-8") if os.path.exists(os.path.join(BASE, ".env")) else open(os.devnull)) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            ENV[k] = v.strip()

if ENV.get("TELEGRAM_OWNER_ID", "").strip().isdigit():
    CHAT_ID = ENV["TELEGRAM_OWNER_ID"].strip()
else:
    with open(os.path.join(BASE, "ops", "owner.json"), encoding="utf-8") as f:
        CHAT_ID = str(json.load(f)["id"])

TOKEN = ENV["TELEGRAM_BOT_TOKEN"]
path = sys.argv[1]
caption = sys.argv[2] if len(sys.argv) > 2 else ""

# multipart/form-data elle kur (sadece stdlib)
boundary = "----ghost" + os.urandom(8).hex()
with open(path, "rb") as f:
    photo = f.read()
fname = os.path.basename(path)

parts = []
parts.append(("chat_id", None, CHAT_ID))
if caption:
    parts.append(("caption", None, caption))
parts.append(("video", fname, photo))
parts.append(("supports_streaming", None, "true"))

body = b""
for name, filename, data in parts:
    body += ("--%s\r\n" % boundary).encode()
    if filename:
        body += ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                 % (name, filename)).encode()
        body += b"Content-Type: application/octet-stream\r\n\r\n"
        body += data if isinstance(data, bytes) else data.encode()
    else:
        body += ('Content-Disposition: form-data; name="%s"\r\n\r\n' % name).encode()
        body += data.encode()
    body += b"\r\n"
body += ("--%s--\r\n" % boundary).encode()

req = urllib.request.Request(
    "https://api.telegram.org/bot%s/sendVideo" % TOKEN, data=body,
    headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
print("gönderildi" if resp.get("ok") else "hata: %s" % resp)
try:  # kokpit dökümü: aynı video uygulamanın Sohbet'inde de görünsün (ghost/channels.py)
    sys.path.insert(0, BASE)
    from ghost import channels
    channels.app_medya(path, caption)
except Exception:
    pass
