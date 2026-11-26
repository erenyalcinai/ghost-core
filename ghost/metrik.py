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
               arac=0, uzunluk=0, tur="sohbet", ilk_satir_ts=None, ek=None):
    """Bir sohbet/iş turunu metrik.jsonl'e yaz. Süreler saniye; ilk çıktı yoksa None.
    ek: iş turlarında (Faz 4.1.0, 2026-11-08) departman/model/kip/ozerk/hata alanları — motor.run_claude yazar."""
    try:
        kayit = {"ts": time.strftime("%F %T", time.localtime(cevap_ts or time.time())),
                 "tur": tur, "kanal": kanal or "",
                 "ilk_sn": round(ilk_cikti_ts - mesaj_ts, 2) if (ilk_cikti_ts and mesaj_ts) else None,
                 # Faz 2.7: ilk satırın sahibe GİTTİĞİ an (M3 ölçüsü); gitmediyse None (kısa tur, sonuç önce geldi)
                 "ilk_satir_sn": round(ilk_satir_ts - mesaj_ts, 2) if (ilk_satir_ts and mesaj_ts) else None,
                 "toplam_sn": round((cevap_ts or time.time()) - mesaj_ts, 2) if mesaj_ts else None,
                 "zaman_asimi": bool(zaman_asimi), "arac": int(arac or 0), "uzunluk": int(uzunluk or 0)}
        if ek:
            for k, v in ek.items():
                kayit.setdefault(k, v)          # çekirdek alanlar ezilmez
        if tur == "sohbet":                     # köken cihaz (2026-11-24): tur BAŞINDA yakalanan, yoksa şimdiki
            ham = getattr(_ILK_CIKTI, "koken", None)
            _ILK_CIKTI.koken = None
            for ad, v in koken_oku(ham).items():
                kayit.setdefault(ad, v)
        with _LOCK:
            with open(_yol("metrik.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(kayit, ensure_ascii=False) + "\n")
        if zaman_asimi and tur == "sohbet":   # iş zaman aşımı kayıp CEVAP değildir (iş kartı 'yarım' der)
            sayac("kayip_cevap")
    except Exception:
        pass


# ---------- köken cihaz (2026-11-24, Kokpit Watch hazırlığı) ----------
# `kanal` alanı köprünün BİRİNCİL kanalıdır (30 günde 1.447 turun hepsi "kokpit"): mesajın telefondan mı, Mac'ten mi,
# Cardputer'dan mı geldiğini söylemez. Saatin beklemeyi azaltıp azaltmadığını ölçmek için tur satırına köken yazılır.
# Kaynak: `channels.son_kanal()` + `channels.son_cihaz()` — ikincisi yönlendirme yamasının (yamalar/03, api.py)
# tuttuğu AYNI alandır; o yama uygulanmadıysa cihaz bilinmez ve köken "kokpit" kalır (ölçüm kanal düzeyinde).
# Köken tur BAŞINDA yakalanır (`ilk_cikti_sifirla`, köprü her turda çağırır): tur sürerken başka cihazdan gelen
# mesaj kaydı kaydırmasın. Tur başında yalnız ucuz kısım okunur (kanal + cihaz kimliği, küçük bir dosya); cihazın
# adı/sınıfı (onay servisine sorgu) tur SONUNDA, cevap gittikten sonra çözülür — ölçüm turu geciktirmez. Köprü değişmez.
KOKEN_SINIFLARI = ("telefon", "mac", "saat", "cardputer", "cep", "tam", "kokpit", "telegram", "bilinmiyor")


def koken_ham():
    """Son mesajın (kanal, cihaz kimliği | None) çifti — ucuz okuma. Asla fırlatmaz."""
    try:
        from ghost import channels
        kanal = channels.son_kanal()
        son_cihaz = getattr(channels, "son_cihaz", None)        # yama 03 yoksa alan da yok
        return kanal, ((son_cihaz() if callable(son_cihaz) else None) if kanal == "kokpit" else None)
    except Exception:
        return None, None


def koken_oku(ham=None):
    """Mesajın geldiği yer → {"koken": sınıf, "koken_kanal": kanal[, "koken_cihaz": ad, "koken_id": kimlik]}.
    `ham`: tur başında yakalanan `koken_ham()` çifti; verilmezse şimdi okunur.
    Sınıf: telegram | telefon (bildirim belirteci olan tam cihaz) | mac | saat | cardputer | cep (sınıfı kayıtsız
    cep cihazı) | tam (ayırt edilemeyen tam cihaz) | kokpit (cihaz bilinmiyor) | bilinmiyor. Asla fırlatmaz."""
    try:
        kanal, cid = ham if ham is not None else koken_ham()
        if kanal != "kokpit":
            return {"koken": "telegram" if kanal == "telegram" else "bilinmiyor", "koken_kanal": kanal or ""}
        cikti = {"koken": "kokpit", "koken_kanal": "kokpit"}
        if not cid:
            return cikti
        cikti["koken_id"] = str(cid)[:16]
        from ghost import kokpit
        c = next((x for x in kokpit.cihazlar() if x.get("id") == cid), None)
        if c is None:
            return cikti
        ad = str(c.get("ad") or "")
        cikti["koken_cihaz"] = " ".join(ad.split())[:24]
        if (c.get("tur") or "tam") == "cep":
            try:
                from ghost import cihaz_sinif                   # sınıf kaydı (ayrı yama) yoksa "cep" kalır
                cikti["koken"] = cihaz_sinif.sinif(cid) or "cep"
            except Exception:
                cikti["koken"] = "cep"
        elif any(x["id"] == cid for x in kokpit.apns_cihazlar()):
            cikti["koken"] = "telefon"                          # Mac belirteç kaydetmez (yerel bildirim)
        else:
            cikti["koken"] = "mac" if "mac" in ad.lower() else "tam"
        return cikti
    except Exception:
        return {"koken": "bilinmiyor", "koken_kanal": ""}


def ilk_cikti_sifirla():
    _ILK_CIKTI.ts = None
    _ILK_CIKTI.arac = 0
    _ILK_CIKTI.koken = koken_ham()       # tur başı: bu turu başlatan mesajın kökeni (ucuz; çözümleme tur sonunda)


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
    ilk_satir = []
    koken, koken_n = {}, {}                           # köken cihaza göre sohbet turları (alanı taşıyan satırlar)
    is_n, is_toplam, is_hata, is_dep = 0, [], 0, {}   # Faz 4.1.0: iş turları (motor.run_claude yazar)
    denetim_n = 0                                     # ikinci göz koşuları (rol=denetim) iş sayılmaz
    try:
        with open(_yol("metrik.jsonl"), encoding="utf-8") as f:
            for satir in f:
                try:
                    r = json.loads(satir)
                    ts = time.mktime(time.strptime(r["ts"], "%Y-%m-%d %H:%M:%S"))  # strptime %F/%T tanımaz
                except Exception:
                    continue
                if ts < esik:
                    continue
                if r.get("tur") == "is":
                    if r.get("rol") == "denetim":
                        denetim_n += 1
                        continue
                    is_n += 1
                    if r.get("toplam_sn") is not None:
                        is_toplam.append(r["toplam_sn"])
                    if r.get("hata") or r.get("zaman_asimi"):
                        is_hata += 1
                    d = r.get("departman") or "genel"
                    is_dep[d] = is_dep.get(d, 0) + 1
                    continue
                if r.get("tur") != "sohbet":
                    continue
                n += 1
                if r.get("ilk_sn") is not None:
                    ilk.append(r["ilk_sn"])
                if r.get("ilk_satir_sn") is not None:
                    ilk_satir.append(r["ilk_satir_sn"])
                if r.get("toplam_sn") is not None:
                    toplam.append(r["toplam_sn"])
                if r.get("zaman_asimi"):
                    kayip += 1
                if r.get("koken"):                    # 2026-11-24 öncesi satırlarda alan yok: dökülmezler
                    k = koken.setdefault(str(r["koken"]), [])
                    if r.get("toplam_sn") is not None:
                        k.append(r["toplam_sn"])
                    koken_n[str(r["koken"])] = koken_n.get(str(r["koken"]), 0) + 1
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return {"var": n > 0 or is_n > 0, "gun": gun, "tur": n,
            "koken": {k: {"tur": koken_n[k], "toplam_p50": _yuzdelik(koken.get(k) or [], 50),
                          "toplam_p95": _yuzdelik(koken.get(k) or [], 95)} for k in koken_n},
            "is_tur": is_n, "is_p50": _yuzdelik(is_toplam, 50), "is_p95": _yuzdelik(is_toplam, 95),
            "is_hata": is_hata, "is_departman": is_dep, "denetim_tur": denetim_n,
            "ilk_p50": _yuzdelik(ilk, 50), "ilk_p95": _yuzdelik(ilk, 95),
            "ilk_satir_p50": _yuzdelik(ilk_satir, 50), "ilk_satir_p95": _yuzdelik(ilk_satir, 95), "ilk_satir_n": len(ilk_satir),
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
    if o.get("ilk_satir_p50") is not None:
        satir.append("ilk satır sahibe p50 %ss · p95 %ss (%d turda gitti)" % (o["ilk_satir_p50"], o["ilk_satir_p95"], o["ilk_satir_n"]))
    if o["toplam_p50"] is not None:
        satir.append("tur p50 %ss · p95 %ss" % (o["toplam_p50"], o["toplam_p95"]))
    satir.append("kayıp cevap %d" % o["kayip"])
    if o.get("koken"):
        satir.append("köken: " + ", ".join(
            "%s %d%s" % (k, v["tur"], " (p50 %ss)" % v["toplam_p50"] if v["toplam_p50"] is not None else "")
            for k, v in sorted(o["koken"].items(), key=lambda kv: -kv[1]["tur"])))
    if o.get("is_tur"):
        dep = ", ".join("%s %d" % (k, v) for k, v in sorted(o["is_departman"].items(), key=lambda kv: -kv[1]))
        satir.append("iş %d tur (p50 %ss · p95 %ss · hata %d; %s)%s" % (
            o["is_tur"], o["is_p50"], o["is_p95"], o["is_hata"], dep,
            " · denetim %d" % o["denetim_tur"] if o.get("denetim_tur") else ""))
    if s:
        satir.append("sayaçlar: " + ", ".join("%s %d" % (k, v) for k, v in sorted(s.items())))
    return " · ".join(satir)


if __name__ == "__main__":
    import sys
    print(metin(int(sys.argv[1]) if len(sys.argv) > 1 else 7))
