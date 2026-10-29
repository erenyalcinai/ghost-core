#!/usr/bin/env python3
"""Ghost durum yedekleme — DB'ler + audit tek kopyaydı (denetim SPOF bulgusu).

Günlük döndürmeli yerel yedek: ghost.db, economy.db, audit.jsonl → ops/backups/
altına tarihli kopya, son KEEP tutulur. SQLite için .backup API (tutarlı kopya,
WAL güvenli). morning_brief.sh günde bir çağırır.

KAPSAM: yerel yedek KORUPSİYON/kaza/hatalı-migration'a karşı korur. DİSK KAYBINA
karşı KORUMAZ (aynı disk) — offsite (uzak) yedek ayrı karar; bkz. rapor notu.
Çalıştır:  python3 ~/jarvis/ops/backup.py
"""
import os
import shutil
import sqlite3
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPS = os.path.join(BASE, "ops")
BACKUP_DIR = os.path.join(OPS, "backups")
KEEP = 3  # son 3 gün (2026-10-29: 7→3; uzak gpg kopya ops/mem_backup.py ile GitHub 'hafiza'
          # dalında günlük, yerel kopya yalnız kaza/bozulma içindir; 1,3 GB → ~0,6 GB)


def _sqlite_backup(src, dst):
    """Tutarlı kopya (WAL güvenli) — canlı DB'yi kilitlemeden .backup API."""
    if not os.path.exists(src):
        return False
    con = sqlite3.connect(src)
    try:
        bck = sqlite3.connect(dst)
        with bck:
            con.backup(bck)
        bck.close()
        return True
    finally:
        con.close()


def run(stamp=None):
    """Yedek al, eskiyi buda. stamp verilmezse... çağıran vermeli (Date.now
    kısıtı yok, os zamanı serbest)."""
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_DIR, stamp)
    os.makedirs(dst, exist_ok=True)
    done = []
    econ_db = os.environ.get("GHOST_ECON_DB") or os.path.join(OPS, "economy.db")
    for name, src in (("ghost.db", os.path.join(OPS, "ghost.db")),
                      ("economy.db", econ_db)):
        if _sqlite_backup(src, os.path.join(dst, name)):
            done.append(name)
    audit = os.path.join(OPS, "audit.jsonl")
    if os.path.exists(audit):
        shutil.copy2(audit, os.path.join(dst, "audit.jsonl"))
        done.append("audit.jsonl")
    _prune()
    return {"stamp": stamp, "backed_up": done, "dir": dst}


def _prune():
    try:
        snaps = sorted(d for d in os.listdir(BACKUP_DIR)
                       if os.path.isdir(os.path.join(BACKUP_DIR, d)))
    except OSError:
        return
    for old in snaps[:-KEEP]:
        shutil.rmtree(os.path.join(BACKUP_DIR, old), ignore_errors=True)


if __name__ == "__main__":
    r = run()
    print("yedek: %s → %s" % (", ".join(r["backed_up"]) or "(boş)", r["dir"]))
