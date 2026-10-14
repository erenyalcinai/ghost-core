"""Nöbetçi-nöbeti (dead-man) testleri — "sessizlik = sağlık" kör noktası.

Kök sebep 2026-08-19: Ghost'un bütün nöbetçileri sorun yoksa susar. Bu doğru
bir tasarım ama ölü bir nöbetçi de aynı sessizliği üretir — yani alarma en çok
ihtiyaç duyulan anda sistem sağlıklı görünür. (2026-08-15'te selfcheck'in
kendisi nöbet anında import hatasıyla düşmüştü ve hiç alarm gitmedi.)

Bu testler damga/bayatlama/susturma mantığının bir daha sessizce bozulmamasını
güvenceler. Özellikle iki tuzak: (1) hiç atmamış nöbetçi ALARM ÜRETMEMELİ —
yoksa aynı dosya Mac'te VPS işleri için sahte alarm verir; (2) susturma KALICI
olmamalı — dirilip tekrar ölen nöbetçi anında haber vermeli.
"""
from tests import common  # noqa: F401  — yolları geçici dizine çeker
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops"))
import heartbeat  # noqa: E402


HEPSI = list(heartbeat.WATCHERS)


def _geriye_al(name, dakika):
    """Damgayı geçmişe çek — bekleyerek test etmek yerine saati oynat."""
    import json
    p = heartbeat._path(name)
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    d["ts"] = int(time.time() - dakika * 60)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f)


class HeartbeatTest(unittest.TestCase):
    def setUp(self):
        for f in os.listdir(heartbeat.BEAT_DIR) if os.path.isdir(
                heartbeat.BEAT_DIR) else []:
            os.remove(os.path.join(heartbeat.BEAT_DIR, f))

    def test_gercek_ops_dizinine_yazmaz(self):
        """Testin kendisi canlı nöbet durumunu bozmasın."""
        self.assertNotIn("/jarvis/ops/heartbeats", heartbeat.BEAT_DIR)

    def test_atmayan_nobetci_alarm_uretmez(self):
        """Bu makinede hiç kurulmamış iş için sahte alarm YOK."""
        self.assertEqual(heartbeat.report(HEPSI), [])

    def test_taze_damga_sessiz(self):
        heartbeat.beat("uptime")
        self.assertEqual(heartbeat.report(HEPSI), [])

    def test_bayat_damga_alarm_verir(self):
        heartbeat.beat("uptime")
        _geriye_al("uptime", 90)          # eşik 40 dk
        msgs = heartbeat.report(HEPSI)
        self.assertEqual(len(msgs), 1)
        self.assertIn("NÖBETÇİ SESSİZ", msgs[0])

    def test_esik_altinda_alarm_yok(self):
        """Tek tur kaçırmak (ağ/yük/timer kayması) alarm üretmemeli."""
        heartbeat.beat("uptime")
        _geriye_al("uptime", 20)          # eşik 40 dk — henüz bayat değil
        self.assertEqual(heartbeat.report(HEPSI), [])

    def test_susturma_tekrari_engeller(self):
        heartbeat.beat("uptime")
        _geriye_al("uptime", 90)
        self.assertEqual(len(heartbeat.report(HEPSI)), 1)
        self.assertEqual(heartbeat.report(HEPSI), [],
                         "ölü nöbetçi her turda bağırmamalı")

    def test_susturma_kalici_degil(self):
        """Dirilip tekrar ölen nöbetçi ANINDA haber vermeli."""
        heartbeat.beat("uptime")
        _geriye_al("uptime", 90)
        heartbeat.report(HEPSI)           # 1. alarm + susturma
        heartbeat.beat("uptime")          # dirildi
        self.assertEqual(heartbeat.report(HEPSI), [])
        _geriye_al("uptime", 90)          # yine öldü
        self.assertEqual(len(heartbeat.report(HEPSI)), 1,
                         "susturma dirilişte temizlenmeliydi")

    PERIYOT = {"uptime": 10 * 60, "selfcheck": 15 * 60, "biz_watch": 3600,
               "marka_deploy": 6 * 3600}

    def test_saglikli_nobetci_asla_alarm_vermez(self):
        """Eşik periyottan küçük/yakınsa, sorunsuz koşan nöbetçi bağırır."""
        for ad, (_etiket, esik) in heartbeat.WATCHERS.items():
            self.assertGreater(esik, self.PERIYOT[ad] * 1.2, ad)

    def test_sik_kosanlar_bir_tur_kacirmayi_tolere_eder(self):
        """≤1 saatlik nöbetçilerde tek tur kaçırmak (ağ/yük) alarm olmamalı."""
        for ad, (_etiket, esik) in heartbeat.WATCHERS.items():
            if self.PERIYOT[ad] <= 3600:
                self.assertGreaterEqual(esik, self.PERIYOT[ad] * 3, ad)

    def test_seyrek_kosanlar_gunlerce_kor_kalmaz(self):
        """Günlük işte '×3 kuralı' 3 gün körlük demek — pay birkaç saat olmalı."""
        for ad, (_etiket, esik) in heartbeat.WATCHERS.items():
            if self.PERIYOT[ad] > 3600:
                self.assertGreaterEqual(esik, self.PERIYOT[ad] + 2 * 3600, ad)
                self.assertLessEqual(esik, self.PERIYOT[ad] + 8 * 3600, ad)

    def test_beat_hata_yutar(self):
        """Damga basılamazsa nöbetçinin ASIL işi çökmemeli."""
        eski = heartbeat.BEAT_DIR
        heartbeat.BEAT_DIR = "/gecersiz/yol/olmayan"
        try:
            heartbeat.beat("uptime")      # patlamamalı
        finally:
            heartbeat.BEAT_DIR = eski


class UptimeKipTest(unittest.TestCase):
    """Yoklama kipleri — hedefe göre 'ayakta' tanımı değişir."""

    def setUp(self):
        try:
            import uptime_watch
        except Exception as e:            # .env yoksa (CI) atla
            self.skipTest("uptime_watch import edilemedi: %r" % e)
        self.u = uptime_watch

    def test_nfc_kart_kati_kipte(self):
        """Kart 404 dönerse 'cevap veriyor' sayılmamalı — kart ölmüş demektir."""
        self.assertIn(self.u.NFC_KART, self.u.STRICT_200)

    def test_hooks_get_ile_yoklanir(self):
        """hooks.owner.example HEAD'e 501 döner — HEAD ile yoklanırsa sahte alarm."""
        self.assertIn("https://hooks.owner.example", self.u.GET_ONLY)
        self.assertNotIn("https://hooks.owner.example", self.u.STRICT_200)

    def test_kritik_varliklar_izlemede(self):
        for url in ("https://ghostinprod.com", "https://hooks.owner.example",
                    self.u.NFC_KART, "https://api.app-a.example.com"):
            self.assertIn(url, self.u.SITES, url)


if __name__ == "__main__":
    unittest.main()
