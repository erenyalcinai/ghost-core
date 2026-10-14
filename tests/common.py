"""Test ortak kurulumu — ghost modülleri import edilmeden ÖNCE yolları
geçici dizine yönlendirir (gerçek ops/ ve ghost.db'ye dokunulmaz)."""
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="ghost-test-")
os.environ["GHOST_OPS"] = _TMP
os.environ["GHOST_DB"] = os.path.join(_TMP, "ghost-test.db")
# DIŞARI ÇIKAN her şeyi kes. Yol izolasyonu yetmiyordu: testler AĞA da
# ulaşıyordu — test_sms_rayi.py gerçek Telegram mesajı gönderiyordu ve her
# test koşusunda sahibin telefonuna sahte SMS bildirimi düşüyordu
# (29 Ağustos 2026, ekran görüntüsüyle yakalandı). Tek işaret, tek kapı.
os.environ["GHOST_TEST"] = "1"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
