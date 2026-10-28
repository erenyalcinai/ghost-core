"""Güvenlik 6. tur (2026-10-29, turu kapatma paketi): FREEZE ortak konumu (vekil + onay servisi + SMTP aktarıcısı
durdurmayı görür), PostHog MCP anahtarı vekilde (readonly=1 zorlanır), GitHub MCP okuma araçları tam ad listesi,
köprünün sırsız MCP kopyası PostHog'u da kapsar, governance aynası."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import tests.common  # noqa: F401
from ghost import config, governance

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _yukle(ad, *parca):
    spec = importlib.util.spec_from_file_location(ad, os.path.join(KOK, *parca))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


vk = _yukle("vekil_servisi_6", "ops", "vekil", "vekil_servisi.py")
on = _yukle("onay_servisi_6", "ops", "onay", "onay_servisi.py")

SIR = {"TELEGRAM_OWNER_ID": "111", "CLOUDFLARE_ACCOUNT_ID": "acc1", "POSTHOG_PROJECT_ID": "000000",
       "CLOUDFLARE_API_TOKEN": "cf-" + "a1b2c3d4e5f6" * 3, "TELEGRAM_BOT_TOKEN": "123456:tg-" + "x" * 30,
       "POSTHOG_MCP_KEY": "phx_" + "m" * 40, "GMAIL_ADDRESS": "owner@example.com"}


class Sahte:
    def __init__(self):
        self.cagri = []

    def __call__(self, host, yontem, yol, bas, govde):
        self.cagri.append((host, yontem, yol, dict(bas), govde))
        return 200, [("Content-Type", "application/json"), ("Connection", "close")], b'{"ok":true}'


def _bayrak(deger):
    d = tempfile.mkdtemp()
    y = os.path.join(d, "durum")
    with open(y, "w") as f:
        f.write(deger)
    return y


class VekilFreeze(unittest.TestCase):
    def _v(self, deger):
        g = Sahte()
        return vk.Vekil(dict(SIR), gonder=g, freeze_yol=_bayrak(deger)), g

    def test_freeze_yazma_403_okuma_gecer(self):
        v, g = self._v("1\n")
        d, _, govde = v.isle("POST", "/cloudflare/client/v4/zones/z1/dns_records", [("Content-Type", "application/json")],
                             b'{"type":"A"}')
        self.assertEqual(d, 403)
        self.assertIn(b"FREEZE", govde)
        self.assertEqual(g.cagri, [])
        self.assertEqual(v.isle("GET", "/cloudflare/client/v4/zones", [], None)[0], 200)

    def test_freeze_telegram_sahibe_ve_getupdates_akar(self):
        v, g = self._v("1")
        self.assertEqual(v.isle("POST", "/telegram/sendMessage", [], b"chat_id=111&text=dondum")[0], 200)
        self.assertEqual(v.isle("POST", "/telegram/getUpdates", [], b"offset=1")[0], 200)   # /unfreeze buradan gelir
        self.assertEqual(len(g.cagri), 2)

    def test_bayrak_sifir_ya_da_yok_serbest(self):
        v, g = self._v("0\n")
        self.assertEqual(v.isle("POST", "/cloudflare/client/v4/zones/z1/dns_records", [], b"{}")[0], 200)
        v2 = vk.Vekil(dict(SIR), gonder=Sahte(), freeze_yol=os.path.join(tempfile.mkdtemp(), "yok"))
        self.assertEqual(v2.isle("POST", "/cloudflare/client/v4/zones/z1/dns_records", [], b"{}")[0], 200)
        self.assertFalse(vk.dondu(os.path.join(tempfile.mkdtemp(), "yok")))


class PosthogMcp(unittest.TestCase):
    def test_readonly_zorlanir_ve_anahtar_vekilden(self):
        g = Sahte()
        v = vk.Vekil(dict(SIR), gonder=g)
        govde = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "exec", "arguments": {"command": "query"}}}).encode()
        d, _, _ = v.isle("POST", "/posthog-mcp/mcp?mode=cli", [("Content-Type", "application/json"), ("Authorization", "Bearer vekil")], govde)
        self.assertEqual(d, 200)
        host, yontem, yol, bas, _ = g.cagri[0]
        self.assertEqual(host, "mcp.posthog.com")
        self.assertIn("readonly=1", yol)
        self.assertTrue(yol.startswith("/mcp?mode=cli"))
        self.assertEqual(bas["Authorization"], "Bearer " + SIR["POSTHOG_MCP_KEY"])

    def test_izin_siniflari(self):
        self.assertEqual(vk.izin("posthog-mcp", "POST", "/mcp?readonly=1&mode=cli", b"{}", "application/json", SIR)[0], "okuma")
        self.assertEqual(vk.izin("posthog-mcp", "POST", "/mcp?mode=cli", b"{}", "application/json", SIR)[0], "yazma")
        self.assertEqual(vk.izin("posthog-mcp", "GET", "/mcp", None, "", SIR)[0], "okuma")

    def test_kur_sh_anahtari_tasir(self):
        with open(os.path.join(KOK, "ops", "vekil", "kur.sh"), encoding="utf-8") as f:
            k = f.read()
        self.assertIn("POSTHOG_MCP_KEY", k.split("ANAHTARLAR=", 1)[1].split("\n", 1)[0])
        self.assertIn('("mcp-posthog.json", "POSTHOG_MCP_KEY", ("headers", "Authorization"))', k)
        self.assertIn("/run/ghost-freeze", k)
        self.assertIn('[ "${DON:-}" = 1 ]', k)


class McpTamAd(unittest.TestCase):
    def test_tam_ad_ve_yedek_kural(self):
        for ad in ("get_me", "list_issues", "search_code", "pull_request_read", "get_file_contents", "list_workflow_runs"):
            self.assertTrue(vk._mcp_okuma_mi(ad), ad)
        for ad in ("create_issue", "push_files", "merge_pull_request", "get_and_delete_thing", "list_then_update_x",
                   "search_and_dismiss", "issue_write", "run_workflow", "download_and_run"):
            self.assertFalse(vk._mcp_okuma_mi(ad), ad)
        self.assertTrue(vk._mcp_okuma_mi("list_some_future_read_tool"))     # listede yok, yazma fiili yok → okuma

    def test_izin_mcp_yazma_fiilli_get_araci_yazmadir(self):
        g = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_and_delete_thing",
                                                                           "arguments": {"owner": "baska", "repo": "r"}}}
        self.assertEqual(vk.izin("github-mcp", "POST", "/mcp/readonly", json.dumps(g).encode(), "application/json", SIR)[0], "red")
        g["params"]["name"] = "get_file_contents"
        self.assertEqual(vk.izin("github-mcp", "POST", "/mcp/readonly", json.dumps(g).encode(), "application/json", SIR)[0], "okuma")


class OnayFreeze(unittest.TestCase):
    def _o(self, deger):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "ayar.json"), "w") as f:
            json.dump({"sahip_id": 5550001, "telegram_token": "x", "izinli_uid": [os.getuid()]}, f)
        return on.Onay(d, kopru_pid=lambda: 777, telegram=lambda m: True, freeze_yol=_bayrak(deger))

    def test_freeze_kart_ve_tuketim_kapali_durum_gorunur(self):
        o = self._o("1\n")
        peer = (4242, os.getuid(), os.getgid())
        r = o.isle({"op": "kart_ac", "eid": "onay-0011223344556677", "onizleme": "x"}, peer)
        self.assertFalse(r["ok"])
        self.assertIn("FREEZE", r["hata"])
        r = o.isle({"op": "tuket", "eid": "onay-0011223344556677", "onizleme_sha": "0" * 64, "tur": "genel"}, peer)
        self.assertFalse(r["ok"])
        self.assertFalse(r["tuketildi"])
        d = o.isle({"op": "durum"}, peer)
        self.assertTrue(d["ok"])
        self.assertTrue(d["freeze"])
        self.assertTrue(o.isle({"op": "ping"}, peer)["ok"])

    def test_bayrak_sifir_serbest(self):
        o = self._o("0\n")
        peer = (4242, os.getuid(), os.getgid())
        self.assertTrue(o.isle({"op": "kart_ac", "eid": "onay-0011223344556677", "onizleme": "x"}, peer)["ok"])
        self.assertFalse(o.isle({"op": "durum"}, peer)["freeze"])


class GovernanceAyna(unittest.TestCase):
    def test_is_frozen_aynayi_yazar(self):
        d = tempfile.mkdtemp()
        ayna = os.path.join(d, "durum")
        with mock.patch.object(governance, "AYNA_DOSYA", ayna):
            self.assertFalse(governance.is_frozen())
            self.assertEqual(open(ayna).read().strip(), "0")
            governance.freeze("test")
            self.assertTrue(governance.is_frozen())
            self.assertEqual(open(ayna).read().strip(), "1")
            self.assertTrue(vk.dondu(ayna))
            p = config.FREEZE_FILE
            os.unlink(p)
            self.assertFalse(governance.is_frozen())
            self.assertEqual(open(ayna).read().strip(), "0")
            self.assertFalse(vk.dondu(ayna))

    def test_dizin_yoksa_sessiz(self):
        with mock.patch.object(governance, "AYNA_DOSYA", "/nonexistent-dir-6/durum"):
            self.assertFalse(governance.is_frozen())


class KopruMcpKopyasi(unittest.TestCase):
    def test_posthog_ayari_vekile_cevrilir(self):
        sys.path.insert(0, os.path.join(KOK, "ops"))
        import telegram_bridge as tb
        from ghost import vekil as gv
        d = tempfile.mkdtemp()
        kaynak = os.path.join(d, "mcp-posthog.json")
        with open(kaynak, "w") as f:
            json.dump({"mcpServers": {"posthog": {"type": "http", "url": "https://mcp.posthog.com/mcp?readonly=1&mode=cli",
                                                  "headers": {"Authorization": "Bearer phx_gercek"}}}}, f)
        with mock.patch.object(tb, "OPS", d), mock.patch.dict(tb.MCP_CONFIGS, {"posthog": kaynak}, clear=True), \
                mock.patch.object(gv, "kurulu", lambda: True), mock.patch.object(gv, "_erisilir", lambda: True):
            tb._mcp_vekile_cevir()
            hedef = tb.MCP_CONFIGS["posthog"]
        self.assertEqual(hedef, os.path.join(d, "mcp_vekil", "mcp-posthog.json"))
        cfg = json.load(open(hedef))["mcpServers"]["posthog"]
        self.assertEqual(cfg["url"], gv.TABAN + "/posthog-mcp/mcp?readonly=1&mode=cli")
        self.assertEqual(cfg["headers"]["Authorization"], "Bearer vekil")
        self.assertNotIn("phx_gercek", open(hedef).read())


if __name__ == "__main__":
    unittest.main()
