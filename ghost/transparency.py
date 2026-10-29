"""Şeffaflık raporu — 'Ghost bu hafta ne yaptı'.

audit.jsonl'i insan-okur özete çevirir: otonom eylemler, onaylar/retler,
alet üretimleri, freeze olayları. Yönetişim omurgasının görünür yüzü
(VISION.md): güç arttıkça hesap verebilirlik de artar. Haftalık cron çağırır.
"""
import json
import os
import time
from collections import Counter

from ghost import config


def _load(since_ts):
    out = []
    try:
        with open(config.AUDIT_LOG, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                try:
                    t = time.mktime(time.strptime(rec["ts"], "%Y-%m-%d %H:%M:%S"))
                except (ValueError, KeyError):
                    continue
                if t >= since_ts:
                    out.append(rec)
    except OSError:
        pass
    return out


def report(days=7, now=None):
    now = now or time.time()
    recs = _load(now - days * 86400)
    if not recs:
        return "📊 Şeffaflık (%d gün): denetim kaydı yok." % days

    by_actor = Counter(r.get("actor", "?") for r in recs)
    by_class = Counter(r.get("class", "?") for r in recs)
    approvals = [r for r in recs if r.get("action") == "approval"]
    onay = sum(1 for r in approvals if r.get("decision") == "onay")
    ret = sum(1 for r in approvals if r.get("decision") == "ret")
    freezes = [r for r in recs if r.get("action") == "freeze"]
    jobs = sum(1 for r in recs if r.get("action") == "job.finish")

    lines = ["📊 Ghost şeffaflık raporu — son %d gün" % days,
             "",
             "Toplam kayıtlı eylem: %d" % len(recs),
             "İş tamamlandı: %d" % jobs,
             "Onay istendi: %d (onaylandın: %d, reddettin: %d)" % (
                 len(approvals), onay, ret)]
    if freezes:
        lines.append("⛔ Freeze olayları: %d" % len(freezes))
    lines.append("")
    lines.append("Eylem sınıfı dağılımı: " + ", ".join(
        "%s:%d" % (k, v) for k, v in by_class.most_common()))
    lines.append("En aktif bileşenler: " + ", ".join(
        "%s:%d" % (k, v) for k, v in by_actor.most_common(5)))

    # Kalıcı/harcama sınıfı eylemler — sahibin gözden geçirmesi gerekenler
    sensitive = [r for r in recs if r.get("class") in ("permanent", "spend")
                 and r.get("actor") != "owner"]
    if sensitive:
        lines.append("")
        lines.append("Dikkat gerektiren eylemler (kalıcı/harcama):")
        for r in sensitive[-8:]:
            lines.append("• [%s] %s: %s" % (r["ts"][5:16], r.get("action", ""),
                                            r.get("detail", "")[:80]))
    # 2026-10-29 (bütünsel denetim 09 #9, 08 #7): ölçü ve ekonomi satırları —
    # ölçülmeyen sürtünme azaltılamaz, hatırlanan maliyet sayılamaz.
    try:
        from ghost import metrik
        lines.append("")
        lines.append(metrik.metin(days))
    except Exception:
        pass
    try:
        lines.append(ekonomi_satiri())
    except Exception:
        pass
    return "\n".join(lines)


def ekonomi_satiri():
    """'Ghost ekonomi' tek satır: sabit gider (ops/giderler.json) + haftalık kota + gelir (biz_state)."""
    try:
        with open(os.path.join(config.OPS, "giderler.json"), encoding="utf-8") as f:
            g = json.load(f)
        toplam = sum(float(k.get("aylik_usd") or 0) for k in g.get("kalemler", []))
        tahmin = sum(1 for k in g.get("kalemler", []) if k.get("durum") != "dogrulandi")
        gider = "gider ≈ $%d/ay (%d kalem tahmin)" % (round(toplam), tahmin)
    except Exception:
        gider = "gider: ops/giderler.json yok"
    kota_s = ""
    try:
        from ghost import kota
        h = next((l for l in kota.oku().get("limitler", []) if l.get("tur") == "weekly_all"), None)
        if h:
            kota_s = " · haftalık kota %%%d" % int(h.get("yuzde") or 0)
    except Exception:
        pass
    gelir = ""
    try:
        from ghost import durum     # Faz 1.9: biz_state durum tablosunda
        b = durum.tasi("biz_state", os.path.join(config.OPS, "biz_state.json"), {}) or {}
        b = b.get("metrics") or b   # ölçüler "metrics" altında (eski kod kökte arıyordu → satır hep boştu)
        ucretli = b.get("d1_paid", b.get("d1_ucretli"))
        if ucretli is not None:
            gelir = " · AppA ücretli %s" % ucretli
        if b.get("appb_proceeds") is not None:
            gelir += " · App B ciro $%s" % b["appb_proceeds"]
        else:
            gelir += " · App B satış: okunamıyor (vendor no)"
    except Exception:
        pass
    return "💰 Ghost ekonomi: %s%s%s" % (gider, kota_s, gelir or " · gelir $0")
