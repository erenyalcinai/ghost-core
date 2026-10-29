"""Zaman aşımı yedek motoru TETİKLEMEZ — ve turu sıfırdan tekrarlatmaz.

Kök sebep (28 Ağustos 2026, sahibin bildirimi): uzun süren bir işte cevap 20
dakika gelmiyor, sonunda codex'in bağlamsız cevabı Ghost'un cevabı gibi
düşüyordu. Zincir şuydu:
  run_claude 600 sn'de kesiliyor → err=True
  → köprü bunu "oturum kurulamadı" sanıp turu YENİ oturumla tekrar koşuyor
    (600 + 600 = 1200 sn = tam 20 dakika sessizlik)
  → yine err → (Faz 2.9: yedek motor YOK, hata olduğu gibi döner)
  → iş yolunda dahası: err=False yapılıp "bitti" sayılıyor ve KALICI BELLEĞE
    yazılıyordu (canlı kanıt: hafıza kaydı #154, iş #36).
Bu dosya üç kapıyı da kilitler.
"""
import os
import sys
import unittest

from tests import common  # noqa: F401

from ghost import motor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "ops"))
import telegram_bridge as tb  # noqa: E402


class TestIsTimeout(unittest.TestCase):
    def test_zaman_asimi_metinleri_yakalanir(self):
        for metin in ("⏱ Zaman aşımı (600 sn) — işi böl.",
                      "⏱ Zaman asimi (1800 sn)",
                      "Error: request timed out",
                      "subprocess.TimeoutExpired",
                      "operation timeout after 60s"):
            self.assertTrue(motor.is_timeout(metin), metin)

    def test_gercek_arizalar_zaman_asimi_sayilmaz(self):
        for metin in ("⚠️ Hata: connection refused",
                      "invalid api key", "No conversation found", "", None):
            self.assertFalse(motor.is_timeout(metin), metin)

    def test_kota_siniri_ile_karismaz(self):
        za = "⏱ Zaman aşımı (600 sn) — işi böl."
        kota = "usage limit reached"
        self.assertFalse(motor.is_cost_limit(za))
        self.assertFalse(motor.is_timeout(kota))

    def test_run_claude_mesaji_isaret_tasir(self):
        """run_claude'un ürettiği metin ile kapının baktığı işaret ayrışmasın."""
        uretilen = "%s (%d sn) — işi böl." % (motor.TIMEOUT_MARK, 600)
        self.assertTrue(motor.is_timeout(uretilen))


class _SahteMgr:
    """_chat_run'ı gerçek köprü nesnesi kurmadan koşturmak için asgari kabuk."""
    def __init__(self, sonuclar):
        self.sonuclar = list(sonuclar)
        self.cagri = 0
        self.chat_sid = "sid-0"
        self.chat_established = True
        self.chat_resumed = False
        self.chat_ctx = 5
        self.history = []
        self.state = {"model": "sonnet"}
        self.kalici = None   # kalıcı süreç kapalı: tek atımlık yol (2026-09-07)

    def run(self, *a, **k):
        self.cagri += 1
        return self.sonuclar.pop(0)


class TestSohbetZamanAsimiKapisi(unittest.TestCase):
    def _kos(self, sonuclar):
        mgr = _SahteMgr(sonuclar)
        eski_run = tb.run_claude
        tb.run_claude = mgr.run
        try:
            res, sid, err = tb.Manager._chat_run(mgr, "prompt", "ham metin",
                                                 [], lambda u: None)
        finally:
            tb.run_claude = eski_run
        return mgr, res, err

    def test_zaman_asiminda_tekrar_yok_codex_yok(self):
        za = ("%s (1800 sn) — işi böl." % motor.TIMEOUT_MARK, "sid-0", True)
        mgr, res, err = self._kos([za])
        self.assertEqual(mgr.cagri, 1, "zaman aşımında tur TEKRARLANMAMALI")
        self.assertNotIn("codex", res)
        self.assertTrue(err, "zaman aşımı hata olarak kalmalı")
        self.assertIn("devam et", res)      # sahibe çıkış yolu gösterilir

    def test_oturum_hatasinda_tek_kurtarma_denenir(self):
        bozuk = ("⚠️ No conversation found", "sid-0", True)
        iyi = ("tamam", "sid-1", False)
        mgr, res, err = self._kos([bozuk, iyi])
        self.assertEqual(mgr.cagri, 2)      # kurtarma turu korunur
        self.assertFalse(err)
        self.assertEqual(res, "tamam")

    def test_kurtarma_zaman_asimina_ugrarsa_yine_codex_yok(self):
        bozuk = ("⚠️ No conversation found", "sid-0", True)
        za = ("%s (1800 sn)" % motor.TIMEOUT_MARK, "sid-1", True)
        mgr, res, err = self._kos([bozuk, za])
        self.assertEqual(mgr.cagri, 2)
        self.assertTrue(err)
        self.assertNotIn("codex", res)

    def test_gercek_arizada_yedek_motor_yok(self):
        """Faz 2.9 (Sahip 2026-10-29): yedek motor sökülü — gerçek arızada bir kurtarma turu, sonra hata olduğu gibi."""
        ariza = ("⚠️ Hata: connection refused", "sid-0", True)
        mgr, res, err = self._kos([ariza, ariza])
        self.assertEqual(mgr.cagri, 2)
        self.assertTrue(err)
        self.assertNotIn("yedek motor", res)
        self.assertIn("connection refused", res)

    def test_kota_sinirinda_yedek_motor_yok(self):
        kota = ("⚠️ usage limit reached", "sid-0", True)
        mgr, res, err = self._kos([kota, kota])
        # 2026-10-29 Faz 0: ham İngilizce metin yerine tek Türkçe satır (_kota_mesaji); eski metin de kabul (geçiş)
        self.assertTrue("kullanım/kota sınırı" in res or "Kota penceresi dolu" in res, res)


class TestNabiz(unittest.TestCase):
    def test_kapali_periyot_thread_acmaz(self):
        stop = tb._nabiz_basla(1, period=0)
        self.assertTrue(hasattr(stop, "set"))

    def test_uzun_turda_mesaj_atar(self):
        gonderilen = []
        eski = tb.send
        tb.send = lambda cid, t, **k: gonderilen.append(t)
        try:
            stop = tb._nabiz_basla(1, period=0.05)
            import time as _t
            _t.sleep(0.17)
            stop.set()
            _t.sleep(0.08)
        finally:
            tb.send = eski
        self.assertGreaterEqual(len(gonderilen), 2)
        self.assertIn("Hâlâ çalışıyorum", gonderilen[0])
        # durdurulduktan sonra susmalı
        n = len(gonderilen)
        import time as _t
        _t.sleep(0.12)
        self.assertEqual(len(gonderilen), n)


if __name__ == "__main__":
    unittest.main()
