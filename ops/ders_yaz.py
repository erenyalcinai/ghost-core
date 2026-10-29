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


def dokunulan(sha):
    """Commit'te değişen yollar (etiket için; Faz 1.4)."""
    r = subprocess.run(["git", "show", "--name-only", "--format=", sha], cwd=BASE, capture_output=True, text=True)
    return [l for l in r.stdout.splitlines() if l.strip()] if r.returncode == 0 else []


def karar_vade_isle(commitler_):
    """Commit gövdesindeki `Karar:` / `Vade:` satırlarını deftere/vadelere indir (Faz 1.4). Her iki `ekle`
    içerik üstünden tekilleştirir; betik istediği kadar sık koşabilir. (eklenen karar, eklenen vade)."""
    from ghost import kararlar, vadeler
    k_n = v_n = 0
    for sha, tarih, _konu, govde in commitler_:
        for konu, karar, durum in dersler.karar_ayikla(govde):
            try:
                onceki = kararlar.say()["toplam"]
                kararlar.ekle(konu, karar, durum, tarih, "commit:" + sha[:7])
                k_n += int(kararlar.say()["toplam"] > onceki)
            except Exception:
                continue
        for ad, vtarih, notu in dersler.vade_ayikla(govde):
            try:
                onceki = len(vadeler.liste(None))
                vadeler.ekle(ad, vtarih, notu, "commit:" + sha[:7])
                v_n += int(len(vadeler.liste(None)) > onceki)
            except Exception:
                continue
    return k_n, v_n


def main():
    sessiz = "--sessiz" in sys.argv
    mevcut = ""
    if os.path.exists(HEDEF):
        with open(HEDEF, encoding="utf-8") as f:
            mevcut = f.read()

    varolan = {s[:7] for s in dersler.yazili_shalar(mevcut)}
    kayitlar = []
    for sha, tarih, konu, govde in commitler():
        ders = dersler.ders_ayikla(govde)
        etiket = dersler.etiketler(dokunulan(sha)) if (ders and sha[:7] not in varolan) else ()
        kayitlar.append((sha, tarih, konu, ders, etiket))
    yeni, eklenen = dersler.guncelle(mevcut, kayitlar)
    k_n, v_n = karar_vade_isle(commitler())
    if (k_n or v_n) and not sessiz:
        print("ders_yaz: %d karar, %d vade commit mesajından işlendi" % (k_n, v_n))
    if eklenen:
        os.makedirs(os.path.dirname(HEDEF), exist_ok=True)
        with open(HEDEF, "w", encoding="utf-8") as f:
            f.write(yeni)
    if not sessiz:
        print("ders_yaz: %d yeni ders" % eklenen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
