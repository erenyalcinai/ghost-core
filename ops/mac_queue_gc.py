#!/usr/bin/env python3
"""Mac iş kuyruğu çöp toplayıcı — ops/mac-queue/done/ altındaki ESKİ iş dosyalarını buda.

NEDEN (2026-10-29 bütünsel denetim, 03-mimari §11 / 09-ops): Mac işçisi (ops/mac_worker.py,
Mac'te koşar) bitirdiği işi VPS'teki ops/mac-queue/done/'a taşır (<id>.json + <id>.sonuc.md) ve
hiç silmez — 473 dosya, 267'si 60 günden eski. Kuyruk dizini gitignore'da, yedeğe girmez;
iş geçmişi audit.jsonl + events tablosunda zaten var. Bu betik yalnız VPS tarafında koşar,
mac_worker.py'ye DOKUNMAZ.

VARSAYILAN KURU KOŞU: ne silineceğini listeler, silmez. Silmek için --uygula.

  python3 ops/mac_queue_gc.py                 # 60 günden eski dosyaları listele (dry-run)
  python3 ops/mac_queue_gc.py --gun 90        # eşik 90 gün
  python3 ops/mac_queue_gc.py --uygula        # gerçekten sil (yalnız done/ altındaki düz dosyalar)

Henüz zamanlayıcıya bağlı DEĞİL (selfcheck cleanup'ına eklenmesi ayrı karar).
"""
import argparse
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DONE_DIR = os.path.join(BASE, "ops", "mac-queue", "done")
VARSAYILAN_GUN = 60


def adaylar(kok=DONE_DIR, gun=VARSAYILAN_GUN, simdi=None):
    """(yol, yaş_gün, bayt) listesi — yalnız done/ altındaki DÜZ dosyalar, mtime > gun."""
    simdi = time.time() if simdi is None else simdi
    sinir = simdi - gun * 86400
    out = []
    try:
        adlar = os.listdir(kok)
    except OSError:
        return out
    for ad in sorted(adlar):
        p = os.path.join(kok, ad)
        if not os.path.isfile(p) or os.path.islink(p):
            continue
        try:
            st = os.stat(p)
        except OSError:
            continue
        if st.st_mtime < sinir:
            out.append((p, int((simdi - st.st_mtime) // 86400), st.st_size))
    return out


def topla(kok=DONE_DIR, gun=VARSAYILAN_GUN, uygula=False, simdi=None):
    """Döndürür: {"aday": n, "silinen": n, "bayt": toplam, "hata": [...]}"""
    liste = adaylar(kok, gun, simdi)
    sonuc = {"aday": len(liste), "silinen": 0, "bayt": sum(b for _, _, b in liste), "hata": []}
    if not uygula:
        return sonuc
    for p, _, _ in liste:
        try:
            os.remove(p)
            sonuc["silinen"] += 1
        except OSError as e:
            sonuc["hata"].append("%s: %s" % (os.path.basename(p), e))
    return sonuc


def main(argv=None):
    ap = argparse.ArgumentParser(description="ops/mac-queue/done eski iş dosyalarını buda (varsayılan kuru koşu)")
    ap.add_argument("--gun", type=int, default=VARSAYILAN_GUN, help="eşik (gün), varsayılan %d" % VARSAYILAN_GUN)
    ap.add_argument("--uygula", action="store_true", help="gerçekten sil")
    ap.add_argument("--kok", default=DONE_DIR, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    liste = adaylar(a.kok, a.gun)
    toplam = os.listdir(a.kok) if os.path.isdir(a.kok) else []
    print("%s: %d dosya, %d tanesi %d günden eski (%.1f MB)" % (
        a.kok, len(toplam), len(liste), a.gun, sum(b for _, _, b in liste) / 1e6))
    if not a.uygula:
        for p, yas, _ in liste[:10]:
            print("  %s  (%d gün)" % (os.path.basename(p), yas))
        if len(liste) > 10:
            print("  … +%d" % (len(liste) - 10))
        print("KURU KOŞU — silmek için --uygula")
        return 0
    s = topla(a.kok, a.gun, uygula=True)
    print("silindi: %d / %d (%.1f MB)%s" % (s["silinen"], s["aday"], s["bayt"] / 1e6,
                                             "; hata: %d" % len(s["hata"]) if s["hata"] else ""))
    return 1 if s["hata"] else 0


if __name__ == "__main__":
    sys.exit(main())
