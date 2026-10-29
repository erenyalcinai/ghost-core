#!/usr/bin/env python3
"""Brifing tekrar filtresi — son 7 günün brifing başlıkları + son sentezin ODAKLAN/ERTELE kararları (Faz 1.16, 2026-10-29).

NEDEN (bütünsel denetim 04 Ö8, 02 d3): sabah brifingi her gün sıfırdan tarıyor, dün söylediğini bugün yine
söyleyebiliyordu; haftalık sentezin "ERTELE" dediği konu ertesi sabah "fırsat" diye geri geliyordu. Bu betik
deterministik bir blok üretir; morning_brief.sh istemin sonuna ekler. Model hiçbir dosya okumaz (kum havuzu).
Çıktı boşsa hiçbir şey basılmaz. Kullanım: python3 ops/brifing_baglam.py [--gun 7]
"""
import glob
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INTEL = os.path.join(BASE, "intel")
SENTEZ = os.path.join(BASE, "dossier", "sentez")
_BASLIK = re.compile(r"^\d\)\s+(.+)$")
_FIRSAT = re.compile(r"^■\s*\d*\s*F[Iİ]RSAT")
_KARAR = re.compile(r"(ODAKLAN|ERTELE)", re.I)
SATIR_TAVAN = 110


def _kisalt(s, n=SATIR_TAVAN):
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


def intel_basliklar(gun=7, simdi=None, dizin=None):
    """[(tarih, [başlık…])] — son `gun` günün brifing dosyalarından 'N) …' satırları + fırsat satırı."""
    dizin = dizin or INTEL          # modül değişkeni çalışma anında okunur (testler yolu değiştirir)
    simdi = simdi or time.time()
    out = []
    for yol in sorted(glob.glob(os.path.join(dizin, "20??-??-??.md"))):
        tarih = os.path.basename(yol)[:-3]
        try:
            ts = time.mktime(time.strptime(tarih, "%Y-%m-%d"))
        except ValueError:
            continue
        if simdi - ts > gun * 86400 or ts > simdi + 86400:
            continue
        basliklar, firsat_bekle = [], False
        try:
            for satir in open(yol, encoding="utf-8", errors="replace"):
                satir = satir.strip()
                if not satir:
                    continue
                m = _BASLIK.match(satir)
                if m:
                    basliklar.append(_kisalt(m.group(1)))
                    continue
                if _FIRSAT.match(satir):
                    firsat_bekle = True
                    continue
                if firsat_bekle and not satir.startswith("■"):
                    basliklar.append("fırsat: " + _kisalt(satir))
                    firsat_bekle = False
                elif satir.startswith("■"):
                    firsat_bekle = False
        except OSError:
            continue
        if basliklar:
            out.append((tarih, basliklar[:5]))
    return out


def son_sentez_kararlari(dizin=None, en_fazla=6):
    """Son dossier/sentez/*.md dosyasından ODAKLAN/ERTELE geçen satırlar. Dosya yoksa []."""
    dizin = dizin or SENTEZ
    yollar = sorted(glob.glob(os.path.join(dizin, "*.md")))
    if not yollar:
        return None, []
    yol = yollar[-1]
    satirlar = []
    try:
        for satir in open(yol, encoding="utf-8", errors="replace"):
            if _KARAR.search(satir) and satir.strip():
                satirlar.append(_kisalt(satir.strip(), 160))
    except OSError:
        return os.path.basename(yol), []
    return os.path.basename(yol), satirlar[:en_fazla]


def blok(gun=7, simdi=None):
    parcalar = []
    basliklar = intel_basliklar(gun, simdi)
    if basliklar:
        parcalar.append("SON %d GÜNDE ZATEN SÖYLEDİKLERİN (aynı haberi yalnız YENİ gelişme varsa ve 'yeni:' diye ver; "
                        "fırsatı tekrar sunma):" % gun)
        for tarih, bs in basliklar:
            for b in bs:
                parcalar.append("- %s: %s" % (tarih, b))
    ad, kararlar = son_sentez_kararlari()
    if kararlar:
        parcalar.append("SON HAFTALIK SENTEZİN KARARLARI (%s) — ERTELE denen konuyu fırsat/öneri diye geri getirme:" % ad)
        parcalar.extend("- " + k for k in kararlar)
    return "\n".join(parcalar)


def main(argv):
    gun = int(argv[argv.index("--gun") + 1]) if "--gun" in argv else 7
    b = blok(gun)
    if b:
        print(b)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
