"""Live Activity (iş kartı) kapanışı — 2026-11-26 teşhisi (dossier/kokpit/canli-etkinlik-kapanmiyor-2026-11-26.md).

Sınananlar: aynı push-to-start belirtecine tek push; start yükünde input-push-token + stale-date; güncelleme belirteci
varsa end (kart hemen kalkar); yoksa ya da end reddedilirse iş numaralı sessiz push (yalnız telefona); her push
ops/apns.jsonl'e tur=la satırı, belirteç değeri satırda YOK; GHOST_TEST altında gerçek gönderici ağa çıkmaz;
/v1/nabiz isler.aktif_idler.
"""
import json
import os
import unittest

from tests import common  # noqa: F401

from ghost import apns, config, health, kokpit  # noqa: E402


def _cihaz(did, ad, apns_token=None, start=None):
    c = kokpit._db()
    c.execute("INSERT OR REPLACE INTO kokpit_devices(id, ad, key_hex, durum, created, last_seen, apns_token, apns_env, "
              "la_start_token) VALUES(?,?,'','aktif','2026-11-26','2026-11-26',?,'development',?)",
              (did, ad, apns_token, start))
    c.commit()
    c.close()


def _gunluk():
    yol = os.path.join(config.OPS, "apns.jsonl")
    if not os.path.exists(yol):
        return []
    with open(yol, encoding="utf-8") as f:
        return [json.loads(s) for s in f if '"tur": "la"' in s]


class CanliEtkinlik(unittest.TestCase):
    START = "5a" * 80
    UPDATE = "6b" * 40
    APNS = "7c" * 32

    def setUp(self):
        c = kokpit._db()
        c.execute("DELETE FROM kokpit_devices")
        c.execute("DELETE FROM kokpit_etkinlik")
        c.commit()
        c.close()
        yol = os.path.join(config.OPS, "apns.jsonl")
        if os.path.exists(yol):
            os.remove(yol)
        # aynı telefonun dört eşleme kaydı (canlı ölçüm 2026-11-26): start belirteci dördünde aynı, bildirim belirteci
        # yalnız sonuncuda (apns_kaydet eskileri boşaltıyor)
        for i in range(3):
            _cihaz("eski%d" % i, "iPhone", None, self.START)
        _cihaz("tel", "iPhone", self.APNS, self.START)
        _cihaz("saat", "Apple Watch", "8d" * 32, None)
        self._la, self._sessiz, self._turler = apns._la_gonder, apns.sessiz, apns.cihaz_turleri
        self.la, self.ses = [], []

        def la(token, env, aps, event):
            self.la.append({"token": token, "aps": aps, "event": event})
            return self.la_sonuc
        self.la_sonuc = (True, 200, "")

        def ses(token, env, veri=None):
            self.ses.append({"token": token, "veri": veri})
            return True, 200, ""
        apns._la_gonder, apns.sessiz = la, ses
        apns.cihaz_turleri = lambda: {"tel": "tam", "saat": "cep"}

    def tearDown(self):
        apns._la_gonder, apns.sessiz, apns.cihaz_turleri = self._la, self._sessiz, self._turler

    def test_baslat_ayni_belirtece_tek_push(self):
        r = apns.etkinlik_baslat(51, "Mühendislik", "kart kapanmıyor")
        self.assertEqual(len(self.la), 1)
        self.assertEqual(len(r), 1)
        aps = self.la[0]["aps"]
        self.assertEqual(aps["event"], "start")
        self.assertEqual(aps["input-push-token"], 1)
        self.assertEqual(aps["attributes"], {"jid": 51, "tur": "Mühendislik"})
        self.assertGreater(aps["stale-date"], aps["timestamp"] + 6 * 3600)
        self.assertEqual(set(aps["content-state"]), {"durum", "ozet", "basladi", "bitti"})

    def test_belirtec_varsa_end_ve_hemen_kalkar(self):
        kokpit.la_token_kaydet("tel", "update", self.UPDATE, jid=52)
        r = apns.etkinlik_bitir(52, "Mühendislik", "bitti", "tamam", basladi=100)
        self.assertEqual([x["event"] for x in self.la], ["end"])
        aps = self.la[0]["aps"]
        self.assertLess(aps["dismissal-date"], aps["timestamp"])          # geçmiş tarih → hemen kalkar
        self.assertEqual(aps["content-state"]["durum"], "bitti")
        self.assertEqual(aps["content-state"]["basladi"], 100)
        self.assertEqual(self.ses, [])                                     # yedeğe gerek yok
        self.assertEqual(kokpit.la_update_tokenlar(52), [])
        self.assertEqual([x["cihaz"] for x in r], ["tel"])

    def test_belirtec_yoksa_yedek_sessiz_push_yalniz_telefona(self):
        r = apns.etkinlik_bitir(53, "İstihbarat", "hata", "")
        self.assertEqual(self.la, [])
        self.assertEqual([s["token"] for s in self.ses], [self.APNS])     # saat yok, eski kayıtların belirteci yok
        self.assertEqual(self.ses[0]["veri"], {"etkinlik_bitti": 53})
        self.assertTrue(r and r[0]["yedek"])

    def test_end_reddedilirse_yedek(self):
        kokpit.la_token_kaydet("tel", "update", self.UPDATE, jid=54)
        self.la_sonuc = (False, 410, '{"reason":"Unregistered"}')
        apns.etkinlik_bitir(54, "iş", "bitti", "")
        self.assertEqual(len(self.la), 1)
        self.assertEqual(self.ses[0]["veri"], {"etkinlik_bitti": 54})

    def test_gunluk_satiri_belirtec_tasimaz(self):
        apns.etkinlik_baslat(55, "iş", "")
        kokpit.la_token_kaydet("tel", "update", self.UPDATE, jid=55)
        self.la_sonuc = (False, 400, '{"reason":"BadDeviceToken"}')
        apns.etkinlik_bitir(55, "iş", "bitti", "")
        g = _gunluk()
        self.assertEqual([x["olay"] for x in g], ["start", "end", "yedek"])
        self.assertTrue(all(x["jid"] == 55 for x in g))
        self.assertEqual(g[1]["neden"], "BadDeviceToken")
        self.assertEqual((g[0]["kod"], g[0]["ok"], g[1]["kod"], g[1]["ok"]), (200, True, 400, False))
        ham = open(os.path.join(config.OPS, "apns.jsonl"), encoding="utf-8").read()
        for t in (self.START, self.UPDATE, self.APNS):
            self.assertNotIn(t, ham)

    def test_test_ortaminda_ag_yok(self):
        apns._la_gonder, apns.sessiz = self._la, self._sessiz              # gerçek göndericiler
        self.assertEqual(apns._la_gonder("ab" * 32, "development", {}, "end"), (False, 0, "test"))
        self.assertEqual(apns.sessiz("ab" * 32, "development", {"etkinlik_bitti": 1}), (False, 0, "test"))
        r = apns.etkinlik_bitir(56, "iş", "bitti", "")
        self.assertTrue(r and not any(x["ok"] for x in r))

    def test_nabiz_aktif_idler(self):
        d = os.path.join(config.OPS, "jobs")
        os.makedirs(d, exist_ok=True)
        for jid, st in ((60, "bitti"), (61, "çalışıyor"), (62, "denetimde"), (63, "iptal")):
            with open(os.path.join(d, "%d.json" % jid), "w", encoding="utf-8") as f:
                json.dump({"status": st, "kind": "iş"}, f)
        try:
            isler = health._isler()
            self.assertEqual(isler["aktif_idler"], [61, 62])
            self.assertEqual(isler["aktif"], 2)
        finally:
            for jid in (60, 61, 62, 63):
                os.remove(os.path.join(d, "%d.json" % jid))


if __name__ == "__main__":
    unittest.main()
