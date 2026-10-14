"""İş nabzı — "İş nasıl?" (Nabız'ın 3. sorusu). Kaynak `ops/biz_digest.py`nin her sabah
yazdığı `biz_state.json` (ASC, D1, PostHog, Sentry). Düne göre fark için `biz_history.json`
(biz_digest her yazımda ekler; 2026-09-04'ten önce tarihçe yoktu → ilk günler fark boş).
Sayı üretmez, ölçmez — yalnız var olan ölçümü sahibin okuyacağı sıraya koyar."""
import json
import os
import time

from ghost import config

STATE = "biz_state.json"
HISTORY = "biz_history.json"
HISTORY_TAVAN = 60
# sahibin okuma sırası: önce vitrin, sonra insanlar, sonra sağlık. (anahtar, etiket, tür)
SIRA = [
    ("asc_state", "vitrin", "metin"),
    ("d1_users", "kullanıcı", "sayi"),
    ("d1_paid", "ödeyen", "sayi"),
    ("ph_users_24h", "aktif (24 sa)", "sayi"),
    ("ph_events_24h", "olay (24 sa)", "sayi"),
    ("sentry_open", "açık hata", "sayi"),
    ("asc_builds", "build", "sayi"),
    ("asc_testers", "testçi", "sayi"),
]
VITRIN = {"PREPARE_FOR_SUBMISSION": "gönderime hazırlanıyor", "WAITING_FOR_REVIEW": "incelemede (bekliyor)",
          "IN_REVIEW": "incelemede", "PENDING_DEVELOPER_RELEASE": "yayına hazır", "READY_FOR_SALE": "yayında",
          "REJECTED": "reddedildi", "DEVELOPER_REJECTED": "geri çekildi"}
KOTU_ARTIS = {"sentry_open"}         # artması kötü olan metrikler


def _oku(ad, default):
    try:
        with open(os.path.join(config.OPS, ad), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def tarihce_ekle(kayit, yol=None):
    """biz_digest her yazımda çağırır: aynı güne ikinci ölçüm son kaydı günceller."""
    yol = yol or os.path.join(config.OPS, HISTORY)
    try:
        with open(yol, encoding="utf-8") as f:
            h = json.load(f)
        if not isinstance(h, list):
            h = []
    except Exception:
        h = []
    if h and h[-1].get("date") == kayit.get("date"):
        h[-1] = kayit
    else:
        h.append(kayit)
    h = h[-HISTORY_TAVAN:]
    tmp = yol + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False)
    os.replace(tmp, yol)
    return len(h)


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
    for k, ad, tur in SIRA:
        if k not in met:
            continue
        v, o = met[k], onc.get(k)
        satir = {"anahtar": k, "ad": ad, "tur": tur, "deger": v,
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
