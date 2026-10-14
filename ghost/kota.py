"""Abonelik kotası — Claude uygulamasındaki "Kullanım" ekranının Ghost karşılığı.

Kullanım yüzdesi Claude Code oturumunun kendi kullanım ucundan okunur; belirteç
sahibin makinesindeki Claude Code girişinden gelir ve yenilenmez (süresi dolunca
ölçüm bayatlar, Nabız "ölçüm eski" der).

Çıktı `ops/kota.json`; `health.nabiz()["kota"]` yalnız okur. Yenileme:
selfcheck (15 dk) + her sohbet turundan sonra (5 dk eşik).
"""
import importlib.util
import json
import os
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timezone

from ghost import config

KOTA_DOSYASI = os.path.join(config.OPS, "kota.json")
BELIRTEC_DOSYASI = os.path.expanduser("~/.secrets/claude_kota_token.json")
UC = "https://api.anthropic.com/api/oauth/usage"
BAYAT_SN = 30 * 60
KEYCHAIN_KOMUT = 'security find-generic-password -s "Claude Code-credentials" -w'
ETIKET = {"session": "Oturum (5 saat)", "weekly_all": "Haftalık · tüm modeller"}
GUNLER = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]


def _mac_modulu():
    yol = os.path.join(config.BASE, "ops", "mac.py")
    spec = importlib.util.spec_from_file_location("ghost_mac_hat", yol)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _belirtec_kayitli():
    try:
        with open(BELIRTEC_DOSYASI, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("accessToken") and d.get("expiresAt", 0) / 1000 - time.time() > 300:
            return d
    except Exception:
        pass
    return None


def _belirtec_mac():
    """Mac Keychain'den Claude Code girişinin erişim belirteci (salt okuma)."""
    r = _mac_modulu().komut(KEYCHAIN_KOMUT, zaman=20)
    metin = (r or {}).get("cikti") or ""
    i, j = metin.find("{"), metin.rfind("}")
    if i < 0 or j < 0:
        raise RuntimeError("Mac Keychain okunamadı: %s" % ((r or {}).get("hata") or metin[:80]))
    o = json.loads(metin[i:j + 1]).get("claudeAiOauth") or {}
    if "user:profile" not in (o.get("scopes") or []):
        raise RuntimeError("Mac belirteci user:profile kapsamı taşımıyor")
    d = {"accessToken": o.get("accessToken"), "expiresAt": o.get("expiresAt", 0),
         "plan": o.get("subscriptionType", ""), "tier": o.get("rateLimitTier", "")}
    try:
        os.makedirs(os.path.dirname(BELIRTEC_DOSYASI), exist_ok=True)
        tmp = BELIRTEC_DOSYASI + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, BELIRTEC_DOSYASI)
    except Exception:
        pass
    return d


def belirtec():
    return _belirtec_kayitli() or _belirtec_mac()


def _plan_adi(d):
    plan = (d.get("plan") or "").lower()
    tier = (d.get("tier") or "").lower()
    if not plan:
        return ""
    carpan = ""
    for x in ("20x", "5x"):
        if x in tier:
            carpan = " " + x
    return plan.capitalize() + carpan


def _yerel(iso):
    """ISO → ('2026-09-07T03:00:00+00:00', 'bugün 06:00' / 'Çar 08:00') İstanbul saatiyle."""
    if not iso:
        return None, ""
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        try:
            from zoneinfo import ZoneInfo
            yerel = t.astimezone(ZoneInfo("Europe/Istanbul"))
        except Exception:
            yerel = t.astimezone()
        bugun = datetime.now(yerel.tzinfo).date()
        fark = (yerel.date() - bugun).days
        if fark == 0:
            gun = "bugün"
        elif fark == 1:
            gun = "yarın"
        else:
            gun = GUNLER[yerel.weekday()]
        return t.isoformat(timespec="seconds"), "%s %s" % (gun, yerel.strftime("%H:%M"))
    except Exception:
        return iso, ""


def normalize(ham):
    """API cevabı → Nabız limit satırları. `limits` dizisi esas; yoksa five_hour/seven_day."""
    satirlar = []
    for l in ham.get("limits") or []:
        tur = l.get("kind") or ""
        model = ((l.get("scope") or {}).get("model") or {}).get("display_name")
        ad = ETIKET.get(tur) or ("Haftalık · %s" % model if model else tur or "limit")
        iso, metin = _yerel(l.get("resets_at"))
        satirlar.append({"ad": ad, "tur": tur, "yuzde": int(round(l.get("percent") or 0)),
                         "onem": l.get("severity") or "normal", "sifirlanma": iso,
                         "sifirlanma_metin": metin, "aktif": bool(l.get("is_active"))})
    if not satirlar:
        for anahtar, tur in (("five_hour", "session"), ("seven_day", "weekly_all")):
            b = ham.get(anahtar) or {}
            if not b:
                continue
            iso, metin = _yerel(b.get("resets_at"))
            y = int(round(b.get("utilization") or 0))
            satirlar.append({"ad": ETIKET[tur], "tur": tur, "yuzde": y,
                             "onem": "critical" if y >= 95 else ("warning" if y >= 75 else "normal"),
                             "sifirlanma": iso, "sifirlanma_metin": metin, "aktif": False})
    return satirlar


def cek(d):
    req = urllib.request.Request(UC, headers={
        "Authorization": "Bearer " + d["accessToken"],
        "anthropic-beta": "oauth-2025-04-20", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _yaz(d):
    tmp = KOTA_DOSYASI + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, KOTA_DOSYASI)


def yenile(min_yas=0):
    """Kotayı çek ve ops/kota.json'a yaz. Testte (GHOST_TEST) hiç dışarı çıkmaz.
    min_yas: kayıt bu kadar saniyeden tazeyse dokunma. Hata → dosyaya `hata` yazılır,
    son başarılı ölçüm korunur."""
    if os.environ.get("GHOST_TEST"):
        return None
    eski = _oku_ham()
    if min_yas and eski.get("ts") and time.time() - eski["ts"] < min_yas:
        return eski
    try:
        d = belirtec()
        ham = cek(d)
        kayit = {"ts": int(time.time()), "at": time.strftime("%F %T"),
                 "plan": _plan_adi(d), "kaynak": "mac-keychain",
                 "limitler": normalize(ham), "hata": None}
    except Exception as e:
        kayit = dict(eski, hata="%s" % e, hata_ts=int(time.time()))
    _yaz(kayit)
    return kayit


def arka_planda_yenile(min_yas=300):
    t = threading.Thread(target=yenile, args=(min_yas,), daemon=True)
    t.start()
    return t


def _oku_ham():
    try:
        with open(KOTA_DOSYASI, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def oku():
    """Nabız için: {var, olculdu, yas_dk, bayat, plan, limitler, hata}."""
    ham = _oku_ham()
    if not ham.get("limitler"):
        return {"var": False, "limitler": [],
                "hata": ham.get("hata") or "Kota henüz okunmadı — Mac'teki Claude girişinden okunur."}
    yas = max(0, int(time.time()) - int(ham.get("ts", 0)))
    return {"var": True, "olculdu": ham.get("at", ""), "yas_dk": yas // 60,
            "bayat": yas > BAYAT_SN, "plan": ham.get("plan", ""),
            "limitler": ham.get("limitler", []), "hata": ham.get("hata")}


def metin():
    """Telegram /kota metni."""
    k = oku()
    if not k["var"]:
        return "📊 Kota: %s" % k.get("hata")
    cizgi = []
    for l in k["limitler"]:
        y = l.get("yuzde", 0)
        isaret = "🔴" if y >= 95 else ("🟡" if y >= 75 else "🟢")
        cizgi.append("%s %s: %%%d%s" % (isaret, l["ad"], y,
                                        (" · sıfırlanma " + l["sifirlanma_metin"]) if l.get("sifirlanma_metin") else ""))
    bas = "📊 Kota" + (" (%s)" % k["plan"] if k.get("plan") else "")
    son = "ölçüm %s%s" % (k["olculdu"], " · ESKİ" if k["bayat"] else "")
    if k.get("hata"):
        son += "\n⚠️ son yenileme hatası: %s" % k["hata"]
    return "%s\n%s\n%s" % (bas, "\n".join(cizgi), son)


def main(argv):
    if not argv or argv[0] == "oku":
        print(json.dumps(oku(), ensure_ascii=False, indent=1))
    elif argv[0] == "yenile":
        print(json.dumps(yenile(), ensure_ascii=False, indent=1))
    elif argv[0] == "metin":
        print(metin())
    else:
        print("kullanım: python3 -m ghost.kota [oku|yenile|metin]")
        return 2
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
