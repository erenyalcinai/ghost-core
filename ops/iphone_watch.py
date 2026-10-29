#!/usr/bin/env python3
"""iPhone rayı nöbetçisi — iki sessiz ölüm biçimini yakalar.

Bu ray iki şekilde HABERSİZ ölür:

1. WDA süreci ölür → launchd yeniden kaldırır, ama kaldıramazsa (imza sorunu,
   cihaz çıkarılmış, telefon kilitli) Ghost bunu ancak bir telefon işi
   başarısız olunca öğrenir. O an, ihtiyacın olduğu andır.
2. PROVİZYON PROFİLİ DOLAR. Geliştirici profilleri süreli; dolduğu gün WDA
   telefonda başlamaz olur. Hiçbir hata Telegram'a düşmez, telefon öylece
   cevap vermez. Bu, yedek parolası ve SMS nabzıyla aynı aile: kurulduğu gün
   çalışan, aylar sonra sessizce bozulan şeyler.

Bu yüzden nöbetçi tarihi ÖNCEDEN sayar: 21 gün kala uyarır, dolduğunda
tekrar uyarır. Bir kez uyarır, durum değişene kadar susar.

İKİ KAYNAK, BİLEREK AYRI:
- Profil tarihi App Store Connect'ten OKUNUR (VPS, doğrudan). Bu bilginin
  Mac'e ihtiyacı yok — telefon çıkarılmış, Mac kapalı, işçi ölü olsa bile
  profilin dolduğunu zamanında öğreniriz. Nöbetçinin en kritik yarısı böylece
  hiçbir aracıya bağlı değil.
- WDA canlılığı Mac'ten GELİR (ops/iphone_report.py durumu VPS'e yazar); VPS
  telefonu göremez, Mac'e inbound erişim de yok. Rapor eskiyse onu da söyler —
  "veri yok" ile "iyi" birbirine karışmasın.

Durum dosyası HİÇ yoksa susar: henüz kurulmamış rayın bozulduğunu bildirmek
gürültüdür. Bir kez yazıldıktan sonra bayatlaması alarmdır.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPS = os.path.join(BASE, "ops")
DURUM = os.path.join(OPS, "iphone_state.json")      # Mac yazar
NOBET = os.path.join(OPS, "iphone_watch_state.json")

PROFIL_ADI = "Ghost WDA Dev"
UYARI_GUN = 21          # profil bitişine bu kadar kala uyar
BAYAT_SAAT = 48         # Mac bu kadar süredir durum yazmadıysa şüphelen
import heartbeat  # noqa: E402  Nabız damgası (Faz 2.10; sys.path[0] = ops)



def _load_env():
    p = os.path.join(BASE, "secrets.env")
    if not os.path.exists(p):
        return
    for line in open(p, encoding="utf-8", errors="replace"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _oku(p, vars_=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return vars_


def bildir(metin, duzeldi=False):
    """GHOST sınıfı alarm (2026-10-29): 30 günde 32 "📵 iPhone durumu güncellenmedi" mesajı
    sahibe gitti, elinden gelen yoktu (Mac/işçi/Wi-Fi meselesi; Sahip 2026-10-29: 13 mini şu an
    Wi-Fi'da değil, sorun yok). Kayıt Nabız'da sarı nokta olur, Telegram'a düşmez.
    Eskisi: ops/send_telegram.py alt süreci."""
    try:
        from ghost import alarm
        if duzeldi:
            alarm.kapat("iphone", "durum")
        else:
            alarm.bildir("iphone", metin, sinif="ghost", anahtar="durum")
    except Exception:
        pass


def kalan_gun(tarih_str):
    """'2027-08-20' → kalan gün. Ayrıştıramazsa None (suçlama, sus)."""
    for kalip in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S",
                  "%Y-%m-%dT%H:%M:%S"):
        try:
            t = datetime.strptime(str(tarih_str)[:19], kalip)
            return (t - datetime.now()).days
        except (ValueError, TypeError):
            continue
    return None


def asc_profil():
    """ASC'den profili oku → (bitis, durum) ya da (None, hata_metni).

    Anahtar/kütüphane yoksa sessiz atlanır: bu makine ASC'siz de çalışabilmeli.
    """
    _load_env()
    try:
        import jwt  # PyJWT
    except ImportError:
        return None, "PyJWT yok"
    try:
        from ghost import vekil     # sır vekili kuruluysa JWT'yi vekil imzalar (Faz 2)
        if vekil.aktif(None, "ASC_KEY_PATH"):
            hdr = {}
        else:
            key = open(os.environ["ASC_KEY_PATH"], encoding="utf-8").read()
            now = int(time.time())
            tok = jwt.encode({"iss": os.environ["ASC_ISSUER_ID"], "iat": now,
                              "exp": now + 600, "aud": "appstoreconnect-v1"},
                             key, algorithm="ES256",
                             headers={"kid": os.environ["ASC_KEY_ID"]})
            hdr = {"Authorization": "Bearer " + tok}
        req = urllib.request.Request(vekil.url("asc", "/v1/profiles?limit=200"), headers=hdr)
        data = json.load(urllib.request.urlopen(req, timeout=30))
    except KeyError:
        return None, "ASC anahtarı tanımsız"
    except Exception as e:
        return None, "ASC okunamadı (%s)" % type(e).__name__
    for p in data.get("data", []):
        a = p.get("attributes", {})
        if a.get("name") == PROFIL_ADI:
            return (a.get("expirationDate"), a.get("profileState")), None
    return None, "profil '%s' portalda YOK (silinmiş olabilir)" % PROFIL_ADI


def main():
    durum = _oku(DURUM)
    nobet = _oku(NOBET, {}) or {}
    uyarilar = []

    # --- 1. Profil: ASC birincil kaynak, Mac raporu yedek ---
    asc, asc_hata = asc_profil()
    bitis = asc[0] if asc else (durum or {}).get("profil_bitis")
    if asc and asc[1] and asc[1] != "ACTIVE":
        uyarilar.append("📵 Provizyon profili '%s' ACTIVE değil (%s) — WDA "
                        "imzası geçersiz." % (PROFIL_ADI, asc[1]))
    if asc_hata and not bitis:
        uyarilar.append("❓ Profil tarihi doğrulanamıyor: %s. Telefon rayının "
                        "son kullanma tarihini şu an kimse saymıyor." % asc_hata)

    gun = kalan_gun(bitis)
    if gun is not None:
        if gun < 0:
            uyarilar.append("📵 Provizyon profili DOLDU (%d gün önce). WDA "
                            "artık başlamaz; yeniden imzalanmalı." % -gun)
        elif gun <= UYARI_GUN:
            uyarilar.append("⏳ Provizyon profili %d gün sonra doluyor (%s). "
                            "Dolduğunda telefon rayı sessizce ölür — şimdiden "
                            "yenileyelim." % (gun, str(bitis)[:10]))

    # --- 2. Canlılık: yalnız Mac raporlarsa ---
    if durum is not None:
        yas_saat = (time.time() - int(durum.get("ts", 0) or 0)) / 3600
        if yas_saat > BAYAT_SAAT:
            uyarilar.append("📵 iPhone durumu %d saattir güncellenmedi — Mac "
                            "rapor yazmıyor olabilir (telefon çıkarılmış ya da "
                            "işçi ölü)." % yas_saat)
        elif durum.get("wda_ready") is False:
            neden = durum.get("neden") or "sebep bildirilmedi"
            uyarilar.append("📵 WDA ayakta değil — telefon Ghost için şu an "
                            "ölü (%s)." % neden)

    imza = "|".join(uyarilar)
    if uyarilar and nobet.get("son_imza") != imza:
        bildir("\n".join(uyarilar))
        nobet["son_imza"] = imza
    elif not uyarilar and nobet.get("son_imza"):
        bildir("✅ iPhone rayı normale döndü.", duzeldi=True)
        nobet["son_imza"] = ""

    nobet["son_kontrol"] = int(time.time())
    nobet["profil_bitis"] = bitis
    heartbeat.beat("iphone_watch")
    json.dump(nobet, open(NOBET, "w", encoding="utf-8"), ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
