"""Giriş kapıları (2026-09-19, Sahip: "Ghost'u benim dışımda kimse kullanamamalı"):
Kokpit eşleme deneme tavanı, webhook metin işi hız sınırı, SSH giriş nöbetçisi."""
import os
import unittest

from tests import common  # noqa: F401

from ghost import config, kokpit, webhook  # noqa: E402
from ops import giris_nobeti  # noqa: E402


class EslesmeTavani(unittest.TestCase):
    def setUp(self):
        c = kokpit._db()
        c.execute("DELETE FROM kokpit_devices WHERE durum='bekliyor'")
        c.commit()
        c.close()
        kokpit._hata_say(sifirla=True)

    def test_bes_yanlis_kodu_iptal_eder(self):
        kod = kokpit.kod_uret("iPhone")
        yanlis = "000000" if kod != "000000" else "111111"
        for _ in range(kokpit.ESLESME_DENEME_TAVANI - 1):
            self.assertIsNone(kokpit.esle(yanlis))
        # tavanın altında doğru kod hâlâ çalışır
        self.assertIsNotNone(kokpit.esle(kod, "tel"))

    def test_tavan_asilinca_dogru_kod_bile_gecmez(self):
        kod = kokpit.kod_uret("iPhone")
        yanlis = "000000" if kod != "000000" else "111111"
        for _ in range(kokpit.ESLESME_DENEME_TAVANI):
            self.assertIsNone(kokpit.esle(yanlis))
        self.assertIsNone(kokpit.esle(kod))  # bekleyen kod silindi
        self.assertFalse(os.path.exists(kokpit._hata_dosyasi()))  # sayaç sıfırlandı

    def test_yeni_kod_sayaci_sifirlar(self):
        kod = kokpit.kod_uret("a")
        yanlis = "000000" if kod != "000000" else "111111"
        for _ in range(kokpit.ESLESME_DENEME_TAVANI - 1):
            kokpit.esle(yanlis)
        kod2 = kokpit.kod_uret("b")
        for _ in range(kokpit.ESLESME_DENEME_TAVANI - 1):
            kokpit.esle("000000" if kod2 != "000000" else "111111")
        self.assertIsNotNone(kokpit.esle(kod2))


class WebhookHiz(unittest.TestCase):
    def setUp(self):
        try:
            os.remove(os.path.join(config.OPS, "webhook_hiz.json"))
        except OSError:
            pass

    def test_tavan_ve_pencere(self):
        t0 = 1_000_000.0
        for i in range(webhook.HIZ_TAVAN):
            self.assertFalse(webhook._hiz_asildi("kestirme", t0 + i))
        self.assertTrue(webhook._hiz_asildi("kestirme", t0 + 100))
        self.assertFalse(webhook._hiz_asildi("baska", t0 + 100))  # kaynak başına
        self.assertFalse(webhook._hiz_asildi("kestirme", t0 + webhook.HIZ_PENCERE_SN + 1))

    def test_handle_hook_429(self):
        for _ in range(webhook.HIZ_TAVAN):
            code, _ = webhook.handle_hook("kestirme", {"text": "durumu özetle"}, True)
            self.assertEqual(code, 200)
        code, msg = webhook.handle_hook("kestirme", {"text": "durumu özetle"}, True)
        self.assertEqual(code, 429)


class GirisNobeti(unittest.TestCase):
    def test_satir_ayristir(self):
        g = giris_nobeti.satir_ayristir(
            "Accepted publickey for ghost from 203.0.113.5 port 33793 ssh2: ED25519 SHA256:abc")
        self.assertEqual(g, {"yontem": "publickey", "kul": "ghost", "ip": "203.0.113.5", "parmak": "SHA256:abc"})
        eski = giris_nobeti.satir_ayristir("Accepted publickey for ghost from 1.2.3.4 port 5")
        self.assertEqual(eski["ip"], "1.2.3.4")
        self.assertIsNone(eski["parmak"])                   # parmak izi yoksa satır yine okunur
        self.assertIsNone(giris_nobeti.satir_ayristir("Failed password for root from 1.2.3.4 port 1"))
        self.assertIsNone(giris_nobeti.satir_ayristir(""))

    def test_degerlendir(self):
        bilinen = {"203.0.113.5": "x"}
        self.assertIsNone(giris_nobeti.degerlendir({"yontem": "publickey", "kul": "ghost", "ip": "203.0.113.5"}, bilinen))
        self.assertEqual(giris_nobeti.degerlendir({"yontem": "publickey", "kul": "ghost", "ip": "9.9.9.9"}, bilinen),
                         "yeni yerden giriş")
        self.assertIn("PAROLA", giris_nobeti.degerlendir({"yontem": "password", "kul": "ghost", "ip": "203.0.113.5"}, bilinen))
        self.assertEqual(giris_nobeti.degerlendir({"yontem": "publickey", "kul": "root", "ip": "203.0.113.5"}, bilinen),
                         "root girişi")
        self.assertIn("YENİ IP", giris_nobeti.degerlendir({"yontem": "publickey", "kul": "root", "ip": "9.9.9.9"}, bilinen))

    def test_ghost_root_anahtari_uyari_degil(self):
        """Ghost'un kendi Mac anahtarıyla (reboot rayı, rsync) root girişi beklenen kullanımdır;
        2026-09-26 sabahı 5 sahte uyarı üretti. Kimlik anahtar, IP değil: yeni IP'den de sessiz.
        Başka anahtarla root girişi hâlâ uyarır."""
        ghost = next(iter(giris_nobeti.GHOST_ROOT_PARMAKLAR))
        self.assertIsNone(giris_nobeti.degerlendir(
            {"yontem": "publickey", "kul": "root", "ip": "9.9.9.9", "parmak": ghost}, {}))
        self.assertIn("root girişi", giris_nobeti.degerlendir(
            {"yontem": "publickey", "kul": "root", "ip": "9.9.9.9", "parmak": "SHA256:baska"}, {}))
        self.assertIn("root girişi", giris_nobeti.degerlendir(
            {"yontem": "publickey", "kul": "root", "ip": "9.9.9.9", "parmak": None}, {}))
        # parola ile root, anahtar ne olursa olsun uyarı
        self.assertIn("PAROLA", giris_nobeti.degerlendir(
            {"yontem": "password", "kul": "root", "ip": "1.1.1.1", "parmak": ghost}, {}))


if __name__ == "__main__":
    unittest.main()
