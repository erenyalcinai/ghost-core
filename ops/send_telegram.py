#!/usr/bin/env python3
"""stdin'den okuduğu metni sahibe Telegram'dan yollar (4000'lik parçalarla)."""
import json
import os
import sys
import urllib.parse
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
    CHAT_ID = int(ENV["TELEGRAM_OWNER_ID"])
else:
    with open(os.path.join(BASE, "ops", "owner.json"), encoding="utf-8") as f:
        CHAT_ID = json.load(f)["id"]

API = "https://api.telegram.org/bot" + ENV["TELEGRAM_BOT_TOKEN"]

text = sys.stdin.read().strip() or "(boş)"
try:  # alarm işaretli metin kokpit uygulamasına da push gider (ghost/apns.py)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ghost import apns, kokpit
    kokpit.outbox_yaz(text, "ghost")   # döküm: uygulamada da görünsün
    apns.alarm(text)
except Exception:
    pass
for i in range(0, len(text), 4000):
    data = urllib.parse.urlencode(
        {"chat_id": CHAT_ID, "text": text[i : i + 4000]}
    ).encode()
    req = urllib.request.Request(API + "/sendMessage", data=data)
    urllib.request.urlopen(req, timeout=30).read()
