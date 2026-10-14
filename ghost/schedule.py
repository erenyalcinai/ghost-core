"""Zamanlayıcı — doğal dile yakın Türkçe kalıplardan kalıcı görev/hatırlatma.

Desteklenen kalıplar (parse_spec):
  Tek seferlik: "10dk" · "2s" · "45 dakika sonra" · "3 saat sonra" ·
                "09:30" (bugün geçtiyse yarın) · "yarın 09:30"
  Tekrarlı:     "her gün 09:00" · "her pazartesi 09:30" (7 gün adı) ·
                "her 6 saat" · "her 30 dakika"

Eylem türleri: reminder (Telegram'a metin) · job (requests kuyruğuna claude
işi). Köprü VPS rolünde 60 sn'de bir tick() çağırır.
"""
import json
import os
import re
import sqlite3
import time

from ghost import config, governance, notify

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schedules(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  spec TEXT NOT NULL,            -- insan-okur özgün tarif
  kind TEXT NOT NULL,            -- reminder | job
  payload TEXT NOT NULL,         -- hatırlatma metni ya da iş prompt'u
  next_ts REAL NOT NULL,
  repeat TEXT,                   -- JSON: {type: daily|weekly|interval, ...} | NULL=tek
  active INTEGER NOT NULL DEFAULT 1,
  created TEXT NOT NULL
);
"""
_TICK_GAP = 55
_last_tick = 0.0

_DAYS = {"pazartesi": 0, "salı": 1, "sali": 1, "çarşamba": 2, "carsamba": 2,
         "perşembe": 3, "persembe": 3, "cuma": 4, "cumartesi": 5,
         "pazar": 6}


def _conn():
    c = sqlite3.connect(config.DB_PATH, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_SCHEMA)
    return c


def _next_daily(hour, minute, now):
    t = time.localtime(now)
    cand = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hour, minute, 0,
                        0, 0, -1))
    return cand if cand > now else cand + 86400


def _next_weekly(weekday, hour, minute, now):
    cand = _next_daily(hour, minute, now)
    for _ in range(8):
        if time.localtime(cand).tm_wday == weekday:
            return cand
        cand += 86400
    return cand


def parse_spec(text, now=None):
    """(repeat|None, next_ts) ya da None (anlaşılmadı)."""
    now = now or time.time()
    s = (text or "").strip().lower()
    if not s:
        return None

    m = re.fullmatch(r"her\s+g[üu]n\s+(\d{1,2})[:.](\d{2})", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        return {"type": "daily", "hour": h, "minute": mi}, _next_daily(h, mi, now)

    m = re.fullmatch(r"her\s+(\w+)\s+(\d{1,2})[:.](\d{2})", s)
    if m and m.group(1) in _DAYS:
        wd, h, mi = _DAYS[m.group(1)], int(m.group(2)), int(m.group(3))
        return ({"type": "weekly", "weekday": wd, "hour": h, "minute": mi},
                _next_weekly(wd, h, mi, now))

    m = re.fullmatch(r"her\s+(\d+)\s*(saat|s|dakika|dk)", s)
    if m:
        n = int(m.group(1))
        mins = n * 60 if m.group(2) in ("saat", "s") else n
        if mins < 5:
            mins = 5  # spam freni
        return {"type": "interval", "minutes": mins}, now + mins * 60

    m = re.fullmatch(r"(\d+)\s*(dk|dakika|s|saat)(\s+sonra)?", s)
    if m:
        n = int(m.group(1))
        mins = n * 60 if m.group(2) in ("s", "saat") else n
        return None, now + mins * 60

    m = re.fullmatch(r"(yar[ıi]n\s+)?(\d{1,2})[:.](\d{2})", s)
    if m:
        h, mi = int(m.group(2)), int(m.group(3))
        if h > 23 or mi > 59:
            return None
        ts = _next_daily(h, mi, now)
        if m.group(1):  # "yarın": bugünkü saat henüz gelmemişse bile yarına at
            t = time.localtime(now)
            today_ts = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, h, mi, 0,
                                    0, 0, -1))
            ts = today_ts + 86400
        return None, ts

    return None


def add(spec_text, kind, payload, now=None):
    """Kayıt ekler; (id, next_ts) döner ya da None (spec anlaşılmadı)."""
    parsed = parse_spec(spec_text, now)
    if not parsed:
        return None
    repeat, next_ts = parsed
    c = _conn()
    try:
        cur = c.execute(
            "INSERT INTO schedules(spec, kind, payload, next_ts, repeat, created) "
            "VALUES(?,?,?,?,?,?)",
            (spec_text, kind, payload, next_ts,
             json.dumps(repeat) if repeat else None, time.strftime("%F %T")))
        c.commit()
        governance.audit("schedule", "add", governance.REVERSIBLE,
                         "%s | %s | %s" % (spec_text, kind, payload[:100]))
        return cur.lastrowid, next_ts
    finally:
        c.close()


def remove(sid):
    c = _conn()
    try:
        n = c.execute("UPDATE schedules SET active=0 WHERE id=?", (sid,)).rowcount
        c.commit()
        return n > 0
    finally:
        c.close()


def list_schedules():
    c = _conn()
    try:
        rows = c.execute(
            "SELECT id, spec, kind, payload, next_ts, repeat FROM schedules "
            "WHERE active=1 ORDER BY next_ts").fetchall()
    finally:
        c.close()
    return [{"id": r[0], "spec": r[1], "kind": r[2], "payload": r[3],
             "next_ts": r[4], "repeat": json.loads(r[5]) if r[5] else None}
            for r in rows]


def _advance(repeat, now):
    if repeat["type"] == "daily":
        return _next_daily(repeat["hour"], repeat["minute"], now)
    if repeat["type"] == "weekly":
        return _next_weekly(repeat["weekday"], repeat["hour"],
                            repeat["minute"], now)
    return now + repeat["minutes"] * 60


def _queue_job(payload, spec):
    from ghost import bus
    bus.istek(payload, "zamanlı görev", "schedule",
              announce="⏰ Zamanlı görev başladı (%s)" % spec)


def tick(send=None, now=None):
    """Vadesi gelenleri ateşle; ateşlenen id listesi döner.

    Teslim sözleşmesi: reminder AT-LEAST-ONCE (bildirim İLETİLMEDEN durum
    ilerlemez — Telegram kesikken hatırlatma kaybolmaz, sonraki tick yeniden
    dener); job AT-MOST-ONCE (önce durum, sonra kuyruk — çökmede zamanlı iş
    iki kez KOŞMAZ, o tur atlanmış olur ve tekrarlıysa sonraki vadede koşar)."""
    global _last_tick
    now = now or time.time()
    if now - _last_tick < _TICK_GAP:
        return []
    _last_tick = now
    if governance.is_frozen():
        return []
    send = send or notify.telegram
    fired = []
    for s in list_schedules():
        if s["next_ts"] > now:
            continue
        if s["kind"] == "reminder":
            # yalnız açık False başarısızlıktır (test stub'ları None döndürür)
            if send("⏰ Hatırlatma: %s" % s["payload"]) is False:
                governance.audit("schedule", "fire.retry", governance.READ,
                                 "#%d bildirim iletilemedi" % s["id"])
                continue  # durum İLERLEMEZ → sonraki tick yeniden dener
        c = _conn()
        try:
            if s["repeat"]:
                c.execute("UPDATE schedules SET next_ts=? WHERE id=?",
                          (_advance(s["repeat"], now), s["id"]))
            else:
                c.execute("UPDATE schedules SET active=0 WHERE id=?", (s["id"],))
            c.commit()
        finally:
            c.close()
        if s["kind"] != "reminder":
            try:
                _queue_job(s["payload"], s["spec"])
            except Exception as e:
                governance.audit("schedule", "fire.lost", governance.READ,
                                 "#%d iş kuyruklanamadı: %r" % (s["id"], e))
        governance.audit("schedule", "fire", governance.READ,
                         "#%d %s" % (s["id"], s["spec"]))
        fired.append(s["id"])
    return fired
