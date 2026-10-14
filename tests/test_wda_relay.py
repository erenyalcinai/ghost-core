"""WDA rölesi — hedef bulma sırası ve doğrulama.

Röle Mac'te koşar, ama mantığı burada test edilebilir: ağ çağrısı tek bir
noktadan (_canli) geçtiği için taklit edilebiliyor. Test edilen asıl şey
SIRA: yanlış bir önbellek yüzünden telefonun kaybedilmemesi.
"""
import os
import sys
import unittest

from tests import common  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "ops"))
import wda_relay  # noqa: E402


class TestHedefBulma(unittest.TestCase):
    def setUp(self):
        self.yedek = {ad: getattr(wda_relay, ad) for ad in
                      ("_canli", "_onbellek_oku", "_onbellek_yaz",
                       "_logdan_ip", "_usb_yedegi", "_tarama")}
        wda_relay._onbellek_yaz = lambda hedef: None
        wda_relay._usb_yedegi = lambda: None
        wda_relay._tarama = lambda: None
        wda_relay._hedef, wda_relay._hedef_ts = None, 0.0
        os.environ.pop("GHOST_WDA_HOST", None)

    def tearDown(self):
        for ad, deger in self.yedek.items():
            setattr(wda_relay, ad, deger)
        wda_relay._hedef, wda_relay._hedef_ts = None, 0.0
        os.environ.pop("GHOST_WDA_HOST", None)

    def test_logdan_ip_son_satiri_alir(self):
        """WDA her başlayışta yeni satır yazar; geçerli olan SONUNCUSU."""
        yol = os.path.join(os.environ["GHOST_OPS"], "wda.log")
        with open(yol, "w", encoding="utf-8") as f:
            f.write("ServerURLHere->http://192.168.1.9:8100<-ServerURLHere\n"
                    "gürültü\n"
                    "ServerURLHere->http://192.168.1.107:8100<-ServerURLHere\n")
        wda_relay.WDA_LOG = yol
        self.assertEqual(wda_relay._logdan_ip(), ("192.168.1.107", 8100))

    def test_bayat_onbellek_telefonu_kaybettirmez(self):
        """Önbellek eski IP'yi tutuyorsa log'daki adrese geçilmeli."""
        wda_relay._onbellek_oku = lambda: ("192.168.1.250", 8100)
        wda_relay._logdan_ip = lambda: ("192.168.1.107", 8100)
        wda_relay._canli = lambda ip, port, zaman=3: ip == "192.168.1.107"
        self.assertEqual(wda_relay.hedef_bul(zorla=True), ("192.168.1.107", 8100))

    def test_hicbir_yol_yoksa_none(self):
        wda_relay._onbellek_oku = lambda: None
        wda_relay._logdan_ip = lambda: None
        wda_relay._canli = lambda *a, **k: False
        self.assertIsNone(wda_relay.hedef_bul(zorla=True))

    def test_acik_port_yetmez_dogrulama_sart(self):
        """Adayın 'canlı' sayılması için WDA cevabı gerekir; port açık olması değil."""
        cagrilar = []
        wda_relay._onbellek_oku = lambda: ("192.168.1.250", 8100)
        wda_relay._logdan_ip = lambda: None
        def sahte(ip, port, zaman=3):
            cagrilar.append(ip)
            return False
        wda_relay._canli = sahte
        self.assertIsNone(wda_relay.hedef_bul(zorla=True))
        self.assertIn("192.168.1.250", cagrilar)

    def test_ortam_degiskeni_once_gelir(self):
        os.environ["GHOST_WDA_HOST"] = "10.0.0.5:8100"
        wda_relay._onbellek_oku = lambda: ("192.168.1.107", 8100)
        wda_relay._logdan_ip = lambda: None
        wda_relay._canli = lambda *a, **k: True
        self.assertEqual(wda_relay.hedef_bul(zorla=True), ("10.0.0.5", 8100))


if __name__ == "__main__":
    unittest.main()
