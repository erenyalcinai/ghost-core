"""Şema sürümü + tek bağlantı noktası (Faz 1.9 durum katmanı, 2026-10-29).

NEDEN (bütünsel denetim 03 6a/6b): ghost.db'ye 20+ modül kendi CREATE TABLE'ıyla giriyor, 29 ayrı json durum
dosyası ops/ altında dağınık, hiçbir yerde "şema hangi sürümde" sorusunun cevabı yok. Bu modül: `PRAGMA user_version`
ile sürümlü göç + `baglan()` tek bağlantı kuralı (WAL, zaman aşımı, satır fabrikası yok). Modüller kendi tablolarını
hâlâ CREATE IF NOT EXISTS ile açar (strangler — hiçbir şey durmaz); yeni tablo/sütun buraya göç olarak eklenir.
"""
import sqlite3

from ghost import config

SURUM = 2


def _goc_2_is_kuyrugu(c):
    """Faz 3.11 (2026-10-30) kuyruk birleşimi: events tablosuna iş sütunları — hedef (vps|mac), kind, sonuc
    (Mac işçisinin yazdığı çıktı), ust_id (request → job dönüşümünün izi). Tablo yoksa önce açılır (taze
    ghost.db: bus henüz dokunmamış olabilir); sütun varsa atlanır (ALTER ADD COLUMN tekrarı hata verir)."""
    c.executescript("""
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  type TEXT NOT NULL,
  source TEXT NOT NULL,
  payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  done_ts TEXT
);
CREATE INDEX IF NOT EXISTS ev_status ON events(status, id);
""")
    var = {r[1] for r in c.execute("PRAGMA table_info(events)").fetchall()}
    for ad, tip in (("hedef", "TEXT"), ("kind", "TEXT"), ("sonuc", "TEXT"), ("ust_id", "INTEGER")):
        if ad not in var:
            c.execute("ALTER TABLE events ADD COLUMN %s %s" % (ad, tip))
    c.execute("CREATE INDEX IF NOT EXISTS ev_is ON events(type, hedef, status, id)")


_GOCLER = {
    1: """
CREATE TABLE IF NOT EXISTS durum(
  ad TEXT PRIMARY KEY,
  deger TEXT NOT NULL,
  ts REAL NOT NULL
);
""",
    2: _goc_2_is_kuyrugu,     # sql metni ya da çağrılabilir (bağlantıyı alır)
}


def baglan(timeout=10, goc=True):
    """sqlite3 bağlantısı: WAL + bekleyen göçler. Göç hatası fırlatır (sessiz yarım şema olmaz)."""
    c = sqlite3.connect(config.DB_PATH, timeout=timeout)
    c.execute("PRAGMA journal_mode=WAL")
    if goc:
        goc_uygula(c)
    return c


def surum(c):
    return int(c.execute("PRAGMA user_version").fetchone()[0])


def goc_uygula(c):
    """user_version < SURUM ise sırayla uygular; her adım tek işlem. Uygulanan sürüm listesi."""
    uygulanan = []
    mevcut = surum(c)
    for s in range(mevcut + 1, SURUM + 1):
        g = _GOCLER[s]
        if callable(g):
            g(c)
        else:
            c.executescript(g)
        c.execute("PRAGMA user_version=%d" % s)
        c.commit()
        uygulanan.append(s)
    return uygulanan
