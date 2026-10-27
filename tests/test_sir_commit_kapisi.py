"""Sır kapısı (ops/githooks/pre-commit, 2026-10-27): sahnelenen değişiklikte sırrın düz değeri,
özel anahtar ya da sır dosyası varsa commit durur; değer çıktıya basılmaz."""
import os
import subprocess
import tempfile
import unittest

from tests import common  # noqa: F401

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KANCA = os.path.join(KOK, "ops", "githooks", "pre-commit")
DEGER = "fakeTOKENvalue-0123456789abcdef"


def _git(d, *a, **kw):
    return subprocess.run(["git", "-C", d, *a], capture_output=True, text=True, **kw)


class SirKapisi(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        d = self.d = self.t.name
        _git(d, "init", "-q")
        _git(d, "config", "user.email", "t@example.com")
        _git(d, "config", "user.name", "t")
        os.makedirs(os.path.join(d, ".git", "hooks"), exist_ok=True)
        with open(os.path.join(d, "secrets.env"), "w") as f:
            f.write("SOME_API_TOKEN=%s\nCLOUDFLARE_ACCOUNT_ID=public-id-1234567890\n" % DEGER)
        with open(os.path.join(d, ".gitignore"), "w") as f:
            f.write("secrets.env\n")
        _git(d, "config", "core.hooksPath", os.path.dirname(KANCA))

    def tearDown(self):
        self.t.cleanup()

    def _commit(self, ad, icerik, zorla=False):
        with open(os.path.join(self.d, ad), "w") as f:
            f.write(icerik)
        _git(self.d, "add", *(["-f"] if zorla else []), ad)
        return _git(self.d, "commit", "-q", "-m", "x")

    def test_duz_deger_durur_ve_basilmaz(self):
        r = self._commit("betik.sh", "curl -H 'x: %s' https://example.com\n" % DEGER)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("SOME_API_TOKEN", r.stderr)
        self.assertNotIn(DEGER, r.stderr + r.stdout)

    def test_sir_dosyasi_ve_ozel_anahtar_durur(self):
        self.assertNotEqual(self._commit("secrets.env", "X=1\n", zorla=True).returncode, 0)
        _git(self.d, "reset", "-q")
        self.assertNotEqual(self._commit("k.txt", "-----BEGIN OPENSSH " + "PRIVATE KEY-----\n").returncode, 0)

    def test_temiz_degisiklik_ve_gizli_olmayan_deger_gecer(self):
        r = self._commit("not.md", "hesap: public-id-1234567890, anahtar $SOME_API_TOKEN\n")
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
