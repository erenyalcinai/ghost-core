"""Kalıcı sohbet süreci (ghost/kalici_oturum.py): tek süreç, çok tur.

Sahte bir `claude` (python betiği) stream-json konuşur: her user satırına
bir assistant + bir result satırı basar; "uyu" derse cevap vermez (zaman aşımı
testi); "öl" derse çıkar (EOF testi).
"""
import json
import os
import stat
import sys
import tempfile
import unittest

from tests import common  # noqa: F401

from ghost import kalici_oturum

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "ops"))
import telegram_bridge as tb  # noqa: E402
from ghost import motor  # noqa: E402  # noqa: E402

SAHTE = r'''#!/usr/bin/env python3
import json, sys, time, os
args = sys.argv[1:]
model = args[args.index("--model") + 1] if "--model" in args else "?"
sid = args[args.index("--session-id") + 1] if "--session-id" in args else (
    args[args.index("--resume") + 1] if "--resume" in args else "yeni-sid")
print(json.dumps({"type": "system", "subtype": "init", "model": model, "session_id": sid}), flush=True)
n = 0
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)["message"]["content"]
    n += 1
    if msg == "uyu":
        time.sleep(30)
    if msg == "uyu-kisa":
        time.sleep(1.5)
    if msg == "öl":
        sys.exit(3)
    print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "cevap %d: %s" % (n, msg)}]}}), flush=True)
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                      "result": "cevap %d: %s (pid %d)" % (n, msg, os.getpid()),
                      "session_id": sid, "usage": {"input_tokens": 5, "cache_read_input_tokens": 100},
                      "modelUsage": {}, "total_cost_usd": 0.001}), flush=True)
'''


def _sahte_claude():
    d = tempfile.mkdtemp(prefix="sahte-claude-")
    p = os.path.join(d, "claude")
    with open(p, "w", encoding="utf-8") as f:
        f.write(SAHTE)
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
    return p


class TestKaliciOturum(unittest.TestCase):
    def setUp(self):
        self.bin = _sahte_claude()
        kalici_oturum.DURUM_DOSYASI = os.path.join(tempfile.mkdtemp(), "kalici.json")
        self.k = kalici_oturum.KaliciOturum(self.bin, [], bosta_sn=0)

    def tearDown(self):
        self.k.kapat()

    def _sonuc(self, satir):
        return json.loads(satir)

    def test_ayni_surecte_iki_tur(self):
        self.k.baslat("fable", "sid-1", resume=False)
        pid = self.k.proc.pid
        s1, _, _, za, oldu = self.k.tur("merhaba", 10)
        s2, _, _, _, _ = self.k.tur("yine", 10)
        self.assertFalse(za or oldu)
        self.assertIn("cevap 1: merhaba", self._sonuc(s1)["result"])
        self.assertIn("cevap 2: yine", self._sonuc(s2)["result"])
        self.assertIn("pid %d" % pid, self._sonuc(s2)["result"])   # süreç değişmedi
        self.assertTrue(self.k.uygun("fable", "sid-1"))

    def test_gec_sonuc_bir_sonraki_turun_cevabi_sanilmaz(self):
        """Harness devam turu (arka plan görevi bitince) boşta ikinci bir result basar;
        köprü onu geç sonuç olarak teslim eder, sonraki tur kendi cevabını alır."""
        gelen = []
        self.k.on_gec_sonuc = gelen.append
        self.k.baslat("fable", "sid-1", resume=False)
        s1, _, _, _, _ = self.k.tur("merhaba", 10)
        self.assertIn("cevap 1", self._sonuc(s1)["result"])
        # devam turu: köprü değil, süreç kendi kendine bir tur daha koşuyor
        self.k.proc.stdin.write(json.dumps({"type": "user", "message": {
            "role": "user", "content": "arka plan bitti"}}) + "\n")
        self.k.proc.stdin.flush()
        import time as _t
        for _ in range(50):
            if self.k.bosta_oku():
                break
            _t.sleep(0.1)
        self.assertEqual(len(gelen), 1)
        self.assertIn("cevap 2: arka plan bitti", self._sonuc(gelen[0])["result"])
        s3, _, _, _, _ = self.k.tur("yine", 10)
        self.assertIn("cevap 3: yine", self._sonuc(s3)["result"])   # kaymadı
        self.assertEqual(self.k.bosta_oku(), [])

    def test_model_ya_da_oturum_degisince_uygun_degil(self):
        self.k.baslat("fable", "sid-1", resume=False)
        self.assertFalse(self.k.uygun("opus", "sid-1"))
        self.assertFalse(self.k.uygun("fable", "sid-2"))

    def test_olaylar_ve_sentetik_baslangic(self):
        self.k.baslat("fable", "sid-1", resume=False)
        olaylar = []
        self.k.tur("selam", 10, on_event=olaylar.append)
        turler = [json.loads(o).get("type") for o in olaylar]
        self.assertEqual(turler[0], "system")           # her turda sentetik init
        self.assertIn("assistant", turler)
        self.assertEqual(turler[-1], "result")

    def test_zaman_asimi_sureci_oldurur(self):
        self.k.baslat("fable", "sid-1", resume=False)
        s, _, _, za, oldu = self.k.tur("uyu", 1)
        self.assertIsNone(s)
        self.assertTrue(za)
        self.assertFalse(self.k.canli())
        self.assertFalse(kalici_oturum.durum_oku().get("pid"))

    def test_surec_olurse_oldu_bayragi(self):
        self.k.baslat("fable", "sid-1", resume=False)
        s, _, _, za, oldu = self.k.tur("öl", 10)
        self.assertIsNone(s)
        self.assertTrue(oldu)
        self.assertFalse(za)

    def test_durum_dosyasi_bosta_mesgul(self):
        self.k.baslat("fable", "sid-1", resume=False)
        d = kalici_oturum.durum_oku()
        self.assertEqual(d["pid"], self.k.proc.pid)
        self.assertFalse(d["mesgul"])
        self.k.tur("x", 10)
        self.assertFalse(kalici_oturum.durum_oku()["mesgul"])

    def test_resume_bayragi_komuta_yansir(self):
        self.k.baslat("fable", "sid-9", resume=True)
        s, _, _, _, _ = self.k.tur("a", 10)
        self.assertEqual(self._sonuc(s)["session_id"], "sid-9")


class TestRunClaudeKalici(unittest.TestCase):
    def setUp(self):
        kalici_oturum.DURUM_DOSYASI = os.path.join(tempfile.mkdtemp(), "kalici.json")
        self.k = kalici_oturum.KaliciOturum(_sahte_claude(), [], bosta_sn=0)

    def tearDown(self):
        self.k.kapat()

    def test_run_claude_kalici_yolu(self):
        usage = []
        res, sid, err = tb.run_claude("selam", "fable", session_id="s1", resume=False,
                                      timeout=10, on_usage=usage.append, kalici=self.k)
        self.assertFalse(err)
        self.assertIn("cevap 1: selam", res)
        self.assertEqual(sid, "s1")
        self.assertEqual(usage[0]["cost"], 0.001)
        pid = self.k.proc.pid
        res2, _, _ = tb.run_claude("devam", "fable", session_id="s1", resume=True,
                                   timeout=10, kalici=self.k)
        self.assertIn("pid %d" % pid, res2)          # aynı süreç
        # /model → farklı model → süreç yenilenir
        res3, _, _ = tb.run_claude("x", "opus", session_id="s1", resume=True,
                                   timeout=10, kalici=self.k)
        self.assertNotIn("pid %d" % pid, res3)
        self.assertIn("cevap 1: x", res3)

    def test_zaman_asimi_isareti(self):
        res, sid, err = tb.run_claude("uyu", "fable", session_id="s1", resume=False,
                                      timeout=1, kalici=self.k)
        self.assertTrue(err)
        self.assertTrue(motor.is_timeout(res))

    def test_surec_olunce_hata(self):
        res, sid, err = tb.run_claude("öl", "fable", session_id="s1", resume=False,
                                      timeout=10, kalici=self.k)
        self.assertTrue(err)
        self.assertIn("Hata", res)


class TestSelfcheckBosta(unittest.TestCase):
    def test_bosta_kalici_cocuk_sayilmaz(self):
        import selfcheck
        kalici_oturum.DURUM_DOSYASI = os.path.join(tempfile.mkdtemp(), "kalici.json")
        selfcheck.JOBS_DIR = tempfile.mkdtemp()
        # Kendimizi "köprü" sayalım: çocuk olarak sahte claude'u açıp durum dosyasına yazalım.
        k = kalici_oturum.KaliciOturum(_sahte_claude(), [], bosta_sn=0)
        k.baslat("fable", "s", resume=False)
        try:
            self.assertTrue(selfcheck._kopru_bosta(os.getpid()))   # boşta: çocuk var ama meşgul değil
            kalici_oturum._durum_yaz(k.proc.pid, True, "fable", "s")
            self.assertFalse(selfcheck._kopru_bosta(os.getpid()))  # meşgul: boşta değil
        finally:
            k.kapat()


if __name__ == "__main__":
    unittest.main()
