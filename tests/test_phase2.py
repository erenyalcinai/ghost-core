import unittest

from tests import common  # noqa: F401
from ghost import engines, notify


class TestEngines(unittest.TestCase):
    def test_available_is_list(self):
        av = engines.available()
        self.assertIsInstance(av, list)
        # claude bu ortamda kurulu değil olabilir — yapı doğru olsun yeter
        for item in av:
            self.assertEqual(len(item), 3)

    def test_catalog_shapes(self):
        for name, binary, cmd, note in engines.CATALOG:
            self.assertTrue("{prompt}" in " ".join(cmd))
            self.assertTrue(name and binary and note)

    def test_run_no_engines_graceful(self):
        # prefer edilen kurulu değilse bile çökmemeli
        out, eng, err = engines.run("merhaba", prefer="olmayan_motor", timeout=5)
        self.assertIsInstance(out, str)


class TestCostLimitSiniri(unittest.TestCase):
    """Maliyet/kota sınırı yedek motoru TETİKLEMEMELİ (2026-08-24 kararı)."""

    def test_limit_metinleri_yakalanir(self):
        for metin in [
            "Claude AI usage limit reached|1787551200",
            "API Error: 429 Too Many Requests",
            "rate_limit_error: quota exceeded",
            "Your credit balance is too low to access the API",
            "HTTP 402 Payment Required",
            "⚠️ Hata: kullanım sınırı doldu",
        ]:
            self.assertTrue(engines.is_cost_limit(metin), metin)

    def test_gercek_arizalar_failover_tetikler(self):
        for metin in [
            "⏱ Zaman aşımı (120 sn) — işi böl.",
            "⚠️ Çalıştırma hatası: FileNotFoundError('claude')",
            "connection reset by peer",
            "Error: EAI_AGAIN getaddrinfo failed",
            "OAuth token expired",
            "",
            None,
        ]:
            self.assertFalse(engines.is_cost_limit(metin), metin)


class TestNotifyFallback(unittest.TestCase):
    def test_telegram_no_config_returns_false(self):
        # boş env → ne telegram ne ntfy → False (çökme yok)
        self.assertFalse(notify.telegram("test", env={}))

    def test_ntfy_no_topic(self):
        self.assertFalse(notify.ntfy("test", env={}))


if __name__ == "__main__":
    unittest.main()
