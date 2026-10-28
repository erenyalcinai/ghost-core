"""Yönetişim v2 — eylem sınıfları, denetim günlüğü, acil durdurma.

Sınıflar (VISION.md'deki durum makinesi):
  READ        oku — serbest
  REVERSIBLE  geri alınabilir yaz — serbest ama denetim günlüğüne
  PERMANENT   kalıcı yaz (deploy, push, silme, store) — onay ister
  SPEND       harcama / dış taahhüt — onay ister (en üst)

`classify(text)` metinden en yüksek sınıfı çıkarır (regex v2; ileride model
sınıflandırması eklenebilir). `audit()` her otonom eylemi ops/audit.jsonl'e
yazar. FREEZE dosyası varken otonom eylemler durur (/freeze · /unfreeze).
"""
import json
import os
import re
import time

from ghost import config

READ = "read"
REVERSIBLE = "reversible"
PERMANENT = "permanent"
SPEND = "spend"

_ORDER = {READ: 0, REVERSIBLE: 1, PERMANENT: 2, SPEND: 3}

# Sıra önemli değil — en yüksek eşleşen sınıf kazanır.
_PATTERNS = [
    (SPEND, re.compile(
        r"\b(satın\s+al|satin\s+al|öde\b|ode\b|ödeme|odeme|para\s+(gönder|yolla)|"
        r"buy\b|purchase|pay\b|payment|subscribe|abone\s+ol|sipariş|siparis|"
        r"transfer\s+et|havale|kart)\b", re.IGNORECASE)),
    (PERMANENT, re.compile(
        r"\b(rm |rmdir|delete|sil\b|siler|kaldır|kaldir|remove|wipe|erase|"
        r"uninstall|destroy|drop\s+table|truncate|"
        r"git\s+push|push\s+et|force|--force|-f\b|deploy|yayınla|yayinla|publish|"
        r"wrangler\s+deploy|eas\s+submit|store'?a\s+gönder|submit|"
        r"ifşa|ifsa|disclose|huntr|bulgu\s+gönder|bulgu\s+gonder|"
        r"reset\s+--hard|overwrite|üzerine\s+yaz|uzerine\s+yaz|"
        r"dns\s+(değiştir|degistir|kaydı?\s+ekle)|domain\s+transfer)", re.IGNORECASE)),
    (REVERSIBLE, re.compile(
        r"\b(yaz\b|oluştur|olustur|create|write|düzenle|duzenle|edit|commit|"
        r"kur\b|install|başlat|baslat|start|restart|kaydet|save)\b", re.IGNORECASE)),
]


def classify(text):
    best = READ
    for cls, pat in _PATTERNS:
        if pat.search(text or "") and _ORDER[cls] > _ORDER[best]:
            best = cls
    return best


def requires_approval(cls, guard_on=True):
    if not guard_on:
        return False
    return _ORDER.get(cls, 0) >= _ORDER[PERMANENT]


AUDIT_YEDEK = "/tmp/ghost_audit_yedek.jsonl"     # ana günlük yazılamazsa (disk dolu/izin)


def audit(actor, action, cls=READ, detail="", decision=None):
    """Her otonom/önemli eylem tek satır JSON olarak günlüğe."""
    rec = {"ts": time.strftime("%F %T"), "actor": actor, "action": action,
           "class": cls, "detail": str(detail)[:500]}
    if decision is not None:
        rec["decision"] = decision
    satir = json.dumps(rec, ensure_ascii=False) + "\n"
    try:
        with open(config.AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(satir)
    except OSError as e:
        # Sessiz kayıp yok (2026-10-27, dış inceleme): yedek dosyaya + stderr'e (journald).
        # selfcheck yedek dosya varsa alarm verir; ana günlüğe dönünce elle birleştirilir.
        rec["audit_hatasi"] = repr(e)[:120]
        try:
            with open(AUDIT_YEDEK, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass
        import sys
        print("AUDIT YAZILAMADI: %s" % satir.strip()[:400], file=sys.stderr)
    return rec


# 6. tur (2026-10-29): acil durdurma bayrağının ORTAK konumu. Vekil ve onay servisi ayrı kullanıcıda, ProtectHome ile
# /home'u göremez; köprü (ghost) her is_frozen() çağrısında (döngü başına) durumu /run/ghost-freeze/durum dosyasına
# "1"/"0" olarak aynalar (dizin tmpfiles.d ile root kurar: ops/vekil/kur.sh; yoksa sessizce atlanır).
# Tek kaynak ops altındaki bayrak dosyası kalır; ayna salt-okunur kopyadır, kaldırma/açma bu dosyaya bakmaz.
AYNA_DOSYA = os.environ.get("GHOST_FREEZE_AYNA") or "/run/ghost-freeze/durum"


def _ayna(dondu):
    try:
        if not os.path.isdir(os.path.dirname(AYNA_DOSYA)):
            return
        yeni = "1" if dondu else "0"
        try:
            with open(AYNA_DOSYA, encoding="utf-8") as f:
                if f.read(1) == yeni:
                    return
        except OSError:
            pass
        with open(AYNA_DOSYA, "w", encoding="utf-8") as f:
            f.write(yeni + "\n")
    except OSError:
        pass


def is_frozen():
    d = os.path.exists(config.FREEZE_FILE)
    _ayna(d)
    return d


def freeze(reason=""):
    with open(config.FREEZE_FILE, "w", encoding="utf-8") as f:
        f.write("%s %s\n" % (time.strftime("%F %T"), reason))
    audit("governance", "freeze", PERMANENT, reason, "manual")


def unfreeze():
    try:
        os.remove(config.FREEZE_FILE)
    except OSError:
        pass
    audit("governance", "unfreeze", PERMANENT, "", "manual")


def audit_tail(n=20):
    """Son n denetim kaydı (şeffaflık raporu / hata ayıklama)."""
    try:
        with open(config.AUDIT_LOG, encoding="utf-8") as f:
            lines = f.readlines()[-n:]
        return [json.loads(l) for l in lines if l.strip()]
    except OSError:
        return []
