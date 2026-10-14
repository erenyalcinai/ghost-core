"""Sahibe bildirim — çekirdek modüllerin (monitör/zamanlayıcı/webhook)
köprüden bağımsız Telegram erişimi. Sessizce başarısız olur (nöbetçi
zaten loglar); test için gönderici enjekte edilebilir.
"""
import json
import os
import urllib.parse
import urllib.request

from ghost import config

# Test koşusu ASLA sahibe mesaj göndermesin. Tek noktada kesiliyor: her testin
# göndericiyi ayrı ayrı taklit etmesini beklemek çalışmadı — unutulan tek test
# (test_sms_rayi.py) her koşuda sahibin telefonunu öttürdü. Gönderilen mesajlar
# GONDERILENLER'e yazılır ki testler yine de doğrulayabilsin.
GONDERILENLER = []


def _test_kipi():
    return os.environ.get("GHOST_TEST") == "1"


def telegram(text, env=None):
    """Birincil kanal Telegram; ulaşamazsa ntfy yedek kanalına düşer
    (Telegram kesintisinde bile Ghost sese ulaşabilir).
    Alarm işaretli metin (🚨/⚠️/❌…) kokpit uygulamasına da push gider (ghost/apns.py)."""
    env = config.load_env() if env is None else env
    try:
        from ghost import apns
        apns.alarm(text)
    except Exception:
        pass
    try:  # nöbetçi mesajı kokpit dökümüne de düşer — uygulamada Telegram'la aynı sohbet görünsün
        from ghost import kokpit
        kokpit.outbox_yaz(text, "ghost")
    except Exception:
        pass
    token = env.get("TELEGRAM_BOT_TOKEN")
    owner = env.get("TELEGRAM_OWNER_ID")
    if token and owner:
        data = urllib.parse.urlencode({"chat_id": owner,
                                       "text": text[:4000]}).encode()
        req = urllib.request.Request(
            "https://api.telegram.org/bot%s/sendMessage" % token, data=data)
        if _test_kipi():          # kapı TAM burada: yapılandırma mantığı korunur,
            GONDERILENLER.append(("telegram", text))   # yalnız ağ kesilir
            return True
        try:
            urllib.request.urlopen(req, timeout=30).read()
            return True
        except Exception:
            pass
    return ntfy(text, env)


def ntfy(text, env=None):
    """Yedek push kanalı — ntfy.sh/<gizli konu>. Telefonda ntfy uygulamasına
    aynı konu eklenince çalışır; hesap gerekmez."""
    env = config.load_env() if env is None else env
    topic = env.get("NTFY_TOPIC")
    if not topic:
        return False
    req = urllib.request.Request("https://ntfy.sh/" + topic,
                                 data=text[:4000].encode("utf-8"),
                                 headers={"Title": "Ghost"})
    if _test_kipi():
        GONDERILENLER.append(("ntfy", text))
        return True
    try:
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except Exception:
        return False
