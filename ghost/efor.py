"""Efor (reasoning effort) ayarı — Claude Code'daki `--effort` seçimi, Ghost için (Sahip 2026-11-02).

Tek kaynak ghost.db `durum` tablosundaki "efor" kaydı: {"seviye": "high"}; kayıt yoksa ya da
"varsayilan" ise CLI'ye bayrak GİTMEZ (CLI/model varsayılanı). Yazan: `/efor` komutu
(ghost/komutlar.py) ve `python3 -m ghost.efor ayar <seviye>`. Okuyan: ghost/motor.py (her
run_claude çağrısı; özerk işler HARİÇ — gece hedefleri sahibin seçtiği max ile kota yakmasın),
ghost/health.py (Kokpit nabzı → Ayarlar'da gösterilir).

Kalıcı sohbet sürecinde efor süreç düzeyindedir: değişince süreç aynı oturumla (`--resume`)
yeniden açılır, konuşma kaybolmaz, yalnız o tur önbellek ısınır. Tur başına efor ("!max")
bilinçli olarak YOK: her seferinde iki kez yeniden açma = iki soğuk tur.
"""
import sys

SEVIYELER = ("low", "medium", "high", "xhigh", "max")
VARSAYILAN = "varsayilan"
_TAKMA = {"dusuk": "low", "düşük": "low", "orta": "medium", "yuksek": "high", "yüksek": "high",
          "en": "max", "default": VARSAYILAN, "varsayılan": VARSAYILAN, "sifirla": VARSAYILAN,
          "off": VARSAYILAN, "kapat": VARSAYILAN}


def coz(metin):
    """Kullanıcı girdisi → seviye | VARSAYILAN | None (tanınmadı)."""
    m = (metin or "").strip().lower()
    m = _TAKMA.get(m, m)
    if m in SEVIYELER or m == VARSAYILAN:
        return m
    return None


def oku():
    """Geçerli seviye ya da None (varsayılan — bayrak yok)."""
    try:
        from ghost import durum
        d = durum.oku("efor") or {}
    except Exception:
        return None
    s = d.get("seviye") if isinstance(d, dict) else None
    return s if s in SEVIYELER else None


def yaz(seviye):
    s = coz(seviye)
    if s is None:
        raise ValueError("efor: %s" % " | ".join(SEVIYELER + (VARSAYILAN,)))
    from ghost import durum
    durum.yaz("efor", {"seviye": s})
    return None if s == VARSAYILAN else s


def arg(seviye):
    """CLI argv parçası."""
    return ["--effort", seviye] if seviye in SEVIYELER else []


def etiket(seviye=None):
    return seviye or "varsayılan"


def main(argv):
    if not argv or argv[0] == "durum":
        print("Efor: %s" % etiket(oku()))
        return 0
    if argv[0] == "ayar" and len(argv) > 1:
        try:
            print("Efor: %s" % etiket(yaz(argv[1])))
        except ValueError as e:
            print(e)
            return 2
        return 0
    print("kullanım: python3 -m ghost.efor durum | ayar <%s>" % "|".join(SEVIYELER + (VARSAYILAN,)))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
