"""APNs cihaz başına konu (Kokpit Watch aşama 1, yama 01): saat (cep türü + bildirim belirteci) kendi paket
kimliğiyle, telefon eskisi gibi; konu istemciden gelmez, cihaz türünden türetilir."""
import unittest

from tests import common  # noqa: F401

from ghost import apns, kokpit  # noqa: E402


def _cihaz(ad, token):
    c = kokpit._db()
    did = "t" + token[:10]
    c.execute("INSERT OR REPLACE INTO kokpit_devices(id, ad, key_hex, durum, created, last_seen, apns_token, apns_env) "
              "VALUES(?,?,'','aktif','2026-11-23','2026-11-23',?,?)", (did, ad, token, "development"))
    c.commit()
    c.close()
    return did


class KonuTest(unittest.TestCase):
    def setUp(self):
        c = kokpit._db()
        c.execute("DELETE FROM kokpit_devices")
        c.commit()
        c.close()
        self.tel = _cihaz("iPhone", "aa" * 32)
        self.saat = _cihaz("Apple Watch", "bb" * 32)
        self._turler, self._gonder, self._sessiz = apns.cihaz_turleri, apns.gonder, apns.sessiz
        apns.cihaz_turleri = lambda: {self.tel: "tam", self.saat: "cep"}
        # konu adı ortamdan (.env) gelebilir; test koddaki varsayılanı sınar, makinenin ayarını değil
        self._ayar = apns._ayar
        apns._ayar = lambda ad, varsayilan="": (varsayilan if ad in ("APNS_TOPIC", "APNS_TOPIC_SAAT")
                                                else self._ayar(ad, varsayilan))
        self.giden = []

        def sahte(token, env, baslik, govde, veri=None, ses=True, rozet=None, collapse=None, onem=None, konu=None, **kw):
            self.giden.append({"token": token, "konu": konu, "onem": onem, "collapse": collapse})
            return True, 200, ""
        apns.gonder = sahte

    def tearDown(self):
        apns.cihaz_turleri, apns.gonder, apns.sessiz = self._turler, self._gonder, self._sessiz
        apns._ayar = self._ayar

    def test_konu_adlari(self):
        self.assertEqual(apns.konu_ana(), "com.example.ghost.kokpit")
        self.assertEqual(apns.konu_saat(), "com.example.ghost.kokpit.watchkitapp")
        self.assertEqual(apns.konu_sec("telefon"), apns.konu_ana())
        self.assertEqual(apns.konu_sec("saat"), apns.konu_saat())
        self.assertEqual(apns.konu_sec("bilinmeyen"), apns.konu_ana())

    def test_herkese_cihaz_basina_konu(self):
        r = apns.herkese("Ghost", "selam", {"sekme": "sohbet"}, collapse="sohbet")
        konular = {g["token"]: g["konu"] for g in self.giden}
        self.assertEqual(konular, {"aa" * 32: "com.example.ghost.kokpit",
                                   "bb" * 32: "com.example.ghost.kokpit.watchkitapp"})
        self.assertEqual({x["cihaz"]: x["sinif"] for x in r}, {self.tel: "telefon", self.saat: "saat"})

    def test_tur_okunamazsa_telefon_sayilir(self):
        apns.cihaz_turleri = lambda: {}
        apns.herkese("Ghost", "selam")
        self.assertEqual({g["konu"] for g in self.giden}, {"com.example.ghost.kokpit"})

    def test_cihaz_turleri_hata_yutar(self):
        apns.cihaz_turleri = self._turler
        eski = kokpit.cihazlar
        kokpit.cihazlar = lambda: (_ for _ in ()).throw(RuntimeError("servis yok"))
        try:
            self.assertEqual(apns.cihaz_turleri(), {})
        finally:
            kokpit.cihazlar = eski

    def test_iptal_cihaz_tur_tablosuna_girmez(self):
        apns.cihaz_turleri = self._turler
        eski = kokpit.cihazlar
        kokpit.cihazlar = lambda: [{"id": "a", "tur": "cep", "durum": "iptal"}, {"id": "b", "tur": "cep", "durum": "aktif"},
                                   {"id": "c", "durum": "aktif"}]
        try:
            self.assertEqual(apns.cihaz_turleri(), {"b": "cep", "c": "tam"})
        finally:
            kokpit.cihazlar = eski

    def test_sessiz_push_saate_gitmez(self):
        gorulen = []
        apns.sessiz = lambda token, env, veri=None: (gorulen.append(token) or (True, 200, ""))
        r = apns.sessiz_herkese({"gorev": 1})
        self.assertEqual(gorulen, ["aa" * 32])
        self.assertEqual([x["cihaz"] for x in r], [self.tel])

    def test_konu_basliga_girer(self):
        """Konu gerçekten APNs başlığına yazılıyor (gonder testte ağa çıkmadığı için başlık kurucusu sınanır)."""
        h = apns.uyari_basliklari("JWT", apns.konu_saat(), "sohbet")
        self.assertIn("apns-topic: com.example.ghost.kokpit.watchkitapp", h)
        self.assertIn("apns-collapse-id: sohbet", h)
        self.assertIn("authorization: bearer JWT", h)
        self.assertIn("apns-push-type: alert", h)
        h = apns.uyari_basliklari("JWT")                        # konu verilmezse telefonunki; collapse yoksa başlık yok
        self.assertIn("apns-topic: com.example.ghost.kokpit", h)
        self.assertFalse([x for x in h if x.startswith("apns-collapse-id")])
        self.assertEqual(len([x for x in h if x.startswith("apns-topic")]), 1)

    def test_onay_servisi_bicimindeki_kayit(self):
        """Tür tablosu onay servisinin gerçek kayıt biçiminden kuruluyor (id, ad, durum, created, last_seen, tur)."""
        apns.cihaz_turleri = self._turler
        eski = kokpit.cihazlar
        kokpit.cihazlar = lambda: [
            {"id": self.tel, "ad": "iPhone", "durum": "aktif", "created": "x", "last_seen": "y", "tur": "tam"},
            {"id": self.saat, "ad": "Apple Watch", "durum": "aktif", "created": "x", "last_seen": "y", "tur": "cep"}]
        try:
            apns.herkese("Ghost", "selam")
        finally:
            kokpit.cihazlar = eski
        self.assertEqual({g["token"]: g["konu"] for g in self.giden},
                         {"aa" * 32: "com.example.ghost.kokpit", "bb" * 32: "com.example.ghost.kokpit.watchkitapp"})

    def test_gonder_imzasi_geriye_uyumlu(self):
        apns.gonder = self._gonder          # gerçek işlev: testte ağa çıkmaz, konu parametresi isteğe bağlı
        self.assertEqual(apns.gonder("aa" * 32, "development", "b", "g"), (False, 0, "test"))
        self.assertEqual(apns.gonder("aa" * 32, "development", "b", "g", konu="x.y"), (False, 0, "test"))


if __name__ == "__main__":
    unittest.main()
