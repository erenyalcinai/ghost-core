import unittest

from tests import common  # noqa: F401
from ghost import motor, notify


class TestCostLimitSiniri(unittest.TestCase):
    """Maliyet/kota sınırı ayrı sınıf (2026-08-24 kararı); yedek motor Faz 2.9'dan beri hiç yok."""

    def test_limit_metinleri_yakalanir(self):
        for metin in [
            "Claude AI usage limit reached|1787551200",
            "API Error: 429 Too Many Requests",
            "rate_limit_error: quota exceeded",
            "Your credit balance is too low to access the API",
            "HTTP 402 Payment Required",
            "⚠️ Hata: kullanım sınırı doldu",
        ]:
            self.assertTrue(motor.is_cost_limit(metin), metin)

    def test_gercek_arizalar_sinir_sayilmaz(self):
        for metin in [
            "⏱ Zaman aşımı (120 sn) — işi böl.",
            "⚠️ Çalıştırma hatası: FileNotFoundError('claude')",
            "connection reset by peer",
            "Error: EAI_AGAIN getaddrinfo failed",
            "OAuth token expired",
            "",
            None,
        ]:
            self.assertFalse(motor.is_cost_limit(metin), metin)


class TestNotifyFallback(unittest.TestCase):
    def test_telegram_no_config_returns_false(self):
        # boş env → ne telegram ne ntfy → False (çökme yok)
        self.assertFalse(notify.telegram("test", env={}))

    def test_ntfy_no_topic(self):
        self.assertFalse(notify.ntfy("test", env={}))


if __name__ == "__main__":
    unittest.main()
