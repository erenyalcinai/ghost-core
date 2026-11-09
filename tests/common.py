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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
