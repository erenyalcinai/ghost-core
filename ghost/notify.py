"""Sahibe bildirim — Telegram gönderiminin TEK yeri (Faz 2.9, 2026-10-29): çekirdek modüller
(monitör/zamanlayıcı/webhook), nöbetçi betikler (selfcheck/uptime/port/app_health/biz_watch) ve
ops/send_*.py CLI'ları buradan geçer. `telegram()` sessizce başarısız olur (nöbetçi zaten loglar),
`telegram_api()` ham çağrıdır ve fırlatır. Köprünün kendi `api()`'si (buton/düzenleme/typing) ayrı kalır.
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


def sahip_id(env=None):
    """Sahibin Telegram chat_id'si: .env TELEGRAM_OWNER_ID, yoksa ops/owner.json (köprü yazar). None = bilinmiyor."""
    env = config.load_env() if env is None else env
    oid = str(env.get("TELEGRAM_OWNER_ID") or "").strip()
    if oid.isdigit():
        return int(oid)
    try:
        with open(os.path.join(config.OPS, "owner.json"), encoding="utf-8") as f:
            return int(json.load(f)["id"])
    except Exception:
        return None


def telegram_api(method, params=None, env=None, timeout=30, govde=None, basliklar=None):
    """Ham Bot API çağrısı — TEK ağ yolu (Faz 2.9: 13 dosyadaki kopya buraya indi). Vekil üstünden
    (anahtar bu süreçte yok). Test kipinde ağa çıkmaz: GONDERILENLER'e ("telegram:<method>", params) düşer.
    Döner: Telegram'ın JSON cevabı (dict). Ağ/HTTP hatası fırlatır — sessizlik isteyen telegram()/medya() sarar."""
    env = config.load_env() if env is None else env
    from ghost import vekil
    if _test_kipi():
        GONDERILENLER.append(("telegram:" + method, params or {}))
        return {"ok": True, "test": True, "result": {}}
    if govde is None:
        govde = urllib.parse.urlencode(params or {}).encode()
    req = urllib.request.Request(vekil.url("telegram", "/" + method, env), data=govde, headers=basliklar or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def telegram(text, env=None, dokum=True):
    """Sahibe metin. Birincil kanal Telegram (4000'lik parçalar); ulaşamazsa ntfy yedek kanalına düşer
    (Telegram kesintisinde bile Ghost sese ulaşabilir).
    dokum=True: alarm işaretli metin (🚨/⚠️/❌…) kokpit uygulamasına push (ghost/apns.py) + nöbetçi mesajı kokpit
    dökümüne de düşer (uygulamada Telegram'la aynı sohbet görünsün). Nöbetçi betiklerin eski ham yolu dokum=False."""
    env = config.load_env() if env is None else env
    if dokum:
        try:
            from ghost import apns
            apns.alarm(text)
        except Exception:
            pass
        try:
            from ghost import kokpit
            kokpit.outbox_yaz(text, "ghost")
        except Exception:
            pass
    from ghost import vekil     # sır vekili kuruluysa anahtar bu süreçte yok (Faz 2)
    owner = sahip_id(env)
    if vekil.hazir("telegram", env) and owner:
        if _test_kipi():          # kapı TAM burada: yapılandırma mantığı korunur,
            GONDERILENLER.append(("telegram", text))   # yalnız ağ kesilir
            return True
        try:
            for i in range(0, max(len(text), 1), 4000):
                telegram_api("sendMessage", {"chat_id": owner, "text": text[i:i + 4000]}, env)
            return True
        except Exception:
            pass
    return ntfy(text, env)


_MEDYA = {"photo": "sendPhoto", "document": "sendDocument", "video": "sendVideo"}


def medya(tur, yol, aciklama="", env=None, timeout=60, dokum=True):
    """Sahibe dosya: tur photo|document|video → sendPhoto/sendDocument/sendVideo (multipart, yalnız stdlib).
    dokum=True: aynı medya kokpit dökümüne de düşer (ghost/channels.app_medya). Döner: Telegram JSON cevabı."""
    if tur not in _MEDYA:
        raise ValueError("medya türü photo|document|video olmalı: %r" % tur)
    env = config.load_env() if env is None else env
    owner = sahip_id(env)
    if not owner:
        raise RuntimeError("sahip chat_id yok (TELEGRAM_OWNER_ID / ops/owner.json)")
    with open(yol, "rb") as f:
        blob = f.read()
    alanlar = [("chat_id", None, str(owner))]
    if aciklama:
        alanlar.append(("caption", None, aciklama))
    if tur == "video":
        alanlar.append(("supports_streaming", None, "true"))
    alanlar.append((tur, os.path.basename(yol), blob))
    sinir = "----ghost" + os.urandom(8).hex()
    govde = b""
    for ad, dosya_adi, veri in alanlar:
        govde += ("--%s\r\n" % sinir).encode()
        if dosya_adi:
            govde += ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n' % (ad, dosya_adi)).encode()
            govde += b"Content-Type: application/octet-stream\r\n\r\n" + veri
        else:
            govde += ('Content-Disposition: form-data; name="%s"\r\n\r\n' % ad).encode() + veri.encode()
        govde += b"\r\n"
    govde += ("--%s--\r\n" % sinir).encode()
    cevap = telegram_api(_MEDYA[tur], {"chat_id": owner, "caption": aciklama, "dosya": os.path.basename(yol)},
                         env, timeout=timeout, govde=govde,
                         basliklar={"Content-Type": "multipart/form-data; boundary=%s" % sinir})
    if dokum:
        try:  # kokpit dökümü: aynı medya uygulamanın Sohbet'inde de görünsün
            from ghost import channels
            channels.app_medya(yol, aciklama)
        except Exception:
            pass
    return cevap


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
