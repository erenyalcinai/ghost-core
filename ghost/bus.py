"""Olay veriyolu — her girdi (mesaj, cron, monitör, webhook, istek) tek tip
olaya iner; kalıcı SQLite kuyruğu (süreç yeniden başlasa da olay kaybolmaz).

Faz 3 (2026-09-03): otomatik iş istekleri ARTIK BURADAN akar — üreticiler
(monitör, zamanlayıcı, hedef, webhook, uptime, haftalık sentez) `istek()` ile
type=request olayı bırakır; köprü `next_pending("request")` ile tüketir.
ops/requests dizini yalnız geriye dönük okunur (yeni yazan yok).
"""
import json
import sqlite3
import time

from ghost import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  type TEXT NOT NULL,           -- chat|job|mac|monitor|incident|webhook|schedule
  source TEXT NOT NULL,         -- telegram|cron|uptime|email|...
  payload TEXT NOT NULL,        -- JSON
  status TEXT NOT NULL DEFAULT 'pending',  -- pending|done|failed
  done_ts TEXT
);
CREATE INDEX IF NOT EXISTS ev_status ON events(status, id);
"""


def _conn():
    c = sqlite3.connect(config.DB_PATH, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_SCHEMA)
    return c


def emit(ev_type, source, payload, status="pending"):
    """status='done' → bilgi amaçlı olay (sohbet akışı gibi): kuyruğa girmez,
    next_pending onu hiç görmez; yalnız zaman çizelgesi (ghost/akis.py) okur."""
    c = _conn()
    try:
        cur = c.execute("INSERT INTO events(ts, type, source, payload, status) VALUES(?,?,?,?,?)",
                        (time.strftime("%F %T"), ev_type, source,
                         json.dumps(payload, ensure_ascii=False), status))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def next_pending(ev_type=None):
    """Sıradaki bekleyen olay (dict) ya da None. ev_type verilirse yalnız o tür."""
    c = _conn()
    try:
        if ev_type:
            r = c.execute("SELECT id, ts, type, source, payload FROM events "
                          "WHERE status='pending' AND type=? ORDER BY id LIMIT 1",
                          (ev_type,)).fetchone()
        else:
            r = c.execute("SELECT id, ts, type, source, payload FROM events "
                          "WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
    finally:
        c.close()
    if not r:
        return None
    return {"id": r[0], "ts": r[1], "type": r[2], "source": r[3],
            "payload": json.loads(r[4])}


def mark(event_id, status="done"):
    c = _conn()
    try:
        c.execute("UPDATE events SET status=?, done_ts=? WHERE id=?",
                  (status, time.strftime("%F %T"), event_id))
        c.commit()
    finally:
        c.close()


def stats():
    c = _conn()
    try:
        rows = c.execute("SELECT status, COUNT(*) FROM events GROUP BY status").fetchall()
    finally:
        c.close()
    return dict(rows)


def istek(prompt, kind, source, announce=None, gate=False, classify=None, model=None):
    """Otomatik iş isteği (köprü işe çevirir). Eski ops/requests/*.json alanlarıyla
    bire bir: prompt, kind, announce, gate (dış kaynaklı → onay kapısı), classify
    (sınıflandırma için güvenilir metin), model."""
    payload = {"prompt": prompt, "kind": kind}
    if announce:
        payload["announce"] = announce
    if gate:
        payload["gate"] = True
    if classify:
        payload["classify"] = classify
    if model:
        payload["model"] = model
    return emit("request", source, payload)


def bekleyen_istekler(limit=50):
    c = _conn()
    try:
        rows = c.execute("SELECT id, ts, source, payload FROM events WHERE status='pending' "
                         "AND type='request' ORDER BY id LIMIT ?", (int(limit),)).fetchall()
    finally:
        c.close()
    return [{"id": r[0], "ts": r[1], "source": r[2], "payload": json.loads(r[3])} for r in rows]
