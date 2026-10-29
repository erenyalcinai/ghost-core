"""Şema sürümü + tek bağlantı noktası (Faz 1.9 durum katmanı, 2026-10-29).

NEDEN (bütünsel denetim 03 6a/6b): ghost.db'ye 20+ modül kendi CREATE TABLE'ıyla giriyor, 29 ayrı json durum
dosyası ops/ altında dağınık, hiçbir yerde "şema hangi sürümde" sorusunun cevabı yok. Bu modül: `PRAGMA user_version`
ile sürümlü göç + `baglan()` tek bağlantı kuralı (WAL, zaman aşımı, satır fabrikası yok). Modüller kendi tablolarını
hâlâ CREATE IF NOT EXISTS ile açar (strangler — hiçbir şey durmaz); yeni tablo/sütun buraya göç olarak eklenir.
"""
import sqlite3

from ghost import config

SURUM = 1
_GOCLER = {
    1: """
CREATE TABLE IF NOT EXISTS durum(
  ad TEXT PRIMARY KEY,
  deger TEXT NOT NULL,
  ts REAL NOT NULL
);
""",
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
        c.executescript(_GOCLER[s])
        c.execute("PRAGMA user_version=%d" % s)
        c.commit()
        uygulanan.append(s)
    return uygulanan
