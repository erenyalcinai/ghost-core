"""Test ortak kurulumu — ghost modülleri import edilmeden ÖNCE yolları
geçici dizine yönlendirir (gerçek ops/ ve ghost.db'ye dokunulmaz)."""
import atexit
import os
import shutil
import sys
import tempfile

# Geçici dosyalar DİSKTE, koşu başına tek kök, çıkışta silinir (2026-11-09): /tmp tmpfs=RAM + usrquota — test koşuları
# 27 bin bayat tmp* dizini bırakmış (~900 MB), kota dolunca dış yedek düştü. TMPDIR dışarıdan verildiyse (pre-push kapısı)
# onun altında çocuk dizin; yoksa ~/.ghost/tmp. Alt süreçler de (claude, git) aynı köke yazar.
_TMP_KOK = os.path.join(os.environ.get("TMPDIR") or os.path.join(os.path.expanduser("~"), ".ghost", "tmp"),
                        "ghost-test-%d" % os.getpid())
os.makedirs(_TMP_KOK, exist_ok=True)
tempfile.tempdir = _TMP_KOK
os.environ["TMPDIR"] = _TMP_KOK
atexit.register(shutil.rmtree, _TMP_KOK, True)
_TMP = tempfile.mkdtemp(prefix="ghost-test-")
os.environ["GHOST_OPS"] = _TMP
os.environ["GHOST_DB"] = os.path.join(_TMP, "ghost-test.db")
# DIŞARI ÇIKAN her şeyi kes. Yol izolasyonu yetmiyordu: testler AĞA da
# ulaşıyordu — test_sms_rayi.py gerçek Telegram mesajı gönderiyordu ve her
# test koşusunda sahibin telefonuna sahte SMS bildirimi düşüyordu
# (29 Ağustos 2026, ekran görüntüsüyle yakalandı). Tek işaret, tek kapı.
os.environ["GHOST_TEST"] = "1"

_KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _KOK)

# SAHİP PROFİLİ SABİT (2026-11-15, kurucu §8.3). Testler belirli bir profile göre yazılıdır (izlenen siteler,
# Masa kök kartları, varlık tohumu). Profil sabitlenmezse ops/sahip.json'da KİM varsa o okunur: kurulu bir
# kopyada bu, kopyanın sahibinin profilidir ve 20 test kırılır (kopyanın push kapısı hep kırmızı kalır).
# Kural: testlerin yazıldığı profil = REFERANS_SITE'yi izleyen profil. Önce örnek, sonra gerçek dosyaya bakılır;
# bu ağaçta ikisinden yalnız biri referans siteyi taşır. Hiçbiri taşımıyorsa sabitleme yapılmaz (eski davranış).
# Dışarıdan GHOST_SAHIP verildiyse ona dokunulmaz.
REFERANS_SITE = "example.com"


def test_profili(kok=_KOK, site=REFERANS_SITE):
    import json
    for ad in ("sahip.example.json", "sahip.json"):
        yol = os.path.join(kok, "ops", ad)
        try:
            with open(yol, encoding="utf-8") as f:
                d = json.load(f)
            siteler = d.get("siteler") or []
        except (OSError, ValueError, AttributeError):
            continue
        if any(isinstance(s, dict) and s.get("alan") == site for s in siteler):
            return yol
    return None


test_profili.__test__ = False   # yardımcı; test toplayıcıları (pytest) test sanmasın
if not os.environ.get("GHOST_SAHIP"):
    _profil = test_profili()
    if _profil:
        os.environ["GHOST_SAHIP"] = _profil
