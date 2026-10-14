"""Faz 4 — Telegram ↔ kokpit eşitliği: medya dökümü, sayfalama, meşguliyet, paylaşım istemi,
nöbetçi mesajlarının dökümü, sesli işareti."""
import json
import os
import sys
import time
import unittest

from tests import common  # noqa: F401

from ghost import akis, bus, channels, config, kokpit, notify  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops"))
import telegram_bridge as tb  # noqa: E402


def _dosya(ad, icerik=b"\xff\xd8\xff\xe0test"):
    p = os.path.join(config.OPS, ad)
    with open(p, "wb") as f:
        f.write(icerik)
    return p


class MedyaDokumu(unittest.TestCase):
    def test_medya_yol_ile_yazilir_kopyalanir_ve_sunulur(self):
        p = _dosya("ekran.jpg")
        rid = kokpit.outbox_yaz("Mac ekranı", "ghost", medya=p)
        os.remove(p)                                  # özgün silinse de kopya yaşar
        r = kokpit.outbox_oku(rid - 1)[0]
        self.assertEqual(r["medya"]["tur"], "gorsel")
        self.assertEqual(r["medya"]["mime"], "image/jpeg")
        self.assertNotIn("yol", r["medya"])           # sunucu yolu istemciye çıkmaz
        yol, mime, ad = kokpit.medya_yol(rid)
        self.assertTrue(os.path.isfile(yol))
        self.assertEqual((mime, ad), ("image/jpeg", "ekran.jpg"))
        self.assertIsNone(kokpit.medya_yol(rid + 999))

    def test_medya_turleri(self):
        self.assertEqual(kokpit.medya_tur("a.mp4"), "video")
        self.assertEqual(kokpit.medya_tur("a.mp3"), "ses")
        self.assertEqual(kokpit.medya_tur("rapor.pdf"), "belge")
        self.assertEqual(kokpit.medya_mime("rapor.pdf"), "application/pdf")
        self.assertIsNone(kokpit.medya_sakla("/yok/boyle/dosya.jpg"))

    def test_app_medya_dokume_yazar(self):
        p = _dosya("foto.png", b"\x89PNG\r\n\x1a\nx")
        rid = channels.app_medya(p, "bak")
        self.assertIsNotNone(rid)
        self.assertEqual(kokpit.outbox_oku(rid - 1)[0]["metin"], "bak")

    def test_sayfalama_en_son_ve_daha_eski(self):
        ids = [kokpit.outbox_yaz("m%d" % i, "ghost") for i in range(6)]
        son3 = kokpit.outbox_oku(-1, 3)
        self.assertEqual([r["id"] for r in son3], ids[-3:])          # en son 3, artan sıra
        eski = kokpit.outbox_oku(0, 2, once=ids[-3])
        self.assertEqual([r["id"] for r in eski], ids[-5:-3])
        self.assertEqual([r["id"] for r in kokpit.outbox_oku(ids[-2])], ids[-1:])


class Mesguliyet(unittest.TestCase):
    def test_sohbet_durumu(self):
        bus.emit("chat", "sohbet", {"tur": "baslangic", "model": "x"}, status="done")
        bus.emit("chat", "sohbet", {"tur": "arac", "ad": "Bash", "ozet": "ls -la"}, status="done")
        d = akis.sohbet_durumu()
        self.assertTrue(d["aktif"])
        self.assertIn("Bash", d["ozet"])
        bus.emit("chat", "sohbet", {"tur": "sonuc", "hata": False}, status="done")
        self.assertFalse(akis.sohbet_durumu()["aktif"])

    def test_zaman_asimi_sentetik_sonuc(self):
        """run_claude stream kipinde result satırı gelmezse sentetik sonuç olayı düşer."""
        olaylar = []
        cb = lambda satir: olaylar.extend(akis.stream_olaylar(satir))  # noqa: E731
        eski = tb.CLAUDE
        tb.CLAUDE = "/bin/sh"      # "-p prompt --output-format ..." → sh hata verir, result yok
        try:
            res, _, err = tb.run_claude("x", "m", on_event=cb, timeout=5, use_mcp=False)
        finally:
            tb.CLAUDE = eski
        self.assertTrue(err)
        self.assertEqual(olaylar[-1]["tur"], "sonuc")
        self.assertTrue(olaylar[-1]["hata"])


class PaylasimEsitligi(unittest.TestCase):
    def test_medya_prompt_ortak(self):
        g = tb._medya_prompt("gorsel", "/x/a.jpg")
        self.assertIn("Read ile aç", g)
        self.assertIn("önemli olanı anlat", g)
        b = tb._medya_prompt("belge", "/x/r.pdf", "r.pdf", "özetle")
        self.assertIn("(ad: r.pdf)", b)
        self.assertTrue(b.endswith("özetle"))

    def test_check_kokpit_paylasim_ve_sesli(self):
        class M:
            chat_id = 1
            def __init__(self): self.sohbet = []
            def chat(self, t, spoken=False): self.sohbet.append(t)
        m = M()
        kokpit.inbox_al(); kokpit.kararlar_al()
        kokpit.mesaj_kuyrukla("[Paylaşım: görsel] /tmp/a.jpg (3 KB)", "c", yol="/tmp/a.jpg", tur="gorsel",
                              ad="a.jpg", not_="bu ne")
        kokpit.mesaj_kuyrukla("selam", "c", sesli=True)
        yollar = []
        eski_rt, eski_send = tb.route_text, tb.send
        tb.route_text = lambda t, mgr, spoken=False: yollar.append((t, spoken))
        tb.send = lambda *a, **k: None
        try:
            tb.check_kokpit(m)
        finally:
            tb.route_text, tb.send = eski_rt, eski_send
        self.assertEqual(m.sohbet, [tb._medya_prompt("gorsel", "/tmp/a.jpg", "a.jpg", "bu ne")])
        self.assertEqual(yollar, [("selam", True)])


class CevapKanali(unittest.TestCase):
    def test_son_kanal_kokpit_ise_cevap_push(self):
        from ghost import apns
        gidenler = []
        eski = apns.herkese
        apns.herkese = lambda *a, **k: gidenler.append(a[0])
        try:
            channels.birincil_ayarla("telegram")
            channels.son_kanal_ayarla("telegram")
            channels.AppKanal().send("cevap")
            self.assertEqual(gidenler, [])                    # Telegram'dan soruldu → push yok
            channels.son_kanal_ayarla("kokpit")
            channels.AppKanal().send("cevap")
            self.assertEqual(gidenler, ["Ghost"])             # uygulamadan soruldu → push
            channels.AppKanal().send("onay?", [[("ok", "ok:1")]])
            self.assertEqual(gidenler[-1], "Ghost onay bekliyor")
            self.assertEqual(channels.birincil(), "telegram")  # birincil karar değişmedi
            channels.son_kanal_ayarla("saçma")
            self.assertEqual(channels.son_kanal(), "kokpit")
        finally:
            apns.herkese = eski


class NobetciDokumu(unittest.TestCase):
    def test_notify_telegram_dokume_yazar(self):
        son = kokpit.outbox_son()
        notify.telegram("⚠️ tünel koptu", env={"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_OWNER_ID": "1"})
        rows = kokpit.outbox_oku(son)
        self.assertEqual(rows[-1]["metin"], "⚠️ tünel koptu")
        self.assertEqual(rows[-1]["kaynak"], "ghost")

    def test_bekleyen_karar_niyet_onizlemesi(self):
        with open(os.path.join(config.OPS, "pending.json"), "w") as f:
            json.dump({"p1": {"text": "yarın 9'da ara", "kind": "intent",
                              "intent": {"preview": "⏰ Hatırlatma kurayım mı?"}},
                       "p2": {"text": "MAIL|a@b.c|Konu|Gövde", "kind": "mail"}}, f)
        k = {x["id"]: x["metin"] for x in kokpit.bekleyen_kararlar()}
        self.assertEqual(k["p1"], "⏰ Hatırlatma kurayım mı?")
        self.assertIn("Kime: a@b.c", k["p2"])


if __name__ == "__main__":
    unittest.main()
