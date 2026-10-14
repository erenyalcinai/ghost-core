import unittest

from tests import common  # noqa: F401
from ghost import entities, voice


class TestEntities(unittest.TestCase):
    def test_seed_and_get(self):
        entities.seed()
        e = entities.get("AppA")
        self.assertIsNotNone(e)
        self.assertEqual(e["type"], "uygulama")
        # ilişkiler iki yönlü görünür
        self.assertTrue(any(r == "backend'i" for r, _ in e["out"]))
        self.assertTrue(any(rel == "sahibi" for _, rel in e["in"]))

    def test_case_insensitive(self):
        entities.seed()
        self.assertIsNotNone(entities.get("appa"))

    def test_add_link_idempotent(self):
        entities.add("TestCo", "şirket", "deneme")
        entities.link("TestCo", "üretir", "TestÜrün")
        entities.link("TestCo", "üretir", "TestÜrün")  # UNIQUE — patlamaz
        e = entities.get("TestCo")
        self.assertEqual(sum(1 for r, d in e["out"] if d == "TestÜrün"), 1)

    def test_render_missing_suggests(self):
        entities.seed()
        out = entities.render("App")
        self.assertIn("Benzerler", out)

    def test_seed_idempotent(self):
        entities.seed()
        entities.seed()
        e = entities.get("Ghost")
        rels = [d for _, d in e["out"]]
        self.assertEqual(rels.count("VPS"), 1)


class TestVoiceStrip(unittest.TestCase):
    def test_strip_emoji_and_truncate(self):
        s = voice._strip_for_speech("👻 Merhaba! Sistem %98 sağlıklı. " + "x" * 2000)
        self.assertNotIn("👻", s)
        self.assertIn("Merhaba!", s)
        self.assertLessEqual(len(s), voice.MAX_CHARS)

    def test_empty(self):
        self.assertIsNone(voice.tts("👻🎙"))  # sadece emoji → konuşulacak şey yok


if __name__ == "__main__":
    unittest.main()
