"""Duyular — sahibin telefonundan gelen izinli bağlam (konum, takvim, cihaz).

Faz 2'de "izinli konum/takvim" *somut senaryo yok* diye ertelenmişti. Senaryo şu:
Ghost, sahibin nerede olduğunu ve gününün nasıl dolu olduğunu bilmeden konuşuyor —
"Mac'e bak" diyor Sahip evde değilken, uzun iş başlatıyor Sahip 10 dk sonra
toplantıdayken, sabah brifingini boş güne göre yazıyor. Bu modül o körlüğü kapatır.

Kaynak TEK yönlüdür: iOS uygulaması (Kokpit, sahibin iPhone 17'si) `POST /v1/duyular`
ile anlık görüntü bırakır; Ghost yalnız okur. Sunucu hiçbir zaman konum SORMAZ,
takvime YAZMAZ — uygulama ne verirse o.

Gizlilik kuralları (bilinçli):
- Ham koordinat saklanır ama Ghost'a giden metinde YER ADI kullanılır (uygulama
  ters coğrafi çözümlemeyi kendi yapar); koordinat yalnız /nerede'de görünür.
- Her duyunun kendi zaman damgası var; BAYAT veri bağlama enjekte EDİLMEZ
  (dünkü konumla bugünkü cevabı kurmak yanlış cevaptan beterdir).
- `kapat()` ana şalter: kapalıyken uç 403 döner ve enjeksiyon durur. Uygulamadaki
  üç anahtar (konum/takvim/cihaz) ayrı bir kat — ikisi de kapatabilir.
- Takvimden yalnız BAŞLIK, saat ve yer alınır; not/katılımcı/URL alanları
  uygulamada zaten gönderilmez.
"""
import json
import os
import time

from ghost import config

DOSYA = "duyular.json"
# Bayatlık sınırları (sn): bu yaştan sonra veri bağlama girmez.
BAYAT = {"konum": 6 * 3600, "takvim": 3 * 3600, "cihaz": 2 * 3600}
DUYULAR = ("konum", "takvim", "cihaz")
ETKINLIK_TAVAN = 12          # tek gönderide alınacak en fazla etkinlik


def _yol():
    return os.path.join(config.OPS, DOSYA)


def oku():
    try:
        with open(_yol(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _yaz_dosya(d):
    y = _yol()
    os.makedirs(os.path.dirname(y), exist_ok=True)
    tmp = y + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, y)


def acik():
    """Ana şalter. Varsayılan AÇIK — uygulama izin verilmeden zaten veri göndermez,
    bu şalter sahibin tek hamlede kapatabilmesi için var."""
    return bool(oku().get("acik", True))


def salter(deger):
    d = oku()
    d["acik"] = bool(deger)
    _yaz_dosya(d)
    return d["acik"]


def _sayi(v, alt=None, ust=None):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    if alt is not None and f < alt:
        return None
    if ust is not None and f > ust:
        return None
    return f


def _metin(v, n=120):
    if not isinstance(v, str):
        return ""
    return v.strip()[:n]


def _temizle_konum(k):
    enlem, boylam = _sayi(k.get("enlem"), -90, 90), _sayi(k.get("boylam"), -180, 180)
    if enlem is None or boylam is None:
        return None
    return {"enlem": round(enlem, 5), "boylam": round(boylam, 5),
            "dogruluk": _sayi(k.get("dogruluk"), 0, 100000),
            "yer": _metin(k.get("yer")), "ulke": _metin(k.get("ulke"), 60),
            "ts": time.time()}


def _temizle_takvim(t):
    et = []
    for e in (t.get("etkinlikler") or [])[:ETKINLIK_TAVAN]:
        if not isinstance(e, dict):
            continue
        ad = _metin(e.get("ad"), 100)
        if not ad:
            continue
        et.append({"ad": ad, "bas": _metin(e.get("bas"), 20), "bit": _metin(e.get("bit"), 20),
                   "yer": _metin(e.get("yer"), 60), "tamgun": bool(e.get("tamgun")),
                   "takvim": _metin(e.get("takvim"), 40)})
    return {"etkinlikler": et, "gun": _metin(t.get("gun"), 10), "ts": time.time()}


def _temizle_cihaz(c):
    return {"pil": _sayi(c.get("pil"), 0, 1), "sarj": bool(c.get("sarj")),
            "dusuk_guc": bool(c.get("dusuk_guc")), "ag": _metin(c.get("ag"), 20),
            "isi": _metin(c.get("isi"), 20), "model": _metin(c.get("model"), 40),
            "ios": _metin(c.get("ios"), 20), "ts": time.time()}


_TEMIZLE = {"konum": _temizle_konum, "takvim": _temizle_takvim, "cihaz": _temizle_cihaz}


def yaz(veri, cihaz=None):
    """Uygulamadan gelen anlık görüntüyü birleştir. Kısmi gönderi normaldir:
    yalnız gelen duyular güncellenir, ötekiler dokunulmadan kalır.
    Dönen: güncellenen duyu adları."""
    if not isinstance(veri, dict):
        return []
    d = oku()
    guncel = []
    for ad in DUYULAR:
        ham = veri.get(ad)
        if not isinstance(ham, dict):
            continue
        temiz = _TEMIZLE[ad](ham)
        if temiz is None:
            continue
        d[ad] = temiz
        guncel.append(ad)
    if guncel:
        d["cihaz_id"] = cihaz or d.get("cihaz_id")
        d["son"] = time.time()
        _yaz_dosya(d)
    return guncel


def yas(duyu, d=None):
    """Duyunun yaşı (sn) — veri yoksa None."""
    d = oku() if d is None else d
    ts = (d.get(duyu) or {}).get("ts")
    return None if not ts else max(0.0, time.time() - float(ts))


def taze(duyu, d=None):
    y = yas(duyu, d)
    return y is not None and y < BAYAT[duyu]


def _yas_metin(sn):
    if sn is None:
        return "?"
    if sn < 90:
        return "az önce"
    if sn < 5400:
        return "%d dk önce" % (sn / 60)
    if sn < 172800:
        return "%d sa önce" % (sn / 3600)
    return "%d gün önce" % (sn / 86400)


def _bugun_etkinlikler(d):
    """Takvimdeki etkinlikler — yalnız gönderildiği gün bugünse anlamlı."""
    t = d.get("takvim") or {}
    if t.get("gun") and t["gun"] != time.strftime("%Y-%m-%d"):
        return []
    return t.get("etkinlikler") or []


def siradaki(d=None):
    """Bugünün kalan ilk etkinliği (saat geçmemiş) — yoksa None."""
    d = oku() if d is None else d
    simdi = time.strftime("%H:%M")
    for e in _bugun_etkinlikler(d):
        if e.get("tamgun"):
            continue
        if (e.get("bas") or "") >= simdi:
            return e
    return None


def ozet(d=None):
    """Nabız için sözlük — uygulama "Sahip" satırını buradan çizer."""
    d = oku() if d is None else d
    if not d.get("son"):
        return {"var": False, "acik": bool(d.get("acik", True))}
    k, c = d.get("konum") or {}, d.get("cihaz") or {}
    sr = siradaki(d)
    return {
        "var": True, "acik": bool(d.get("acik", True)),
        "yer": k.get("yer") or "", "konum_yas": _yas_metin(yas("konum", d)),
        "konum_taze": taze("konum", d),
        "etkinlik_sayisi": len(_bugun_etkinlikler(d)),
        "siradaki": ("%s %s" % (sr.get("bas", ""), sr.get("ad", ""))).strip() if sr else "",
        "pil": c.get("pil"), "sarj": bool(c.get("sarj")), "ag": c.get("ag") or "",
        "cihaz_taze": taze("cihaz", d),
    }


def satir():
    """Sohbet turuna iliştirilen TEK satır. Şalter kapalıysa ya da her duyu
    bayatsa boş döner — Ghost eski konumla cevap kurmasın."""
    d = oku()
    if not d.get("acik", True):
        return ""
    p = []
    k = d.get("konum") or {}
    if taze("konum", d) and k.get("yer"):
        p.append("konum %s (%s)" % (k["yer"], _yas_metin(yas("konum", d))))
    if taze("takvim", d):
        et = _bugun_etkinlikler(d)
        sr = siradaki(d)
        if sr:
            p.append("bugün %d etkinlik, sıradaki %s %s" % (len(et), sr.get("bas", ""), sr.get("ad", "")))
        elif et:
            p.append("bugün %d etkinlik, kalanı yok" % len(et))
        else:
            p.append("bugün takvim boş")
    c = d.get("cihaz") or {}
    if taze("cihaz", d) and c.get("pil") is not None:
        p.append("telefon %%%d%s%s" % (round(c["pil"] * 100), " şarjda" if c.get("sarj") else "",
                                       " · düşük güç" if c.get("dusuk_guc") else ""))
    if not p:
        return ""
    return ("[SAHİBİN BAĞLAMI (kendi telefonundan, izin verdiği duyular): %s. "
            "Konu ilgisizse yok say; ilgiliyse SOR-MADAN kullan.]\n" % " · ".join(p))


def metin():
    """/nerede ve /duyular için okunabilir tam metin."""
    d = oku()
    if not d.get("acik", True):
        return "🔕 Duyular KAPALI (/duyular ac ile aç). Son veri saklı ama kullanılmıyor."
    if not d.get("son"):
        return ("🫥 Henüz duyu verisi yok. Kokpit'te Ayarlar → Duyular altından "
                "konum/takvim/cihaz anahtarlarını aç ve iOS izinlerini ver.")
    s = []
    k = d.get("konum") or {}
    if k.get("ts"):
        bayat = "" if taze("konum", d) else "  ⚠️ bayat, bağlama girmiyor"
        s.append("📍 %s%s\n   %.5f, %.5f (±%s m) · %s%s" % (
            k.get("yer") or "yer adı çözülemedi", (" · " + k["ulke"]) if k.get("ulke") else "",
            k.get("enlem", 0), k.get("boylam", 0),
            round(k["dogruluk"]) if k.get("dogruluk") else "?", _yas_metin(yas("konum", d)), bayat))
    else:
        s.append("📍 konum yok (izin verilmemiş ya da anahtar kapalı)")
    t = d.get("takvim") or {}
    if t.get("ts"):
        et = _bugun_etkinlikler(d)
        if not et:
            s.append("📅 bugün etkinlik yok (%s)" % _yas_metin(yas("takvim", d)))
        else:
            sat = ["📅 bugün %d etkinlik (%s)" % (len(et), _yas_metin(yas("takvim", d)))]
            for e in et:
                if e.get("tamgun"):
                    sat.append("   • tüm gün — %s" % e["ad"])
                else:
                    sat.append("   • %s–%s %s%s" % (e.get("bas", ""), e.get("bit", ""), e["ad"],
                                                    (" @ " + e["yer"]) if e.get("yer") else ""))
            s.append("\n".join(sat))
    else:
        s.append("📅 takvim yok (izin verilmemiş ya da anahtar kapalı)")
    c = d.get("cihaz") or {}
    if c.get("ts"):
        s.append("📱 %s · iOS %s · pil %%%d%s · %s%s (%s)" % (
            c.get("model") or "iPhone", c.get("ios") or "?",
            round((c.get("pil") or 0) * 100), " şarjda" if c.get("sarj") else "",
            c.get("ag") or "ağ?", " · düşük güç" if c.get("dusuk_guc") else "",
            _yas_metin(yas("cihaz", d))))
    return "\n".join(s)


def bugun_metin():
    """/bugun — yalnız takvim."""
    d = oku()
    if not d.get("acik", True):
        return "🔕 Duyular kapalı — takvim okunmuyor (/duyular ac)."
    t = d.get("takvim") or {}
    if not t.get("ts"):
        return "📅 Takvim izni yok. Kokpit → Ayarlar → Duyular → Takvim."
    if not taze("takvim", d):
        return ("📅 Takvim verisi bayat (%s). Kokpit'i bir aç, tazelensin."
                % _yas_metin(yas("takvim", d)))
    et = _bugun_etkinlikler(d)
    if not et:
        return "📅 Bugün takvimin boş."
    sat = ["📅 Bugün (%d):" % len(et)]
    for e in et:
        if e.get("tamgun"):
            sat.append("• tüm gün — %s" % e["ad"])
        else:
            sat.append("• %s–%s %s%s" % (e.get("bas", ""), e.get("bit", ""), e["ad"],
                                         (" @ " + e["yer"]) if e.get("yer") else ""))
    return "\n".join(sat)


if __name__ == "__main__":
    import sys
    a = sys.argv[1:]
    if a and a[0] in ("ac", "kapat"):
        print("Duyular:", "AÇIK" if salter(a[0] == "ac") else "KAPALI")
    elif a and a[0] == "satir":
        print(satir() or "(boş — veri yok ya da bayat)")
    elif a and a[0] == "json":
        print(json.dumps(oku(), ensure_ascii=False, indent=1))
    else:
        print(metin())
