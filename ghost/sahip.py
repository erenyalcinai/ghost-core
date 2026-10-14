"""Sahip profili — Ghost'un KİME çalıştığı tek yerde (2026-10-14, açık çekirdek kararı).

Kişisel sabitler (ad, siteler, Masa kök kartları, varlık tohumu, ASR sözlüğü)
koddan çıkıp buraya geldi. Kaynak sırası:
  1. ops/sahip.json          — gerçek profil (açık yayında DIŞARIDA kalır)
  2. ops/sahip.example.json  — kurgu örnek (ghost-core bununla koşar/test eder)
  3. hiçbiri yoksa boş sözlük — modüller makul boş varsayılanlarla çalışır.
Yol `GHOST_SAHIP` ortam değişkeniyle de verilebilir (testler için).
"""
import json
import os

from ghost import config

_cache = None
_cache_yol = None
HATA = None   # son yükleme hatası (bozuk JSON vb.) — selfcheck okur, import KIRILMAZ


def adaylar():
    ozel = os.environ.get("GHOST_SAHIP")
    if ozel:
        return [ozel]
    return [os.path.join(config.BASE, "ops", ad) for ad in ("sahip.json", "sahip.example.json")]


def yol():
    """Okunabilen ilk aday (gerçek → örnek); hiçbiri yoksa None."""
    for p in adaylar():
        if os.path.exists(p):
            return p
    return None


def _oku(p):
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict):
        raise ValueError("profil sözlük değil")
    return d


def profil(yenile=False):
    """Profil sözlüğü (önbellekli). Gerçek dosya BOZUKSA (elle düzenleme hatası) import
    kırılmaz: hata HATA'ya yazılır, sıradaki aday (örnek) ya da boş sözlük kullanılır —
    köprü ayakta kalır, selfcheck "profil bozuk" diye uyarır."""
    global _cache, _cache_yol, HATA
    p = yol()
    if _cache is None or yenile or p != _cache_yol:
        _cache_yol = p
        _cache, HATA = {}, None
        for aday in adaylar():
            if not os.path.exists(aday):
                continue
            try:
                _cache = _oku(aday)
                _cache_yol = aday
                break
            except (OSError, ValueError) as e:
                HATA = "%s: %s" % (os.path.basename(aday), e)
    return _cache


def durum():
    """selfcheck için: (sorun_metni | None). Gerçek profil yoksa ya da bozuksa uyarır."""
    profil()
    if HATA:
        return "👤 Sahip profili BOZUK (%s) — Ghost örnek/boş profille koşuyor, ops/sahip.json'u düzelt." % HATA
    if not _cache_yol or os.path.basename(_cache_yol) != "sahip.json":
        if os.environ.get("GHOST_SAHIP"):
            return None
        return "👤 Sahip profili yok (ops/sahip.json) — Ghost örnek profille koşuyor."
    return None


def ad():
    return profil().get("ad") or "Sahip"


def siteler():
    """Sıralı site listesi: alan, baslik, proje, url, izle, trafik, strict200, get_only."""
    return list(profil().get("siteler") or [])


def site_meta():
    return {s["alan"]: (s.get("baslik", ""), s.get("proje", "")) for s in siteler()}


def izlenen_urller():
    return [s["url"] for s in siteler() if s.get("izle") and s.get("url")]


def strict200():
    return {s["url"] for s in siteler() if s.get("strict200") and s.get("url")}


def get_only():
    return {s["url"] for s in siteler() if s.get("get_only") and s.get("url")}


def nfc_kart():
    """Fiziksel NFC kartın gittiği URL (profilde "nfc": true) ya da None."""
    return next((s["url"] for s in siteler() if s.get("nfc") and s.get("url")), None)


def trafik_siteleri():
    return tuple(s["alan"] for s in siteler() if s.get("trafik"))


def masa_kok():
    return tuple(profil().get("masa_kok") or ("Ghost", "Raporlar", "Paylaşımlar"))


def asr_sozluk():
    return list(profil().get("asr_sozluk") or [])


def ev_sehirleri():
    """Sahibin alışıldık şehirleri (site trafiğinde "sahip imzası" süzgeci)."""
    return list(profil().get("ev_sehirleri") or [])


def ssh_parmak_izleri():
    """Ghost'un kendi SSH anahtarlarının parmak izleri (giriş nöbetçisi bunları uyarı saymaz)."""
    return list(profil().get("ssh_parmak_izleri") or [])


def varliklar():
    return [tuple(v) for v in profil().get("varliklar") or []]


def iliskiler():
    return [tuple(v) for v in profil().get("iliskiler") or []]
