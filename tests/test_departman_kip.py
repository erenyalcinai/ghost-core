"""Faz 4 (2026-11-08): iki kip — tek (persona öneki) / departman (gerçek ajan: --agents/--agent),
işaret çözümü motor'da, iş turu ölçüsü, /mod komutu, ghost.departman ver."""
import json
import os
import unittest
from unittest import mock

from tests import common  # noqa: F401
from ghost import agents, departman, komutlar, metrik, motor


class _Ctx:
    STATE_FILE = "x"

    def __init__(self):
        self.gonderilen = []

    def send(self, chat_id, text, **kw):
        self.gonderilen.append(text)

    def save_json(self, *a, **k):
        pass


class _Mgr:
    chat_id = 1
    state = {}


class TestYamlV2(unittest.TestCase):
    def test_alanlar(self):
        d = agents.DEPARTMENTS
        self.assertNotIn("kazanc", d)                       # K6: economy arşivde, blok silindi
        self.assertFalse(agents.ajan_mi("finans"))           # yalnız persona
        self.assertTrue(agents.ajan_mi("muhendislik"))
        self.assertIn("Bash", d["muhendislik"]["izin"])
        self.assertIn("mcp__github__*", d["muhendislik"]["izin"])
        self.assertEqual(d["muhendislik"]["model"], "opus")   # Sahip 2026-11-08 "opus 5.5 iyi gibi" (pilot 3×3 hepsi %100)
        self.assertEqual(d["muhendislik"]["butce_hafta"], 10)
        self.assertTrue(d["istihbarat"]["aciklama"])
        self.assertEqual(agents.ajanlar(), ["muhendislik", "istihbarat", "operasyon"])

    def test_deger(self):
        self.assertEqual(agents._deger("[a, b]"), ["a", "b"])
        self.assertIs(agents._deger("false"), False)
        self.assertEqual(agents._deger("10"), 10)
        self.assertEqual(agents._deger("inherit"), "inherit")


class TestKip(unittest.TestCase):
    def tearDown(self):
        agents.mod_yaz("tek")

    def test_varsayilan_tek(self):
        self.assertEqual(agents.mod_oku(), "tek")

    def test_yaz_oku_takma(self):
        self.assertEqual(agents.mod_yaz("departman"), "departman")
        self.assertEqual(agents.mod_oku(), "departman")
        self.assertEqual(agents.mod_yaz("1"), "tek")
        self.assertEqual(agents.mod_yaz("2"), "departman")
        with self.assertRaises(ValueError):
            agents.mod_yaz("ucuncu")

    def test_system_prefix_tek_persona(self):
        p = agents.system_prefix("deploy et")
        self.assertIn("Mühendislik", p)
        self.assertNotIn("[DEPARTMAN:", p)

    def test_system_prefix_departman_isaret(self):
        agents.mod_yaz("departman")
        self.assertEqual(agents.system_prefix("deploy et"), "[DEPARTMAN:muhendislik]\n")
        # Finans ajan değil → departman kipinde de persona
        self.assertIn("Finans", agents.system_prefix("bu ayki gelir gider durumu ne"))
        self.assertEqual(agents.system_prefix("şiir yaz"), "")

    def test_isaret_coz_sonuncu_kazanir(self):
        agents.mod_yaz("departman")
        k, t = agents.isaret_coz("[DEPARTMAN:operasyon]\nKALKAN [DEPARTMAN:muhendislik]\ngörev")
        self.assertEqual(k, "muhendislik")
        self.assertEqual(t, "KALKAN görev")
        k, t = agents.isaret_coz("işaretsiz metin")
        self.assertIsNone(k)
        self.assertEqual(t, "işaretsiz metin")
        # işin açık model seçimi işarette: [DEPARTMAN:x@model] (Sahip 2026-11-08 önem/zorluğa göre model)
        k, t, m = agents.isaret_coz_tam("[DEPARTMAN:operasyon]\nKALKAN [DEPARTMAN:istihbarat@fable]\ngörev")
        self.assertEqual((k, t, m), ("istihbarat", "KALKAN görev", "fable"))
        k, t, m = agents.isaret_coz_tam("[DEPARTMAN:istihbarat]\ngörev")
        self.assertEqual((k, t, m), ("istihbarat", "görev", None))
        self.assertEqual(agents.isaret_coz("[DEPARTMAN:istihbarat@opus]\ngörev"), ("istihbarat", "görev"))

    def test_isaret_tek_kipte_silinir_anahtar_yok(self):
        k, t = agents.isaret_coz("[DEPARTMAN:muhendislik]\ngörev")
        self.assertIsNone(k)
        self.assertEqual(t, "görev")

    def test_ajan_tanim_ve_args(self):
        tanim = agents.ajan_tanim("istihbarat")
        self.assertIn("description", tanim)
        self.assertIn("İstihbarat departmanı", tanim["prompt"])
        self.assertIn("AYRI bir ajansın", tanim["prompt"])
        self.assertIn("mcp__playwright__*", tanim["tools"])
        with mock.patch.dict(agents.DEPARTMENTS["istihbarat"], {"model": "inherit"}):
            self.assertNotIn("model", agents.ajan_tanim("istihbarat"))          # inherit → bayrak yok
            with mock.patch("ghost.modeller.tam_model", lambda m: "claude-%s-5-5" % m):
                self.assertEqual(agents.ajan_tanim("istihbarat", model="fable")["model"], "claude-fable-5-5")
        args = agents.ajan_args("istihbarat")
        self.assertEqual(args[0], "--agents")
        self.assertEqual(json.loads(args[1]).keys(), {"istihbarat"})   # tek seviye: yalnız kendi tanımı
        self.assertEqual(args[2:], ["--agent", "istihbarat"])

    def test_ajan_model_acik(self):
        with mock.patch.dict(agents.DEPARTMENTS["operasyon"], {"model": "haiku"}):
            with mock.patch("ghost.modeller.tam_model", lambda m: "claude-%s-5-5" % m):
                self.assertEqual(agents.ajan_tanim("operasyon")["model"], "claude-haiku-5-5")
            self.assertEqual(agents.ajan_model("operasyon"), "haiku")
            with mock.patch("ghost.modeller.tam_model", lambda m: "claude-%s-5-5" % m):
                # işin açık seçimi yaml'ı ezer; yalnız o departmana uygulanır
                j = json.loads(agents.ajan_args("operasyon", model="fable")[1])
                self.assertEqual(j["operasyon"]["model"], "claude-fable-5-5")
                self.assertEqual(json.loads(agents.ajan_json())["operasyon"]["model"], "claude-haiku-5-5")
        self.assertEqual(agents.ajan_model("operasyon"), "opus")   # yaml varsayılanı (Sahip 2026-11-08 "opus 5.5 iyi")
        with mock.patch.dict(agents.DEPARTMENTS["operasyon"], {"model": "inherit"}):
            self.assertIsNone(agents.ajan_model("operasyon"))

    def test_koordinator_notu(self):
        n = agents.koordinator_notu("muhendislik")
        self.assertIn("ghost.departman ver muhendislik", n)


class _SahteProc:
    returncode = 0

    def __init__(self, out):
        self._out = out

    def communicate(self, timeout=None):
        return self._out, ""


class TestMotorIsYolu(unittest.TestCase):
    def setUp(self):
        self.cmds = []
        self.out = json.dumps({"result": "tamam", "session_id": "s1", "num_turns": 4})

        def sahte_popen(cmd, **kw):
            self.cmds.append(cmd)
            return _SahteProc(self.out)
        self.p1 = mock.patch.object(motor.subprocess, "Popen", sahte_popen)
        self.p2 = mock.patch.object(motor.modeller, "tam_model", lambda m: m)
        self.p1.start(); self.p2.start()
        try:
            os.remove(os.path.join(os.environ["GHOST_OPS"], "metrik.jsonl"))
        except OSError:
            pass

    def tearDown(self):
        self.p1.stop(); self.p2.stop()
        agents.mod_yaz("tek")

    def _kayitlar(self):
        yol = os.path.join(os.environ["GHOST_OPS"], "metrik.jsonl")
        return [json.loads(l) for l in open(yol, encoding="utf-8")]

    def test_tek_kip_bayrak_yok_olcum_var(self):
        res, sid, err = motor.run_claude("[Ghost Mühendislik departmanı olarak çalış. x]\n\ngörev", "sonnet",
                                         session_id="s1", use_mcp=False)
        self.assertEqual(res, "tamam"); self.assertFalse(err)
        cmd = self.cmds[-1]
        self.assertNotIn("--agent", cmd)
        k = self._kayitlar()[-1]
        self.assertEqual(k["tur"], "is"); self.assertEqual(k["departman"], ""); self.assertEqual(k["kip"], "tek")
        self.assertEqual(k["arac"], 3); self.assertFalse(k["hata"])

    def test_departman_kip_agent_bayragi(self):
        agents.mod_yaz("departman")
        motor.run_claude("araç notu\n[DEPARTMAN:muhendislik]\nKALKAN görev", "sonnet", session_id="s1",
                         use_mcp=False, ozerk=True)
        cmd = self.cmds[-1]
        self.assertEqual(cmd[2], "araç notu\nKALKAN görev")           # işaret istemden silindi
        i = cmd.index("--agents")
        self.assertIn("muhendislik", json.loads(cmd[i + 1]))
        self.assertEqual(cmd[cmd.index("--agent") + 1], "muhendislik")
        k = self._kayitlar()[-1]
        self.assertEqual(k["departman"], "muhendislik"); self.assertEqual(k["kip"], "departman"); self.assertTrue(k["ozerk"])

    def test_departman_model_acik_ise_degisir(self):
        agents.mod_yaz("departman")
        with mock.patch.dict(agents.DEPARTMENTS["istihbarat"], {"model": "haiku"}):
            motor.run_claude("[DEPARTMAN:istihbarat]\ngörev", "fable", session_id="s1", use_mcp=False)
        cmd = self.cmds[-1]
        self.assertEqual(cmd[cmd.index("--model") + 1], "haiku")
        # işin açık seçimi (işaret @model) yaml varsayılanını ezer; ajan tanımına da aynı model yazılır
        with mock.patch.dict(agents.DEPARTMENTS["istihbarat"], {"model": "haiku"}):
            motor.run_claude("[DEPARTMAN:istihbarat@fable]\ngörev", "sonnet", session_id="s1", use_mcp=False)
        cmd = self.cmds[-1]
        self.assertEqual(cmd[2], "görev")
        self.assertEqual(cmd[cmd.index("--model") + 1], "fable")
        self.assertEqual(json.loads(cmd[cmd.index("--agents") + 1])["istihbarat"]["model"], "fable")
        self.assertEqual(self._kayitlar()[-1]["model"], "fable")

    # ---- 4.1.6 (2026-11-09): köprü iş yolunda system_prefix yazmaz, route() anahtarını dep= ile geçirir ----

    def _sistem(self, cmd):
        return cmd[cmd.index("--append-system-prompt") + 1] if "--append-system-prompt" in cmd else None

    def test_dep_tek_kipte_persona_sistem_istemine(self):
        motor.run_claude("araç notu\ngörev", "sonnet", session_id="s1", use_mcp=False, dep="muhendislik")
        cmd = self.cmds[-1]
        self.assertEqual(cmd[2], "araç notu\ngörev")                     # istem dokunulmadı
        self.assertNotIn("--agent", cmd); self.assertNotIn("--agents", cmd)
        self.assertIn("Mühendislik departmanı olarak çalış", self._sistem(cmd))
        self.assertEqual(self._sistem(cmd), agents.persona_sistem("muhendislik"))
        self.assertEqual(cmd[cmd.index("--model") + 1], "sonnet")        # persona yolunda model miras
        k = self._kayitlar()[-1]
        self.assertEqual(k["departman"], "muhendislik"); self.assertEqual(k["kip"], "tek")

    def test_dep_departman_kipinde_ajan(self):
        agents.mod_yaz("departman")
        motor.run_claude("görev", "sonnet", session_id="s1", use_mcp=False, dep="istihbarat")
        cmd = self.cmds[-1]
        self.assertEqual(cmd[cmd.index("--agent") + 1], "istihbarat")
        self.assertIsNone(self._sistem(cmd))
        self.assertEqual(self._kayitlar()[-1]["departman"], "istihbarat")

    def test_dep_finans_departman_kipinde_persona(self):
        agents.mod_yaz("departman")                                        # Finans ajan değil → yine persona
        motor.run_claude("görev", "sonnet", session_id="s1", use_mcp=False, dep="finans")
        cmd = self.cmds[-1]
        self.assertNotIn("--agent", cmd)
        self.assertIn("Finans", self._sistem(cmd))
        self.assertEqual(self._kayitlar()[-1]["departman"], "finans")

    def test_dep_acik_isaret_kopru_yonlendirmesinden_ustun(self):
        agents.mod_yaz("departman")
        motor.run_claude("[DEPARTMAN:operasyon@fable]\ngörev", "sonnet", session_id="s1", use_mcp=False,
                         dep="muhendislik")
        cmd = self.cmds[-1]
        self.assertEqual(cmd[2], "görev")
        self.assertEqual(cmd[cmd.index("--agent") + 1], "operasyon")
        self.assertEqual(cmd[cmd.index("--model") + 1], "fable")
        self.assertIsNone(self._sistem(cmd))

    def test_dep_yok_ya_da_bilinmiyor_bayrak_yok(self):
        for dep in (None, "", "yok_boyle_departman"):
            motor.run_claude("görev", "sonnet", session_id="s1", use_mcp=False, dep=dep)
            cmd = self.cmds[-1]
            self.assertNotIn("--agent", cmd); self.assertIsNone(self._sistem(cmd))
            self.assertEqual(self._kayitlar()[-1]["departman"], "")

    def test_ajan_anahtar_ve_persona_sistem(self):
        self.assertIsNone(agents.ajan_anahtar("muhendislik"))              # tek kip
        self.assertIsNone(agents.persona_sistem(None)); self.assertIsNone(agents.persona_sistem("yok"))
        self.assertTrue(agents.persona_sistem("finans").startswith("[Ghost Finans departmanı"))
        agents.mod_yaz("departman")
        self.assertEqual(agents.ajan_anahtar("muhendislik"), "muhendislik")
        self.assertIsNone(agents.ajan_anahtar("finans")); self.assertIsNone(agents.ajan_anahtar(None))

    def test_kalici_sohbette_koordinator_notu(self):
        agents.mod_yaz("departman")
        gorulen = {}

        class _Kalici:
            def uygun(self, *a, **k):
                return True

            def tur(self, prompt, limit, on_event):
                gorulen["prompt"] = prompt
                return (json.dumps({"result": "ok", "session_id": "s"}), "", "", False, True)
        motor.run_claude("[DEPARTMAN:operasyon]\nsite ayakta mı", "fable", session_id="s", kalici=_Kalici())
        self.assertIn("Koordinatör notu", gorulen["prompt"])
        self.assertNotIn("[DEPARTMAN:", gorulen["prompt"])
        self.assertTrue(gorulen["prompt"].endswith("site ayakta mı"))

    def test_hata_da_olculur(self):
        self.out = json.dumps({"result": "patladı", "is_error": True, "session_id": "s1", "num_turns": 1})
        res, sid, err = motor.run_claude("görev", "sonnet", session_id="s1", use_mcp=False)
        self.assertTrue(err)
        self.assertTrue(self._kayitlar()[-1]["hata"])


class TestDenetimAyrimi(unittest.TestCase):
    """İlk gerçek koşu bulgusu (2026-11-08 iş #136): işaret denetçiye sızdı → denetçi --agent ile koştu."""

    def tearDown(self):
        agents.mod_yaz("tek")

    def test_denetci_istemi_isaretsiz(self):
        from ghost import verify
        p = verify.build_prompt("[DEPARTMAN:istihbarat]\nKALKAN görev", "rapor")
        self.assertNotIn("[DEPARTMAN:", p)
        self.assertIn("KALKAN görev", p)

    def test_review_rol_gecer_ve_eski_imza_dusmez(self):
        from ghost import verify
        gorulen = {}

        def yeni(prompt, model, use_mcp=None, timeout=None, ozerk=False, rol=None):
            gorulen["rol"] = rol
            return ("KARAR: geçti\nGEREKÇE: ok\nKONTROL: -", "s", False)

        def eski(prompt, model, use_mcp=None, timeout=None, ozerk=False):
            gorulen["eski"] = True
            return ("KARAR: geçti\nGEREKÇE: ok\nKONTROL: -", "s", False)
        karar, _, _ = verify.review("emir", "rapor", yeni, kind="iş", jid=1)
        self.assertEqual(karar, verify.GECTI); self.assertEqual(gorulen["rol"], "denetim")
        karar, _, _ = verify.review("emir", "rapor", eski, kind="iş", jid=2)
        self.assertEqual(karar, verify.GECTI); self.assertTrue(gorulen["eski"])

    def test_motor_rol_olcuye_duser(self):
        try:
            os.remove(os.path.join(os.environ["GHOST_OPS"], "metrik.jsonl"))
        except OSError:
            pass
        out = json.dumps({"result": "ok", "session_id": "s1", "num_turns": 2})
        with mock.patch.object(motor.subprocess, "Popen", lambda cmd, **kw: _SahteProc(out)), \
             mock.patch.object(motor.modeller, "tam_model", lambda m: m):
            motor.run_claude("denetim istemi", "sonnet", session_id="s1", use_mcp=False, rol="denetim")
            motor.run_claude("iş istemi", "sonnet", session_id="s2", use_mcp=False)
        yol = os.path.join(os.environ["GHOST_OPS"], "metrik.jsonl")
        kayitlar = [json.loads(l) for l in open(yol, encoding="utf-8")]
        self.assertEqual([k["rol"] for k in kayitlar], ["denetim", "is"])
        o = metrik.ozet(1)
        self.assertEqual(o["is_tur"], 1); self.assertEqual(o["denetim_tur"], 1)
        self.assertIn("denetim 1", metrik.metin(1))


class TestHafizaButceKarne(unittest.TestCase):
    """4.1.3 sohbet --agents · 4.1.4 departman hafızası, bütçe, karne tablosu."""

    def setUp(self):
        for ad in ("metrik.jsonl", "verify.jsonl"):
            try:
                os.remove(os.path.join(os.environ["GHOST_OPS"], ad))
            except OSError:
                pass

    def test_hafiza_ozeti_ve_istem(self):
        import tempfile
        kok = tempfile.mkdtemp(prefix="dep-")
        self.assertEqual(agents.hafiza_ozeti("istihbarat", kok=kok), "")           # klasör yok → boş
        d = agents.departman_dizin("istihbarat", kok); os.makedirs(d)
        open(os.path.join(d, "a.md"), "w", encoding="utf-8").write("# A notu\n...")
        open(os.path.join(d, "b.txt"), "w").write("md değil")
        oz = agents.hafiza_ozeti("istihbarat", kok=kok)
        self.assertIn("a.md — A notu", oz); self.assertNotIn("b.txt", oz)
        self.assertIn("A notu", agents.ajan_tanim("istihbarat", kok=kok)["prompt"])

    def test_sohbet_args_ve_kalici_cmd(self):
        from ghost import kalici_oturum
        a = agents.sohbet_args()
        self.assertEqual(a[0], "--agents"); self.assertEqual(set(json.loads(a[1])), {"muhendislik", "istihbarat", "operasyon"})
        self.assertEqual(kalici_oturum._ajan_args(), a)
        with mock.patch.object(agents, "ajan_json", side_effect=RuntimeError("yaml bozuk")):
            self.assertEqual(agents.sohbet_args(), [])                              # sohbet düşmez

    def _is_yaz(self, dep, sn, hata=False, rol="is", ts=None):
        import time
        t = ts or time.time()
        metrik.tur_kaydet("is", t - sn, None, t, tur="is", ek={"departman": dep, "hata": hata, "rol": rol})

    def test_butce_ve_karne(self):
        from ghost import departman, verify, config
        self._is_yaz("istihbarat", 100); self._is_yaz("istihbarat", 50, hata=True); self._is_yaz("", 10)
        self._is_yaz("istihbarat", 5, rol="denetim")                                # sayılmaz
        verify.log({"jid": 1, "kind": "dep:istihbarat", "karar": "geçti"})
        verify.log({"jid": 2, "kind": "dep:istihbarat", "karar": "yanlış"})
        verify.log({"jid": 3, "kind": "hedef", "karar": "geçti"})
        self.assertTrue(os.path.exists(config.VERIFY_LOG))
        self.assertEqual(departman.butce_durum("istihbarat"), (2, 10))
        self.assertEqual(departman.butce_durum("muhendislik"), (0, 10))
        t = departman.karne_tablosu(7)
        self.assertIn("| İstihbarat | 2 |", t); self.assertIn("geçti 1, yanlış 1", t); self.assertIn("2/10", t)
        self.assertIn("Genel (departmansız) | 1 |", t)
        self.assertNotIn("Mühendislik", t)

    def test_karne_bos_ve_gundem_ekler(self):
        from ghost import departman
        self.assertEqual(departman.karne_tablosu(7), "")

    def test_ver_butce_uyarisi(self):
        from ghost import departman
        import io, contextlib
        with mock.patch.dict(agents.DEPARTMENTS["istihbarat"], {"butce_hafta": 1}):
            self._is_yaz("istihbarat", 10)
            err = io.StringIO()
            with mock.patch("ghost.bus.istek", lambda *a, **k: 5), contextlib.redirect_stderr(err):
                is_id, _ = departman.ver("istihbarat", "x")
            self.assertEqual(is_id, 5); self.assertIn("bütçe 1", err.getvalue())    # uyarır, engellemez


class TestMetrikIs(unittest.TestCase):
    def setUp(self):
        try:
            os.remove(os.path.join(os.environ["GHOST_OPS"], "metrik.jsonl"))
        except OSError:
            pass

    def test_ozet_ve_metin(self):
        import time
        t = time.time()
        metrik.tur_kaydet("is", t - 30, None, t, tur="is", ek={"departman": "istihbarat", "hata": False})
        metrik.tur_kaydet("is", t - 10, None, t, tur="is", zaman_asimi=True, ek={"departman": "", "hata": True})
        metrik.tur_kaydet("kokpit", t - 5, t - 3, t, tur="sohbet")
        o = metrik.ozet(1)
        self.assertEqual(o["tur"], 1); self.assertEqual(o["is_tur"], 2); self.assertEqual(o["is_hata"], 1)
        self.assertEqual(o["is_departman"], {"istihbarat": 1, "genel": 1})
        self.assertEqual(o["kayip"], 0)                       # iş zaman aşımı kayıp cevap sayılmaz
        m = metrik.metin(1)
        self.assertIn("iş 2 tur", m); self.assertIn("istihbarat 1", m)

    def test_ek_cekirdegi_ezmez(self):
        import time
        t = time.time()
        metrik.tur_kaydet("is", t - 1, None, t, tur="is", ek={"tur": "sohbet", "x": 1})
        k = json.loads(open(os.path.join(os.environ["GHOST_OPS"], "metrik.jsonl"), encoding="utf-8").read().splitlines()[-1])
        self.assertEqual(k["tur"], "is"); self.assertEqual(k["x"], 1)


class TestKomutVeVer(unittest.TestCase):
    def tearDown(self):
        agents.mod_yaz("tek")
        os.environ.pop("GHOST_OZERK", None)

    def test_mod_komutu(self):
        ctx, mgr = _Ctx(), _Mgr()
        komutlar.bul("/mod")("", mgr, ctx)
        self.assertIn("Kip: tek", ctx.gonderilen[-1])
        komutlar.bul("/mod")("departman", mgr, ctx)
        self.assertEqual(agents.mod_oku(), "departman")
        self.assertIn("Kip: departman", ctx.gonderilen[-1])
        komutlar.bul("/kip")("yok", mgr, ctx)
        self.assertIn("tek | departman", ctx.gonderilen[-1])
        komutlar.bul("/departmanlar")("", mgr, ctx)
        self.assertIn("kip: departman", ctx.gonderilen[-1]); self.assertIn("yalnız persona", ctx.gonderilen[-1])

    def test_ver_istek_yazar(self):
        gorulen = {}

        def sahte_istek(prompt, kind, source, **kw):
            gorulen.update(prompt=prompt, kind=kind, source=source, **kw)
            return 77
        with mock.patch("ghost.bus.istek", sahte_istek):
            is_id, kip = departman.ver("muhendislik", "testleri incele")
        self.assertEqual(is_id, 77); self.assertEqual(kip, "tek")
        self.assertEqual(gorulen["kind"], "dep:muhendislik")
        self.assertTrue(gorulen["prompt"].startswith("[DEPARTMAN:muhendislik]\n"))
        self.assertEqual(gorulen["classify"], "testleri incele")   # işaret sınıflandırmaya girmez
        self.assertNotIn("gate", gorulen)
        self.assertIsNone(gorulen["model"])                        # yaml varsayılanını motor uygular, iş taşımaz

    def test_ver_model_secimi(self):
        # Sahip 2026-11-08: varsayılan opus; açık --model ve önem (PERMANENT/SPEND) yükseltmesi işarete biner
        self.assertEqual(departman.model_sec("istihbarat", "haberleri tara"), ("opus", "departman varsayılanı", False))
        self.assertEqual(departman.model_sec("istihbarat", "haberleri tara", "sonnet"), ("sonnet", "açık seçim", True))
        m, neden, isarete = departman.model_sec("muhendislik", "siteyi deploy et")
        self.assertEqual((m, isarete), ("fable", True)); self.assertIn("permanent", neden)
        with mock.patch.dict(agents.DEPARTMENTS["istihbarat"], {"model": "inherit"}):
            self.assertEqual(departman.model_sec("istihbarat", "oku")[0], None)
        gorulen = {}

        def sahte_istek(prompt, kind, source, **kw):
            gorulen.update(prompt=prompt, **kw)
            return 78
        with mock.patch("ghost.bus.istek", sahte_istek):
            departman.ver("istihbarat", "haberleri tara", model="fable")
            self.assertTrue(gorulen["prompt"].startswith("[DEPARTMAN:istihbarat@fable]\n"))
            self.assertEqual(gorulen["model"], "fable")
            departman.ver("muhendislik", "siteyi deploy et")
            self.assertTrue(gorulen["prompt"].startswith("[DEPARTMAN:muhendislik@fable]\n"))
            self.assertEqual(gorulen["classify"], "siteyi deploy et")

    def test_ver_hatalar(self):
        with self.assertRaises(ValueError):
            departman.ver("finans", "x")                      # ajan değil
        with self.assertRaises(ValueError):
            departman.ver("yok", "x")
        with self.assertRaises(ValueError):
            departman.ver("muhendislik", "  ")
        os.environ["GHOST_OZERK"] = "1"
        with self.assertRaises(ValueError):                  # kalkan kaçağı yok
            departman.ver("muhendislik", "x")


if __name__ == "__main__":
    unittest.main()
