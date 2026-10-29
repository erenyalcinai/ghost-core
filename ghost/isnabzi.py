"""İş nabzı — "İş nasıl?" (Nabız'ın 3. sorusu). Kaynak `ops/biz_digest.py`nin her sabah
yazdığı `biz_state.json` (ASC, D1, PostHog, Sentry). Düne göre fark için `biz_history.json`
(biz_digest her yazımda ekler; 2026-09-04'ten önce tarihçe yoktu → ilk günler fark boş).
Sayı üretmez, ölçmez — yalnız var olan ölçümü sahibin okuyacağı sıraya koyar.
2026-10-29: App B satırları eklendi (biz_digest ürün-agnostik); eski öneksiz AppA anahtarları
(asc_*) tarihçede sürer, yeni kayıtta aynı değer appa_* adıyla da var — burada eski ad okunur,
yoksa yeni ada düşülür (eski history kaydında anahtar yoksa satır sessizce atlanır)."""
import json
import os
import time

from ghost import config, durum

STATE = "biz_state.json"
HISTORY = "biz_history.json"
HISTORY_TAVAN = 60
# sahibin okuma sırası: önce canlı ürün (App B), sonra AppA vitrin, insanlar, sağlık.
# (anahtar, etiket, tür, ürün); anahtar "a|b" = a yoksa b okunur (eski/yeni ad).
SIRA = [
    ("appb_state", "App B vitrin", "metin", "App B"),
    ("appb_indirme_gun", "App B indirme (gün)", "sayi", "App B"),
    ("appb_iap_gun", "App B IAP (gün)", "sayi", "App B"),
    ("appb_ciro_gun", "App B ciro (gün)", "metin", "App B"),
    ("appb_reviews", "App B yorum", "sayi", "App B"),
    ("appb_iap_state", "App B IAP", "metin", "App B"),
    ("asc_state|appa_state", "vitrin", "metin", "AppA"),
    ("d1_users", "kullanıcı", "sayi", "AppA"),
    ("d1_paid", "ödeyen", "sayi", "AppA"),
    ("ph_users_24h", "aktif (24 sa)", "sayi", "AppA"),
    ("ph_events_24h", "olay (24 sa)", "sayi", "AppA"),
    ("sentry_open", "açık hata", "sayi", "AppA"),
    ("asc_builds|appa_builds", "build", "sayi", "AppA"),
    ("asc_testers|appa_testers", "testçi", "sayi", "AppA"),
    ("appa_reviews", "AppA yorum", "sayi", "AppA"),
]
VITRIN = {"PREPARE_FOR_SUBMISSION": "gönderime hazırlanıyor", "WAITING_FOR_REVIEW": "incelemede (bekliyor)",
          "IN_REVIEW": "incelemede", "PENDING_DEVELOPER_RELEASE": "yayına hazır", "READY_FOR_SALE": "yayında",
          "READY_FOR_DISTRIBUTION": "yayında", "REJECTED": "reddedildi", "DEVELOPER_REJECTED": "geri çekildi",
          "APPROVED": "onaylı", "MISSING_METADATA": "meta veri eksik", "yok": "yok"}
KOTU_ARTIS = {"sentry_open"}         # artması kötü olan metrikler


def _deger(met, anahtar):
    """'a|b' → ilk bulunan; (bulundu mu, değer)."""
    for k in anahtar.split("|"):
        if k in met:
            return True, met[k]
    return False, None


def _oku(ad, default):
    """Faz 1.9: durum tablosu (`biz_state` / `biz_history`); eski json ilk okumada taşınır."""
    try:
        return durum.tasi(ad[:-5] if ad.endswith(".json") else ad, os.path.join(config.OPS, ad), default)
    except Exception:
        return default


def tarihce_ekle(kayit, yol=None):
    """biz_digest her yazımda çağırır: aynı güne ikinci ölçüm son kaydı günceller."""
    yol = yol or os.path.join(config.OPS, HISTORY)   # eski json (varsa bir kez içeri alınır)

    def _ekle(h):
        h = list(h) if isinstance(h, list) else []
        if h and h[-1].get("date") == kayit.get("date"):
            h[-1] = kayit
        else:
            h.append(kayit)
        return h[-HISTORY_TAVAN:]
    durum.tasi("biz_history", yol, None)
    return len(durum.guncelle("biz_history", _ekle, []))


def _onceki(tarih, h):
    for k in reversed(h):
        if k.get("date") and k["date"] < (tarih or ""):
            return k
    return None


def oku():
    st = _oku(STATE, {}) or {}
    met = st.get("metrics") or {}
    if not met:
        return {"var": False}
    h = _oku(HISTORY, []) or []
    onc = (_onceki(st.get("date"), h) or {}).get("metrics") or {}
    out = []
    for k, ad, tur, urun in SIRA:
        var, v = _deger(met, k)
        if not var:
            continue
        o = _deger(onc, k)[1]
        k = k.split("|")[0]
        satir = {"anahtar": k, "ad": ad, "tur": tur, "urun": urun, "deger": v,
                 "goster": VITRIN.get(v, v) if tur == "metin" else v}
        if o is not None and o != v:
            if tur == "sayi" and isinstance(v, (int, float)) and isinstance(o, (int, float)):
                satir["fark"] = v - o
                satir["yon"] = "kotu" if ((v > o) == (k in KOTU_ARTIS)) else "iyi"
            else:
                satir["onceki"] = VITRIN.get(o, o)
        out.append(satir)
    try:
        yas_gun = int((time.time() - time.mktime(time.strptime(st.get("date", ""), "%Y-%m-%d"))) // 86400)
    except Exception:
        yas_gun = None
    return {"var": True, "tarih": st.get("date", ""), "yas_gun": yas_gun, "metrikler": out,
            "onceki_tarih": (_onceki(st.get("date"), h) or {}).get("date")}
