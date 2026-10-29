"""Webhook kaydı BİLGİ olayıdır (status=done), kuyruk değil — 2026-10-29 denetimi.

handle_hook her çağrıda bus.emit("webhook", …) yazar; tüketicisi yoktur (claim_next yalnız
'request' çeker). Varsayılan 'pending' ile 1.345 kayıt 2026-08-12'den beri sahipsiz bekliyordu.
İş açan dallar ayrıca bus.istek() ile 'request' olayı bırakır — kuyruğa giren yalnız odur."""
import unittest
from unittest import mock

from tests import common  # noqa: F401
from ghost import bus, config, notify, webhook


def _hepsi(tur):
    import sqlite3
    c = sqlite3.connect(config.DB_PATH, timeout=10)
    try:
        rows = c.execute("SELECT id, source, status FROM events WHERE type=? ORDER BY id", (tur,)).fetchall()
    finally:
        c.close()
    return [{"id": r[0], "source": r[1], "status": r[2]} for r in rows]


def _bekleyen(tur):
    return [e for e in _hepsi(tur) if e["status"] == "pending"]


class WebhookBilgiOlayi(unittest.TestCase):
    def test_emit_done_ile_yazilir(self):
        with mock.patch.object(bus, "emit") as em, mock.patch.object(bus, "istek"), \
                mock.patch.object(notify, "telegram"):
            webhook.handle_hook("kisayol", {"text": "notları özetle"}, True)
            webhook.handle_hook("sms", {"from": "x", "body": "selam"}, True)
            webhook.handle_hook("diger", {"ping": 1}, True)
        self.assertEqual(em.call_count, 3)
        for c in em.call_args_list:
            self.assertEqual(c.args[0], "webhook")
            self.assertEqual(c.kwargs.get("status"), "done", c)

    def test_webhook_kuyruga_girmez_istek_girer(self):
        # gerçek (test) DB — paket içinde DB paylaşılır, o yüzden kendi kaynağımızı arıyoruz:
        # webhook olayı bekleyen kuyrukta GÖRÜNMEZ, iş isteği görünür
        kaynak = "bilgi-olayi-testi"
        istem = "bilgi-olayi-testi: notları özetle"   # istek olayının source'u sabit 'webhook' → prompt'tan ayırt
        with mock.patch.object(notify, "telegram"), \
                mock.patch.object(webhook, "_hiz_asildi", return_value=False):
            webhook.handle_hook(kaynak, {"ping": 1}, True)
            webhook.handle_hook(kaynak, {"text": istem}, True)
        bekleyen_webhook = [e for e in _bekleyen("webhook") if e["source"] == kaynak]
        self.assertEqual(bekleyen_webhook, [])
        yazilan = [e for e in _hepsi("webhook") if e["source"] == kaynak]
        self.assertEqual(len(yazilan), 2)
        self.assertTrue(all(e["status"] == "done" for e in yazilan), yazilan)
        istekler = [e for e in bus.bekleyen_istekler(500) if e["payload"].get("prompt") == istem]
        self.assertEqual(len(istekler), 1)
        self.assertEqual(istekler[0]["source"], "webhook")
        bus.mark(istekler[0]["id"], "done")

    def test_yetkisiz_ve_donmus_hic_yazmaz(self):
        with mock.patch.object(bus, "emit") as em:
            self.assertEqual(webhook.handle_hook("x", {"a": 1}, False)[0], 403)
        em.assert_not_called()


if __name__ == "__main__":
    unittest.main()
