"""Kesilen sohbet mesajı — köprü turun ORTASINDA yeniden başlarsa mesaj kaybolmasın.

KÖK SEBEP (2026-09-26, Sahip: "yanıt gelmemesi, kök neden ne"): Ubuntu'nun
unattended-upgrades'i her sabah 06:00-07:00 arasında paket güncelliyor; python3'ün
kullandığı bir kütüphane (bugün libexpat1) yenilenince needrestart ghost-bridge'i
yeniden başlatıyor. Cevabı yazan claude süreci köprünün çocuğu → onunla ölüyor.
Köprü açılışta yalnız akışı "kesildi" işaretliyordu (akis.bitmemis_kapat); sahibin
mesajı hiçbir yerde durmadığı için cevap SESSİZCE kayboluyordu. 16 günde 4 kez
(10, 18, 22, 26 Eylül). Aynı boşluk Ghost'un kendi restart'ında da vardı.

ÇÖZÜM: sohbet işçisi bir mesaja başlarken onu diske yazar, bitince siler. Köprü
açılırken dosya doluysa mesaj bir kez daha kuyruğa alınır (sahibe de "yeniden
alıyorum" denir). Tek deneme: aynı mesaj köprüyü iki kez düşürüyorsa döngüye
girmek yerine sahibe "tekrar yaz" denir. 2 saatten eski kesik de tekrar koşmaz
(bayat cevap, bayat bağlam).
"""
import json
import os
import time

from ghost import config

TAVAN_SN = 2 * 3600      # bundan eski kesik mesaj yeniden koşmaz
MAX_DENEME = 1           # yeniden kuyruğa alma en fazla bu kadar
ONEK = ("[KÖPRÜ TURUN ORTASINDA YENİDEN BAŞLADI — bu mesaj cevapsız kalmıştı; "
        "baştan, tam cevapla]\n")


def _dosya():
    return os.path.join(config.OPS, "sohbet_kesik.json")


def basladi(text, spoken=False, deneme=0):
    """Sohbet işçisi bir mesaja başlıyor: diske yaz (atomik)."""
    try:
        yol = _dosya()
        tmp = yol + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"text": text, "spoken": bool(spoken), "deneme": int(deneme),
                       "ts": int(time.time())}, f, ensure_ascii=False)
        os.replace(tmp, yol)
    except Exception:
        pass


def bitti():
    """Tur bitti (cevap üretildi ya da hata yakalandı): kaydı sil."""
    try:
        os.remove(_dosya())
    except FileNotFoundError:
        pass
    except Exception:
        pass


def devral(simdi=None):
    """Köprü AÇILIŞINDA çağrılır. Dosyayı okur ve siler.

    None → kesik mesaj yok. Yoksa sözlük:
      {"vazgec": False, "text": ONEK+metin, "spoken", "deneme": n+1, "yas": sn}
      {"vazgec": True,  "neden": "deneme"|"bayat", "text": ham metin, ...}
    """
    yol = _dosya()
    try:
        with open(yol, encoding="utf-8") as f:
            k = json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        bitti()
        return None
    bitti()
    simdi = time.time() if simdi is None else simdi
    yas = max(0, int(simdi - int(k.get("ts") or 0)))
    text = str(k.get("text") or "")
    if not text:
        return None
    deneme = int(k.get("deneme") or 0)
    out = {"text": text, "spoken": bool(k.get("spoken")), "deneme": deneme, "yas": yas}
    if deneme >= MAX_DENEME:
        out.update(vazgec=True, neden="deneme")
        return out
    if yas > TAVAN_SN:
        out.update(vazgec=True, neden="bayat")
        return out
    out.update(vazgec=False, deneme=deneme + 1,
               text=text if text.startswith(ONEK) else ONEK + text)
    return out
