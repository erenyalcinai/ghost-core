"""Codex üçüncü göz (Faz 2.9, Sahip onayı 2026-10-29): kota (gün 3 / hafta 10, durum katmanı), uygunluk
kapıları (kurulu/FREEZE/özerk), sır sızdırmayan çalışma kopyası, test kipinde ağ yok, verify hakem kuralı
(yalnız sıkılaştırır), /codex komutu."""
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from tests import common  # noqa: F401

from ghost import codex, durum, governance, komutlar, verify  # noqa: E402


def _kurulu_say():
    return mock.patch.object(codex, "CODEX", "/bin/true")


class Kota(unittest.TestCase):
    def setUp(self):
        durum.sil(codex.DURUM_AD)

    def test_sayac_gun_ve_hafta(self):
        with _kurulu_say():
            for _ in range(3):
                self.assertTrue(codex.uygun()[0])
                codex._sayac("yama", "x", 1.0)
            ok, neden = codex.uygun()
        self.assertFalse(ok)
        self.assertIn("günlük", neden)
        k = codex.kota()
        self.assertEqual((k["gun_n"], k["hafta_n"]), (3, 3))
        self.assertEqual(k["son"]["kullanim"], "yama")

    def test_gun_degisince_gunluk_sifirlanir_haftalik_kalir(self):
        with _kurulu_say(), mock.patch.object(codex, "_gun", lambda: "2026-10-28"):
            codex._sayac("sor", "x", 1)
        with _kurulu_say(), mock.patch.object(codex, "_gun", lambda: "2026-10-29"):
            k = codex.kota()
            self.assertEqual(k["gun_n"], 0)
            self.assertEqual(k["hafta_n"], 1)

    def test_kurulu_degil_freeze_ozerk(self):
        with mock.patch.object(codex, "CODEX", "/yok/codex"):
            self.assertIn("kurulu", codex.uygun()[1])
        with _kurulu_say(), mock.patch.object(governance, "is_frozen", lambda: True):
            self.assertEqual(codex.uygun()[1], "FREEZE")
        with _kurulu_say(), mock.patch.dict(os.environ, {"GHOST_OZERK": "1"}):
            self.assertIn("özerk", codex.uygun()[1])

    def test_kos_test_kipinde_ag_yok_kota_dusmez(self):
        codex.KOSULANLAR.clear()
        with _kurulu_say():
            metin, hata = codex.kos("selam", tempfile.mkdtemp(), "sor")
        self.assertFalse(hata)
        self.assertEqual(codex.KOSULANLAR[-1][0], "sor")
        self.assertEqual(codex.kota()["gun_n"], 0)

    def test_kota_doluysa_kos_atlar(self):
        with _kurulu_say():
            for _ in range(3):
                codex._sayac("sor", "x", 1)
            metin, hata = codex.kos("selam", "/tmp", "sor")
        self.assertTrue(hata)
        self.assertIn("atlandı", metin)


class GirisHatasi(unittest.TestCase):
    def test_oturum_dusmusse_kota_dusmez(self):
        durum.sil(codex.DURUM_AD)

        class _R:
            returncode = 1
            stdout = ""
            stderr = "ERROR codex_login::auth::manager: Failed to refresh token: Your access token could not be refreshed."
        with _kurulu_say(), mock.patch.object(codex, "_test", lambda: False), \
                mock.patch.object(codex.subprocess, "run", lambda *a, **k: _R()), \
                mock.patch.object(governance, "audit", lambda *a, **k: None):
            metin, hata = codex.kos("x", tempfile.mkdtemp(), "yama")
        self.assertTrue(hata)
        self.assertIn("codex login", metin)
        self.assertEqual(codex.kota()["gun_n"], 0)


class CalismaKopyasi(unittest.TestCase):
    def _repo(self):
        d = tempfile.mkdtemp(prefix="codex-test-repo-", dir=os.path.expanduser("~"))
        subprocess.run(["git", "init", "-q", d], check=True)
        os.makedirs(os.path.join(d, "ghost"))
        with open(os.path.join(d, "ghost", "x.py"), "w") as f:
            f.write("print(1)\n")
        with open(os.path.join(d, ".gitignore"), "w") as f:
            f.write("secrets.env\n.env\n")
        with open(os.path.join(d, "secrets.env"), "w") as f:
            f.write("TOKEN=gizli\n")
        with open(os.path.join(d, "ops_owner.p8"), "w") as f:
            f.write("anahtar\n")
        with open(os.path.join(d, "yeni.md"), "w") as f:
            f.write("izlenmeyen ama ignore değil\n")
        return d

    def test_sirlar_girmez_izlenmeyen_girer(self):
        d = self._repo()          # ev dizini altında (codex yalnız oraya bakar)
        try:
            kopya, n = codex.calisma_kopyasi(d)
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(n, 3)   # ghost/x.py, .gitignore, yeni.md
        self.assertTrue(os.path.exists(os.path.join(kopya, "ghost", "x.py")))
        self.assertTrue(os.path.exists(os.path.join(kopya, "yeni.md")))
        self.assertFalse(os.path.exists(os.path.join(kopya, "secrets.env")))
        self.assertFalse(os.path.exists(os.path.join(kopya, "ops_owner.p8")))

    def test_ev_disi_ve_gizli_dizin_reddedilir(self):
        with self.assertRaises(ValueError):
            codex._repo_dogrula("/usr/lib")
        with self.assertRaises(ValueError):
            codex._repo_dogrula("~/.ghost/araclar")
        with self.assertRaises(ValueError):
            codex._repo_dogrula("~/.codex")

    def test_sir_adlari(self):
        for rel in ("ops/.env", "secrets.env", "a/b/AuthKey.p8", "ops/ghost.db", "ops/owner.json", "ops/audit.jsonl"):
            self.assertTrue(codex._sir_mi(rel), rel)
        for rel in ("ghost/notify.py", "README.md", ".gitignore", "ops/env_ornek.md"):
            self.assertFalse(codex._sir_mi(rel), rel)


class VerifyHakem(unittest.TestCase):
    """Claude 'doğrulanamadı' + PERMANENT iş → codex hakem; yalnız sıkılaştırır."""
    def _review(self, claude_out, codex_out, request="deploy et ve sil"):
        runner = lambda p, m, **k: (claude_out, "sid", False)  # noqa: E731
        with mock.patch.object(codex, "uygun", lambda: (True, "")), \
                mock.patch.object(codex, "ucuncu_goz", lambda r, s: (codex_out, False)), \
                mock.patch.object(verify, "log", lambda rec: rec), \
                mock.patch.object(governance, "audit", lambda *a, **k: None):
            return verify.review(request, "yaptım, dosya silindi ve deploy edildi, hepsi tamam oldu", runner, kind="iş", jid=1)

    def test_codex_yanlis_derse_karar_sikilasir(self):
        karar, not_, rec = self._review("KARAR: doğrulanamadı\nGEREKÇE: bakamadım", "KARAR: yanlış\nGEREKÇE: deploy çıktısı yok")
        self.assertEqual(karar, verify.YANLIS)
        self.assertIn("codex", not_)
        self.assertEqual(rec["codex"], verify.YANLIS)

    def test_codex_gecti_derse_belirsiz_kalir(self):
        karar, not_, rec = self._review("KARAR: doğrulanamadı\nGEREKÇE: bakamadım", "KARAR: geçti\nGEREKÇE: çıktı tutarlı")
        self.assertEqual(karar, verify.BELIRSIZ)
        self.assertIn("codex: geçti", not_)

    def test_claude_karar_verdiyse_codex_cagrilmaz(self):
        cagri = []
        runner = lambda p, m, **k: ("KARAR: geçti\nGEREKÇE: tamam", "sid", False)  # noqa: E731
        with mock.patch.object(codex, "uygun", lambda: (True, "")), \
                mock.patch.object(codex, "ucuncu_goz", lambda r, s: cagri.append(1) or ("", False)), \
                mock.patch.object(verify, "log", lambda rec: rec), mock.patch.object(governance, "audit", lambda *a, **k: None):
            verify.review("deploy et", "oldu", runner, kind="iş")
        self.assertEqual(cagri, [])

    def test_ozerk_iste_codex_yok(self):
        cagri = []
        runner = lambda p, m, **k: ("KARAR: doğrulanamadı\nGEREKÇE: x", "sid", False)  # noqa: E731
        with mock.patch.object(codex, "uygun", lambda: (True, "")), \
                mock.patch.object(codex, "ucuncu_goz", lambda r, s: cagri.append(1) or ("", False)), \
                mock.patch.object(verify, "log", lambda rec: rec), mock.patch.object(governance, "audit", lambda *a, **k: None):
            verify.review("deploy et ve sil", "oldu", runner, kind="iş", ozerk=True)
        self.assertEqual(cagri, [])


class KomutCodex(unittest.TestCase):
    class _Ctx:
        def __init__(self):
            self.gonderilen = []

        def send(self, cid, text, **k):
            self.gonderilen.append(text)

    class _Mgr:
        chat_id = 1
        state = {"model": "fable"}

    def test_durum_ve_kota_dolu(self):
        ctx, mgr = self._Ctx(), self._Mgr()
        komutlar.bul("/codex")("", mgr, ctx)
        self.assertIn("Kullanım", ctx.gonderilen[-1])
        with mock.patch.object(codex, "uygun", lambda: (False, "günlük tavan doldu (3)")):
            komutlar.bul("/codex")("bak", mgr, ctx)
        self.assertIn("tavan", ctx.gonderilen[-1])

    def test_gorev_thread_ile_kosar_repo_ayrilir(self):
        ctx, mgr = self._Ctx(), self._Mgr()
        gorulen = {}

        def sahte_sor(gorev, repo=None):
            gorulen.update(gorev=gorev, repo=repo)
            return "bulgu: x", False, None
        import threading
        with mock.patch.object(codex, "uygun", lambda: (True, "")), mock.patch.object(codex, "sor", sahte_sor):
            komutlar.bul("/codex")("testleri incele @~/appb", mgr, ctx)
            for t in threading.enumerate():
                if t is not threading.current_thread() and t.daemon:
                    t.join(5)
        self.assertEqual(gorulen, {"gorev": "testleri incele", "repo": "~/appb"})
        self.assertIn("bulgu: x", ctx.gonderilen[-1])

    def test_motorlar_yedek_yok(self):
        ctx, mgr = self._Ctx(), self._Mgr()
        komutlar.bul("/motorlar")("", mgr, ctx)
        self.assertIn("yedek motor yok", ctx.gonderilen[-1])


if __name__ == "__main__":
    unittest.main()
