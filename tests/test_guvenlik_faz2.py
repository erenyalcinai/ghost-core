"""Güvenlik Faz 2 devamı (2026-10-28, 2. tur denetim): kapı tarafı.
Biçim: izinli iş geçer, gerisi reddedilir. Saldırı senaryosu değil, sınır tanımı."""
import os
import sys
import time
import unittest
from unittest import mock

import tests.common  # noqa: F401

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(KOK, "ops"))
import guvenlik_kapisi as g  # noqa: E402


def bash(k):
    return g.karar({"tool_name": "Bash", "tool_input": {"command": k}})


def arac(ad, **gi):
    return g.karar({"tool_name": ad, "tool_input": gi})


class SirDizini(unittest.TestCase):
    def test_sir_dizini_arsivlenmez_okunmaz_digeri_serbest(self):
        self.assertTrue(bash("tar czf /tmp/y.tgz ~/.secrets"))
        self.assertTrue(bash("tar czf /tmp/y.tgz -C ~ .secrets"))
        self.assertTrue(bash("cd ~/.secrets && tar cz ."))
        self.assertTrue(arac("Grep", pattern="x", path=os.path.expanduser("~/.secrets")))
        self.assertIsNone(bash("tar czf /tmp/y.tgz dossier/"))
        self.assertIsNone(bash("ls ~/jarvis/dossier"))


class OrtamDokumu(unittest.TestCase):
    def test_surec_ortami_okunmaz_ad_ve_normal_ps_serbest(self):
        self.assertTrue(bash("ps eww"))
        self.assertTrue(bash("ps axe"))
        self.assertTrue(arac("Read", file_path="/proc/self/environ"))
        self.assertIsNone(bash("ps -ef | grep claude"))
        self.assertIsNone(bash("ps -o pid,user,etime -p 1"))
        self.assertIsNone(bash("ps aux | head"))

    def test_tirnak_icindeki_set_komut_degil(self):
        self.assertIsNone(bash("grep -rhoE '(send|edit|get|set|delete)[A-Z]+' ghost ops | sort | uniq -c"))
        self.assertTrue(bash("set | head"))


class Ozerk(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"GHOST_OZERK": "1"})
        p.start()
        self.addCleanup(p.stop)

    def test_gelen_kutusu_ve_freeze_yazilmaz_rapor_yazilir(self):
        self.assertTrue(arac("Write", file_path=os.path.join(KOK, "ops", "kokpit_inbox", "m.json"), content="{}"))
        self.assertTrue(arac("Write", file_path=os.path.join(KOK, "ops", "kokpit_kararlar", "k.json"), content="{}"))
        self.assertTrue(arac("Write", file_path=os.path.join(KOK, "ops", "FREEZE"), content=""))
        self.assertTrue(bash("echo '{}' > ops/kokpit_inbox/m.json"))
        self.assertIsNone(arac("Write", file_path=os.path.join(KOK, "dossier", "rapor.md"), content="# r"))

    def test_git_okuma_serbest_yazma_kapali(self):
        for k in ("git tag -l", "git tag", "git merge-base main HEAD", "git log --oneline -3", "git status"):
            self.assertIsNone(bash(k), k)
        for k in ("git tag v1.0", "git tag -d v1.0", "git merge feature", "git commit -m x", "git push"):
            self.assertTrue(bash(k), k)

    def test_yerel_post_yalniz_vekil_ve_gosom(self):
        self.assertIsNone(bash("curl -s -X POST http://127.0.0.1:8797/posthog/api/projects/000000/query/ -d '{}'"))
        self.assertIsNone(bash("curl -s -X POST http://127.0.0.1:8081/api/v1/jobs -d '{}'"))
        self.assertTrue(bash("curl -s -X POST http://127.0.0.1:8765/komut -d '{}'"))


class DorduncuTur(unittest.TestCase):
    def test_yedek_klasoru_sir_sayilir(self):
        self.assertTrue(bash("cat ~/.ghost/yedek-sir/mac-1/x"))
        self.assertTrue(bash("python3 ops/mac.py komut 'cat $HOME/.ghost/yedek-sir/mac-1/x'"))
        self.assertIsNone(bash("ls ~/.ghost/"))

    def test_kalici_dosyaya_kod_govdesinden_yazmak_onay_ister(self):
        self.assertTrue(bash("python3 -c \"open('/home/ghost/.ssh/authorized_keys','a').write('x')\""))
        self.assertTrue(bash("python3 - <<'E'\nfrom pathlib import Path\nPath('/home/ghost/.bashrc').write_text('x')\nE\n"))
        self.assertIsNone(bash("python3 -c \"print(open('/home/ghost/.ssh/authorized_keys').read().count('ssh-'))\""))

    def test_ozerkte_mac_yazma_fiilleri_kapali_okuma_serbest(self):
        with mock.patch.dict(os.environ, {"GHOST_OZERK": "1"}):
            for k in ("python3 ops/mac.py komut 'ls'", "python3 ops/mac.py yaz 'merhaba'", "python3 ops/mac.py tus cmd+n",
                      "python3 ops/mac.py tikla 10 10", "python3 ops/mac_cmd.py 'ls'"):
                self.assertTrue(bash(k), k)
            for k in ("python3 ops/mac.py durum", "python3 ops/mac.py ekran /tmp/e.png", "python3 ops/mac.py pencereler",
                      "python3 ops/mac.py anahtar"):
                self.assertIsNone(bash(k), k)
        self.assertIsNone(bash("python3 ops/mac.py komut 'ls'"))      # gözetimli: serbest


class Korunan(unittest.TestCase):
    def test_dizin_icine_bash_cp_korunur(self):
        # 3. tur: cp/install/rsync ile ops/vekil|onay|githooks İÇİNE yazmak da onay ister (dizin girişi)
        for yol in ("ops/vekil/vekil_servisi.py", "ops/onay/onay_servisi.py", "ops/githooks/pre-push"):
            self.assertTrue(bash("cp /tmp/x ~/jarvis/%s" % yol), yol)     # hazırlık ağacında da gerçek repo yolu
        self.assertIsNone(bash("cp /tmp/x ~/jarvis/dossier/not.md"))

    def test_guven_noktasi_kodu_onay_ister_digeri_serbest(self):
        for yol in ("ops/telegram_bridge.py", "ghost/api.py", "ghost/kokpit.py", "ghost/kurallar.py",
                    "ghost/bus.py", "ops/vekil/vekil_servisi.py"):
            self.assertTrue(arac("Edit", file_path=os.path.join(KOK, yol), old_string="a", new_string="b"), yol)
        self.assertIsNone(arac("Edit", file_path=os.path.join(KOK, "ghost", "masa.py"), old_string="a", new_string="b"))
        self.assertIsNone(arac("Edit", file_path=os.path.join(KOK, "tests", "test_guvenlik_kapisi.py"),
                               old_string="a", new_string="b"))

    def test_komut_ozeti_sahne_ile_ayni(self):
        from ghost import sahne
        for k in ("cp a b", "cp  a   b ; : onay-0011223344556677", "x && : onay-0011223344556677",
                  "ssh h 'ls ; : onay-0011223344556677'"):
            self.assertEqual(g._komut_ozeti(k), sahne.komut_ozeti(k), k)
        self.assertEqual(g._komut_ozeti("cp a b ; : onay-0011223344556677"), g._komut_ozeti("cp a b"))
        self.assertNotEqual(g._komut_ozeti("cp a b"), g._komut_ozeti("cp a c"))


class OnayKomutKarti(unittest.TestCase):
    def test_tek_tirnakli_kart_acmak_serbest_genisleme_ve_zincir_taranir(self):
        kopya = "cp ~/.faz5agac/ops/vekil/vekil_servisi.py ~/jarvis/ops/vekil/"   # bash kuralı yol METNİNE bakar
        self.assertIsNone(bash("python3 -m ghost.sahne onay-komut 'Kopya' '%s'" % kopya))
        self.assertIsNone(bash("cd ~/jarvis && python3 -m ghost.sahne onay-komut 'K' '%s' 'açıklama'" % kopya))
        self.assertTrue(bash('python3 -m ghost.sahne onay-komut "K" "$(%s)"' % kopya))      # çift tırnak: genişler
        self.assertTrue(bash("python3 -m ghost.sahne onay-komut 'K' 'x' ; %s" % kopya))       # zincir: kart değil
        self.assertTrue(bash(kopya))                                                       # komutun kendisi onay ister


class Hiz(unittest.TestCase):
    def test_buyuk_heredoc_kanca_tavaninin_altinda(self):
        k = "python3 - <<'E'\n" + "print(1)\n" * 20000 + "E\n"
        t = time.time()
        bash(k)
        self.assertLess(time.time() - t, 3.0)


if __name__ == "__main__":
    unittest.main()
