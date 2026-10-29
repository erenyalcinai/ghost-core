"""Onaylı komutun sonucu — kart/push/kayıt (Faz 1.12, 2026-10-29, denetim 06 #8).

NEDEN: Sahip onay kartına "Onayla" diyor, komut sohbet oturumunda koşuyor, ama kartta ne olduğu
görünmüyordu — sonucu ancak Ghost'un cevabı gelince (bazen dakikalar sonra) öğreniyordu. Akış
kaydedicisi (akis.olay_kaydedici) her stream satırını buraya da verir: `onay-<16hex>` içeren Bash
aracı çağrısı görülünce işaretlenir, eşleşen araç sonucu gelince süre + ilk satırlar + çıktı hash'i
`ops/onay_sonuc.jsonl`'e yazılır, Kokpit'e time-sensitive push ("✅ Onay koştu · 3 sn") gider ve
denetim günlüğüne düşer. Sohbet oturumundan bağımsız, saf gözlem; asla fırlatmaz."""
import hashlib
import json
import os
import re
import threading
import time

from ghost import config

ONAY = re.compile(r"\bonay-[0-9a-f]{16}\b")
_BEKLEYEN = {}          # tool_use_id → {"eid", "ts", "komut"}
_LOCK = threading.Lock()
OZET_TAVAN = 160


def _yol():
    return os.path.join(config.OPS, "onay_sonuc.jsonl")


def _icerik_metni(icerik):
    if isinstance(icerik, list):
        return "\n".join(x.get("text", "") for x in icerik if isinstance(x, dict))
    return str(icerik or "")


def izle(satir, simdi=None, bildir=None):
    """Bir stream-json satırı. Sonuç üretildiyse kayıt sözlüğünü döner, yoksa None."""
    try:
        o = json.loads(satir)
    except Exception:
        return None
    if not isinstance(o, dict):
        return None
    simdi = simdi or time.time()
    t = o.get("type")
    icerik = (o.get("message") or {}).get("content") or []
    if not isinstance(icerik, list):
        return None
    if t == "assistant":
        for b in icerik:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Bash":
                komut = str((b.get("input") or {}).get("command") or "")
                m = ONAY.search(komut)
                if m and b.get("id"):
                    with _LOCK:
                        _BEKLEYEN[b["id"]] = {"eid": m.group(0), "ts": simdi, "komut": komut[:300]}
        return None
    if t != "user":
        return None
    for b in icerik:
        if not (isinstance(b, dict) and b.get("type") == "tool_result"):
            continue
        with _LOCK:
            bek = _BEKLEYEN.pop(b.get("tool_use_id"), None)
        if not bek:
            continue
        cikti = _icerik_metni(b.get("content"))
        hata = bool(b.get("is_error"))
        kayit = {"ts": time.strftime("%F %T", time.localtime(simdi)), "eid": bek["eid"],
                 "sure_sn": round(simdi - bek["ts"], 1), "hata": hata,
                 "ozet": " ".join(cikti.split())[:OZET_TAVAN],
                 "hash": hashlib.sha256(cikti.encode("utf-8", "replace")).hexdigest()[:12],
                 "komut": bek["komut"]}
        try:
            with open(_yol(), "a", encoding="utf-8") as f:
                f.write(json.dumps(kayit, ensure_ascii=False) + "\n")
        except Exception:
            pass
        try:
            from ghost import governance
            governance.audit("onay", "sonuc", governance.READ,
                             "%s %s %.1f sn %s" % (kayit["eid"], "HATA" if hata else "ok", kayit["sure_sn"], kayit["hash"]))
        except Exception:
            pass
        try:
            (bildir or _bildir)(kayit)
        except Exception:
            pass
        return kayit
    return None


def _bildir(kayit):
    """Sahip 2026-10-29: "onayla ilgili koştu/bekliyor bildirimleri çok yoruyor" → başarıda push YOK (sonuç zaten sohbet
    cevabında; kayıt + audit kalır). Yalnız HATA push'lanır, o da normal düzeyde (Odak'ı delmez)."""
    if not kayit["hata"]:
        return
    from ghost import apns
    apns.herkese("❌ Onaylı komut hata verdi · %.0f sn" % kayit["sure_sn"], (kayit["ozet"] or kayit["komut"])[:200],
                 {"sekme": "kararlar"}, collapse=kayit["eid"], onem="active")


def son(n=10):
    try:
        with open(_yol(), encoding="utf-8") as f:
            satirlar = f.readlines()[-n:]
        return [json.loads(x) for x in satirlar if x.strip()]
    except Exception:
        return []
