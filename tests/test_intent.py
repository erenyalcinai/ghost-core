import unittest

from tests import common  # noqa: F401
from ghost import intent


class TestIntentDetect(unittest.TestCase):
    def test_reminder_with_time(self):
        it = intent.detect("yarın 09:00 toplantıyı hatırlat")
        self.assertEqual(it["kind"], "hatirlat")
        self.assertEqual(it["spec"], "yarın 09:00")
        self.assertIn("toplantıyı", it["payload"])

    def test_reminder_relative(self):
        it = intent.detect("bana 20dk sonra çayı hatırlat")
        self.assertEqual(it["kind"], "hatirlat")
        self.assertIn("çayı", it["payload"])
        self.assertNotIn("bana", it["payload"])  # dolgu sözcük temizlenir

    def test_reminder_without_time_is_chat(self):
        self.assertIsNone(intent.detect("bana şunu sonra hatırlat olur mu"))

    def test_recurring_job(self):
        it = intent.detect("her gün 08:30 siteleri kontrol et sorun varsa yaz")
        self.assertEqual(it["kind"], "gorev")
        self.assertEqual(it["spec"], "her gün 08:30")
        self.assertIn("siteleri", it["payload"])

    def test_watch_url(self):
        it = intent.detect("şu sayfayı izlemeye al https://ornek.com/fiyat")
        self.assertEqual(it["kind"], "izle")
        self.assertEqual(it["payload"], "https://ornek.com/fiyat")

    def test_watch_bare_domain(self):
        it = intent.detect("rakip.com sitesini takip et")
        self.assertEqual(it["kind"], "izle")
        self.assertTrue(it["payload"].startswith("https://"))

    def test_remember(self):
        it = intent.detect("unutma: appa lansmanı eylülde")
        self.assertEqual(it["kind"], "ogren")
        self.assertIn("lansman", it["payload"])

    def test_goal(self):
        it = intent.detect("hedefim: appa'e 100 aktif kullanıcı")
        self.assertEqual(it["kind"], "hedef")
        self.assertIn("100", it["payload"])

    def test_ordinary_chat_none(self):
        for t in ("bugün hava nasıl", "sence slash mantıklı mı",
                  "app-a.example.com down mu", "naber", "izlemek güzel şeydir",
                  "/help", "her şey yolunda mı"):
            self.assertIsNone(intent.detect(t), t)

    def test_long_text_skipped(self):
        self.assertIsNone(intent.detect("hatırlat " + "x" * 500))


if __name__ == "__main__":
    unittest.main()


class TestTarihKusuru(unittest.TestCase):
    """Araya giren dolgu kelimesi GÜNÜ düşürmemeli.

    29 Ağustos 2026'da bulundu, aylardır oradaydı: _find_spec yalnız BİTİŞİK
    pencere deniyordu. "yarın sabah 09:00" ayrıştırılamayınca geriye tek başına
    "09:00" kalıyor, hatırlatma 24 saat ERKEN kuruluyordu.
    """
    def test_dolgu_kelimesi_yarini_dusurmez(self):
        it = intent.detect("yarın sabah 09:00 toplantıyı hatırlat")
        self.assertIsNotNone(it)
        self.assertEqual(it["spec"], "yarın 09:00")

    def test_zaman_kelimeleri_nottan_ayiklanir(self):
        it = intent.detect("yarın 09:00 toplantıyı hatırlat")
        self.assertNotIn("yarın", it["payload"])
        self.assertNotIn("09:00", it["payload"])

    def test_tekrarlayan_gorev_bozulmadi(self):
        it = intent.detect("her gün 08:30 siteleri kontrol et sorun varsa yaz")
        self.assertEqual(it["kind"], "gorev")
        self.assertEqual(it["spec"], "her gün 08:30")
        self.assertIn("siteleri", it["payload"])

    def test_zamansiz_hatirlatma_hala_sohbete_gider(self):
        self.assertIsNone(intent.detect("bunu bir ara hatırlatırım sana"))
