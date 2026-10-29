"""Faz 1 (docs/kokpit.md): stream-json → events → SSE; zaman çizelgesi; Sahne v1;
eylem/paylaşım rayları; run_claude akış kipi (sahte CLI ile)."""
import json
import os
import sqlite3
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from tests import common  # noqa: F401
from tests.test_kokpit import _istek

from ghost import akis, api, bus, config, kokpit, sahne  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops"))
import telegram_bridge as tb  # noqa: E402
from ghost import motor  # noqa: E402  (Faz 2.9: CLI koşumu motorda)

STREAM = [
    {"type": "system", "subtype": "init", "model": "claude-x", "session_id": "sid-1"},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Bakıyorum."},
        {"type": "tool_use", "name": "Bash", "input": {"command": "git log -1"}}]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "content": [{"type": "text", "text": "abc1234 son commit"}]}]}},
    {"type": "result", "subtype": "success", "is_error": False, "duration_ms": 1200, "num_turns": 2,
     "result": "Son commit abc1234.", "session_id": "sid-1", "total_cost_usd": 0.01,
     "usage": {"input_tokens": 10, "output_tokens": 5}, "modelUsage": {}},
]


class StreamAyristirma(unittest.TestCase):
    def test_satirlar_normalize(self):
        turler = [e["tur"] for s in STREAM for e in akis.stream_olaylar(json.dumps(s))]
        self.assertEqual(turler, ["baslangic", "metin", "arac", "arac_sonuc", "sonuc"])
        arac = akis.stream_olaylar(json.dumps(STREAM[1]))[1]
        self.assertEqual((arac["ad"], arac["ozet"]), ("Bash", "git log -1"))
        self.assertEqual(akis.stream_olaylar("bu json değil"), [])
        self.assertEqual(akis.stream_olaylar('{"type":"garip"}'), [])

    def test_kaydedici_events_e_yazar_kuyruga_girmez(self):
        # bekleyen olay kuyruğu görünmez kalmalı: sohbet olayları status=done
        onceki = bus.stats().get("pending", 0)
        cb = akis.olay_kaydedici("sohbet")
        son = akis.olay_son()
        for s in STREAM:
            cb(json.dumps(s))
        cb("çöp satır")  # fırlatmaz
        rows = akis.olaylar(son)
        self.assertEqual([r["tur"] for r in rows], ["baslangic", "metin", "arac", "arac_sonuc", "sonuc"])
        self.assertTrue(all(r["kaynak"] == "sohbet" for r in rows))
        self.assertEqual(bus.stats().get("pending", 0), onceki)
        self.assertEqual(akis.olaylar(rows[-1]["id"]), [])


class ZamanCizelgesi(unittest.TestCase):
    def test_birlesik_ve_sirali(self):
        with open(config.AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "2026-09-01 10:00:00", "actor": "bridge", "action": "git.commit",
                                "class": "reversible", "detail": "kokpit: 1a2b3c4 sahne"}) + "\n")
            f.write(json.dumps({"ts": "2026-09-01 12:00:00", "actor": "webhook", "action": "sentry",
                                "class": "read", "detail": "Sentry yeni hata"}) + "\n")
        jd = os.path.join(config.OPS, "jobs")
        os.makedirs(jd, exist_ok=True)
        with open(os.path.join(jd, "7.json"), "w") as f:
            json.dump({"status": "bitti", "kind": "denetim", "prompt": "repo tara",
                       "started": "2026-09-01 11:00:00", "ended": "2026-09-01 11:05:00"}, f)
        rows = akis.zaman_cizelgesi(limit=50)
        ts = [r["ts"] for r in rows]
        self.assertEqual(ts, sorted(ts, reverse=True))
        kay = {r["kaynak"] for r in rows}
        self.assertTrue({"audit", "is"} <= kay, kay)
        commit = next(r for r in rows if r["tur"] == "git.commit")
        self.assertEqual(commit["baglanti"], akis.GITHUB_REPO + "/commit/1a2b3c4")
        sentry = next(r for r in rows if r["tur"] == "sentry")
        self.assertEqual(sentry["baglanti"], akis.SENTRY_URL)
        # sayfalama: once → yalnız daha eski
        eski = akis.zaman_cizelgesi(limit=50, once="2026-09-01 11:30:00")
        self.assertTrue(all(r["ts"] < "2026-09-01 11:30:00" for r in eski))
        sadece_is = akis.zaman_cizelgesi(kaynaklar=["is"])
        self.assertTrue(all(r["kaynak"] == "is" for r in sadece_is) and sadece_is)


# Her bileşenden bir örnek (eski sahne.ornek(); demo üreticisi 2026-09-07'de üretimden çıktı).
TAM_SAHNE = {"baslik": "Örnek sahne", "bilesenler": [
    {"tur": "metin", "metin": "Sahne v1 — her bileşenden bir örnek."},
    {"tur": "kart", "baslik": "Nabız", "metin": "Her şey yolunda.", "durum": "iyi"},
    {"tur": "liste", "ogeler": ["ilk", "ikinci", {"metin": "üçüncü", "detay": "açıklama"}]},
    {"tur": "tablo", "sutunlar": ["site", "durum"], "satirlar": [["app-a.example.com", "iyi"], ["app-b.example.com", "iyi"]]},
    {"tur": "grafik", "baslik": "Ziyaret", "tip": "cizgi", "etiketler": ["Pzt", "Sal", "Çar"],
     "seriler": [{"ad": "example.com", "degerler": [12, 18, 9]}]},
    {"tur": "gorsel", "url": "/v1/sahne/mac.jpg"},
    {"tur": "diff", "metin": "--- a\n+++ b\n-eski satır\n+yeni satır"},
    {"tur": "satir", "cocuklar": [
        {"tur": "eylem", "id": "onay-ornek", "etiket": "Onayla", "veri": "evet"},
        {"tur": "eylem", "id": "ret-ornek", "etiket": "Vazgeç", "veri": "hayır", "stil": "tehlike"}]},
    {"tur": "sutun", "cocuklar": [{"tur": "form", "id": "form-ornek", "baslik": "Kısa form",
                                   "alanlar": [{"ad": "not", "etiket": "Not", "tip": "metin"}]}]},
    {"tur": "canli", "kaynak": "mac", "baslik": "Mac ekranı"},
]}


class Sahne(unittest.TestCase):
    def test_goster_oku_seq_uyari(self):
        s0 = sahne.oku()["seq"]
        seq, uyari = sahne.goster(TAM_SAHNE)
        self.assertEqual(seq, s0 + 1)
        self.assertEqual(uyari, [], uyari)
        d = sahne.oku()
        self.assertEqual((d["surum"], d["seq"], d["baslik"]), (1, seq, "Örnek sahne"))
        seq2, uyari2 = sahne.goster([{"tur": "uzaylı"}, {"tur": "canli", "kaynak": "buzdolabı"},
                                     {"tur": "kart"}], "eksik")
        self.assertEqual(seq2, seq + 1)
        self.assertEqual(len(uyari2), 3, uyari2)
        # akışa iz; döküme "Sahne güncellendi" satırı YOK (2026-10-04, gürültüydü)
        self.assertTrue(any(e["tur"] == "sahne" for e in akis.olaylar(0, 1000)))
        self.assertFalse(any("Sahne güncellendi" in (r.get("metin") or "") for r in kokpit.outbox_oku(0, 1000)))

    def test_bekle_degisince_doner(self):
        d = sahne.oku()
        t = time.time()
        threading.Timer(0.3, lambda: sahne.goster([{"tur": "metin", "metin": "yeni"}], "y")).start()
        yeni = sahne.bekle(d["seq"], bekle=5)
        self.assertLess(time.time() - t, 3)
        self.assertEqual(yeni["seq"], d["seq"] + 1)

    def test_eylem_kuyruga_eren_mesaji(self):
        kokpit.inbox_al()
        m = sahne.eylem("onay-x", "evet", None, "cihazA")
        f = sahne.eylem("form-x", None, {"not": "selam", "adet": 3}, "cihazA")
        gelen = kokpit.inbox_al()
        self.assertEqual([g["metin"] for g in gelen], [m, f])
        self.assertTrue(m.startswith("[Sahne eylem: onay-x] evet"))
        self.assertIn("adet: 3", f)
        self.assertEqual(kokpit.outbox_oku(0, 1000)[-1]["kaynak"], "sahip")


class ApiFaz1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), api.H)
        cls.srv.daemon_threads = True
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        cls.did, cls.key = kokpit.cihaz_ekle("test-faz1")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _get(self, path):
        return _istek(self.base, "GET", path, self.key, self.did)

    def _post(self, path, body):
        return _istek(self.base, "POST", path, self.key, self.did, body)

    def test_akis_ve_olaylar(self):
        st, j = self._get("/v1/akis?limit=5")
        self.assertEqual(st, 200)
        self.assertIn("akis", j)
        son = akis.olay_son()
        akis.olay_yaz("sohbet", {"tur": "metin", "metin": "x"})
        st, j = self._get("/v1/olaylar?since=%d&bekle=1" % son)
        self.assertEqual(st, 200)
        self.assertEqual(j["olaylar"][-1]["metin"], "x")
        self.assertGreater(j["son"], son)

    def test_sahne_uzun_yoklama_ve_eylem(self):
        d = sahne.oku()
        st, j = self._get("/v1/sahne")
        self.assertEqual((st, j["seq"]), (200, d["seq"]))
        threading.Timer(0.3, lambda: sahne.goster([{"tur": "metin", "metin": "a"}], "b")).start()
        st, j = self._get("/v1/sahne?since=%d&bekle=5" % d["seq"])
        self.assertEqual((st, j["seq"], j["baslik"]), (200, d["seq"] + 1, "b"))
        kokpit.inbox_al()
        st, j = self._post("/v1/eylem", {"id": "btn1", "veri": "tamam"})
        self.assertEqual(st, 200)
        self.assertEqual(kokpit.inbox_al()[0]["metin"], "[Sahne eylem: btn1] tamam")
        st, _ = self._post("/v1/eylem", {"veri": "idsiz"})
        self.assertEqual(st, 400)

    def test_canli_kaynaklar_testte_uykuda(self):
        for p in ("/v1/sahne/mac.jpg", "/v1/sahne/telefon.jpg", "/v1/sahne/telefon.mjpeg"):
            st, j = self._get(p)
            self.assertEqual(st, 503, p)
            self.assertIn("uzuv", j["hata"])

    def test_paylas_metin_ve_gorsel(self):
        kokpit.inbox_al()
        st, j = self._post("/v1/paylas", {"tur": "url", "url": "https://ornek.com/x", "not": "bak"})
        self.assertEqual(st, 200)
        self.assertEqual(kokpit.inbox_al()[0]["metin"], "[Paylaşım: url] https://ornek.com/x\nbak")
        # ham görsel: JPEG başlığı, image/jpeg gövdesi
        ham = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        ts, nonce = str(int(time.time())), os.urandom(8).hex()
        yol = "/v1/paylas?not=ekran"
        h = {"Content-Type": "image/jpeg", "X-Ghost-Device": self.did, "X-Ghost-Ts": ts,
             "X-Ghost-Nonce": nonce, "X-Ghost-Sig": kokpit.imza(self.key, ts, nonce, "POST", yol, ham)}
        req = urllib.request.Request(self.base + yol, data=ham, method="POST", headers=h)
        with urllib.request.urlopen(req, timeout=10) as r:
            j = json.loads(r.read().decode())
        self.assertTrue(j["ok"])
        m = kokpit.inbox_al()[0]["metin"]
        self.assertTrue(m.startswith("[Paylaşım: görsel] "))
        dosya = m.split("] ", 1)[1].split(" (")[0]
        self.assertTrue(os.path.exists(dosya) and dosya.endswith(".jpg"), m)
        self.assertIn("ekran", m)
        st, j = self._post("/v1/paylas", {"tur": "metin", "metin": ""})
        self.assertEqual(st, 400)
        # belge (PDF) — ham gövde, dosya adı sorguda; ad güvenli hale gelir
        ham = b"%PDF-1.4 test"
        ts, nonce = str(int(time.time())), os.urandom(8).hex()
        yol = "/v1/paylas?ad=fatura%20eyl%C3%BCl../x.pdf&not=oku"
        h = {"Content-Type": "application/pdf", "X-Ghost-Device": self.did, "X-Ghost-Ts": ts,
             "X-Ghost-Nonce": nonce, "X-Ghost-Sig": kokpit.imza(self.key, ts, nonce, "POST", yol, ham)}
        req = urllib.request.Request(self.base + yol, data=ham, method="POST", headers=h)
        with urllib.request.urlopen(req, timeout=10) as r:
            j = json.loads(r.read().decode())
        m = kokpit.inbox_al()[0]["metin"]
        self.assertTrue(m.startswith("[Paylaşım: belge] "), m)
        dosya = m.split("] ", 1)[1].split(" (")[0]
        self.assertTrue(os.path.exists(dosya) and dosya.endswith("x.pdf") and ".." not in os.path.basename(dosya))
        self.assertEqual(open(dosya, "rb").read(), ham)

    def test_sse_akisi(self):
        ts, nonce = str(int(time.time())), os.urandom(8).hex()
        d = sahne.oku()
        yol = "/v1/sse?olay=%d&outbox=%d&sahne=%d" % (akis.olay_son(), kokpit.outbox_son(), d["seq"])
        h = {"X-Ghost-Device": self.did, "X-Ghost-Ts": ts, "X-Ghost-Nonce": nonce,
             "X-Ghost-Sig": kokpit.imza(self.key, ts, nonce, "GET", yol, b"")}
        req = urllib.request.Request(self.base + yol, headers=h)
        r = urllib.request.urlopen(req, timeout=10)
        self.assertTrue(r.headers["Content-Type"].startswith("text/event-stream"))
        threading.Timer(0.2, lambda: kokpit.outbox_yaz("sse selam", "ghost")).start()
        threading.Timer(0.4, lambda: akis.olay_yaz("sohbet", {"tur": "arac", "ad": "Bash", "ozet": "ls"})).start()
        gorulen = set()
        son = time.time() + 6
        while time.time() < son and not {"merhaba", "mesaj", "olay"} <= gorulen:
            satir = r.readline().decode("utf-8")
            if satir.startswith("event: "):
                gorulen.add(satir[7:].strip())
        r.close()
        self.assertTrue({"merhaba", "mesaj", "olay"} <= gorulen, gorulen)


class RunClaudeAkis(unittest.TestCase):
    """Sahte CLI: stdout'a stream-json satırları basar; run_claude on_event ile koşar."""
    def _sahte(self, satirlar, rc=0, bekle=0):
        d = tempfile.mkdtemp(prefix="sahte-claude-")
        p = os.path.join(d, "claude")
        with open(p, "w") as f:
            f.write("#!/bin/sh\n")
            for s in satirlar:
                f.write("echo '%s'\n" % s.replace("'", "'\\''"))
                if bekle:
                    f.write("sleep %s\n" % bekle)
            f.write("echo 'hata metni' 1>&2\nexit %d\n" % rc)
        os.chmod(p, stat.S_IRWXU)
        return p

    def setUp(self):
        self._claude = motor.CLAUDE

    def tearDown(self):
        motor.CLAUDE = self._claude

    def test_akis_kipi_sonuc_ve_olaylar(self):
        motor.CLAUDE = self._sahte([json.dumps(s) for s in STREAM])
        gelen, kullanim = [], {}
        res, sid, err = tb.run_claude("selam", "m", session_id="sid-1", use_mcp=False,
                                      timeout=20, on_usage=kullanim.update, on_event=gelen.append)
        self.assertFalse(err, res)
        self.assertEqual((res, sid), ("Son commit abc1234.", "sid-1"))
        self.assertEqual(len(gelen), 4)
        self.assertEqual(kullanim.get("cost"), 0.01)

    def test_json_kipi_degismedi(self):
        motor.CLAUDE = self._sahte([json.dumps(STREAM[-1])])
        res, sid, err = tb.run_claude("selam", "m", session_id="sid-1", use_mcp=False, timeout=20)
        self.assertEqual((res, sid, err), ("Son commit abc1234.", "sid-1", False))

    def test_sonucsuz_olum_hata(self):
        motor.CLAUDE = self._sahte([json.dumps(STREAM[0])], rc=3)
        res, _, err = tb.run_claude("selam", "m", use_mcp=False, timeout=20, on_event=lambda s: None)
        self.assertTrue(err)
        self.assertIn("hata metni", res)

    def test_zaman_asimi_akis_kipinde(self):
        motor.CLAUDE = self._sahte([json.dumps(STREAM[0])] * 6, bekle=1)
        res, _, err = tb.run_claude("selam", "m", use_mcp=False, timeout=2, on_event=lambda s: None)
        self.assertTrue(err)
        self.assertIn("Zaman", res)


if __name__ == "__main__":
    unittest.main()


class Apns(unittest.TestCase):
    """Faz 2: token kaydı ve push kapısı (testte ağa çıkılmaz)."""
    def test_token_kayit_ve_liste(self):
        did, key = kokpit.cihaz_ekle("push-test")
        self.assertFalse(kokpit.apns_kaydet(did, "ZZZ", "development"))      # hex değil
        self.assertTrue(kokpit.apns_kaydet(did, "AB" * 32, "development"))
        cs = [c for c in kokpit.apns_cihazlar() if c["id"] == did]
        self.assertEqual((cs[0]["apns_token"], cs[0]["apns_env"]), ("ab" * 32, "development"))
        # Aynı token ikinci bir kayda yazılırsa eskisinden düşer; liste token başına TEK kayıt
        # (2026-09-07: 5 aktif kayıt aynı token → her bildirim 5 kez).
        did2, _ = kokpit.cihaz_ekle("push-test-2")
        self.assertTrue(kokpit.apns_kaydet(did2, "AB" * 32, "development"))
        sahipler = [c["id"] for c in kokpit.apns_cihazlar() if c["apns_token"] == "ab" * 32]
        self.assertEqual(sahipler, [did2])
        self.assertTrue(kokpit.apns_kaydet(did, "AB" * 32, "development"))   # geri al: test devamı did'i bekliyor
        from ghost import apns
        r = apns.herkese("selam", "gövde")
        self.assertTrue(all(not x["ok"] for x in r))                          # test: ağ yok
        kokpit.iptal(did)
        self.assertFalse(any(c["id"] == did for c in kokpit.apns_cihazlar()))
        self.assertEqual(set(apns.yuk("b", "g", {"sekme": "kararlar"})), {"aps", "sekme"})

    def test_api_apns_ucu(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), api.H)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d" % srv.server_address[1]
        did, key = kokpit.cihaz_ekle("push-api")
        st, j = _istek(base, "POST", "/v1/apns", key, did, {"token": "cd" * 32, "env": "development"})
        self.assertEqual((st, j["ok"]), (200, True))
        st, _ = _istek(base, "POST", "/v1/apns", key, did, {"token": "kısa"})
        self.assertEqual(st, 400)
        srv.shutdown()


class PushKapsami(unittest.TestCase):
    def test_alarm_isareti(self):
        from ghost import apns
        self.assertTrue(apns.alarm_mi("🚨 app-a.example.com düştü"))
        self.assertTrue(apns.alarm_mi("  ⚠️ tünel kopuk"))
        self.assertFalse(apns.alarm_mi("✅ her şey yolunda"))
        self.assertFalse(apns.alarm_mi("selam"))
        self.assertEqual(apns.alarm("bilgi mesajı"), [])
        # notify.telegram alarm kancası fırlatmaz (testte ağ yok)
        from ghost import notify
        self.assertTrue(notify.telegram("🚨 test alarmı", {"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_OWNER_ID": "1"}))


class LiveActivity(unittest.TestCase):
    def test_token_kayit_ve_bitis(self):
        from ghost import apns
        did, key = kokpit.cihaz_ekle("la-test")
        kokpit.apns_kaydet(did, "ab" * 32, "development")
        self.assertTrue(kokpit.la_token_kaydet(did, "start", "cd" * 32))
        self.assertTrue(kokpit.la_token_kaydet(did, "update", "ef" * 32, jid=41))
        self.assertFalse(kokpit.la_token_kaydet(did, "update", "ef" * 32))          # jid yok
        self.assertEqual(kokpit.la_start_tokenlar()[-1]["token"], "cd" * 32)
        self.assertEqual(kokpit.la_update_tokenlar(41)[0]["env"], "development")
        r = apns.etkinlik_baslat(41, "Mühendislik", "repo tara")
        self.assertTrue(r and not r[-1]["ok"])                                       # test: ağ yok
        apns.etkinlik_bitir(41, "Mühendislik", "bitti", "tamam")
        self.assertEqual(kokpit.la_update_tokenlar(41), [])                          # temizlendi
        st = apns.la_durum(41, "x", "bitti", "ö", 1, 2)
        self.assertEqual((st["durum"], st["bitti"]), ("bitti", 2))
        kokpit.iptal(did)

    def test_api_etkinlik_ucu(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), api.H)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d" % srv.server_address[1]
        did, key = kokpit.cihaz_ekle("la-api")
        st, j = _istek(base, "POST", "/v1/etkinlik", key, did, {"tur": "start", "token": "12" * 32})
        self.assertEqual((st, j["ok"]), (200, True))
        st, j = _istek(base, "POST", "/v1/etkinlik", key, did, {"tur": "update", "token": "34" * 32, "jid": "7"})
        self.assertEqual(st, 200)
        st, _ = _istek(base, "POST", "/v1/etkinlik", key, did, {"tur": "update", "token": "34" * 32})
        self.assertEqual(st, 400)
        srv.shutdown()


class BitmemisAkis(unittest.TestCase):
    """Köprü turun ortasında ölürse 'Ghost çalışıyor' takılı kalıyordu (Sahip, 2026-09-03).
    Sentetik sonucu yazan kod aynı süreçteydi; restart onu hiç koşturmuyordu."""

    def setUp(self):
        c = sqlite3.connect(config.DB_PATH, timeout=10)
        c.execute("DELETE FROM events WHERE type='chat'")
        c.commit()
        c.close()

    def test_kesilmis_akis_acilista_kapanir(self):
        akis.olay_yaz("sohbet", {"tur": "baslangic"})
        akis.olay_yaz("sohbet", {"tur": "arac", "ad": "Bash", "ozet": "test"})
        self.assertTrue(akis.sohbet_durumu()["aktif"])       # kesilmiş tur: meşgul görünüyor
        self.assertEqual(akis.bitmemis_kapat(), 1)
        d = akis.sohbet_durumu()
        self.assertFalse(d["aktif"])                          # açılış süpürmesi düzeltti
        self.assertEqual(d["ozet"], "")

    def test_biten_akis_tekrar_kapatilmaz(self):
        akis.olay_yaz("sohbet", {"tur": "baslangic"})
        akis.olay_yaz("sohbet", {"tur": "sonuc"})
        self.assertEqual(akis.bitmemis_kapat(), 0)            # zaten kapalı, gürültü yok
        self.assertFalse(akis.sohbet_durumu()["aktif"])

    def test_birden_cok_kaynak(self):
        akis.olay_yaz("sohbet", {"tur": "arac", "ad": "Bash"})
        akis.olay_yaz("is#7", {"tur": "arac", "ad": "Read"})
        akis.olay_yaz("is#8", {"tur": "sonuc"})
        self.assertEqual(akis.bitmemis_kapat(), 2)            # yalnız açık olanlar
        self.assertFalse(akis.sohbet_durumu()["aktif"])
        self.assertFalse(akis.sohbet_durumu("is#7")["aktif"])

    def test_sahne_kaydi_kapatilmaz(self):
        """source='sahne' bir tur değil, sahne basımlarının defteri — hiç 'sonuc' almaz.
        Süpürme oraya uzansaydı her açılışta sahte bir 'kesildi' satırı birikirdi."""
        akis.olay_yaz("sahne", {"tur": "sahne", "baslik": "Nabız", "seq": 1})
        self.assertEqual(akis.bitmemis_kapat(), 0)
        c = sqlite3.connect(config.DB_PATH, timeout=10)
        n = c.execute("SELECT COUNT(*) FROM events WHERE type='chat' AND source='sahne'").fetchone()[0]
        c.close()
        self.assertEqual(n, 1)          # tek satır kaldı, sentetik sonuç eklenmedi

    def test_sure_tur_basindan_sayilir(self):
        """Sayaç turun BAŞINDAN sayar, son araç çağrısından değil; ve önceki turun olayları
        karışmaz. Uygulama sekme/açılışta sayacı sıfırlıyordu (Sahip, 2026-09-26): kaynak
        artık sunucu."""
        akis.olay_yaz("sohbet", {"tur": "baslangic"})
        akis.olay_yaz("sohbet", {"tur": "sonuc"})                   # önceki tur, bitti
        b = akis.olay_yaz("sohbet", {"tur": "baslangic"})
        akis.olay_yaz("sohbet", {"tur": "arac", "ad": "Bash", "ozet": "x"})
        c = sqlite3.connect(config.DB_PATH, timeout=10)
        c.execute("UPDATE events SET ts=? WHERE id=?",
                  (time.strftime("%F %T", time.localtime(time.time() - 90)), b))
        c.commit()
        c.close()
        d = akis.sohbet_durumu()
        self.assertTrue(d["aktif"])
        self.assertTrue(88 <= d["sure_sn"] <= 95, d)              # 90 sn önce başladı
        self.assertEqual(d["basladi"][:16], time.strftime("%F %H:%M", time.localtime(time.time() - 90)))
        akis.olay_yaz("sohbet", {"tur": "sonuc"})
        self.assertEqual(akis.sohbet_durumu()["sure_sn"], 0)

    def test_sessizlik_tavani_yedek_kapi(self):
        """Süpürme kaçırsa bile (köprü hiç yeniden başlamadıysa) uzun sessizlik ölüm sayılır."""
        akis.olay_yaz("sohbet", {"tur": "arac", "ad": "Bash"})
        c = sqlite3.connect(config.DB_PATH, timeout=10)
        eski = time.strftime("%F %T", time.localtime(time.time() - akis.MESGUL_TAVAN_SN - 60))
        c.execute("UPDATE events SET ts=? WHERE type='chat'", (eski,))
        c.commit()
        c.close()
        self.assertFalse(akis.sohbet_durumu()["aktif"])


class AcikNabiz(unittest.TestCase):
    """sahip.kim Ghost kartı: /v1/acik/nabiz 'durum' alanı. 2026-10-26 kusuru: sohbet_durumu()
    dict'i hep doğru sayılıyordu → kart hep 'sahiple konuşuyor'; 'is' yanlış kaynağa bakıyordu."""
    def _cek(self, sohbet, is_aktif):
        from unittest import mock
        api.H._ACIK_CACHE = {"at": 0.0, "obj": None}
        srv = ThreadingHTTPServer(("127.0.0.1", 0), api.H)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d" % srv.server_address[1]
        try:
            with mock.patch.object(akis, "sohbet_durumu", return_value={"aktif": sohbet, "ozet": "", "ts": "", "sure_sn": 0, "basladi": ""}), \
                 mock.patch.object(api.health, "_isler", return_value={"aktif": is_aktif, "bekleyen_onay": 0, "ozet": []}):
                st, j = _istek(base, "GET", "/v1/acik/nabiz")
        finally:
            srv.shutdown()
            api.H._ACIK_CACHE = {"at": 0.0, "obj": None}
        self.assertEqual(st, 200)
        return j

    def test_bosta_nobette(self):
        j = self._cek(sohbet=False, is_aktif=0)
        self.assertEqual(j["durum"], "nobette")
        self.assertEqual(set(j), {"gun", "siteler_ok", "siteler_n", "durum"})  # kimlik/içerik sızmaz

    def test_sohbet_oncelikli(self):
        self.assertEqual(self._cek(sohbet=True, is_aktif=1)["durum"], "sohbet")

    def test_is_kuyrugu(self):
        self.assertEqual(self._cek(sohbet=False, is_aktif=2)["durum"], "is")
