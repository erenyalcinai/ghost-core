#!/usr/bin/env python3
"""Geri yükleme provası — yedeğin GERÇEKTEN açılıp açılmadığını kanıtlar.

Alınmış ama hiç denenmemiş yedek, yedek değildir; bozuk olduğunu en kötü günde
öğrenirsin. Bu betik uzaktaki dalı çeker, şifreyi çözer, DB'yi AÇAR ve satır
sayar. Yerel dosyalara hiç bakmaz — sahiden uzaktan dönüş provası.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "ornek-org/jarvis"
BRANCH = "hafiza"


def _load_env():
    p = os.path.join(BASE, "secrets.env")
    for line in open(p, encoding="utf-8", errors="replace"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _gh_token():
    with open(os.path.join(BASE, "mcp-github.json"), encoding="utf-8") as f:
        return json.load(f)["mcpServers"]["github"]["headers"][
            "Authorization"].split()[1]


def main():
    _load_env()
    token = _gh_token()
    pw = os.environ["GHOST_BACKUP_PASS"]
    notlar = []

    # Diskte aç, /tmp'de DEĞİL: /tmp 1,9 GB tmpfs + usrquota; 54 MB arşiv açılırken
    # "Disk quota exceeded" (2026-10-05 provası böyle düştü). Kök bölümde 20 GB var.
    prova_kok = os.path.join(BASE, "ops", "backups", ".prova")
    os.makedirs(prova_kok, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ghostprova-", dir=prova_kok) as tmp:
        rp = os.path.join(tmp, "repo")
        env = dict(os.environ, GH_TOKEN=token)
        helper = "!f(){ echo username=x-access-token; echo password=$GH_TOKEN; };f"
        r = subprocess.run(
            ["git", "-c", "credential.helper=" + helper, "clone", "-q",
             "--depth", "1", "-b", BRANCH,
             "https://github.com/%s.git" % REPO, rp],
            env=env, capture_output=True, text=True)
        if r.returncode != 0:
            print("PROVA ✗ dal çekilemedi: %s" % r.stderr.strip()[:200])
            return 1

        d = os.path.join(rp, "gunluk")
        # Parçalı arşiv (2026-10-29): '<damga>.tar.gz.gpg.NN' → damgaya göre grupla, en yenisini birleştir
        dosyalar = sorted(x for x in os.listdir(d) if ".tar.gz.gpg" in x)
        snaps = sorted({x.split(".tar.gz.gpg")[0] for x in dosyalar})
        if not snaps:
            print("PROVA ✗ günlük anlık görüntü yok")
            return 1
        parcalar = [x for x in dosyalar if x.split(".tar.gz.gpg")[0] == snaps[-1]]
        son = os.path.join(tmp, "son.tar.gz.gpg")
        with open(son, "wb") as birlesik:
            for x in parcalar:
                with open(os.path.join(d, x), "rb") as pf:
                    shutil.copyfileobj(pf, birlesik)
        notlar.append("uzakta %d günlük görüntü (%d parça)" % (len(snaps), len(parcalar)))

        tar = os.path.join(tmp, "c.tar.gz")
        r = subprocess.run(
            ["gpg", "--batch", "--quiet", "--yes", "--decrypt",
             "--passphrase-fd", "0", "-o", tar, son],
            input=pw.encode(), capture_output=True)
        if r.returncode != 0:
            print("PROVA ✗ şifre çözülemedi: %s" % r.stderr.decode()[:200])
            return 1
        notlar.append("şifre çözüldü")

        out = os.path.join(tmp, "x")
        with tarfile.open(tar) as t:
            t.extractall(out)
        kok = os.path.join(out, os.listdir(out)[0])

        man = json.load(open(os.path.join(kok, "MANIFEST.json"), encoding="utf-8"))
        notlar.append("manifest %d kalem" % len(man["icerik"]))

        db = os.path.join(kok, "ops", "ghost.db")
        if not os.path.exists(db):
            print("PROVA ✗ ghost.db arşivde yok")
            return 1
        con = sqlite3.connect(db)
        tablolar = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        toplam = 0
        for t in tablolar:
            try:
                toplam += con.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
            except sqlite3.Error:
                pass
        bozuk = con.execute("PRAGMA integrity_check").fetchone()[0]
        con.close()
        if bozuk != "ok":
            print("PROVA ✗ ghost.db bütünlük: %s" % bozuk)
            return 1
        notlar.append("ghost.db açıldı: %d tablo, %d satır, bütünlük ok"
                      % (len(tablolar), toplam))

        for gerekli in ["secrets.env", "ops/audit.jsonl"]:
            if os.path.exists(os.path.join(kok, gerekli)):
                notlar.append("%s var" % gerekli)
            else:
                notlar.append("⚠ %s YOK" % gerekli)

    print("PROVA ✓ %s — %s" % (snaps[-1],
                               " · ".join(notlar)))
    st = os.path.join(BASE, "ops", "backup_drill.json")
    json.dump({"utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "sonuc": "ok", "notlar": notlar}, open(st, "w"), ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
