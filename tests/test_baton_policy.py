"""Bayrak devri politikası testleri.

Bu modülde hata = KAYIP MESAJ. İki dinleyici aynı anda Telegram'dan çekerse
güncellemeler rastgele bölünür. Testler bu yüzden asimetrik yazıldı: boşluk
üreten kararlar (kimse dinlemiyor) kabul edilebilir, çakışma üreten kararlar
(iki taraf birden dinliyor) kabul edilemez.
"""
from tests import common  # noqa: F401
import unittest

from ghost import baton_policy as bp


class MacDevralmaTest(unittest.TestCase):
    def test_esik_altinda_devralmaz(self):
        self.assertFalse(bp.mac_devralmali(60, 522))

    def test_ssh_olu_ve_origin_olu_ise_devralir(self):
        self.assertTrue(bp.mac_devralmali(120, 522))
        self.assertTrue(bp.mac_devralmali(300, 521))

    def test_origin_ayaktaysa_DEVRALMAZ(self):
        # AĞ BÖLÜNMESİ: SSH kopuk ama VPS yaşıyor ve büyük ihtimalle hâlâ
        # dinliyor. Devralmak tam da yasak olan çakışmayı üretirdi.
        self.assertFalse(bp.mac_devralmali(600, 200))
        self.assertFalse(bp.mac_devralmali(600, 404))

    def test_macin_kendi_agi_yoksa_devralmaz(self):
        # Yoklama bile yapılamıyorsa Mac Telegram'a da bağlanamaz;
        # devralmak boş bayrak taşımak olur.
        self.assertFalse(bp.mac_devralmali(600, None))

    def test_kismi_ariza_uyarisi(self):
        self.assertTrue(bp.kismi_ariza(120, 200))     # SSH ölü, web ayakta
        self.assertFalse(bp.kismi_ariza(120, 522))    # ikisi de ölü → devralma
        self.assertFalse(bp.kismi_ariza(30, 200))     # eşik altı
        self.assertFalse(bp.kismi_ariza(120, None))   # kendi ağı yok


class VpsAcilisTest(unittest.TestCase):
    def test_taze_mac_heartbeat_varsa_devreder(self):
        b = {"owner": "mac", "hb": 1000}
        self.assertEqual(bp.vps_acilis_karari(5, b, 1010, 45), "devret")

    def test_bayat_mac_heartbeat_pencerede_bekletir(self):
        # Mac 200 sn önce yazmış → ölmüş olabilir; ama hemen sahiplenme,
        # pencere boyunca taze yazar mı diye bak.
        b = {"owner": "mac", "hb": 1000}
        self.assertEqual(bp.vps_acilis_karari(10, b, 1200, 45), "bekle")

    def test_pencere_dolunca_sahiplenir(self):
        b = {"owner": "mac", "hb": 1000}
        self.assertEqual(bp.vps_acilis_karari(95, b, 1200, 45), "sahiplen")

    def test_kendi_bayat_kaydina_guvenip_hemen_sahiplenmez(self):
        # ÇÖKÜŞ SENARYOSU: VPS ölmeden önce dosyada "vps" yazıyordu. Açılışta
        # ona bakıp hemen dinlemeye başlamak, devralmış Mac ile çakışmaktı.
        b = {"owner": "vps", "hb": 900}
        self.assertEqual(bp.vps_acilis_karari(5, b, 1200, 45), "bekle")
        self.assertEqual(bp.vps_acilis_karari(95, b, 1200, 45), "sahiplen")

    def test_taze_kendi_izi_varsa_beklemeden_sahiplenir(self):
        # Sıradan yeniden başlatma: VPS 5 sn önce heartbeat yazmış. Mac
        # devralmak için 120 sn kesintisiz erişimsizlik ister — devralmış
        # OLAMAZ. 90 sn beklemek her restart'ta sebepsiz sessizlik olurdu.
        b = {"owner": "vps", "hb": 1195}
        self.assertEqual(bp.vps_acilis_karari(2, b, 1200, 45), "sahiplen")

    def test_bayat_kendi_izi_pencere_bekletir(self):
        # Gerçek kesinti: iz 10 dakika eski. Mac bu sürede devralmış olabilir.
        b = {"owner": "vps", "hb": 600}
        self.assertEqual(bp.vps_acilis_karari(2, b, 1200, 45), "bekle")

    def test_hizli_yol_mac_devralma_esigini_asamaz(self):
        # Hızlı yolun sınırı, Mac'in devralma eşiğinden KISA olmalı; aksi
        # halde Mac devralmışken VPS de sahiplenir → çakışma.
        self.assertLessEqual(bp.MAC_DEVRALMA_SN, bp.VPS_BEKLEME_SN + 45)

    def test_bos_baton_da_pencere_bekler(self):
        self.assertEqual(bp.vps_acilis_karari(5, {}, 1200, 45), "bekle")
        self.assertEqual(bp.vps_acilis_karari(5, None, 1200, 45), "bekle")

    def test_pencere_mac_yazma_periyodundan_uzun(self):
        # Tasarım kısıtı: pencere, Mac'in heartbeat periyodundan uzun olmalı.
        # Mac turu ≈ long-poll(10sn) + ssh; 45 sn'lik failover'dan da uzun.
        self.assertGreater(bp.VPS_BEKLEME_SN, 45)


class AtlananOzetTest(unittest.TestCase):
    def test_bos_ise_mesaj_yok(self):
        self.assertIsNone(bp.atlanan_ozeti([]))

    def test_atlananlar_sessizce_yutulmaz(self):
        m = bp.atlanan_ozeti(["deploy yap", "napıyorsun"])
        self.assertIn("2 mesaj", m)
        self.assertIn("deploy yap", m)
        self.assertIn("tekrar yaz", m.lower())

    def test_uzun_liste_kirpilir(self):
        m = bp.atlanan_ozeti(["m%d" % i for i in range(12)])
        self.assertIn("+7 tane daha", m)


if __name__ == "__main__":
    unittest.main()
