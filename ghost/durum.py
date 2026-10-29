"""Durum katmanı — ad → JSON değer, tek tablo (Faz 1.9, 2026-10-29).

NEDEN (bütünsel denetim 03 6a): `ops/*.json` durum dosyaları (sayaçlar, son koşum damgaları, bayatlık işaretleri)
her modülde ayrı oku/yaz/tmp+replace koduyla tutuluyordu — 17 kopya yardımcı, test izolasyonu dosya dosya. Artık
`durum.oku(ad)` / `durum.yaz(ad, deger)`; değer JSON'a çevrilebilen her şey. Atomik güncelleme `guncelle(ad, fn)`
(BEGIN IMMEDIATE). Göç: `tasi(ad, yol)` eski json dosyasını bir kez içeri alır ve `.tasindi` olarak bırakır.
CLI: python3 -m ghost.durum liste [önek] | oku <ad> | yaz <ad> '<json>' | sil <ad>
"""
import json
import os
import sys
import time

from ghost import semalar


def _c():
    return semalar.baglan()


def oku(ad, varsayilan=None):
    c = _c()
    try:
        r = c.execute("SELECT deger FROM durum WHERE ad=?", (ad,)).fetchone()
    finally:
        c.close()
    if not r:
        return varsayilan
    try:
        return json.loads(r[0])
    except ValueError:
        return varsayilan


def yaz(ad, deger, simdi=None):
    c = _c()
    try:
        c.execute("INSERT INTO durum(ad,deger,ts) VALUES(?,?,?) ON CONFLICT(ad) DO UPDATE SET deger=excluded.deger, ts=excluded.ts",
                  (ad, json.dumps(deger, ensure_ascii=False), simdi or time.time()))
        c.commit()
    finally:
        c.close()
    return deger


def guncelle(ad, fn, varsayilan=None):
    """Atomik oku-değiştir-yaz: fn(eski) → yeni. Yazarlar arasında yarış yok (BEGIN IMMEDIATE)."""
    c = _c()
    try:
        c.execute("BEGIN IMMEDIATE")
        r = c.execute("SELECT deger FROM durum WHERE ad=?", (ad,)).fetchone()
        eski = varsayilan
        if r:
            try:
                eski = json.loads(r[0])
            except ValueError:
                eski = varsayilan
        yeni = fn(eski)
        c.execute("INSERT INTO durum(ad,deger,ts) VALUES(?,?,?) ON CONFLICT(ad) DO UPDATE SET deger=excluded.deger, ts=excluded.ts",
                  (ad, json.dumps(yeni, ensure_ascii=False), time.time()))
        c.commit()
        return yeni
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def sil(ad):
    c = _c()
    try:
        n = c.execute("DELETE FROM durum WHERE ad=?", (ad,)).rowcount
        c.commit()
        return n > 0
    finally:
        c.close()


def liste(onek=""):
    c = _c()
    try:
        return [{"ad": a, "ts": t, "boyut": len(d)} for a, d, t in
                c.execute("SELECT ad,deger,ts FROM durum WHERE ad LIKE ? ORDER BY ad", (onek + "%",))]
    finally:
        c.close()


def ts(ad):
    c = _c()
    try:
        r = c.execute("SELECT ts FROM durum WHERE ad=?", (ad,)).fetchone()
        return float(r[0]) if r else None
    finally:
        c.close()


def tasi(ad, yol, varsayilan=None):
    """Eski json dosyasını bir kez durum tablosuna al. Tabloda kayıt varsa dosyaya bakılmaz; dosya `.tasindi` olur.
    Döner: tablodaki değer (yoksa varsayilan)."""
    mevcut = oku(ad, None)
    if mevcut is not None:
        return mevcut
    try:
        with open(yol, encoding="utf-8") as f:
            deger = json.load(f)
    except (OSError, ValueError):
        return varsayilan
    yaz(ad, deger)
    try:
        os.replace(yol, yol + ".tasindi")
    except OSError:
        pass
    return deger


def _main(argv):
    if not argv or argv[0] == "liste":
        for r in liste(argv[1] if len(argv) > 1 else ""):
            print("%-32s %s  %d B" % (r["ad"], time.strftime("%F %T", time.localtime(r["ts"])), r["boyut"]))
        return 0
    if argv[0] == "oku":
        print(json.dumps(oku(argv[1]), ensure_ascii=False, indent=1))
        return 0
    if argv[0] == "yaz":
        yaz(argv[1], json.loads(argv[2]))
        print("tamam")
        return 0
    if argv[0] == "sil":
        print("tamam" if sil(argv[1]) else "yok")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
