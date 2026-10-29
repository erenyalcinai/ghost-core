"""Abonelik kotası — Claude uygulamasındaki "Kullanım" ekranının Ghost karşılığı.

Kullanım yüzdesi Claude Code oturumunun kendi kullanım ucundan okunur; belirteç
sahibin makinesindeki Claude Code girişinden gelir ve yenilenmez (süresi dolunca
ölçüm bayatlar, Nabız "ölçüm eski" der).

Çıktı durum tablosu `kota` (Faz 1.9; eski ops/kota.json ilk okumada taşınır); `health.nabiz()["kota"]` yalnız okur. Yenileme:
selfcheck (15 dk) + her sohbet turundan sonra (5 dk eşik).
"""
import importlib.util
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from ghost import config, durum

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
        exp = d.get("expiresAt") or 0
        if d.get("accessToken") and (not exp or exp / 1000 - time.time() > 300):
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
    durum.yaz("kota", d)             # Faz 1.9: ops/kota.json → durum tablosu


def yenile(min_yas=0):
    """Kotayı çek ve durum 'kota'ya yaz. Testte (GHOST_TEST) hiç dışarı çıkmaz.
    min_yas: kayıt bu kadar saniyeden tazeyse dokunma. Hata → dosyaya `hata` yazılır,
    son başarılı ölçüm korunur."""
    if os.environ.get("GHOST_TEST"):
        return None
    eski = _oku_ham()
    simdi = time.time()
    # Kısıtlayıcı son DENEMEYE bakar, son başarıya değil (2026-10-19 kök neden: 14 Ekim'den
    # beri her hata eski ts'yi koruyordu → her turda yeni istek → uç 429 verdi → kilit).
    son_deneme = max(eski.get("ts") or 0, eski.get("hata_ts") or 0)
    if min_yas and son_deneme and simdi - son_deneme < min_yas:
        return eski
    if (eski.get("bekle_ts") or 0) > simdi:  # 429 Retry-After süresi dolmadı
        return eski
    try:
        d = belirtec()
        ham = cek(d)
        kayit = {"ts": int(simdi), "at": time.strftime("%F %T"),
                 "plan": _plan_adi(d), "kaynak": "mac-keychain",
                 "limitler": normalize(ham), "hata": None}
    except urllib.error.HTTPError as e:
        kayit = dict(eski, hata="HTTP %s" % e.code, hata_ts=int(simdi))
        if e.code == 429:
            try:
                bekle = int(e.headers.get("Retry-After") or 0)
            except ValueError:
                bekle = 0
            kayit["bekle_ts"] = int(simdi + max(60, min(bekle or 1800, 6 * 3600)))
        elif e.code == 401:
            try:
                os.remove(BELIRTEC_DOSYASI)  # bayat belirteç → bir sonraki tur Mac'ten okur
            except OSError:
                pass
    except Exception as e:
        kayit = dict(eski, hata="%s" % e, hata_ts=int(simdi))
    _yaz(kayit)
    return kayit


def arka_planda_yenile(min_yas=300):
    t = threading.Thread(target=yenile, args=(min_yas,), daemon=True)
    t.start()
    return t


def _oku_ham():
    try:
        return durum.tasi("kota", KOTA_DOSYASI, {}) or {}     # eski dosya varsa bir kez içeri alınır
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


# ---------- kota-farkında zamanlama (2026-10-29, bütünsel denetim 04 Ö7 / 02 b2) ----------
PENCERE_ESIK = 85      # 5 saatlik pencere bu yüzdeyi aştıysa özerk iş ERTELENİR
PENCERE_BAYAT_SN = 2 * 3600   # ölçüm bundan eskiyse (Mac kapalı) karar verilemez → engelleme


def pencere_durumu(simdi=None):
    """5 saatlik oturum penceresi: {bilinir, yuzde, dolu, sifirlanma_ts, sifirlanma_metin, yas_sn}.

    NEDEN: 2026-10-28'de pencere iki kez doldu; zamanlı iş #55 8 sn'de "5-hour limit"
    hatasıyla öldü, sohbet 1800 sn bekledi. Özerk iş kuyruktan çekilmeden önce buraya
    bakar; dolu ise sıfırlanma anına ertelenir. Ölçüm bayatsa (Mac'ten okunur) ya da
    yoksa `bilinir=False` döner ve kimse engellenmez (yanlış pozitif = kayıp iş).
    """
    simdi = simdi or time.time()
    ham = _oku_ham()
    yas = max(0, int(simdi) - int(ham.get("ts", 0) or 0))
    oturum = next((l for l in ham.get("limitler") or [] if l.get("tur") == "session"), None)
    if not oturum or yas > PENCERE_BAYAT_SN:
        return {"bilinir": False, "yuzde": None, "dolu": False, "sifirlanma_ts": None,
                "sifirlanma_metin": "", "yas_sn": yas}
    sifir_ts = None
    try:
        from datetime import datetime
        if oturum.get("sifirlanma"):
            sifir_ts = datetime.fromisoformat(oturum["sifirlanma"]).timestamp()
    except Exception:
        sifir_ts = None
    yuzde = int(oturum.get("yuzde") or 0)
    # pencere geçmişte sıfırlanmışsa ölçüm bayat sayılır → dolu değil
    dolu = yuzde >= PENCERE_ESIK and (sifir_ts is None or sifir_ts > simdi)
    return {"bilinir": True, "yuzde": yuzde, "dolu": dolu, "sifirlanma_ts": sifir_ts,
            "sifirlanma_metin": oturum.get("sifirlanma_metin") or "", "yas_sn": yas}


def ozerk_uygun(simdi=None):
    """Özerk/zamanlı iş şimdi koşsun mu? (uygun, gerekçe)."""
    d = pencere_durumu(simdi)
    if d["dolu"]:
        return False, "kota penceresi %%%d dolu, sıfırlanma %s" % (d["yuzde"], d["sifirlanma_metin"] or "?")
    return True, ""


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
