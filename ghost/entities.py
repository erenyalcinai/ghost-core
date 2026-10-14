"""Dünya modeli v1 — varlık grafiği (VISION.md: 'asistandan zihne').

Kişiler, şirketler, projeler, siteler, cihazlar ve ARALARINDAKİ İLİŞKİLER.
Düz hafızanın (memory) üstünde yapısal katman: "X neyle bağlantılı?"
sorusuna cevap verir. seed() dossier'deki bilinen varlıkları yükler;
zamanla /varlik ve otomatik çıkarımla büyür.
"""
import sqlite3
import time

from ghost import config, sahip

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  type TEXT NOT NULL DEFAULT 'şey',   -- kişi|şirket|proje|site|uygulama|cihaz|hesap|şey
  notes TEXT,
  created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS relations(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  src INTEGER NOT NULL REFERENCES entities(id),
  rel TEXT NOT NULL,                   -- 'barındırır', 'sahibi', 'reposu', ...
  dst INTEGER NOT NULL REFERENCES entities(id),
  created TEXT NOT NULL,
  UNIQUE(src, rel, dst)
);
"""

# Varlık tohumu profilden gelir (ghost/sahip.py → ops/sahip.json); kod kişisel
# sabit taşımaz. Boş profilde yalnız Ghost'un kendi iskeleti kalır.
SEED = sahip.varliklar() or [("Ghost", "proje", "Bu altyapı")]
SEED_REL = sahip.iliskiler()


def _conn():
    c = sqlite3.connect(config.DB_PATH, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_SCHEMA)
    return c


def add(name, etype="şey", notes=None):
    c = _conn()
    try:
        # Var olan kaydın türünü/notunu varsayılan değerle EZME — link() gibi
        # dolaylı çağrılar 'şey' ile gelir, bilgi kaybolmasın.
        c.execute("INSERT INTO entities(name, type, notes, created) VALUES(?,?,?,?) "
                  "ON CONFLICT(name) DO UPDATE SET "
                  "type = CASE WHEN excluded.type != 'şey' THEN excluded.type "
                  "ELSE entities.type END, "
                  "notes = COALESCE(excluded.notes, entities.notes)",
                  (name, etype, notes, time.strftime("%F %T")))
        c.commit()
        return c.execute("SELECT id FROM entities WHERE name=?", (name,)).fetchone()[0]
    finally:
        c.close()


def link(src_name, rel, dst_name):
    a, b = add(src_name), add(dst_name)
    c = _conn()
    try:
        c.execute("INSERT OR IGNORE INTO relations(src, rel, dst, created) "
                  "VALUES(?,?,?,?)", (a, rel, b, time.strftime("%F %T")))
        c.commit()
    finally:
        c.close()


def get(name):
    """Varlık + tüm ilişkileri (iki yönlü). None = yok."""
    c = _conn()
    try:
        row = c.execute("SELECT id, name, type, notes FROM entities "
                        "WHERE name=? COLLATE NOCASE", (name,)).fetchone()
        if not row:
            return None
        eid, ename, etype, notes = row
        out_rels = c.execute(
            "SELECT r.rel, e.name FROM relations r JOIN entities e ON e.id=r.dst "
            "WHERE r.src=?", (eid,)).fetchall()
        in_rels = c.execute(
            "SELECT e.name, r.rel FROM relations r JOIN entities e ON e.id=r.src "
            "WHERE r.dst=?", (eid,)).fetchall()
        return {"name": ename, "type": etype, "notes": notes,
                "out": out_rels, "in": in_rels}
    finally:
        c.close()


def search(q):
    c = _conn()
    try:
        like = "%" + q + "%"
        return c.execute("SELECT name, type FROM entities WHERE name LIKE ? "
                         "OR notes LIKE ? LIMIT 10", (like, like)).fetchall()
    finally:
        c.close()


def seed():
    """Yalnız YOKSA ekle — elle güncellenen notları ezme (registry.seed gibi)."""
    c = _conn()
    try:
        existing = {r[0].lower() for r in
                    c.execute("SELECT name FROM entities").fetchall()}
    finally:
        c.close()
    for name, etype, notes in SEED:
        if name.lower() not in existing:
            add(name, etype, notes)
    for s, r, d in SEED_REL:
        link(s, r, d)  # ilişkiler UNIQUE — tekrar zararsız


# ---- Mac↔VPS paylaşımlı beyin: dışa aktar / birleştir (memsync taşır) ----
def export_rows():
    """(entities, relations) — id'ler makineye özel (AUTOINCREMENT),
    bu yüzden ilişkiler ad üzerinden taşınır."""
    c = _conn()
    try:
        ents = c.execute("SELECT name, type, notes, created FROM entities "
                         "ORDER BY id").fetchall()
        rels = c.execute(
            "SELECT s.name, r.rel, d.name, r.created FROM relations r "
            "JOIN entities s ON s.id=r.src JOIN entities d ON d.id=r.dst "
            "ORDER BY r.id").fetchall()
        return ents, rels
    finally:
        c.close()


def merge_rows(ents, rels):
    """Gelen grafı birleştir: yeni varlık/ilişki eklenir; var olan varlıkta
    yalnız BOŞ alan doldurulur ('şey' türü ve NULL not). Dolu not EZİLMEZ —
    zaman damgası olmadığından hangi tarafın güncel olduğu bilinemez; kayıp
    ve ping-pong yerine append-mostly (memory.merge_rows ile aynı felsefe).
    Değişen sayı."""
    changed = 0
    c = _conn()
    try:
        for row in ents:
            name, etype, notes = row[0], row[1], row[2]
            created = row[3] if len(row) > 3 else time.strftime("%F %T")
            cur = c.execute("SELECT id, type, notes FROM entities "
                            "WHERE name=? COLLATE NOCASE", (name,)).fetchone()
            if not cur:
                c.execute("INSERT INTO entities(name, type, notes, created) "
                          "VALUES(?,?,?,?)",
                          (name, etype or "şey", notes, created))
                changed += 1
                continue
            eid, ltype, lnotes = cur
            ntype = etype if (ltype == "şey" and etype and etype != "şey") else ltype
            nnotes = lnotes if lnotes is not None else notes
            if (ntype, nnotes) != (ltype, lnotes):
                c.execute("UPDATE entities SET type=?, notes=? WHERE id=?",
                          (ntype, nnotes, eid))
                changed += 1
        c.commit()
        for row in rels:
            src, rel, dst = row[0], row[1], row[2]
            created = row[3] if len(row) > 3 else time.strftime("%F %T")
            pair = []
            for nm in (src, dst):
                r = c.execute("SELECT id FROM entities WHERE name=? "
                              "COLLATE NOCASE", (nm,)).fetchone()
                if not r:
                    c.execute("INSERT INTO entities(name, type, created) "
                              "VALUES(?,'şey',?)", (nm, created))
                    r = c.execute("SELECT id FROM entities WHERE name=?",
                                  (nm,)).fetchone()
                pair.append(r[0])
            n = c.execute("INSERT OR IGNORE INTO relations(src, rel, dst, created) "
                          "VALUES(?,?,?,?)", (pair[0], rel, pair[1], created)).rowcount
            changed += n
        c.commit()
        return changed
    finally:
        c.close()


def render(name):
    """/varlik çıktısı — telefonda okunur grafik görünümü."""
    e = get(name)
    if not e:
        hits = search(name)
        if hits:
            return "Bulunamadı: %s\nBenzerler: %s" % (
                name, ", ".join(h[0] for h in hits))
        return "Varlık yok: %s (/varlik ekle <ad> | <tür> | <not>)" % name
    lines = ["🕸 %s (%s)" % (e["name"], e["type"])]
    if e["notes"]:
        lines.append(e["notes"])
    for rel, dst in e["out"]:
        lines.append("  → %s: %s" % (rel, dst))
    for src, rel in e["in"]:
        lines.append("  ← %s (%s)" % (src, rel))
    return "\n".join(lines)
