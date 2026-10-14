"""Bölgeler — coğrafi tetik (Faz 6). Faz 5'in konumu PASİFTİ: Ghost "neredesin"
sorusuna cevap veriyor ama yer değişimi hiçbir şey BAŞLATMIYORDU.

Burada Ghost adlandırılmış daireler tanımlar (`ops/bolgeler.json`); uygulama
bunları CLLocationManager'a bölge olarak kaydeder ve giriş/çıkışta Ghost'a haber
verir. Haber, sahibin bir mesajı gibi köprüye düşer — yani Ghost giriş anında
DÜŞÜNÜR, sabit bir cümle basmaz ("fabrikaya vardın" değil, "fabrikadasın, dün
konuştuğumuz EPS kalıp listesi hâlâ açık").

Sınırlar bilinçli:
- iOS tek uygulamada en çok **20** bölge izler; tavan burada da var (TAVAN).
- Yarıçap 100 m'nin altında iOS güvenilmez sonuç verir; taban 100 m.
- Bölge olayı Ghost'u KOŞTURUR, yani para/token harcar. Aynı bölge için
  `SOGUMA_SN` içinde ikinci tetik yutulur (kapı önünde volta atmak Ghost'u
  10 kez uyandırmasın).
"""
import json
import math
import os
import time

from ghost import config, governance

DOSYA = "bolgeler.json"
TAVAN = 20              # iOS'un uygulama başına bölge sınırı
TABAN_YARICAP = 100     # m — altında iOS güvenilmez
VARSAYILAN_YARICAP = 200
SOGUMA_SN = 900         # aynı bölge+olay için tekrar tetiği yut (15 dk)


def _yol():
    return os.path.join(config.OPS, DOSYA)


def oku():
    try:
        with open(_yol(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {"bolgeler": []}
    except Exception:
        return {"bolgeler": []}


def _yaz(d):
    y = _yol()
    os.makedirs(os.path.dirname(y), exist_ok=True)
    tmp = y + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, y)


def liste():
    return oku().get("bolgeler", [])


def _bul(ad):
    a = (ad or "").strip().lower()
    for b in liste():
        if b["ad"].lower() == a:
            return b
    return None


def ekle(ad, enlem, boylam, yaricap=VARSAYILAN_YARICAP, giris=True, cikis=False):
    """Aynı adla varsa ÜSTÜNE yazar (yeniden tanımlama doğal iş)."""
    ad = (ad or "").strip()[:40]
    if not ad:
        return None, "ad boş"
    try:
        enlem, boylam, yaricap = float(enlem), float(boylam), int(yaricap)
    except (TypeError, ValueError):
        return None, "koordinat sayı değil"
    if not (-90 <= enlem <= 90 and -180 <= boylam <= 180):
        return None, "koordinat aralık dışı"
    yaricap = max(TABAN_YARICAP, min(yaricap, 20000))
    d = oku()
    kalan = [b for b in d.get("bolgeler", []) if b["ad"].lower() != ad.lower()]
    if len(kalan) >= TAVAN:
        return None, "bölge tavanı %d (iOS sınırı) — önce birini sil" % TAVAN
    b = {"ad": ad, "enlem": round(enlem, 6), "boylam": round(boylam, 6),
         "yaricap": yaricap, "giris": bool(giris), "cikis": bool(cikis),
         "olusturuldu": time.strftime("%F %T")}
    d["bolgeler"] = kalan + [b]
    d["seq"] = int(d.get("seq") or 0) + 1     # uygulama değişimi bundan anlar
    _yaz(d)
    governance.audit("bolgeler", "ekle", governance.REVERSIBLE,
                     "%s @ %.5f,%.5f r=%dm" % (ad, enlem, boylam, yaricap))
    return b, None


def buradan(ad, yaricap=VARSAYILAN_YARICAP, giris=True, cikis=False):
    """Telefonun ŞU ANKİ konumundan bölge kur — "burayı fabrika diye kaydet".
    Konum bayatsa reddeder; yanlış yere kurulan bölge sessizce hiç tetiklenmez."""
    from ghost import duyular
    d = duyular.oku()
    k = d.get("konum") or {}
    if not k.get("ts"):
        return None, "telefondan konum gelmemiş (Kokpit → Ayarlar → Duyular → Konum)"
    if not duyular.taze("konum", d):
        return None, "konum bayat — Kokpit'i bir aç, tazelensin"
    b, hata = ekle(ad, k["enlem"], k["boylam"], yaricap, giris, cikis)
    if b:
        b["yer"] = k.get("yer") or ""
    return b, hata


def sil(ad):
    d = oku()
    once = len(d.get("bolgeler", []))
    d["bolgeler"] = [b for b in d.get("bolgeler", []) if b["ad"].lower() != (ad or "").strip().lower()]
    if len(d["bolgeler"]) == once:
        return False
    d["seq"] = int(d.get("seq") or 0) + 1
    _yaz(d)
    governance.audit("bolgeler", "sil", governance.REVERSIBLE, ad)
    return True


def _soguma_gecti(ad, olay):
    d = oku()
    anahtar = "%s|%s" % ((ad or "").lower(), olay)
    son = (d.get("son_tetik") or {}).get(anahtar)
    simdi = time.time()
    if son and simdi - float(son) < SOGUMA_SN:
        return False
    d.setdefault("son_tetik", {})[anahtar] = simdi
    _yaz(d)
    return True


def olay(ad, olay_tipi, cihaz=None):
    """Uygulamadan giriş/çıkış haberi → Ghost'a düşen mesaj metni (ya da None:
    bilinmeyen bölge / soğuma penceresi)."""
    b = _bul(ad)
    if not b:
        return None
    if olay_tipi not in ("giris", "cikis"):
        return None
    if olay_tipi == "giris" and not b.get("giris"):
        return None
    if olay_tipi == "cikis" and not b.get("cikis"):
        return None
    if not _soguma_gecti(b["ad"], olay_tipi):
        return None
    metin = ("[Bölge] Sahip '%s' bölgesine GİRDİ." if olay_tipi == "giris"
             else "[Bölge] Sahip '%s' bölgesinden ÇIKTI.") % b["ad"]
    metin += (" Bu bir otomatik tetik — bölgeyle ilgili bekleyen bir iş, açık bir konu ya da "
              "söylenmesi gereken bir şey varsa söyle; yoksa tek satırla geç, uzatma.")
    governance.audit("bolgeler", olay_tipi, governance.READ, b["ad"])
    return metin


def metin():
    b = liste()
    if not b:
        return ("📍 Tanımlı bölge yok. Ghost bir yere varınca/ayrılınca tetiklenmez.\n"
                "Kur: /bolge ekle <ad> [yarıçap m] — telefonun ŞU ANKİ konumunu kaydeder.")
    s = ["📍 Bölgeler (%d/%d):" % (len(b), TAVAN)]
    for x in b:
        yon = "·".join([t for t, v in (("giriş", x.get("giris")), ("çıkış", x.get("cikis"))) if v]) or "kapalı"
        s.append("• %s — %dm · %s\n   %.5f, %.5f" % (x["ad"], x["yaricap"], yon, x["enlem"], x["boylam"]))
    return "\n".join(s)


def mesafe(enlem1, boylam1, enlem2, boylam2):
    """Haversine (m) — testler ve 'hangi bölgedesin' için."""
    r = 6371000.0
    f1, f2 = math.radians(enlem1), math.radians(enlem2)
    df, dl = math.radians(enlem2 - enlem1), math.radians(boylam2 - boylam1)
    a = math.sin(df / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def icinde():
    """Telefonun son konumuna göre şu an içinde olunan bölgeler."""
    from ghost import duyular
    k = (duyular.oku().get("konum") or {})
    if not k.get("ts"):
        return []
    return [b["ad"] for b in liste()
            if mesafe(k["enlem"], k["boylam"], b["enlem"], b["boylam"]) <= b["yaricap"]]


if __name__ == "__main__":
    import sys
    a = sys.argv[1:]
    if a and a[0] == "ekle" and len(a) >= 2:
        b, h = buradan(a[1], int(a[2]) if len(a) > 2 else VARSAYILAN_YARICAP)
        print("HATA: " + h if h else "kuruldu: %s" % json.dumps(b, ensure_ascii=False))
    elif a and a[0] == "sil" and len(a) >= 2:
        print("silindi" if sil(a[1]) else "yok")
    elif a and a[0] == "json":
        print(json.dumps(oku(), ensure_ascii=False, indent=1))
    else:
        print(metin())
