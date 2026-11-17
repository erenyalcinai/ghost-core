"""Araçsız alt model çağrısının dar ortamı (2026-11-15, kurucu §8.1): abonelik kipinde yalnız belirteç,
API anahtarı kipinde yalnız anahtar, ikisi de yokken kimliksiz; her kipte HOME/PATH/LANG dışında değişken
taşınmaz, kimlik isteme ve komut satırına girmez."""
import os
import subprocess
import sys
import unittest
from unittest import mock

from tests import common  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops"))
import karantina  # noqa: E402
from ghost import epizod, modeller  # noqa: E402

BELIRTEC, ANAHTAR = "sahte-abonelik-belirteci-test", "sahte-api-anahtari-test"
B, A = "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"
TEMEL = {"HOME": "/ev", "PATH": "/bin", "LANG": "C.UTF-8"}
# Alt sürece ASLA geçmemesi gereken türden değişkenler (ad örnekleri; değerler sahte)
GURULTU = {"TELEGRAM_BOT_TOKEN": "sahte-telegram", "GHOST_OPS": "/x", "CLOUDFLARE_API_TOKEN": "sahte-cf",
           "JARVIS_CLAUDE_ARGS": "--x", "ANTHROPIC_BASE_URL": "http://baska.invalid"}
IZINLI = set(TEMEL) | {B, A}


class AltModelEnv(unittest.TestCase):
    def _env(self, ortam=None, kopru=None):
        return modeller.alt_model_env(kopru or {}, ortam=dict(TEMEL, **GURULTU, **(ortam or {})))

    def test_abonelik_kipi_yalniz_belirtec(self):
        for env in (self._env(ortam={B: BELIRTEC}), self._env(kopru={B: BELIRTEC})):
            self.assertEqual(env, dict(TEMEL, **{B: BELIRTEC}))

    def test_api_kipi_yalniz_anahtar(self):
        for env in (self._env(ortam={A: ANAHTAR}), self._env(kopru={A: ANAHTAR})):
            self.assertEqual(env, dict(TEMEL, **{A: ANAHTAR}))

    def test_ikisi_de_yok_kimliksiz(self):
        self.assertEqual(self._env(), TEMEL)
        self.assertEqual(self._env(kopru={B: "", A: ""}), TEMEL)           # boş değer kimlik sayılmaz

    def test_belirtec_varken_anahtar_tasinmaz(self):
        """Canlı (abonelikli) kurulumun davranışı: anahtar nerede durursa dursun alt sürece gitmez."""
        for ortam, kopru in (({B: BELIRTEC, A: ANAHTAR}, {}), ({A: ANAHTAR}, {B: BELIRTEC}),
                             ({B: BELIRTEC}, {A: ANAHTAR}), ({}, {B: BELIRTEC, A: ANAHTAR})):
            env = self._env(ortam=ortam, kopru=kopru)
            self.assertEqual(env.get(B), BELIRTEC)
            self.assertNotIn(A, env)

    def test_surec_ortami_dosyadan_once(self):
        self.assertEqual(self._env(ortam={B: "ortamdan"}, kopru={B: "dosyadan"})[B], "ortamdan")
        self.assertEqual(self._env(ortam={A: "ortamdan"}, kopru={A: "dosyadan"})[A], "ortamdan")

    def test_baska_degisken_girmez(self):
        kopru = dict(GURULTU, **{A: ANAHTAR, "TELEGRAM_OWNER_ID": "1", "CLAUDE_BIN": "/x/claude"})
        self.assertLessEqual(set(self._env(kopru=kopru)), IZINLI)
        self.assertEqual(set(self._env(kopru=kopru)), set(TEMEL) | {A})

    def test_eski_kalipla_ayni_abonelikte(self):
        """Düzeltmeden önceki dört satırlık kalıp ile birebir aynı sonuç (belirteç olan her durumda)."""
        def eski(ortam, kopru):
            env = {k: ortam[k] for k in ("HOME", "PATH", "LANG", B) if ortam.get(k)}
            if not env.get(B) and kopru.get(B):
                env[B] = kopru[B]
            return env
        for ortam, kopru in (({B: BELIRTEC}, {}), ({}, {B: BELIRTEC}), ({B: "o"}, {B: "d"}),
                             ({B: BELIRTEC, A: ANAHTAR}, {A: ANAHTAR}), ({"HOME": ""}, {B: BELIRTEC})):
            tam = dict(TEMEL, **GURULTU, **ortam)
            self.assertEqual(modeller.alt_model_env(kopru, ortam=tam), eski(tam, kopru))

    def test_kopru_okunamazsa_surec_ortami(self):
        with mock.patch.object(modeller.config, "load_env", side_effect=OSError("okunamadı")):
            self.assertEqual(modeller.alt_model_env(ortam=dict(TEMEL, **{A: ANAHTAR})), dict(TEMEL, **{A: ANAHTAR}))


class CagriYerleri(unittest.TestCase):
    """Açık çekirdekteki iki çağrı yeri (epizod, karantina) üç kipte doğru ortamı kuruyor."""

    def _ortam(self, **ek):
        temiz = {k: v for k, v in os.environ.items() if k not in (B, A)}
        return mock.patch.dict(os.environ, dict(temiz, **GURULTU, **ek), clear=True)

    def _kipler(self):
        return (({B: BELIRTEC}, B, BELIRTEC), ({A: ANAHTAR}, A, ANAHTAR), ({}, None, None))

    def test_epizod(self):
        for kopru, ad, deger in self._kipler():
            with self._ortam(), mock.patch.object(epizod.config, "load_env", return_value=dict(kopru, CLAUDE_BIN="/x/claude")):
                argv, env = epizod._model_komut("İSTEM")
            self.assertLessEqual(set(env), IZINLI)
            self.assertEqual({k: env[k] for k in (B, A) if k in env}, {ad: deger} if ad else {})
            self.assertFalse([p for p in argv if BELIRTEC in p or ANAHTAR in p])   # kimlik komut satırında yok
            self.assertIn("--tools", argv); self.assertEqual(argv[argv.index("--tools") + 1], "")

    def test_karantina(self):
        for kopru, ad, deger in self._kipler():
            gorulen = {}

            def sahte_run(argv, **kw):
                gorulen.update(argv=argv, env=kw.get("env"), cwd=kw.get("cwd"))
                return subprocess.CompletedProcess(argv, 0, "Özet.\nTALİMAT: hayır\n", "")
            from ghost import config
            with self._ortam(), mock.patch.object(config, "load_env", return_value=dict(kopru, CLAUDE_BIN="/x/claude")), \
                    mock.patch.object(karantina.subprocess, "run", side_effect=sahte_run):
                cikti, talimat = karantina.oku_karantinada("dış metin", "ne diyor?")
            env, argv = gorulen["env"], gorulen["argv"]
            self.assertLessEqual(set(env), IZINLI)
            self.assertEqual({k: env[k] for k in (B, A) if k in env}, {ad: deger} if ad else {})
            self.assertFalse([p for p in argv if BELIRTEC in p or ANAHTAR in p])   # istem + bayraklar: kimlik yok
            self.assertEqual(argv[argv.index("--tools") + 1], "")                  # araçsız
            self.assertIn("--strict-mcp-config", argv)
            self.assertNotIn(BELIRTEC, cikti); self.assertNotIn(ANAHTAR, cikti)
            self.assertIs(talimat, False)

    def test_karantina_surec_ortamindaki_anahtar(self):
        """Dosyada kimlik yok, süreç ortamında API anahtarı var (systemd EnvironmentFile yolu)."""
        gorulen = {}

        def sahte_run(argv, **kw):
            gorulen["env"] = kw.get("env")
            return subprocess.CompletedProcess(argv, 0, "Özet.\nTALİMAT: hayır\n", "")
        from ghost import config
        with self._ortam(**{A: ANAHTAR}), mock.patch.object(config, "load_env", return_value={}), \
                mock.patch.object(karantina.subprocess, "run", side_effect=sahte_run):
            karantina.oku_karantinada("x", "")
        self.assertEqual(gorulen["env"].get(A), ANAHTAR)
        self.assertNotIn(B, gorulen["env"])
        self.assertLessEqual(set(gorulen["env"]), IZINLI)


if __name__ == "__main__":
    unittest.main()
