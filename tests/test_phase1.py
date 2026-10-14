import time
import unittest

from tests import common  # noqa: F401
from ghost import monitors, schedule
from ghost.webhook import handle_hook


class TestScheduleParse(unittest.TestCase):
    NOW = time.mktime((2026, 8, 12, 12, 0, 0, 0, 0, -1))  # Çarşamba 12:00

    def test_daily(self):
        rep, ts = schedule.parse_spec("her gün 09:00", self.NOW)
        self.assertEqual(rep["type"], "daily")
        lt = time.localtime(ts)
        self.assertEqual((lt.tm_hour, lt.tm_min), (9, 0))
        self.assertGreater(ts, self.NOW)  # bugün 09:00 geçti → yarın

    def test_weekly(self):
        rep, ts = schedule.parse_spec("her cuma 18:30", self.NOW)
        self.assertEqual(rep["type"], "weekly")
        self.assertEqual(time.localtime(ts).tm_wday, 4)

    def test_interval_and_floor(self):
        rep, ts = schedule.parse_spec("her 6 saat", self.NOW)
        self.assertEqual(rep, {"type": "interval", "minutes": 360})
        rep, _ = schedule.parse_spec("her 1 dk", self.NOW)
        self.assertEqual(rep["minutes"], 5)  # spam freni

    def test_once_relative(self):
        rep, ts = schedule.parse_spec("10dk", self.NOW)
        self.assertIsNone(rep)
        self.assertAlmostEqual(ts, self.NOW + 600, delta=1)
        rep, ts = schedule.parse_spec("2 saat sonra", self.NOW)
        self.assertAlmostEqual(ts, self.NOW + 7200, delta=1)

    def test_once_clock(self):
        _, ts = schedule.parse_spec("09:30", self.NOW)  # geçti → yarın
        self.assertGreater(ts, self.NOW)
        _, ts2 = schedule.parse_spec("yarın 14:00", self.NOW)
        self.assertGreater(ts2, self.NOW + 86400 - 7200)

    def test_invalid(self):
        self.assertIsNone(schedule.parse_spec("saçma sapan bir şey"))
        self.assertIsNone(schedule.parse_spec("99:99"))


class TestScheduleTick(unittest.TestCase):
    def test_reminder_fires_once(self):
        sent = []
        now = time.time()
        sid, _ = schedule.add("10dk", "reminder", "su iç", now=now - 700)
        schedule._last_tick = 0
        fired = schedule.tick(send=sent.append, now=now)
        self.assertIn(sid, fired)
        self.assertTrue(any("su iç" in s for s in sent))
        # tek seferlik → pasif
        self.assertNotIn(sid, [s["id"] for s in schedule.list_schedules()])

    def test_interval_reschedules(self):
        now = time.time()
        sid, _ = schedule.add("her 30 dakika", "reminder", "tekrar", now=now - 1900)
        schedule._last_tick = 0
        schedule.tick(send=lambda t: None, now=now)
        rows = [s for s in schedule.list_schedules() if s["id"] == sid]
        self.assertTrue(rows and rows[0]["next_ts"] > now)


class TestMonitors(unittest.TestCase):
    def test_change_detection(self):
        sent = []
        mid = monitors.add("test", "http://ornek/", interval_min=1)
        content = {"v": "<html><body>fiyat: 100</body></html>"}
        now = time.time()
        monitors._last_tick = 0
        monitors.tick(fetch=lambda u: content["v"], send=sent.append, now=now)
        self.assertEqual(sent, [])  # ilk koşu: taban çizgisi
        content["v"] = "<html><body>fiyat: 90</body></html>"
        monitors._last_tick = 0
        fired = monitors.tick(fetch=lambda u: content["v"], send=sent.append,
                              now=now + 120)
        self.assertIn(mid, fired)
        self.assertTrue(any("DEĞİŞTİ" in s for s in sent))
        monitors.remove(mid)

    def test_html_noise_ignored(self):
        sent = []
        mid = monitors.add("gurultu", "http://ornek2/", interval_min=1)
        now = time.time()
        monitors._last_tick = 0
        monitors.tick(fetch=lambda u: "<p>aynı  içerik</p>", send=sent.append,
                      now=now)
        monitors._last_tick = 0
        fired = monitors.tick(fetch=lambda u: "<p>aynı\n içerik</p><script>x=1</script>",
                              send=sent.append, now=now + 120)
        self.assertEqual(fired, [])  # boşluk/script farkı değişim sayılmaz
        monitors.remove(mid)


class TestWebhook(unittest.TestCase):
    def test_auth_required(self):
        code, _ = handle_hook("test", {"a": 1}, secret_ok=False)
        self.assertEqual(code, 403)

    def test_ok_records_event(self):
        from ghost import bus
        code, _ = handle_hook("testkaynak", {"job": "durumu özetle"},
                              secret_ok=True)
        self.assertEqual(code, 200)
        s = bus.stats()
        self.assertTrue(sum(s.values()) >= 1)


if __name__ == "__main__":
    unittest.main()
