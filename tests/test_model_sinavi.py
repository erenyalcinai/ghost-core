"""ops/model_sinavi.py — Faz 4.1.5 model sınavı: puanlama, koşu kaydı (sahte koşucu), özet tablosu, körleme + Sahip puanı."""
import importlib.util
import json
import os
import tempfile
import unittest
import unittest.mock

from tests import common  # noqa: F401

_spec = importlib.util.spec_from_file_location("model_sinavi", os.path.join(os.path.dirname(os.path.dirname(__file__)), "ops", "model_sinavi.py"))
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)


class TestSinav(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sinav-")
        self.eski = ms.KOK
        ms.KOK = self.tmp
        ms.GOREVLER = os.path.join(self.tmp, "gorevler.json")
        json.dump([{"id": 1, "departman": "istihbarat", "gorev": "soru 1", "beklenen": ["85", "kota"]},
                   {"id": 2, "departman": "muhendislik", "gorev": "soru 2", "beklenen": ["x"]}],
                  open(ms.GOREVLER, "w", encoding="utf-8"))

    def tearDown(self):
        ms.KOK = self.eski
        ms.GOREVLER = os.path.join(self.eski, "gorevler.json")

    def test_puanla(self):
        self.assertEqual(ms.puanla("eşik 85 kota.py", ["85", "kota"]), (1.0, []))
        self.assertEqual(ms.puanla("yalnız 85", ["85", "kota"]), (0.5, ["kota"]))
        self.assertEqual(ms.puanla("", []), (None, []))

    def test_kos_ozet_kor_puan(self):
        gorulen = []

        def kosucu(cmd, env, zaman):
            gorulen.append(cmd)
            model = cmd[cmd.index("--model") + 1]
            self.assertEqual(env.get("GHOST_OZERK"), "1")
            self.assertIn("--agent", cmd)
            metin = "eşik 85 kota.py x" if "opus" in model else "bilmem"
            return 0, json.dumps({"result": metin, "total_cost_usd": 0.5, "num_turns": 3}), ""

        def runner(prompt, model, use_mcp=None, timeout=None, ozerk=False, rol=None):
            self.assertNotIn("[DEPARTMAN:", prompt)
            return ("KARAR: geçti\nGEREKÇE: ok\nKONTROL: -", "s", False)
        from unittest import mock
        with mock.patch.object(ms.modeller, "tam_model", lambda m: "claude-%s-5-5" % m):
            m = ms.kos("deneme", None, ("sonnet", "opus"), 60, denetim=True, kosucu=kosucu, runner=runner)
        self.assertEqual(len(gorulen), 4)                               # 2 görev × 2 model
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "deneme", "1-opus.json")))
        k = json.load(open(os.path.join(self.tmp, "deneme", "1-opus.json"), encoding="utf-8"))
        self.assertEqual(k["puan"], 1.0); self.assertEqual(k["denetim"], "geçti"); self.assertFalse(k["hata"])
        k2 = json.load(open(os.path.join(self.tmp, "deneme", "1-sonnet.json"), encoding="utf-8"))
        self.assertEqual(k2["puan"], 0.0); self.assertEqual(k2["eksik"], ["85", "kota"])
        self.assertIn("| opus | 2 | 100% | 2/2 |", m)
        self.assertIn("| sonnet | 2 | 0% |", m)
        # yeniden koşu var olanı atlar
        ms.kos("deneme", None, ("sonnet", "opus"), 60, denetim=True, kosucu=kosucu, runner=runner)
        self.assertEqual(len(gorulen), 4)
        # körleme + puan
        yollar = ms.kor("deneme")
        self.assertEqual(len(yollar), 2)
        sayfa = open(yollar[0], encoding="utf-8").read()
        self.assertIn("## A", sayfa); self.assertNotIn("opus", sayfa.split("Seçim")[0].split("## A")[1])
        anahtar = json.load(open(os.path.join(self.tmp, "deneme", "anahtar.json"), encoding="utf-8"))
        harf = next(h for h, mdl in anahtar["1"].items() if mdl == "opus")
        self.assertEqual(ms.puan("deneme", 1, harf), "opus")
        self.assertIn("| opus | 2 | 100% | 2/2 |", open(os.path.join(self.tmp, "deneme", "ozet.md"), encoding="utf-8").read())
        with self.assertRaises(ValueError):
            ms.puan("deneme", 1, "D")

    def test_hata_kaydi(self):
        def kosucu(cmd, env, zaman):
            return 1, "", "patladı"
        with unittest.mock.patch.object(ms.modeller, "tam_model", lambda m: m):
            ms.kos("hata", "1", ("sonnet",), 60, denetim=True, kosucu=kosucu)
        k = json.load(open(os.path.join(self.tmp, "hata", "1-sonnet.json"), encoding="utf-8"))
        self.assertTrue(k["hata"]); self.assertNotIn("denetim", k)       # hatalı koşu denetlenmez


if __name__ == "__main__":
    unittest.main()
