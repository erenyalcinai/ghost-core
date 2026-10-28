"""Sır tarayıcısı (ops/githooks/sir_tara.py, 2026-10-28 Faz 1 madde 14): pre-commit atlansa da
(hookspath, plumbing) push edilen aralıktaki her commit taranır; değer çıktıya basılmaz.
Sahte token'lar çalışma anında üretilir — bu dosyanın kendisi taramaya takılmaz."""
import importlib.util
import os
import secrets
import string
import subprocess
import tempfile
import unittest

from tests import common  # noqa: F401

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIZIN = os.environ.get("GHOST_SIR_TARA_DIZIN") or os.path.join(KOK, "ops", "githooks")
TARA = os.path.join(DIZIN, "sir_tara.py")
KURULU = os.path.exists(TARA)
ABC = string.ascii_letters + string.digits


def _rast(n, abc=ABC):
    return "".join(secrets.choice(abc) for _ in range(n))


def _yukle():
    spec = importlib.util.spec_from_file_location("sir_tara_test", TARA)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _git(d, *a, **kw):
    return subprocess.run(["git", "-C", d, *a], capture_output=True, text=True, **kw)


@unittest.skipUnless(KURULU, "ops/githooks/sir_tara.py henüz kurulu değil (onaylı kurulum bekliyor)")
class Desenler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = _yukle()

    def test_genel_tokenlar_yakalanir(self):
        ornekler = {
            "GitHub ince ayarlı PAT": "github_pat_" + _rast(30),
            "GitHub token": "ghp_" + _rast(36),
            "Telegram bot token": "%d:%s" % (secrets.randbelow(9 * 10 ** 8) + 10 ** 8, _rast(35, ABC + "_-")),
            "Anthropic anahtarı": "sk-ant-api03-" + _rast(40),
            "OpenAI/sk anahtarı": "sk-proj-" + _rast(40),
            "AWS erişim anahtarı": "AKIA" + _rast(16, string.ascii_uppercase + string.digits),
            "Slack token": "xoxb-" + _rast(24),
            "Cloudflare API token": "CLOUDFLARE_API_TOKEN=" + _rast(40),
        }
        for ad, tok in ornekler.items():
            self.assertIn(ad, self.t.satir_tara("x = '%s'" % tok, {}), ad)

    def test_yer_tutucu_ve_yoksay_isareti(self):
        self.assertEqual(self.t.satir_tara("ghp_" + "x" * 36, {}), [])
        tok = "ghp_" + _rast(36)
        self.assertEqual(self.t.satir_tara("t = '%s'  # sir-tara: yoksay" % tok, {}), [])
        gercek = "gercekDEGER-" + _rast(20)       # gerçek değer işaretle SUSMAZ
        self.assertEqual(self.t.satir_tara(gercek + "  # sir-tara: yoksay", {gercek: "X_TOKEN"}),
                         ["X_TOKEN değeri"])

    def test_temiz_metin(self):
        for s in ("curl -H \"Authorization: Bearer $GHOST_GITHUB_TOKEN\"", "sk-learn ile model",
                  "commit 0123456789abcdef0123456789abcdef01234567", "saat 12:30:45"):
            self.assertEqual(self.t.satir_tara(s, {}), [], s)


@unittest.skipUnless(KURULU, "ops/githooks/sir_tara.py henüz kurulu değil (onaylı kurulum bekliyor)")
class PushKapisi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        k = self.tmp.name
        self.uzak, self.d = os.path.join(k, "uzak.git"), os.path.join(k, "is")
        subprocess.run(["git", "init", "-q", "--bare", self.uzak], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", self.d], check=True)
        for a in (("user.email", "t@example.com"), ("user.name", "t")):
            _git(self.d, "config", *a)
        _git(self.d, "remote", "add", "origin", self.uzak)
        self._yaz("not.md", "ilk\n")
        _git(self.d, "add", "not.md")
        _git(self.d, "commit", "-q", "-m", "ilk")
        _git(self.d, "push", "-q", "origin", "main")
        _git(self.d, "config", "core.hooksPath", DIZIN)       # hook'lar BUNDAN SONRA devrede

    def tearDown(self):
        self.tmp.cleanup()

    def _yaz(self, ad, icerik):
        with open(os.path.join(self.d, ad), "w") as f:
            f.write(icerik)

    def _plumbing_commit(self, ad, icerik, mesaj):
        """pre-commit'e hiç uğramayan commit (hash-object + commit-tree + update-ref)."""
        self._yaz(ad, icerik)
        _git(self.d, "add", ad)
        agac = _git(self.d, "write-tree").stdout.strip()
        ust = _git(self.d, "rev-parse", "HEAD").stdout.strip()
        c = _git(self.d, "commit-tree", agac, "-p", ust, "-m", mesaj).stdout.strip()
        _git(self.d, "update-ref", "refs/heads/main", c)
        _git(self.d, "reset", "-q")

    def _push(self, *a, **env):
        return _git(self.d, "push", "-q", "origin", *a, env={**os.environ, **env})

    def test_plumbing_ile_gelen_sir_push_ta_durur_ve_basilmaz(self):
        tok = "ghp_" + _rast(36)
        self._plumbing_commit("betik.sh", "TOKEN=%s\n" % tok, "sır")
        self._plumbing_commit("betik.sh", "TOKEN=$GHOST_GITHUB_TOKEN\n", "sildim")   # ara commit'te kalır
        r = self._push("main", GHOST_SKIP_TESTS="1")          # test kaçışı sır taramasını ATLAMAZ
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("GitHub token", r.stderr)
        self.assertNotIn(tok, r.stderr + r.stdout)
        self.assertNotEqual(_git(self.uzak, "rev-parse", "main").stdout, _git(self.d, "rev-parse", "main").stdout)

    def test_yeni_dal_ve_temiz_push(self):
        _git(self.d, "checkout", "-q", "-b", "yan")
        self._plumbing_commit("a.md", "sk-ant-api03-%s\n" % _rast(40), "x")
        _git(self.d, "update-ref", "refs/heads/yan", _git(self.d, "rev-parse", "main").stdout.strip())
        self.assertNotEqual(self._push("yan", GHOST_SKIP_TESTS="1").returncode, 0)
        _git(self.d, "checkout", "-q", "main")
        _git(self.d, "reset", "-q", "--hard", "origin/main")
        self._yaz("not.md", "temiz değişiklik\n")
        _git(self.d, "commit", "-qam", "temiz")
        r = self._push("main")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_kacis_kayda_gecer(self):
        self._plumbing_commit("b.md", "ghp_%s\n" % _rast(36), "x")
        r = self._push("main", GHOST_SKIP_SIR="1", GHOST_SKIP_TESTS="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SIR TARAMASI ATLANDI", r.stderr)


if __name__ == "__main__":
    unittest.main()
