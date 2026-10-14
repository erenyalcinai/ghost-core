import unittest

from tests import common  # noqa: F401
from ghost import agents, email, governance, transparency


class TestDepartments(unittest.TestCase):
    def test_parsed(self):
        self.assertIn("muhendislik", agents.DEPARTMENTS)
        self.assertIn("finans", agents.DEPARTMENTS)
        d = agents.DEPARTMENTS["muhendislik"]
        self.assertTrue(d["persona"])
        self.assertIn("claude_code", d["araclar"])
        self.assertIn("kod", d["anahtar"])

    def test_routing(self):
        key, _ = agents.route("şu repoda bug var, PR aç")
        self.assertEqual(key, "muhendislik")
        key, _ = agents.route("bu ayki gelir gider durumu ne")
        self.assertEqual(key, "finans")
        key, _ = agents.route("app-a.example.com sitesi ayakta mı, dns kontrol")
        self.assertEqual(key, "operasyon")
        key, _ = agents.route("rakip firmayı araştır")
        self.assertEqual(key, "istihbarat")

    def test_no_match_returns_none(self):
        key, dep = agents.route("bana bir şiir yaz")
        self.assertIsNone(key)

    def test_word_boundary_no_false_positive(self):
        # 'bugün' içindeki 'bug' Mühendislik'e yönlendirmemeli
        key, _ = agents.route("bugün hava nasıl")
        self.assertIsNone(key)

    def test_site_down_to_operations(self):
        key, _ = agents.route("app-a.example.com down mu, erişilemiyor mu")
        self.assertEqual(key, "operasyon")

    def test_system_prefix(self):
        p = agents.system_prefix("deploy et")
        self.assertIn("Mühendislik", p)
        self.assertEqual(agents.system_prefix("şiir yaz"), "")


class TestEmail(unittest.TestCase):
    def test_no_key_refuses(self):
        ok, msg = email.send("x@y.com", "konu", "gövde", env={})
        self.assertFalse(ok)
        self.assertIn("RESEND_API_KEY", msg)

    def test_can_send(self):
        self.assertFalse(email.can_send(env={}))
        self.assertTrue(email.can_send(env={"RESEND_API_KEY": "re_x"}))


class TestTransparency(unittest.TestCase):
    def test_empty(self):
        # now'u geleceğe al → pencere tüm kayıtların ilerisinde → boş dal
        r = transparency.report(days=7, now=99999999999)
        self.assertIn("denetim kaydı yok", r)

    def test_counts_recent_actions(self):
        governance.audit("bridge", "job.finish", governance.READ, "iş bitti")
        governance.audit("owner", "approval", governance.PERMANENT, "deploy",
                         "onay")
        governance.audit("email", "send", governance.PERMANENT, "x@y | konu")
        r = transparency.report(days=7)
        self.assertIn("İş tamamlandı", r)
        self.assertIn("Dikkat gerektiren", r)  # email send = permanent


if __name__ == "__main__":
    unittest.main()
