"""Faz 3 omurga: olay veriyolu istek kuyruğu, kanal soyutlaması, komut kayıt defteri,
köprünün bus tüketicisi."""
import os
import sys
import unittest

from tests import common  # noqa: F401

from ghost import bus, channels, config, komutlar, kokpit  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops"))
import telegram_bridge as tb  # noqa: E402


class Veriyolu(unittest.TestCase):
    def test_istek_ve_tur_filtresi(self):
        bus.emit("email", "webhook", {"from": "x"})               # başka tür, önde bekliyor
        eid = bus.istek("repo tara", "denetim", "test", announce="🔎 başladı", gate=True, classify="oku")
        ev = bus.next_pending("request")
        self.assertEqual(ev["id"], eid)
        self.assertEqual(ev["payload"], {"prompt": "repo tara", "kind": "denetim", "announce": "🔎 başladı",
                                         "gate": True, "classify": "oku"})
        self.assertTrue(any(r["id"] == eid for r in bus.bekleyen_istekler()))
        bus.mark(eid)
        self.assertFalse(any(r["id"] == eid for r in bus.bekleyen_istekler()))


class Kanallar(unittest.TestCase):
    def test_telegram_parcalar_ve_butonlar(self):
        cagrilar = []
        k = channels.TelegramKanal(lambda m, **kw: cagrilar.append((m, kw)), 7)
        k.send("a" * 4500, buttons=[[("✅", "ok:1")]])
        self.assertEqual(len(cagrilar), 2)
        self.assertIn("reply_markup", cagrilar[0][1])
        self.assertNotIn("reply_markup", cagrilar[1][1])
        self.assertEqual(cagrilar[0][1]["chat_id"], 7)

    def test_app_kanal_dokum_ve_birincil(self):
        son = kokpit.outbox_son()
        channels.AppKanal().send("selam", None)
        self.assertEqual(kokpit.outbox_oku(son)[-1]["metin"], "selam")
        self.assertEqual(channels.birincil(), "telegram")
        channels.birincil_ayarla("kokpit")
        self.assertEqual(channels.birincil(), "kokpit")
        self.assertIn("birincil kokpit", channels.durum_metni())
        with self.assertRaises(ValueError):
            channels.birincil_ayarla("faks")
        channels.birincil_ayarla("telegram")

    def test_dagitici_bir_kanal_duserse_digerleri_yasar(self):
        class Bozuk:
            def send(self, t, b=None): raise RuntimeError("kopuk")
        alinan = []
        class Iyi:
            def send(self, t, b=None): alinan.append(t)
        channels.Kanallar(Bozuk(), Iyi()).send("x")
        self.assertEqual(alinan, ["x"])

    def test_kopru_send_iki_kanala(self):
        cagrilar = []
        eski = tb.api
        tb.api = lambda *a, **k: cagrilar.append(a) or {}
        try:
            son = kokpit.outbox_son()
            tb.send(1, "**kalın** metin")
            self.assertEqual(cagrilar[0][0], "sendMessage")
            # Telegram düz metin alır (Markdown soyulur); uygulama HAM Markdown alır ve kendi çizer
            self.assertEqual(kokpit.outbox_oku(son)[-1]["metin"], "**kalın** metin")
        finally:
            tb.api = eski


class KayitDefteri(unittest.TestCase):
    class Ctx:
        def __init__(self): self.gonderilen = []
        def send(self, cid, text, buttons=None): self.gonderilen.append(text)
        def durum_text(self, mgr): return "durum-ok"
        def saglik_text(self, mgr): return "saglik-ok"
    class Mgr:
        chat_id = 1
        def jobs_summary(self): return "işler"
        def cancel(self, a): return "iptal " + a

    def test_bul_ve_liste(self):
        self.assertIs(komutlar.bul("/status"), komutlar.bul("/durum"))
        self.assertIsNone(komutlar.bul("/yok"))
        adlar = [k for k, _ in komutlar.liste()]
        self.assertIn("/kanal", adlar)
        self.assertNotIn("/status", adlar)                      # takma ad listeye girmez

    def test_islemler(self):
        ctx, mgr = self.Ctx(), self.Mgr()
        komutlar.bul("/durum")("", mgr, ctx)
        komutlar.bul("/cancel")("", mgr, ctx)
        komutlar.bul("/cancel")("3", mgr, ctx)
        komutlar.bul("/kanal")("", mgr, ctx)
        komutlar.bul("/cihazlar")("", mgr, ctx)
        komutlar.bul("/istekler")("", mgr, ctx)
        self.assertEqual(ctx.gonderilen[0], "durum-ok")
        self.assertIn("Kullanım", ctx.gonderilen[1])
        self.assertEqual(ctx.gonderilen[2], "iptal 3")
        self.assertIn("Kanallar", ctx.gonderilen[3])
        self.assertIn("Kokpit cihazları", ctx.gonderilen[4])

    def test_kopru_handle_command_kayit_defterine_gider(self):
        gonderilen = []
        eski = tb.send
        tb.send = lambda cid, t, buttons=None: gonderilen.append(t)
        try:
            tb.handle_command("/kanal", self.Mgr())
            self.assertIn("birincil", gonderilen[-1])
        finally:
            tb.send = eski
        self.assertTrue(any(c == "kanal" for c, _ in tb.komut_listesi()))


class KopruTuketici(unittest.TestCase):
    class Mgr:
        chat_id = 1
        def __init__(self): self.isler, self.bekleyen = [], []
        def enqueue_job(self, text, kind="iş", model=None): self.isler.append((text, kind, model))
        def add_pending(self, text, kind, model=None, **kw): self.bekleyen.append((text, kind)); return "p1"

    def test_bus_istegi_ise_donusur(self):
        gonderilen = []
        e_send, e_na = tb.send, tb.needs_approval
        tb.send = lambda cid, t, buttons=None: gonderilen.append((t, buttons))
        tb.needs_approval = lambda text, mgr: text.startswith("SİL")
        try:
            while bus.next_pending("request"):
                bus.mark(bus.next_pending("request")["id"])
            a = bus.istek("repo tara", "denetim", "test", announce="🔎 başladı", model="opus")
            b = bus.istek("SİL her şeyi", "olay", "test", gate=True)
            mgr = self.Mgr()
            tb.check_requests(mgr)
            self.assertEqual(len(mgr.isler), 1)
            self.assertTrue(mgr.isler[0][0].endswith("repo tara") and mgr.isler[0][2] == "opus")
            self.assertEqual(mgr.bekleyen, [("SİL her şeyi", "olay")])
            self.assertTrue(any(b_ and b_[0][0][1] == "ok:p1" for _, b_ in gonderilen if b_))
            self.assertIn(("🔎 başladı", None), gonderilen)
            self.assertIsNone(bus.next_pending("request"))
        finally:
            tb.send, tb.needs_approval = e_send, e_na


class GocmusKomutlar(unittest.TestCase):
    class Ctx(KayitDefteri.Ctx):
        STATE_FILE = os.path.join(config.OPS, "state-test.json")
        HELP = "yardım"
        def save_json(self, path, obj): self.kayit = (path, obj)
        def log(self, m): pass
    class Mgr(KayitDefteri.Mgr):
        def __init__(self):
            self.state = {"guard": True}
            self.history = [("soru", "cevap")]
            self.chat_ctx, self.chat_window, self.chat_turns = 1000, 100000, 3
            self.chat_carry = None
        def _save_chat(self): self.kaydedildi = True

    def test_hepsi_defterde(self):
        for c in ("/help", "/mcp", "/new", "/tazele", "/model", "/guard", "/sesli", "/ogren", "/motorlar",
                  "/departmanlar", "/rapor", "/is", "/iş", "/kazanc",
                  "/denetle", "/hedef", "/varlik", "/hafiza",
                  "/izle", "/hatirlat", "/gorev", "/araclar", "/job", "/mac", "/cc", "/arastir",
                  "/fleet", "/istihbarat", "/denetim", "/deploy", "/sahne", "/kanal", "/istekler"):
            self.assertIsNotNone(komutlar.bul(c), c)

    def test_model_guard_help(self):
        ctx, mgr = self.Ctx(), self.Mgr()
        komutlar.bul("/model")("opus", mgr, ctx)
        self.assertEqual((mgr.state["model"], ctx.kayit[0]), ("opus", ctx.STATE_FILE))
        komutlar.bul("/model")("gpt", mgr, ctx)
        self.assertIn("Motor:", ctx.gonderilen[-1])
        komutlar.bul("/guard")("off", mgr, ctx)
        self.assertFalse(mgr.state["guard"])
        komutlar.bul("/help")("", mgr, ctx)
        # /help = köprü HELP + Claude Code komut listesi (cc_komutlar.yardim, 2026-09-04)
        self.assertTrue(ctx.gonderilen[-1].startswith("yardım"))
        self.assertIn("Claude Code", ctx.gonderilen[-1])

    def test_new_ve_tazele(self):
        ctx, mgr = self.Ctx(), self.Mgr()
        komutlar.bul("/tazele")("", mgr, ctx, _cmd="/tazele")
        self.assertIn("K: soru", mgr.chat_carry)
        self.assertTrue(mgr.kaydedildi)
        mgr2 = self.Mgr()
        komutlar.bul("/new")("", mgr2, ctx, _cmd="/new")
        self.assertEqual(mgr2.history, [])
        self.assertIn("Yeni sohbet", ctx.gonderilen[-1])

    def test_sahne_komutu(self):
        from ghost import sahne
        ctx, mgr = self.Ctx(), self.Mgr()
        s0 = sahne.oku()["seq"]
        komutlar.bul("/sahne")("nabiz", mgr, ctx)
        self.assertEqual(sahne.oku()["seq"], s0 + 1)
        self.assertEqual(sahne.oku()["baslik"], "Nabız")
        self.assertIn("Sahne basıldı", ctx.gonderilen[-1])
        self.assertEqual(sahne.dogrula(sahne.hazir_nabiz()["bilesenler"][0]), [])
