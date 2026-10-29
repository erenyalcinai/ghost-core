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
import os
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


def girdi(sha, tarih, konu, ders, etiket=()):
    """Tek bir dersin markdown bloğu; etiketler başlık sonunda `#ad` olarak (Faz 1.4)."""
    ek = (" " + " ".join("#" + e for e in etiket)) if etiket else ""
    return "## %s · %s — %s%s\n%s\n" % (tarih, sha[:7], konu.strip(), ek, ders.strip())


# ---- Faz 1.4 (2026-10-29): dersler TÜKETİLEBİLİR olsun ----
# 285 ders playbooks/dersler.md'de duruyordu ama hiçbir oturum okumuyordu. Etiket (dokunulan
# dosyadan), konuya göre arama ve tur başında ≤5 satırlık bağlam notu; commit gövdesindeki
# `Karar:` / `Vade:` blokları da defter/vadelere iner (ders_yaz.py).
_BASLIK = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s+·\s+([0-9a-f]{7,40})\s+—\s+(.*)$")
_ETIKET = re.compile(r"\s#([\w\-]+)")
_KARAR = re.compile(r"^karar(?:\[(gecerli|rafta|geri_alindi|iptal)\])?\s*:\s*(.+?)\s+[—–-]\s+(.+)$", re.IGNORECASE)
_VADE = re.compile(r"^vade\s*:\s*(.+?)\s*\|\s*(\d{4}-\d{2}-\d{2})(?:\s*\|\s*(.*))?$", re.IGNORECASE)
_YOL_ETIKET = (("kokpit/ios", "kokpit"), ("ops/vekil", "vekil"), ("ops/onay", "onay"), ("ops/guvenlik_kapisi", "kapi"),
               ("ops/telegram_bridge", "kopru"), ("ops/githooks", "githook"), ("tests/", "test"), ("playbooks/", "playbook"),
               ("dossier/", "dossier"), ("marka/", "marka"))
BAGLAM_TAVAN = 5


def etiketler(dosyalar):
    """Commit'te dokunulan yollardan 1-4 etiket: ghost/metrik.py → metrik, ops/mac.py → mac, kokpit/ios/… → kokpit."""
    out = []
    for yol in dosyalar or []:
        yol = (yol or "").strip()
        if not yol:
            continue
        e = None
        for onek, ad in _YOL_ETIKET:
            if yol.startswith(onek):
                e = ad
                break
        if e is None and (yol.startswith("ghost/") or yol.startswith("ops/")):
            e = os.path.splitext(os.path.basename(yol))[0].lower()
            if e in ("__init__",):
                e = None
        if e and e not in out:
            out.append(e)
    return out[:4]


def kayitlar_oku(metin):
    """dersler.md → [{tarih, sha, konu, ders, etiket}] (eskiden yeniye)."""
    out, suan = [], None
    for satir in (metin or "").splitlines():
        m = _BASLIK.match(satir)
        if m:
            if suan:
                out.append(suan)
            baslik = m.group(3)
            et = _ETIKET.findall(" " + baslik)
            konu = _ETIKET.sub("", " " + baslik).strip()
            suan = {"tarih": m.group(1), "sha": m.group(2), "konu": konu, "ders": "", "etiket": et}
        elif suan is not None and satir.strip() and not satir.startswith("<!--"):
            suan["ders"] = (suan["ders"] + " " + satir.strip()).strip()
    if suan:
        out.append(suan)
    return out


def ara(mesaj, kayitlar, k=BAGLAM_TAVAN):
    """Mesajın anahtar terimleriyle (kök+önek) konu+ders+etiket üzerinde eşleşme; ≥2 terim (tek terimli mesajda 1).
    Eşit puanda yeni ders önce. Sohbet selamı gibi terimsiz mesaj → []."""
    from ghost import memory
    terimler = memory.anahtar_terimler(mesaj, en_fazla=10)
    if not terimler:
        return []
    kokler = [memory.kok(t) for t in terimler]
    esik = 1 if len(terimler) <= 2 else 2
    puanli = []
    for i, r in enumerate(kayitlar):
        govde = memory._tr("%s %s %s" % (r["konu"], r["ders"], " ".join(r.get("etiket") or [])))
        puan = sum(1 for kk in kokler if kk and kk in govde)
        if puan >= esik:
            puanli.append((puan, i, r))
    puanli.sort(key=lambda x: (-x[0], -x[1]))
    return [r for _, _, r in puanli[:k]]


def baglam_notu(mesaj, metin=None, k=BAGLAM_TAVAN, satir_tavan=220):
    """Tur başına girecek ≤k satır (~300 token); eşleşme yoksa ''. metin=None → playbooks/dersler.md okunur."""
    if metin is None:
        try:
            from ghost import config
            with open(os.path.join(config.BASE, "playbooks", "dersler.md"), encoding="utf-8") as f:
                metin = f.read()
        except Exception:
            return ""
    rows = ara(mesaj, kayitlar_oku(metin), k)
    out = []
    for r in rows:
        d = r["ders"] if len(r["ders"]) <= satir_tavan else r["ders"][:satir_tavan].rsplit(" ", 1)[0] + "…"
        out.append("- (%s) %s: %s" % (r["tarih"], r["konu"][:80], d))
    return "\n".join(out)


def karar_ayikla(govde):
    """`Karar[rafta]: <konu> — <karar>` satırları → [(konu, karar, durum)]."""
    out = []
    for satir in (govde or "").splitlines():
        m = _KARAR.match(satir.strip())
        if m:
            out.append((m.group(2).strip(), m.group(3).strip(), (m.group(1) or "gecerli").lower()))
    return out


def vade_ayikla(govde):
    """`Vade: <ad> | YYYY-MM-DD | not` satırları → [(ad, tarih, not)]."""
    out = []
    for satir in (govde or "").splitlines():
        m = _VADE.match(satir.strip())
        if m:
            out.append((m.group(1).strip(), m.group(2), (m.group(3) or "").strip()))
    return out


def guncelle(mevcut, kayitlar):
    """Dosya metnini yeni derslerle güncelle. Döner: (yeni_metin, eklenen).

    kayitlar: (sha, tarih, konu, ders[, etiket]) listesi, eskiden yeniye.
    Zaten yazılı sha atlanır → betik istediği kadar sık koşabilir.
    """
    metin = mevcut or ""
    varolan = yazili_shalar(metin)
    yeniler = []
    for kayit in kayitlar:
        sha, tarih, konu, ders = kayit[:4]
        etiket = kayit[4] if len(kayit) > 4 else ()
        if not ders or sha[:7] in {s[:7] for s in varolan}:
            continue
        varolan.add(sha[:7])
        yeniler.append(girdi(sha, tarih, konu, ders, etiket))
    if not yeniler:
        return metin, 0
    if not metin.strip():
        metin = "%s\n\n%s\n" % (BASLIK, ONSOZ)
    return metin.rstrip("\n") + "\n\n" + "\n".join(yeniler), len(yeniler)
