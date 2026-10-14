"""Duran hedefler — sürekli biliş (VISION.md: Ghost hiç durmaz).

Sahip bir hedef tanımlar ("AppA'e 100 kullanıcı", "X konusunu derinlemesine
araştır"); Ghost boş dururken PERİYODİK olarak hedefin üzerinde bir birim
ilerleme yapar ve raporlar. Tik VPS köprü döngüsünden gelir; 6 saatte bir
en uzun süredir dokunulmamış hedef çalışılır (bütçe freni). FREEZE durdurur.
"""
import json
import os
import sqlite3
import time

from ghost import config, governance

_SCHEMA = """
CREATE TABLE IF NOT EXISTS goals(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  detail TEXT,
  status TEXT NOT NULL DEFAULT 'aktif',   -- aktif|duraklatıldı|tamam
  last_worked REAL NOT NULL DEFAULT 0,
  work_count INTEGER NOT NULL DEFAULT 0,
  created TEXT NOT NULL
);
"""
WORK_GAP_H = 6      # bir hedefe en erken bu kadar saatte bir dokunulur
_TICK_GAP = 300     # tik en fazla 5 dk'da bir koşar
_last_tick = 0.0


def _conn():
    c = sqlite3.connect(config.DB_PATH, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_SCHEMA)
    return c


def add(title, detail=""):
    c = _conn()
    try:
        cur = c.execute("INSERT INTO goals(title, detail, created) VALUES(?,?,?)",
                        (title, detail, time.strftime("%F %T")))
        c.commit()
        governance.audit("goals", "add", governance.REVERSIBLE, title[:150])
        return cur.lastrowid
    finally:
        c.close()


def set_status(gid, status):
    c = _conn()
    try:
        n = c.execute("UPDATE goals SET status=? WHERE id=?", (status, gid)).rowcount
        c.commit()
        return n > 0
    finally:
        c.close()


def list_goals(all_states=False):
    c = _conn()
    try:
        q = "SELECT id, title, status, work_count, last_worked FROM goals"
        if not all_states:
            q += " WHERE status='aktif'"
        rows = c.execute(q + " ORDER BY id").fetchall()
    finally:
        c.close()
    return [{"id": r[0], "title": r[1], "status": r[2], "work_count": r[3],
             "last_worked": r[4]} for r in rows]


def _queue_work(goal):
    from ghost import bus
    prompt = (
        "DURAN HEDEF ÇALIŞMASI — otomatik (sürekli biliş).\n"
        "Hedef: %s\n%s\n"
        "Bu hedef üzerinde BİR BİRİM somut ilerleme yap (araştır/üret/analiz "
        "et — hangisi hedefe hizmet ediyorsa). Daha önce yapılanlar hafızanda "
        "olabilir; /hafiza kayıtlarındaki bağlamı kullan, tekrarlama.\n"
        "RAPOR: (1) bu turda ne yaptın (2) somut çıktı (3) sonraki adım. "
        "Kısa ve keskin; telefonda okunacak.\n"
        "SINIR: geri-alınamaz/harcamalı hiçbir şey yapma — sadece öner."
        % (goal["title"], ("Detay: " + goal["detail"]) if goal.get("detail") else ""))
    bus.istek(prompt, "hedef", "goal",
              announce="🎯 Hedef çalışılıyor: %s" % goal["title"][:80])


def tick(now=None):
    """Sırası gelen TEK hedefi çalışmaya al (varsa). Ateşlenen id ya da None."""
    global _last_tick
    now = now or time.time()
    if now - _last_tick < _TICK_GAP:
        return None
    _last_tick = now
    if governance.is_frozen():
        return None
    c = _conn()
    try:
        row = c.execute(
            "SELECT id, title, detail FROM goals WHERE status='aktif' "
            "AND last_worked < ? ORDER BY last_worked LIMIT 1",
            (now - WORK_GAP_H * 3600,)).fetchone()
        if not row:
            return None
        gid, title, detail = row
        prev_worked = c.execute("SELECT last_worked FROM goals WHERE id=?",
                                (gid,)).fetchone()[0]
        c.execute("UPDATE goals SET last_worked=?, work_count=work_count+1 "
                  "WHERE id=?", (now, gid))
        c.commit()
    finally:
        c.close()
    goal = {"id": gid, "title": title, "detail": detail}
    try:
        _queue_work(goal)
    except Exception as e:
        # kuyruklanamadı → 'çalışıldı' izini geri al; sonraki tick yeniden
        # dener (aksi halde hedef 6 saat sessizce atlanmış görünürdü)
        c = _conn()
        try:
            c.execute("UPDATE goals SET last_worked=?, work_count=work_count-1 "
                      "WHERE id=?", (prev_worked, gid))
            c.commit()
        finally:
            c.close()
        governance.audit("goals", "work.lost", governance.READ,
                         "#%d kuyruklanamadı: %r" % (gid, e))
        return None
    governance.audit("goals", "work", governance.REVERSIBLE,
                     "#%d %s" % (gid, title[:100]))
    return gid
