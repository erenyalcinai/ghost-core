"""mem_backup.tesisat() — felaket kurtarma tesisatı (2026-11-04, uzun iş #7 adım 5).

Gerçek /etc'ye ve systemctl'e dokunmaz: kaynak dizin sabitleri geçici ağaca,
komut koşucusu (_cmd) sahteye çevrilir."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest

from tests import common  # noqa: F401  (GHOST_TEST, yollar)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPS = os.path.join(BASE, "ops")


def _yukle():
    if OPS not in sys.path:
        sys.path.insert(0, OPS)
    spec = importlib.util.spec_from_file_location("mem_backup_tesisat_test",
                                                  os.path.join(OPS, "mem_backup.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dosya(yol, metin="x\n", mod=None):
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    with open(yol, "w", encoding="utf-8") as f:
        f.write(metin)
    if mod is not None:
        os.chmod(yol, mod)


class SahteKomut:
    """_cmd yerine: argümana göre sabit çıktı; kayıt tutar."""

    def __init__(self):
        self.cagrilar = []

    def __call__(self, args, env=None, timeout=60):
        self.cagrilar.append((list(args), env))
        a = " ".join(args)
        if args[:2] == ["crontab", "-l"]:
            return "*/15 * * * * echo a\n0 8 * * * echo b\n"
        if "is-enabled" in args:
            return "disabled\n" if "sohbet" in args[-1] else "enabled\n"
        if "list-unit-files" in args:
            return "ghost-bridge.service enabled enabled\nnginx.service enabled enabled\n"
        if args[:2] == ["loginctl", "show-user"]:
            return "Linger=yes\n"
        if a == "apt-mark showmanual":
            return "nginx\nffmpeg\n"
        if "pip" in args:
            return "httpx==0.27.0\n"
        if args[:2] == ["npm", "ls"]:
            return "/usr/lib\n└── @openai/codex@1.0.0\n"
        return None   # snap vb. yok


class TesisatTest(unittest.TestCase):
    def setUp(self):
        self.mb = _yukle()
        td = tempfile.TemporaryDirectory(prefix="tesisat-")
        self.addCleanup(td.cleanup)
        self.kok = k = td.name
        # sistem birimleri
        _dosya(os.path.join(k, "etc/systemd/system/ghost-bridge.service"))
        _dosya(os.path.join(k, "etc/systemd/system/ghost-brief.timer"))
        _dosya(os.path.join(k, "etc/systemd/system/nginx.service"))   # ghost-* değil → alınmaz
        # kullanıcı birimleri
        ku = os.path.join(k, "home/.config/systemd/user")
        for ad in ("ghost-api.service", "ghost-nabiz.service", "ghost-sohbet.service",
                   "ghost-tunnelwatch.timer"):
            _dosya(os.path.join(ku, ad))
        # nginx
        for ad in ("ghost-hooks", "ghost-app", "default"):
            _dosya(os.path.join(k, "etc/nginx/sites-available", ad))
        os.makedirs(os.path.join(k, "etc/nginx/sites-enabled"))
        for ad in ("ghost-hooks", "ghost-app"):
            os.symlink("../sites-available/" + ad, os.path.join(k, "etc/nginx/sites-enabled", ad))
        # conf
        self.dropin = os.path.join(k, "etc/systemd/system.control/ghost-bridge.service.d/50-MemoryHigh.conf")
        _dosya(self.dropin, "[Service]\nMemoryHigh=2600M\n")
        self.sshd = os.path.join(k, "etc/ssh/sshd_config.d/10-ghost-hardening.conf")
        _dosya(self.sshd, "PasswordAuthentication no\n")
        self.yabanci = os.path.join(k, "etc/ssh/sshd_config.d/50-cloud-init.conf")   # ghost değil
        _dosya(self.yabanci)
        self.kilitli = os.path.join(k, "etc/ufw/user.rules")
        _dosya(self.kilitli, "kural\n", mod=0)
        self.addCleanup(os.chmod, self.kilitli, 0o600)
        self.buyuk = os.path.join(k, "etc/needrestart/conf.d/ghost.conf")
        _dosya(self.buyuk, "a" * 64)
        # araçlar
        lb = os.path.join(k, "home/.local/bin")
        _dosya(os.path.join(lb, "uv"))
        os.symlink(os.path.join(lb, "uv"), os.path.join(lb, "claude"))

        self.mb.SISTEM_BIRIM_DIZIN = os.path.join(k, "etc/systemd/system")
        self.mb.KULLANICI_BIRIM_DIZIN = ku
        self.mb.NGINX_DIZIN = os.path.join(k, "etc/nginx")
        self.mb.CONF_DESENLERI = [
            os.path.join(k, "etc/systemd/system.control/ghost-*.d/*.conf"),
            os.path.join(k, "etc/ssh/sshd_config.d/10-ghost-*.conf"),
            self.buyuk,
            self.kilitli,
            os.path.join(k, "etc/yok/olmayan.conf"),
        ]
        self.mb.ARAC_DIZINLERI = [lb, os.path.join(k, "home/.ghost/araclar")]
        self.sahte = SahteKomut()
        self.mb._cmd = self.sahte
        self.stage = os.path.join(k, "stage")
        os.makedirs(self.stage)

    def _kos(self):
        man = self.mb.tesisat(self.stage)
        return dict(man), os.path.join(self.stage, "tesisat")

    def _oku(self, *p):
        with open(os.path.join(*p), encoding="utf-8") as f:
            return f.read()

    def test_sistem_birimleri_ve_tam_liste(self):
        man, t = self._kos()
        self.assertEqual(man["tesisat/systemd/"], 2)
        self.assertEqual(sorted(os.listdir(os.path.join(t, "systemd"))),
                         ["BIRIMLER.txt", "DURUM.txt", "ghost-bridge.service", "ghost-brief.timer"])
        self.assertIn("ghost-bridge.service\tenabled", self._oku(t, "systemd", "DURUM.txt"))
        self.assertIn("nginx.service", self._oku(t, "systemd", "BIRIMLER.txt"))

    def test_kullanici_birimleri_enabled_ve_linger(self):
        man, t = self._kos()
        self.assertEqual(man["tesisat/systemd-kullanici/"], 4)
        durum = self._oku(t, "systemd-kullanici", "DURUM.txt")
        self.assertTrue(durum.startswith("# linger\tyes"))
        self.assertIn("ghost-api.service\tenabled", durum)
        self.assertIn("ghost-sohbet.service\tdisabled", durum)
        self.assertIn("ghost-tunnelwatch.timer\tenabled", durum)
        # kullanıcı systemctl'i --user + XDG_RUNTIME_DIR ile çağrılır
        kul = [(a, e) for a, e in self.sahte.cagrilar if a[:2] == ["systemctl", "--user"]]
        self.assertEqual(len(kul), 4)
        self.assertTrue(all(e and e.get("XDG_RUNTIME_DIR") for _, e in kul))

    def test_nginx_yalniz_ghost_ve_enabled_listesi(self):
        man, t = self._kos()
        self.assertEqual(man["tesisat/nginx/"], 2)
        self.assertNotIn("default", os.listdir(os.path.join(t, "nginx")))
        self.assertEqual(self._oku(t, "nginx", "ENABLED.txt").split(), ["ghost-app", "ghost-hooks"])

    def test_conf_ayna_yol_ve_okunamadi(self):
        if os.geteuid() == 0:
            self.skipTest("root her dosyayı okur")
        man, t = self._kos()
        cf = os.path.join(t, "conf")
        self.assertEqual(self._oku(cf, self.dropin.lstrip("/")), "[Service]\nMemoryHigh=2600M\n")
        self.assertTrue(os.path.exists(os.path.join(cf, self.sshd.lstrip("/"))))
        self.assertFalse(os.path.exists(os.path.join(cf, self.yabanci.lstrip("/"))))
        oku = self._oku(cf, "OKUNAMADI.txt")
        self.assertIn(self.kilitli + "\tokunamadı (root)", oku)
        self.assertNotIn("olmayan.conf", oku)          # yok olan dosya sessiz geçilir
        self.assertEqual(man["tesisat/conf/"], 3)      # drop-in + sshd + needrestart

    def test_conf_tavani(self):
        self.mb.CONF_TAVAN = 10
        man, t = self._kos()
        self.assertIn(self.buyuk + "\tçok büyük", self._oku(t, "conf", "OKUNAMADI.txt"))
        self.assertFalse(os.path.exists(os.path.join(t, "conf", self.buyuk.lstrip("/"))))

    def test_paket_listeleri_ve_araclar(self):
        man, t = self._kos()
        pk = os.path.join(t, "paketler")
        self.assertEqual(self._oku(pk, "apt-manual.txt"), "nginx\nffmpeg\n")
        self.assertIn("httpx==0.27.0", self._oku(pk, "pip-user.txt"))
        self.assertIn("@openai/codex", self._oku(pk, "npm-global.txt"))
        self.assertFalse(os.path.exists(os.path.join(pk, "snap.txt")))   # komut yok → dosya yok
        araclar = self._oku(pk, "araclar.txt")
        self.assertIn("uv\n", araclar)
        self.assertIn("claude\t→ ", araclar)
        self.assertEqual(man["tesisat/paketler/"], 4)

    def test_crontab_ve_kurulum_notu(self):
        man, t = self._kos()
        self.assertEqual(man["tesisat/crontab.txt"], 2)
        not_ = self._oku(t, "KURULUM.md")
        self.assertEqual(not_, self.mb.KURULUM_NOTU)
        for parca in ("playbooks/felaket-kurtarma.md", "sirlar.env", "veri_tasi.py",
                      "systemd-kullanici", "linger", "KÖPRÜ EN SON", "OKUNAMADI.txt",
                      "ghost_runtime.pub"):
            self.assertIn(parca, not_)

    def test_komutlar_yokken_dusmez(self):
        self.mb._cmd = lambda *a, **k: None
        man, t = self._kos()
        self.assertNotIn("tesisat/crontab.txt", man)
        self.assertEqual(man["tesisat/paketler/"], 1)          # yalnız araclar.txt
        self.assertIn("ghost-api.service\t?", self._oku(t, "systemd-kullanici", "DURUM.txt"))
        self.assertTrue(os.path.exists(os.path.join(t, "ortam.txt")))

    def test_gercek_cmd_olmayan_komut(self):
        mb = _yukle()
        self.assertIsNone(mb._cmd(["olmayan-komut-ghost-xyz"]))
        self.assertEqual(mb._cmd([sys.executable, "-c", "print('a')"]), "a\n")

    def test_sir_dosyasi_desenlerde_yok(self):
        mb = _yukle()
        for d in mb.CONF_DESENLERI:
            for yasak in ("secrets", "sirlar", ".env", ".key", ".p8", "private"):
                self.assertNotIn(yasak, d.lower(), d)

    def test_manifest_json_serilesir(self):
        man = self.mb.tesisat(self.stage)
        json.dumps([{"yol": r, "boyut": s} for r, s in man])
        self.assertTrue(all(r.startswith("tesisat/") for r, _ in man))


class SnapshotEvTest(unittest.TestCase):
    """snapshot(): FILE_DESENLERI + EV_DIRS (2026-11-04). Gerçek BASE/ev dizinine dokunmaz."""

    def setUp(self):
        self.mb = _yukle()
        self.tmp = tempfile.TemporaryDirectory()
        k = self.tmp.name
        self.base = os.path.join(k, "base")
        self.ev = os.path.join(k, "ev")
        self.stage = os.path.join(k, "stage")
        os.makedirs(self.stage)
        _dosya(os.path.join(self.base, "secrets.env"), "A=1\n")
        _dosya(os.path.join(self.base, ".env"), "W=1\n")
        _dosya(os.path.join(self.base, "mcp-sentry.json"), "{}")
        _dosya(os.path.join(self.base, "mcp.json"), "{}")
        _dosya(os.path.join(self.ev, ".secrets", "AuthKey_X.p8"), "k")
        _dosya(os.path.join(self.ev, ".claude", "projects", "-home-ghost-jarvis", "memory", "MEMORY.md"), "m")
        _dosya(os.path.join(self.ev, "site-tools", "purify.py"), "p")
        _dosya(os.path.join(self.ev, "site-tools", "__pycache__", "purify.cpython.pyc"), "c")
        self.mb.BASE = self.base
        self.mb.EV_KOK = self.ev
        self.mb.DBS = []
        self.mb.DIRS = []

    def tearDown(self):
        self.tmp.cleanup()

    def test_dosyalar_desenler_ve_ev_dizinleri(self):
        man = dict(self.mb.snapshot(self.stage))
        for rel in ["secrets.env", ".env", "mcp-sentry.json", "mcp.json"]:
            self.assertIn(rel, man)
            self.assertTrue(os.path.isfile(os.path.join(self.stage, rel)))
        self.assertTrue(os.path.isfile(os.path.join(self.stage, "ev", ".secrets", "AuthKey_X.p8")))
        self.assertTrue(os.path.isfile(os.path.join(
            self.stage, "ev", ".claude", "projects", "-home-ghost-jarvis", "memory", "MEMORY.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.stage, "ev", "site-tools", "purify.py")))
        self.assertFalse(os.path.exists(os.path.join(self.stage, "ev", "site-tools", "__pycache__")))
        self.assertEqual(man["ev/.secrets/"], 1)

    def test_eksik_ev_dizini_dusmez(self):
        self.mb.EV_DIRS = ["yok-boyle-bir-dizin"]
        man = self.mb.snapshot(self.stage)
        self.assertFalse(any(r.startswith("ev/") for r, _ in man))

    def test_mutlak_ve_ust_yol_stage_disina_yazmaz(self):
        disari = os.path.join(self.tmp.name, "disari")
        _dosya(os.path.join(disari, "x.txt"), "x")
        self.mb.EV_DIRS = [disari, "../disari", ".secrets/../../disari"]
        once = sorted(os.listdir(self.tmp.name))
        man = self.mb.snapshot(self.stage)
        self.assertFalse(any(r.startswith("ev/") for r, _ in man))
        self.assertEqual(sorted(os.listdir(self.tmp.name)), once)
        self.assertEqual(os.listdir(disari), ["x.txt"])

    def test_desen_kopyasi_tekrarlanmaz(self):
        self.mb.FILES = list(self.mb.FILES) + ["mcp.json"]
        man = self.mb.snapshot(self.stage)
        self.assertEqual([r for r, _ in man].count("mcp.json"), 1)


class TmpKokDisktedirTest(unittest.TestCase):
    """2026-11-09: dış yedek /tmp'de (tmpfs=RAM, usrquota) 'Disk quota exceeded' ile düştü → geçici arşiv ops/backups/.tmp'de.

    Mutlak '/tmp' karşılaştırması YOK: pre-push kapısı ağacı /tmp'ye kopyalayıp koşar, orada
    repo'nun kendisi /tmp altındadır. Değişmez olan şey: geçici kök repo'ya GÖRE ops/backups/.tmp."""

    def test_tmp_kok_repo_icinde(self):
        mod = _yukle()
        self.assertEqual(os.path.relpath(mod.TMP_KOK, mod.BASE), os.path.join("ops", "backups", ".tmp"))
        self.assertEqual(os.path.dirname(mod.TMP_KOK), os.path.join(mod.OPS, "backups"))
        self.assertNotEqual(os.path.realpath(mod.TMP_KOK), os.path.realpath(tempfile.gettempdir()))
        import inspect
        self.assertIn("dir=TMP_KOK", inspect.getsource(mod.main))


if __name__ == "__main__":
    unittest.main()
