#!/usr/bin/env python3
"""Commit mesajlarındaki `Ders:` bloklarını playbooks/dersler.md'ye taşır.

Koşum: knowsync (VPS, 15 dk) — yani ders, yazıldıktan en geç bir çeyrek saat
sonra aranabilir yerdedir. Elle: python3 ops/ders_yaz.py [--sessiz]

Yalnız VPS'te koşar (knowsync_vps.sh): kaynak git geçmişi, iki makinede de
aynı — iki taraf da üretirse aynı metni iki kez commit'lemiş oluruz. Mac
dosyayı senkronla alır.

Çıktı dosyası dokümandır → push test kapısını tetiklemez.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ghost import dersler  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEDEF = os.path.join(BASE, "playbooks", "dersler.md")
AYIRAC, KAYIT_SONU = "\x1f", "\x1e"


def commitler():
    """Tüm geçmiş, eskiden yeniye: (sha, tarih, konu, gövde)."""
    bicim = AYIRAC.join(["%H", "%ad", "%s", "%b"]) + KAYIT_SONU
    r = subprocess.run(["git", "log", "--reverse", "--date=short",
                        "--format=" + bicim],
                       cwd=BASE, capture_output=True, text=True)
    if r.returncode != 0:
        return []
    out = []
    for ham in r.stdout.split(KAYIT_SONU):
        ham = ham.strip("\n")
        if not ham.strip():
            continue
        parca = ham.split(AYIRAC)
        if len(parca) < 4:
            continue
        out.append((parca[0], parca[1], parca[2], parca[3]))
    return out


def main():
    sessiz = "--sessiz" in sys.argv
    mevcut = ""
    if os.path.exists(HEDEF):
        with open(HEDEF, encoding="utf-8") as f:
            mevcut = f.read()

    kayitlar = [(sha, tarih, konu, dersler.ders_ayikla(govde))
                for sha, tarih, konu, govde in commitler()]
    yeni, eklenen = dersler.guncelle(mevcut, kayitlar)
    if eklenen:
        os.makedirs(os.path.dirname(HEDEF), exist_ok=True)
        with open(HEDEF, "w", encoding="utf-8") as f:
            f.write(yeni)
    if not sessiz:
        print("ders_yaz: %d yeni ders" % eklenen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
