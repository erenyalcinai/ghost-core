"""APNs — kokpit uygulamasına push bildirimi (docs/kokpit.md Faz 2).

AYRI MODÜL: ghost/notify.py Telegram/ntfy yedek bildirim rayıdır (eski, testli);
APNs onun yanına üçüncü kanal olarak burada durur, onu ezmez.

Anahtar: Apple portal 2026-09-02, "Ghost Kokpit APNs" (Key ID secrets.env
APNS_KEY_ID, Team TEAMID0000, Sandbox & Production, Team Scoped). Cihaz
token'ları kokpit_devices.apns_token'da; ortam (development/production)
uygulamanın imzasına göre cihazdan gelir (Xcode dev build → sandbox).

Taşıma: curl --http2 (VPS curl'ü HTTP/2 destekliyor; urllib desteklemiyor).
JWT: PyJWT ES256, 50 dk önbellek. Testte (GHOST_TEST) ağa çıkılmaz.

CLI:  python3 -m ghost.apns gonder "başlık" "gövde"      # tüm aktif cihazlar
      python3 -m ghost.apns durum
Ne zaman: köprü send()'inde butonlu (onay isteyen) mesaj; nöbetçi alarmı;
biten iş. Her mesaja push YOK — gürültü kanalı öldürür.
"""
import json
import os
import subprocess
import sys
import time

from ghost import config

_ENV = config.load_env()          # .env + secrets.env (dict döner, os.environ'a YAZMAZ)
_jwt_cache = {"tok": None, "t": 0}


def _ayar(ad, varsayilan=""):
    return os.environ.get(ad) or _ENV.get(ad) or varsayilan
HOST = {"development": "https://api.sandbox.push.apple.com",
        "production": "https://api.push.apple.com"}


def hazir():
    from ghost import vekil     # sır vekili kuruluysa .p8 vekilde, JWT ondan (Faz 2)
    if vekil.aktif(None, "APNS_KEY_PATH") and _ayar("APNS_KEY_ID") and _ayar("APNS_TEAM_ID"):
        return True
    return bool(_ayar("APNS_KEY_ID") and _ayar("APNS_TEAM_ID")
                and os.path.exists(_ayar("APNS_KEY_PATH")))


def _jwt():
    if _jwt_cache["tok"] and time.time() - _jwt_cache["t"] < 50 * 60:
        return _jwt_cache["tok"]
    from ghost import vekil
    tok = vekil.apns_jwt()
    if tok:
        _jwt_cache.update(tok=tok, t=time.time())
        return tok
    import jwt  # PyJWT
    with open(_ayar("APNS_KEY_PATH")) as f:
        key = f.read()
    tok = jwt.encode({"iss": _ayar("APNS_TEAM_ID"), "iat": int(time.time())},
                     key, algorithm="ES256", headers={"kid": _ayar("APNS_KEY_ID")})
    _jwt_cache.update(tok=tok, t=time.time())
    return tok


ONEM = ("passive", "active", "time-sensitive")


def onem_sec(collapse=None, veri=None):
    """Faz 1.12 (2026-10-29, denetim 06 #7) + Sahip aynı gün "bildirimler çok yoruyor": onay/karar kartı NORMAL (active)
    düzeyde — Odak'ı delmez; gerisi passive (bildirim merkezinde durur, ses/titreşim yok). time-sensitive kullanılmaz."""
    c = (collapse or "")
    if c.startswith(("karar", "onay")) or (veri or {}).get("sekme") == "kararlar":
        return "active"
    return "passive"


def yuk(baslik, govde, veri=None, ses=True, rozet=None, onem=None):
    aps = {"alert": {"title": baslik[:80], "body": govde[:500]}}
    onem = onem or onem_sec(None, veri)
    if onem in ONEM:
        aps["interruption-level"] = onem
    if ses and onem != "passive":
        aps["sound"] = "default"
    if rozet is not None:
        aps["badge"] = int(rozet)
    p = {"aps": aps}
    if veri:
        p.update(veri)
    return p


def gonder(token, env, baslik, govde, veri=None, ses=True, rozet=None, collapse=None, onem=None):
    """Tek cihaza push → (ok, durum_kodu, apns_yanit). Asla fırlatmaz."""
    onem = onem or onem_sec(collapse, veri)
    if os.environ.get("GHOST_TEST"):
        return False, 0, "test"
    if not hazir():
        return False, 0, "APNs anahtarı yok"
    url = "%s/3/device/%s" % (HOST.get(env or "development", HOST["development"]), token)
    hdr = ["-H", "authorization: bearer " + _jwt(),
           "-H", "apns-topic: " + _ayar("APNS_TOPIC", "com.example.ghost.kokpit"),
           "-H", "apns-push-type: alert", "-H", "apns-priority: 10",
           "-H", "content-type: application/json"]
    if collapse:
        hdr += ["-H", "apns-collapse-id: " + collapse[:64]]
    try:
        r = subprocess.run(["curl", "-sS", "--http2", "-m", "15", "-o", "-", "-w", "\n%{http_code}",
                            "-X", "POST", *hdr, "-d",
                            json.dumps(yuk(baslik, govde, veri, ses, rozet, onem), ensure_ascii=False), url],
                           capture_output=True, text=True, timeout=25)
        out = r.stdout.strip().rsplit("\n", 1)
        kod = int(out[-1]) if out and out[-1].isdigit() else 0
        govde_y = out[0] if len(out) > 1 else ""
        return kod == 200, kod, (govde_y or r.stderr)[:200]
    except Exception as e:
        return False, 0, repr(e)


def sessiz(token, env, veri=None):
    """Sessiz (background) push: uyandır, gösterme. Faz 6 — kuyrukta telefona
    yazılacak görev olduğunda uygulamayı arka planda koşturmak için.

    Alert push'tan üç farkı var ve üçü de zorunlu: push-type `background`,
    öncelik 5 (Apple 10'u sessizde REDDEDER), gövdede yalnız `content-available`.
    Teslim garantisi YOK — iOS kısar; bu yüzden çağıran taraf hep yedekli olmalı
    (öne gelince + SSE + arka plan tazelemesi)."""
    if os.environ.get("GHOST_TEST"):
        return False, 0, "test"
    if not hazir():
        return False, 0, "APNs anahtarı yok"
    url = "%s/3/device/%s" % (HOST.get(env or "development", HOST["development"]), token)
    yuk_ = {"aps": {"content-available": 1}}
    if veri:
        yuk_.update(veri)
    hdr = ["-H", "authorization: bearer " + _jwt(),
           "-H", "apns-topic: " + _ayar("APNS_TOPIC", "com.example.ghost.kokpit"),
           "-H", "apns-push-type: background", "-H", "apns-priority: 5",
           "-H", "content-type: application/json"]
    try:
        r = subprocess.run(["curl", "-sS", "--http2", "-m", "15", "-o", "-", "-w", "\n%{http_code}",
                            "-X", "POST", *hdr, "-d", json.dumps(yuk_, ensure_ascii=False), url],
                           capture_output=True, text=True, timeout=25)
        out = r.stdout.strip().rsplit("\n", 1)
        kod = int(out[-1]) if out and out[-1].isdigit() else 0
        return kod == 200, kod, (out[0] if len(out) > 1 else r.stderr)[:200]
    except Exception as e:
        return False, 0, repr(e)


def sessiz_herkese(veri=None):
    from ghost import kokpit
    return [dict(cihaz=c["id"], ok=sessiz(c["apns_token"], c["apns_env"], veri)[0])
            for c in kokpit.apns_cihazlar()]


ALARM_ON = ("🚨", "⚠️", "❌", "🔴", "⛔", "🆘")


def alarm_mi(text):
    """Nöbetçi/alarm metni mi? Başındaki işarete bakar — her bilgi mesajı push değildir."""
    t = (text or "").lstrip()
    return t.startswith(ALARM_ON)


def alarm(text, veri=None):
    """Alarm metnini push'la (notify.telegram / send_telegram kancası). Asla fırlatmaz."""
    try:
        if not alarm_mi(text):
            return []
        ilk = text.strip().splitlines()[0][:80]
        return herkese("Ghost alarm", text.strip()[:300], veri or {"sekme": "nabiz"},
                       collapse="alarm:" + ilk[:40])
    except Exception:
        return []


# ---------------------------------------------------------------- Live Activity
def _la_gonder(token, env, aps, event):
    """ActivityKit push: apns-push-type liveactivity, topic <bundle>.push-type.liveactivity."""
    if os.environ.get("GHOST_TEST"):
        return False, 0, "test"
    if not hazir():
        return False, 0, "APNs anahtarı yok"
    url = "%s/3/device/%s" % (HOST.get(env or "development", HOST["development"]), token)
    hdr = ["-H", "authorization: bearer " + _jwt(),
           "-H", "apns-topic: " + _ayar("APNS_TOPIC", "com.example.ghost.kokpit") + ".push-type.liveactivity",
           "-H", "apns-push-type: liveactivity", "-H", "apns-priority: 10",
           "-H", "content-type: application/json"]
    try:
        r = subprocess.run(["curl", "-sS", "--http2", "-m", "15", "-o", "-", "-w", "\n%{http_code}",
                            "-X", "POST", *hdr, "-d", json.dumps({"aps": aps}, ensure_ascii=False), url],
                           capture_output=True, text=True, timeout=25)
        out = r.stdout.strip().rsplit("\n", 1)
        kod = int(out[-1]) if out and out[-1].isdigit() else 0
        return kod == 200, kod, (out[0] if len(out) > 1 else r.stderr)[:200]
    except Exception as e:
        return False, 0, repr(e)


def la_durum(jid, tur, durum, ozet="", basladi=None, bitti=None):
    """content-state — Swift IsEtkinligi.ContentState ile bire bir."""
    return {"durum": durum, "ozet": (ozet or "")[:120],
            "basladi": basladi or int(time.time()), "bitti": bitti}


def etkinlik_baslat(jid, tur, ozet=""):
    """Push-to-start: kayıtlı start token'ı olan tüm cihazlarda Live Activity açar."""
    from ghost import kokpit
    sonuc = []
    st = la_durum(jid, tur, "çalışıyor", ozet)
    for c in kokpit.la_start_tokenlar():
        aps = {"timestamp": int(time.time()), "event": "start",
               "content-state": st, "attributes-type": "IsEtkinligi",
               "attributes": {"jid": int(jid), "tur": (tur or "iş")[:40]},
               "alert": {"title": "İş #%d başladı" % int(jid), "body": (ozet or tur or "")[:100]}}
        ok, kod, y = _la_gonder(c["token"], c["env"], aps, "start")
        sonuc.append({"cihaz": c["id"], "ok": ok, "kod": kod, "yanit": y})
        if kod == 410:
            kokpit.la_start_sil(c["id"])
    return sonuc


def etkinlik_bitir(jid, tur, durum, ozet="", basladi=None):
    """Update token'ı gelen etkinlikleri kapat (event=end); token gelmediyse sessiz."""
    from ghost import kokpit
    sonuc = []
    st = la_durum(jid, tur, durum, ozet, basladi, int(time.time()))
    for e in kokpit.la_update_tokenlar(jid):
        aps = {"timestamp": int(time.time()), "event": "end", "content-state": st,
               "dismissal-date": int(time.time()) + 15 * 60}
        ok, kod, y = _la_gonder(e["token"], e["env"], aps, "end")
        sonuc.append({"cihaz": e["device_id"], "ok": ok, "kod": kod, "yanit": y})
    kokpit.la_update_sil(jid)
    return sonuc


def herkese(baslik, govde, veri=None, collapse=None, onem=None):
    """Kayıtlı token'ı olan tüm aktif cihazlara. Sonuç listesi; 410 (Unregistered) token'ı siler."""
    from ghost import kokpit
    sonuc = []
    for c in kokpit.apns_cihazlar():
        ok, kod, y = gonder(c["apns_token"], c["apns_env"], baslik, govde, veri, collapse=collapse, onem=onem)
        sonuc.append({"cihaz": c["id"], "ok": ok, "kod": kod, "yanit": y})
        try:  # Faz 1.13 (denetim 02 e2): her push ops/apns.jsonl'e — teslim oranı ölçülebilsin
            from ghost import config as _cfg
            with open(os.path.join(_cfg.OPS, "apns.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.strftime("%F %T"), "cihaz": c["id"], "baslik": baslik[:60],
                                    "collapse": collapse, "onem": onem or onem_sec(collapse, veri), "ok": ok, "kod": kod},
                                   ensure_ascii=False) + "\n")
        except Exception:
            pass
        if kod == 410 or (kod == 400 and "BadDeviceToken" in y):
            kokpit.apns_sil(c["id"])
    return sonuc


def main(argv):
    if not argv or argv[0] == "durum":
        from ghost import kokpit
        print("anahtar:", "hazır" if hazir() else "YOK", "| kayıtlı cihaz:", len(kokpit.apns_cihazlar()))
        return 0
    if argv[0] == "gonder" and len(argv) >= 3:
        for s in herkese(argv[1], argv[2]):
            print(json.dumps(s, ensure_ascii=False))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
