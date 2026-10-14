"""Gün — "Ghost bugün ne yaptı?" ve "Ghost'un cevapsız soruları" (Nabız'ın 1. ve 2. sorusu).

Nabız 2026-09-04'e kadar altyapı sayıyordu (yük, disk, nöbetçi yaşı); sahibin bakıp karar
verebileceği şey ise üç soru: benden ne bekliyor · Ghost bugün ne yaptı · iş nasıl. Bu modül
ilk ikisini deterministik kaynaklardan derler — model yok, tahmin yok:
  commit'ler (git log), biten işler (ops/jobs), audit eylemleri, telefona yazılan
  hatırlatıcılar (kokpit_gorevler), mesaj sayısı (kokpit_outbox).
"""
import glob
import json
import os
import re
import subprocess
import time

from ghost import config

SENKRON = re.compile(r"^oto: bilgi senkron")     # otomatik commit'ler gün özetine girmez
BITEN = ("bitti", "hata", "iptal")
SESSIZ_AUDIT = {"read"}                          # okuma sınıfı audit satırı "yaptım" sayılmaz


def _bugun():
    return time.strftime("%F")


def commitler(tarih=None):
    tarih = tarih or _bugun()
    try:
        out = subprocess.run(["git", "-C", config.BASE, "log", "--since=%s 00:00" % tarih,
                              "--format=%h\t%ci\t%s"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    rows = []
    for l in out.splitlines():
        p = l.split("\t", 2)
        if len(p) < 3 or SENKRON.match(p[2]):
            continue
        rows.append({"sha": p[0], "saat": p[1][11:16], "baslik": p[2][:140]})
    return rows


def isler(tarih=None):
    tarih = tarih or _bugun()
    out = []
    for p in glob.glob(os.path.join(config.OPS, "jobs", "*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                j = json.load(f)
            jid = int(os.path.basename(p)[:-5])
        except Exception:
            continue
        zaman = j.get("ended") or j.get("bitis") or j.get("started") or ""
        if not str(zaman).startswith(tarih):
            continue
        out.append({"id": jid, "durum": j.get("status", "?"), "tur": j.get("kind", ""),
                    "ozet": (j.get("prompt") or "")[:90]})
    out.sort(key=lambda o: -o["id"])
    return out


def audit_sayilari(tarih=None):
    """Bugünkü audit eylemleri: {action: adet} — read sınıfı ve startup dışarıda."""
    tarih = tarih or _bugun()
    say = {}
    p = os.path.join(config.OPS, "audit.jsonl")
    try:
        with open(p, encoding="utf-8") as f:
            for l in f:
                if not l.startswith('{"ts": "%s' % tarih):
                    continue
                try:
                    o = json.loads(l)
                except ValueError:
                    continue
                if o.get("class") in SESSIZ_AUDIT or o.get("action") in ("startup",):
                    continue
                a = o.get("action") or "?"
                say[a] = say.get(a, 0) + 1
    except OSError:
        pass
    return say


def gorevler(tarih=None):
    """Telefona bugün yazılan hatırlatıcı/etkinlik sayısı (kokpit_gorevler, durum=yazildi)."""
    tarih = tarih or _bugun()
    try:
        from ghost import kokpit
        c = kokpit._db()
        n = c.execute("select count(*) from kokpit_gorevler where ts like ? and durum=?",
                      (tarih + "%", "yazildi")).fetchone()[0]
        c.close()
        return int(n)
    except Exception:
        return 0


def mesajlar(tarih=None):
    tarih = tarih or _bugun()
    try:
        from ghost import kokpit
        c = kokpit._db()
        r = dict(c.execute("select kaynak, count(*) from kokpit_outbox where ts like ? group by kaynak",
                           (tarih + "%",)).fetchall())
        c.close()
        return {"sahip": int(r.get("sahip", 0)), "ghost": int(r.get("ghost", 0))}
    except Exception:
        return {"sahip": 0, "ghost": 0}


def bugun():
    """Nabız 'Bugün' bölümü: en fazla 4 başlık satırı + sayılar + ham listeler."""
    tarih = _bugun()
    c, i, a, g, m = commitler(tarih), isler(tarih), audit_sayilari(tarih), gorevler(tarih), mesajlar(tarih)
    satir = []
    if c:
        satir.append({"simge": "commit", "metin": "%d commit — %s" % (len(c), c[0]["baslik"])})
    biten = [x for x in i if x["durum"] in BITEN]
    if biten:
        hatali = sum(1 for x in biten if x["durum"] == "hata")
        satir.append({"simge": "is", "metin": "%d iş bitti%s — son: %s" % (
            len(biten), (" (%d hatalı)" % hatali) if hatali else "", biten[0]["tur"] or "iş")})
    if a:
        en = sorted(a.items(), key=lambda kv: -kv[1])[:3]
        satir.append({"simge": "eylem", "metin": "eylemler: " + ", ".join("%s ×%d" % kv for kv in en)})
    if g:
        satir.append({"simge": "telefon", "metin": "%d hatırlatıcı/etkinlik telefonuna yazıldı" % g})
    if not satir:
        satir.append({"simge": "bos", "metin": "Henüz kayda değer bir şey yapmadım."})
    return {"tarih": tarih, "satirlar": satir[:4],
            "sayilar": {"commit": len(c), "is": len(biten), "eylem": sum(a.values()), "gorev": g,
                        "mesaj": m["sahip"] + m["ghost"]},
            "commitler": c[:5], "isler": i[:5]}


SORU_TAVAN = 3


def sorular(limit=30):
    """Ghost'un cevapsız soruları: son Sahip mesajından SONRA gelen Ghost mesajlarından
    butonlu olanlar ya da soru işaretiyle bitenler. Tahmin değil, kural — Sahip cevap
    yazınca kendiliğinden listeden düşer."""
    try:
        from ghost import kokpit
        son = kokpit.outbox_son()
        rows = kokpit.outbox_oku(since=max(0, son - limit), limit=limit)
    except Exception:
        return []
    son_sahip = -1
    for k, r in enumerate(rows):
        if r.get("kaynak") == "sahip":
            son_sahip = k
    out = []
    for r in rows[son_sahip + 1:]:
        if r.get("kaynak") != "ghost":
            continue
        metin = (r.get("metin") or "").strip()
        if metin.startswith(("🖼 Sahne güncellendi", "📊 ")):   # sahne ipucu satırları soru değil
            continue
        son_satir = metin.splitlines()[-1].strip() if metin else ""
        if r.get("butonlar") or son_satir.endswith("?"):
            out.append({"id": r.get("id"), "ts": r.get("ts", ""),
                        "metin": (son_satir if son_satir.endswith("?") else metin)[:160],
                        "butonlu": bool(r.get("butonlar"))})
    return out[-SORU_TAVAN:]
