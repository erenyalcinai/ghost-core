"""Test koşusu dış dünyaya ÇIKMAZ — ne mesaj, ne bildirim.

KÖK SEBEP (29 Ağustos 2026, Sahip ekran görüntüsüyle yakaladı): test_sms_rayi.py
webhook'u çağırıyordu, webhook da notify.telegram'ı — yani her test koşusunda
sahibin telefonuna 4 sahte bildirim düşüyordu ("SMS — Apple / Kod: 123456"
gibi). tests/common.py yalnız DOSYA yollarını izole ediyordu; ağ açıktı.

Ders alınan kısım: "her test göndericiyi taklit etsin" kuralı çalışmaz —
unutulan tek test yeter. Bu yüzden kapı tek noktada, çıkışın kendisinde.
"""
import os
import unittest

from tests import common  # noqa: F401

from ghost import config, notify

# .env OLMADAN da anlamlı olsun. Kapı testleri 29 Ağustos'ta CI'da (ve push
# kapısının arşiv koşusunda) kırmızıydı: notify.telegram yapılandırma yoksa ağ
# kapısına HİÇ ulaşmadan ntfy'ye düşüp False dönüyor, yani "mesaj kesildi mi"
# sorusu hiç sorulmuyordu. Testin kendi ortamını kurması gerekir; yoksa test
# ".env var mı" sorusunu ölçer, kapıyı değil.
SAHTE_ENV = {"TELEGRAM_BOT_TOKEN": "test-token",
             "TELEGRAM_OWNER_ID": "1",
             "NTFY_TOPIC": "test-konu"}


class TestKapi(unittest.TestCase):
    def setUp(self):
        self._env_yedek = config.load_env
        config.load_env = lambda *a, **k: dict(SAHTE_ENV)
        notify.GONDERILENLER.clear()

    def tearDown(self):
        config.load_env = self._env_yedek
        notify.GONDERILENLER.clear()

    def test_test_kipi_isareti_konulmus(self):
        self.assertEqual(os.environ.get("GHOST_TEST"), "1",
                         "tests/common.py GHOST_TEST koymalı")

    def test_telegram_agi_kullanmaz(self):
        """Gönderim ağa çıkmadan yakalanmalı."""
        once = len(notify.GONDERILENLER)
        notify.telegram("bu mesaj ASLA gitmemeli")
        self.assertEqual(len(notify.GONDERILENLER), once + 1)
        self.assertEqual(notify.GONDERILENLER[-1][0], "telegram")

    def test_ntfy_de_kapali(self):
        once = len(notify.GONDERILENLER)
        notify.ntfy("yedek kanal da susmalı", env={"NTFY_TOPIC": "x"})
        self.assertEqual(notify.GONDERILENLER[-1][0], "ntfy")
        self.assertEqual(len(notify.GONDERILENLER), once + 1)

    def test_sms_rayi_bildirim_gondermez(self):
        """Kusurun doğduğu tam yol: webhook → notify → sahibin telefonu."""
        from ghost import webhook
        once = len(notify.GONDERILENLER)
        webhook.handle_hook("sms", {"from": "Apple", "body": "Kod: 123456"}, True)
        # bildirim ayrı iplikte gidiyor; yakalandığını görmek için bekle
        for _ in range(200):
            if len(notify.GONDERILENLER) > once:
                break
            import time
            time.sleep(0.01)
        self.assertGreater(len(notify.GONDERILENLER), once,
                           "sms rayı bildirim üretiyor — kapıdan geçmeli")
        self.assertIn("Kod: 123456", notify.GONDERILENLER[-1][1])

    def test_kopru_api_cagrisi_da_kapali(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "ops"))
        import telegram_bridge as tb
        self.assertTrue(tb.api("sendMessage", chat_id=1, text="gitmemeli")
                        .get("test"), "köprünün api()'si test kipinde susmalı")
