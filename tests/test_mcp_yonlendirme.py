"""MCP yönlendirmesi (2026-10-19): salt-okunur GitHub, github-yaz, sosumi, posthog, varsayılan küme."""
import importlib.util
import os
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("tb", os.path.join(BASE, "ops", "telegram_bridge.py"))
tb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tb)


class MCPYonlendirme(unittest.TestCase):
    def test_github_okuma_salt_okunur(self):
        self.assertEqual(tb._mcp_servers_for("son commit ne diyor"), ["github"])

    def test_github_yazma_tam_uc(self):
        s = tb._mcp_servers_for("ghost-core'da bir issue aç")
        self.assertIn("github-yaz", s)
        self.assertNotIn("github", s)

    def test_sosumi_swift(self):
        self.assertIn("sosumi", tb._mcp_servers_for("SwiftData ModelContainer nasıl kurulur"))
        self.assertIn("sosumi", tb._mcp_servers_for("App B widget'ı"))

    def test_posthog_anahtar_kelime(self):
        self.assertEqual(tb._mcp_servers_for("posthog'da aktif kullanıcı sayısı"), ["posthog"])

    def test_cloudflare_mcp_yok(self):
        self.assertEqual(tb._mcp_servers_for("cloudflare dns kaydı"), [])

    def test_belirsiz_varsayilan_kume(self):
        s = tb._mcp_servers_for("şu işi bir düşünelim")
        self.assertEqual(s, list(tb.MCP_DEFAULT))
        self.assertNotIn("posthog", s)
        self.assertNotIn("github-yaz", s)

    def test_config_dosyalari_var(self):
        # Anahtar taşıyan config'ler (github, github-yaz, posthog) gitignore'da;
        # kapı `git archive` kopyasında koştuğu için orada OLAMAZLAR. Kurulu
        # makinede (secrets.env var) hepsi, arşiv/CI'da yalnız takip edilen
        # sosumi aranır. (2026-10-19→24: bu satır 5 gün boyunca her push'u
        # kesti, GitHub'a 56 commit gitmedi.)
        for ad in ("github", "github-yaz", "sosumi", "posthog"):
            self.assertIn(ad, tb.MCP_CONFIGS)
        kurulu = os.path.exists(os.path.join(BASE, "secrets.env"))
        beklenen = ("github", "github-yaz", "sosumi", "posthog") if kurulu else ("sosumi",)
        for ad in beklenen:
            self.assertTrue(os.path.exists(tb.MCP_CONFIGS[ad]), ad)

    def test_tool_note_varsayilan(self):
        self.assertEqual(tb._tool_note(list(tb.MCP_DEFAULT)), "")


if __name__ == "__main__":
    unittest.main()
