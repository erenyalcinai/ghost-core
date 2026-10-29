"""Ghost'un kendi ölçüleri — tur gecikmesi, özerk karne ve sürtünme sayaçları.

NEDEN (2026-10-29, bütünsel denetim 09 #3/#9, 04 Ö9, 06 §2.2): Ghost'un ilk cevap
gecikmesi, kayıp cevap sayısı, özerk işin değeri ve güvenlik katmanının sürtünmesi
HİÇ ölçülmüyordu; "birkaç kat daha iyi" hedefi ölçüsüz kalıyordu. Bu modül iki
şey tutar:

  * `ops/metrik.jsonl`  — tur kayıtları: mesaj alındı → ilk görünür çıktı → cevap
    gitti (sn), kanal, zaman aşımı, araç sayısı. Nabız p50/p95'i buradan okur.
  * `ops/metrik_sayac.json` — adlandırılmış sayaçlar (gün bazlı): özerk iş, onay
    kartı, kapı engeli, karantina, kayıp cevap, echo_token… Haftalık sentez ve
    karne bunları toplar.

İlke: ölçüm hiçbir akışı yavaşlatmaz ve asla fırlatmaz (hepsi try/except).
Kişisel içerik yazılmaz — yalnız sayı, süre, kanal ve tür.
"""
import json
import os
import threading
import time

from ghost import config

_LOCK = threading.Lock()
_ILK_CIKTI = threading.local()


def _yol(ad):
    return os.path.join(config.OPS, ad)


# ---------- tur kayıtları ----------

def tur_kaydet(kanal, mesaj_ts, ilk_cikti_ts, cevap_ts, zaman_asimi=False,
               arac=0, uzunluk=0, tur="sohbet"):
    """Bir sohbet/iş turunu metrik.jsonl'e yaz. Süreler saniye; ilk çıktı yoksa None."""
    try:
        kayit = {"ts": time.strftime("%F %T", time.localtime(cevap_ts or time.time())),
                 "tur": tur, "kanal": kanal or "",
                 "ilk_sn": round(ilk_cikti_ts - mesaj_ts, 2) if (ilk_cikti_ts and mesaj_ts) else None,
                 "toplam_sn": round((cevap_ts or time.time()) - mesaj_ts, 2) if mesaj_ts else None,
                 "zaman_asimi": bool(zaman_asimi), "arac": int(arac or 0), "uzunluk": int(uzunluk or 0)}
        with _LOCK:
            with open(_yol("metrik.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(kayit, ensure_ascii=False) + "\n")
        if zaman_asimi:
            sayac("kayip_cevap")
    except Exception:
        pass


def ilk_cikti_sifirla():
    _ILK_CIKTI.ts = None
    _ILK_CIKTI.arac = 0


def ilk_cikti_sarmala(on_event):
    """run_claude'un on_event geri çağrısını sarar: ilk satırın anını ve araç
    çağrısı sayısını iş parçacığına özel tutar (akış kaydını değiştirmez)."""
    ilk_cikti_sifirla()

    def _cb(satir):
        try:
            # 2026-10-29 düzeltme: stream'in ilk satırı `system/init` olayıydı (anında gelir) →
            # ilk_sn hep 0,0 çıkıyordu. Görünür çıktı = ilk `assistant` olayı (metin ya da araç).
            if '"type":"assistant"' in satir or '"type": "assistant"' in satir:
                if getattr(_ILK_CIKTI, "ts", None) is None:
                    _ILK_CIKTI.ts = time.time()
                if '"tool_use"' in satir:
                    _ILK_CIKTI.arac = getattr(_ILK_CIKTI, "arac", 0) + 1
        except Exception:
            pass
        if on_event:
            on_event(satir)
    return _cb


def ilk_cikti():
    """(ilk_cikti_ts | None, arac_sayisi) — sarmalayıcı bu iş parçacığında koştuysa."""
    return getattr(_ILK_CIKTI, "ts", None), getattr(_ILK_CIKTI, "arac", 0)


def _yuzdelik(degerler, p):
    if not degerler:
        return None
    s = sorted(degerler)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return round(s[k], 1)


def ozet(gun=7, simdi=None):
    """Nabız için: son `gun` günün tur sayısı, ilk çıktı p50/p95, toplam p50/p95, kayıp."""
    simdi = simdi or time.time()
    esik = simdi - gun * 86400
    ilk, toplam, kayip, n = [], [], 0, 0
    try:
        with open(_yol("metrik.jsonl"), encoding="utf-8") as f:
            for satir in f:
                try:
                    r = json.loads(satir)
                    ts = time.mktime(time.strptime(r["ts"], "%Y-%m-%d %H:%M:%S"))  # strptime %F/%T tanımaz
                except Exception:
                    continue
                if ts < esik or r.get("tur") != "sohbet":
                    continue
                n += 1
                if r.get("ilk_sn") is not None:
                    ilk.append(r["ilk_sn"])
                if r.get("toplam_sn") is not None:
                    toplam.append(r["toplam_sn"])
                if r.get("zaman_asimi"):
                    kayip += 1
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return {"var": n > 0, "gun": gun, "tur": n,
            "ilk_p50": _yuzdelik(ilk, 50), "ilk_p95": _yuzdelik(ilk, 95),
            "toplam_p50": _yuzdelik(toplam, 50), "toplam_p95": _yuzdelik(toplam, 95),
            "kayip": kayip}


# ---------- sayaçlar ----------

SAYAC_AD = "metrik.sayac"      # Faz 1.9 (2026-10-29): ops/metrik_sayac.json → durum tablosu (ilk okumada taşınır)


def _sayac_oku():
    try:
        from ghost import durum
        return durum.tasi(SAYAC_AD, _yol("metrik_sayac.json"), {}) or {}
    except Exception:
        return {}


def sayac(ad, n=1, gun=None):
    """Gün bazlı sayaç: {gun: {ad: sayi}}. Asla fırlatmaz; yeni toplamı döner."""
    gun = gun or time.strftime("%F")
    try:
        from ghost import durum
        _sayac_oku()                                  # eski dosya varsa bir kez taşı

        def _art(d):
            d = d or {}
            g = d.setdefault(gun, {})
            g[ad] = int(g.get(ad, 0)) + int(n)
            esik = time.strftime("%F", time.localtime(time.time() - 90 * 86400))
            for k in [k for k in d if k < esik]:   # 90 günden eski günleri at
                d.pop(k, None)
            return d
        return durum.guncelle(SAYAC_AD, _art, {})[gun][ad]
    except Exception:
        return None


def sayac_toplam(gun=7, simdi=None):
    """Son `gun` günün sayaç toplamları {ad: sayi}."""
    simdi = simdi or time.time()
    esik = time.strftime("%F", time.localtime(simdi - gun * 86400))
    top = {}
    for g, adlar in _sayac_oku().items():
        if g < esik:
            continue
        for ad, n in (adlar or {}).items():
            top[ad] = top.get(ad, 0) + int(n)
    return top


def bugun(ad):
    return int(_sayac_oku().get(time.strftime("%F"), {}).get(ad, 0))


def metin(gun=7):
    """Haftalık sentez / /durum için tek paragraf."""
    o = ozet(gun)
    s = sayac_toplam(gun)
    if not o["var"] and not s:
        return "📏 Metrik: henüz kayıt yok (ölçüm 2026-10-29'da başladı)."
    satir = ["📏 Metrik (%d gün): %d sohbet turu" % (gun, o["tur"])]
    if o["ilk_p50"] is not None:
        satir.append("ilk çıktı p50 %ss · p95 %ss" % (o["ilk_p50"], o["ilk_p95"]))
    if o["toplam_p50"] is not None:
        satir.append("tur p50 %ss · p95 %ss" % (o["toplam_p50"], o["toplam_p95"]))
    satir.append("kayıp cevap %d" % o["kayip"])
    if s:
        satir.append("sayaçlar: " + ", ".join("%s %d" % (k, v) for k, v in sorted(s.items())))
    return " · ".join(satir)


if __name__ == "__main__":
    import sys
    print(metin(int(sys.argv[1]) if len(sys.argv) > 1 else 7))
