"""Şeffaflık raporu — 'Ghost bu hafta ne yaptı'.

audit.jsonl'i insan-okur özete çevirir: otonom eylemler, onaylar/retler,
alet üretimleri, freeze olayları. Yönetişim omurgasının görünür yüzü
(VISION.md): güç arttıkça hesap verebilirlik de artar. Haftalık cron çağırır.
"""
import json
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
    return "\n".join(lines)
