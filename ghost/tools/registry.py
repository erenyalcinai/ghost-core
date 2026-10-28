"""Alet Kayıt Defteri — Ghost'un elindeki her yetenek tek yerde.

Her alet: ad, tür, açıklama, komut şablonu, izin sınıfı, kullanım istatistiği
(çalışma/hata/ortalama süre → güvenilirlik). Router ve sentez katmanı buradan
besleniyor; yeni üretilen aletler buraya kaydolur ve kalıcı olur.
"""
import sqlite3
import time

from ghost import config, governance

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tools(
  name TEXT PRIMARY KEY,
  kind TEXT NOT NULL,          -- engine|automation|mcp|api|script|shell
  descr TEXT NOT NULL,
  command TEXT,                -- şablon: {args} yer tutucu; MCP/API için ipucu
  perm_class TEXT NOT NULL DEFAULT 'read',
  runs INTEGER NOT NULL DEFAULT 0,
  fails INTEGER NOT NULL DEFAULT 0,
  total_secs REAL NOT NULL DEFAULT 0,
  created TEXT NOT NULL,
  origin TEXT NOT NULL DEFAULT 'seed'   -- seed|synthesized|manual
);
"""

# Ghost'un bugünkü cephaneliği — çekirdek kurulumda kaydolur.
SEED = [
    ("claude_code", "engine", "Birincil akıl/kod motoru (Claude Code CLI); MCP evrenine buradan erişilir",
     "claude -p {args} --output-format json", governance.REVERSIBLE),
    ("shell", "shell", "Ham kabuk komutu (sandbox içinde)", "{args}", governance.PERMANENT),
    ("playwright_mcp", "automation", "Headless tarayıcı (web gezinme/test/screenshot) — claude_code üzerinden MCP",
     None, governance.REVERSIBLE),
    ("github_mcp", "mcp", "GitHub: repo/PR/issue/CI — claude_code üzerinden MCP", None, governance.REVERSIBLE),
    ("sentry_mcp", "mcp", "Sentry hata takibi — claude_code üzerinden MCP", None, governance.READ),
    ("cloudflare_api", "api", "Cloudflare REST (DNS/Pages/Workers/analytics); sır vekili ya da env token",
     ("curl -s %s/{args}" % __import__("ghost.vekil", fromlist=["x"]).url("cloudflare", "/client/v4")
      if __import__("ghost.vekil", fromlist=["x"]).aktif() else
      "curl -s -H 'Authorization: Bearer $CLOUDFLARE_API_TOKEN' https://api.cloudflare.com/client/v4/{args}"),
     governance.PERMANENT),
    ("posthog_api", "api", "PostHog salt-okunur analitik (HogQL)", None, governance.READ),
    ("send_telegram", "script", "Sahibe Telegram mesajı", "python3 ops/send_telegram.py", governance.REVERSIBLE),
    ("send_photo", "script", "Sahibe Telegram görseli", "python3 ops/send_photo.py {args}", governance.REVERSIBLE),
    ("whisper", "script", "Ses→metin (yerel whisper.cpp)", "whisper-cli -m {model} -f {args}", governance.READ),
    ("ffmpeg", "script", "Ses/video dönüştürme", "ffmpeg {args}", governance.REVERSIBLE),
    ("mem_backup", "script", "Hafızanın şifreli dış yedeği (GitHub hafiza dalı) + geri yükleme provası",
     "python3 ops/mem_backup.py", governance.REVERSIBLE),
    ("listen", "script", "Dinleme organı: HN tam eşleşme + App Store yorumları, tekilleştirilmiş",
     "python3 ops/listen.py", governance.READ),
    ("iphone", "automation", "iPhone 13 mini (Ghost'un cihazı) — ekran/dokunma/yazma; "
     "mobile-mcp + WebDriverAgent, YALNIZ Mac işlerinden erişilir (telefon Mac'e bağlı)",
     None, governance.REVERSIBLE),
]


def _conn():
    c = sqlite3.connect(config.DB_PATH, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_SCHEMA)
    return c


def register(name, kind, descr, command=None, perm_class=governance.READ,
             origin="manual"):
    c = _conn()
    try:
        c.execute(
            "INSERT INTO tools(name, kind, descr, command, perm_class, created, origin) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
            "kind=excluded.kind, descr=excluded.descr, command=excluded.command, "
            "perm_class=excluded.perm_class",
            (name, kind, descr, command, perm_class, time.strftime("%F %T"), origin))
        c.commit()
    finally:
        c.close()


def seed():
    for name, kind, descr, command, perm in SEED:
        c = _conn()
        try:
            exists = c.execute("SELECT 1 FROM tools WHERE name=?", (name,)).fetchone()
        finally:
            c.close()
        if not exists:
            register(name, kind, descr, command, perm, origin="seed")


def get(name):
    c = _conn()
    try:
        r = c.execute("SELECT name, kind, descr, command, perm_class, origin "
                      "FROM tools WHERE name=?", (name,)).fetchone()
    finally:
        c.close()
    if not r:
        return None
    # Envanter görünümü. NOT: runs/fails/total_secs sütunları şemada duruyor
    # (eski) ama artık güncellenmiyor — güvenilirlik skoru gerçek çalıştırmaya
    # bağlı değildi (run_tool söküldü). Registry = "Ghost'un yetenek envanteri".
    return {"name": r[0], "kind": r[1], "descr": r[2], "command": r[3],
            "perm_class": r[4], "origin": r[5]}


def list_tools():
    c = _conn()
    try:
        rows = c.execute("SELECT name, kind, descr, origin "
                         "FROM tools ORDER BY kind, name").fetchall()
    finally:
        c.close()
    return [{"name": r[0], "kind": r[1], "descr": r[2], "origin": r[3]}
            for r in rows]
