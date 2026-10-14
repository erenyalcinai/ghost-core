"""Otomatik ders yazımı — commit mesajındaki `Ders:` bloğu → playbooks/dersler.md.

NEDEN VAR (2026-08-21): zor bir işten çıkan çıkarım commit mesajının gövdesinde
kalıyordu. Commit mesajı doğru yer ama ARANMAYAN yer: altı ay sonra kimse
`git log -S` ile ders aramaz, `playbooks/` okur. Ders, üretildiği yerde değil
BAKILAN yerde durmalı.

Sözleşme: commit gövdesinde satır başında `Ders:` geçen paragraf derstir;
paragraf boş satırda biter. Bir commit'te birden çok blok olabilir.

Buradaki her şey saf metin işi (git yok, dosya yok) — test edilebilir olsun
diye. Git tarafı ops/ders_yaz.py'de.
"""
import re

BASLIK = "# Dersler — işten çıkan kalıcı çıkarımlar"
ONSOZ = (
    "<!-- OTOMATİK ÜRETİLİR: ops/ders_yaz.py, commit mesajındaki `Ders:`\n"
    "     bloğunu buraya taşır. Elle düzenleme — commit mesajı kaynaktır;\n"
    "     bir ders yanlışsa yeni bir commit'te düzelt. Sıra: eskiden yeniye. -->"
)
_DERS = re.compile(r"^ders\s*:\s*(.*)$", re.IGNORECASE)
_SHA = re.compile(r"^##\s+.*·\s+([0-9a-f]{7,40})\s+—", re.MULTILINE)


def ders_ayikla(govde):
    """Commit gövdesinden ders metnini çıkar. Yoksa None.

    Paragraf boş satırda biter; devam satırları tek boşlukla birleşir
    (dosyada tek akan paragraf olsun diye — git'in 72 sütun sarması ders
    metnine ait değil, yazım biçimine ait)."""
    if not govde:
        return None
    bloklar, suan = [], None
    for satir in govde.splitlines():
        m = _DERS.match(satir.strip())
        if m:
            if suan:
                bloklar.append(suan)
            suan = [m.group(1).strip()]
        elif suan is not None:
            if not satir.strip():
                bloklar.append(suan)
                suan = None
            else:
                suan.append(satir.strip())
    if suan:
        bloklar.append(suan)
    metinler = [" ".join(p for p in b if p).strip() for b in bloklar]
    metinler = [m for m in metinler if m]
    return "\n\n".join(metinler) or None


def yazili_shalar(metin):
    """Dosyada zaten duran commit sha'ları (tekrar yazmamak için)."""
    return set(_SHA.findall(metin or ""))


def girdi(sha, tarih, konu, ders):
    """Tek bir dersin markdown bloğu."""
    return "## %s · %s — %s\n%s\n" % (tarih, sha[:7], konu.strip(), ders.strip())


def guncelle(mevcut, kayitlar):
    """Dosya metnini yeni derslerle güncelle. Döner: (yeni_metin, eklenen).

    kayitlar: (sha, tarih, konu, ders) listesi, eskiden yeniye.
    Zaten yazılı sha atlanır → betik istediği kadar sık koşabilir.
    """
    metin = mevcut or ""
    varolan = yazili_shalar(metin)
    yeniler = []
    for sha, tarih, konu, ders in kayitlar:
        if not ders or sha[:7] in {s[:7] for s in varolan}:
            continue
        varolan.add(sha[:7])
        yeniler.append(girdi(sha, tarih, konu, ders))
    if not yeniler:
        return metin, 0
    if not metin.strip():
        metin = "%s\n\n%s\n" % (BASLIK, ONSOZ)
    return metin.rstrip("\n") + "\n\n" + "\n".join(yeniler), len(yeniler)
