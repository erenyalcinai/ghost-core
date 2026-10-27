"""Genel Monitör Motoru — "şunu izle, değişince haber ver" primitifi.

Kayıt: URL + aralık. Tik: vadesi gelenleri çek, normalize et (script/stil/
etiket temizliği), hash'le, değişimde sahibe haber ver + istersen analiz işi
bırak (requests kuyruğu → köprü claude ile inceler). uptime_watch'un genel
hali; OSINT/fiyat/rakip izlemenin temeli. Köprü ana döngüsü VPS rolünde
60 sn'de bir tick() çağırır.
"""
import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.request

from ghost import config, governance, notify

_SCHEMA = """
CREATE TABLE IF NOT EXISTS monitors(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  interval_min INTEGER NOT NULL DEFAULT 30,
  analyze INTEGER NOT NULL DEFAULT 0,      -- 1: değişimde claude analizi iste
  last_run REAL NOT NULL DEFAULT 0,
  last_hash TEXT,
  last_status TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created TEXT NOT NULL
);
"""
_TICK_GAP = 55  # sn — ana döngü sık çağırsa da en fazla dakikada bir koş
_last_tick = 0.0


def _conn():
    c = sqlite3.connect(config.DB_PATH, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_SCHEMA)
    return c


def add(name, url, interval_min=30, analyze=False):
    c = _conn()
    try:
        cur = c.execute(
            "INSERT INTO monitors(name, url, interval_min, analyze, created) "
            "VALUES(?,?,?,?,?)",
            (name, url, int(interval_min), 1 if analyze else 0,
             time.strftime("%F %T")))
        c.commit()
        governance.audit("monitors", "add", governance.REVERSIBLE,
                         "%s %s %ddk" % (name, url, interval_min))
        return cur.lastrowid
    finally:
        c.close()


def remove(mid):
    c = _conn()
    try:
        n = c.execute("UPDATE monitors SET active=0 WHERE id=?", (mid,)).rowcount
        c.commit()
        return n > 0
    finally:
        c.close()


def list_monitors():
    c = _conn()
    try:
        rows = c.execute(
            "SELECT id, name, url, interval_min, last_run, last_status "
            "FROM monitors WHERE active=1 ORDER BY id").fetchall()
    finally:
        c.close()
    return [{"id": r[0], "name": r[1], "url": r[2], "interval_min": r[3],
             "last_run": r[4], "last_status": r[5]} for r in rows]


def _normalize(html):
    """Dinamik gürültüyü azalt: script/style at, etiketleri soy, boşluğu topla."""
    txt = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    txt = re.sub(r"(?s)<[^>]+>", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


def _fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "GhostMonitor/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(1_000_000).decode("utf-8", "ignore")


def _queue_analysis(mon, note):
    """True=kuyruklandı (olay veriyolu, type=request — Faz 3)."""
    try:
        from ghost import bus
        prompt = ("MONİTÖR ALARMI: '%s' (%s) izlenen sayfada değişim algılandı.\n"
                  "Sayfayı aç, neyin değiştiğini belirle ve Sahip için önemini "
                  "1-3 cümleyle özetle. Önemsizse 'önemsiz' de ve kısa kes."
                  % (mon["name"], mon["url"]))
        bus.istek(prompt, "monitör", "monitor", announce=note)
        return True
    except Exception:
        return False


def tick(fetch=None, send=None, now=None):
    """Vadesi gelen monitörleri koş. (test: fetch/send/now enjekte edilir)"""
    global _last_tick
    now = now or time.time()
    if now - _last_tick < _TICK_GAP:
        return []
    _last_tick = now
    if governance.is_frozen():
        return []
    fetch = fetch or _fetch
    send = send or notify.telegram
    fired = []
    for mon in list_monitors():
        if now - mon["last_run"] < mon["interval_min"] * 60:
            continue
        c = _conn()
        try:
            row = c.execute("SELECT last_hash, analyze FROM monitors WHERE id=?",
                            (mon["id"],)).fetchone()
            last_hash, analyze = row
        finally:
            c.close()
        try:
            content = _normalize(fetch(mon["url"]))
            h = hashlib.sha256(content.encode()).hexdigest()
            status = "ok"
        except Exception as e:
            h, status = last_hash, "erişilemedi: %r" % e
        changed = bool(last_hash) and h != last_hash and status == "ok"
        # Önce İLET, sonra commit et: bildirim düşerse hash/durum eskisinde
        # kalır → sonraki turda yeniden denenir (değişim alarmı KAYBOLMAZ).
        # Yalnız açık False başarısızlıktır (test stub'ları None döndürür).
        delivered = True
        if changed:
            note = "👁 '%s' DEĞİŞTİ: %s" % (mon["name"], mon["url"])
            governance.audit("monitors", "change", governance.READ, note)
            if analyze:
                delivered = _queue_analysis(mon, note)
            else:
                delivered = send(note) is not False
            try:  # kural motoru (2026-10-26): monitor:<ad> tetiği, mevcut davranışa EK
                from ghost import kurallar
                kurallar.tetikle("monitor", mon["name"], {"url": mon["url"], "id": mon["id"]})
            except Exception:
                pass
            if delivered:
                fired.append(mon["id"])
        elif status.startswith("erişilemedi") and mon["last_status"] == "ok":
            delivered = send("👁 '%s' ERİŞİLEMİYOR: %s"
                             % (mon["name"], mon["url"])) is not False
        c = _conn()
        try:
            if delivered:
                c.execute("UPDATE monitors SET last_run=?, last_hash=?, "
                          "last_status=? WHERE id=?", (now, h, status, mon["id"]))
            else:  # iletilemedi: yalnız last_run ilerlesin (interval korunur)
                c.execute("UPDATE monitors SET last_run=? WHERE id=?",
                          (now, mon["id"]))
            c.commit()
        finally:
            c.close()
    return fired
