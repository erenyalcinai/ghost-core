"""Hafıza Sarayı çekirdeği — SQLite (+FTS5 varsa) kalıcı bellek.

remember(): her öğrenme/olay tek kayıt. recall(): tam-metin arama.
FTS5 yoksa LIKE'a düşer (VPS/Mac stok Python farkları için).
Faz 3'te otomatik enjeksiyon ve öğrenme döngüsü bunun üstüne gelir.
"""
import sqlite3
import time

from ghost import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'not',
  text TEXT NOT NULL,
  meta TEXT,
  deleted INTEGER NOT NULL DEFAULT 0
);
"""
# Eski DB'ler için: sütun yoksa ekle (tombstone senkronu — silmeler yayılsın)
_MIGRATE = "ALTER TABLE memories ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0"
_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
USING fts5(text, content='memories', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, text)
  VALUES('delete', old.id, old.text);
END;
"""


def _conn():
    """(bağlantı, fts_var_mı) — sqlite3.Connection'a öznitelik eklenemez."""
    c = sqlite3.connect(config.DB_PATH, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_SCHEMA)
    try:
        c.execute(_MIGRATE)  # eski DB'ye deleted sütunu
        c.commit()
    except sqlite3.OperationalError:
        pass  # zaten var
    fts = True
    try:
        c.executescript(_FTS)
    except sqlite3.OperationalError:
        fts = False
    return c, fts


def forget(substr):
    """Metninde substr geçen kayıtları tombstone'la (silmez, deleted=1 yapar —
    böylece silme senkronla iki makineye yayılır). Etkilenen sayı."""
    c, _ = _conn()
    try:
        n = c.execute("UPDATE memories SET deleted=1 WHERE text LIKE ? "
                      "AND deleted=0", ("%" + substr + "%",)).rowcount
        c.commit()
        return n
    finally:
        c.close()


def remember(text, kind="not", meta=None):
    text = (text or "").strip()
    if not text:
        return None
    c, _ = _conn()
    try:
        cur = c.execute("INSERT INTO memories(ts, kind, text, meta) VALUES(?,?,?,?)",
                        (time.strftime("%F %T"), kind, text, meta))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def recall(query, k=5):
    """[(ts, kind, text)] — en alakalı k kayıt."""
    query = (query or "").strip()
    if not query:
        return []
    c, fts = _conn()
    try:
        if fts:
            try:
                q = " OR ".join(t for t in query.split() if t)
                rows = c.execute(
                    "SELECT m.ts, m.kind, m.text FROM memories_fts f "
                    "JOIN memories m ON m.id = f.rowid "
                    "WHERE memories_fts MATCH ? AND m.deleted=0 "
                    "ORDER BY rank LIMIT ?", (q, k)).fetchall()
                if rows:
                    return rows
            except sqlite3.OperationalError:
                pass
        like = "%" + query + "%"
        return c.execute(
            "SELECT ts, kind, text FROM memories WHERE text LIKE ? "
            "AND deleted=0 ORDER BY id DESC LIMIT ?", (like, k)).fetchall()
    finally:
        c.close()


def count():
    c, _ = _conn()
    try:
        return c.execute("SELECT COUNT(*) FROM memories WHERE deleted=0").fetchone()[0]
    finally:
        c.close()


# ---- Mac↔VPS paylaşımlı beyin: dışa aktar / birleştir ----
def export_rows():
    """Tüm kayıtlar [(ts, kind, text, meta, deleted)] — tombstone'lar dahil,
    yoksa silmeler karşı makinede dirilir."""
    c, _ = _conn()
    try:
        return c.execute("SELECT ts, kind, text, meta, deleted FROM memories "
                         "ORDER BY id").fetchall()
    finally:
        c.close()


def merge_rows(rows):
    """Gelen kayıtları birleştir. (ts,text) yoksa ekle; varsa ve gelen
    deleted=1 ise yereli de tombstone'la (silme yayılır). Değişen sayı."""
    changed = 0
    c, _ = _conn()
    try:
        for row in rows:
            ts, kind, text, meta = row[0], row[1], row[2], row[3]
            deleted = row[4] if len(row) > 4 else 0
            exists = c.execute("SELECT id, deleted FROM memories "
                               "WHERE ts=? AND text=? LIMIT 1",
                               (ts, text)).fetchone()
            if not exists:
                c.execute("INSERT INTO memories(ts, kind, text, meta, deleted) "
                          "VALUES(?,?,?,?,?)", (ts, kind, text, meta, deleted))
                changed += 1
            elif deleted and not exists[1]:
                c.execute("UPDATE memories SET deleted=1 WHERE id=?", (exists[0],))
                changed += 1
        c.commit()
        return changed
    finally:
        c.close()
