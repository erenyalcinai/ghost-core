#!/usr/bin/env python3
"""Mac ghost.db test kirliliğini temizler (BİR KEZLİK).

Geçmiş `unittest discover` koşuları (config-before-common bug'ından önce)
gerçek ops/ghost.db'ye yüzlerce test satırı yazmıştı. O bug düzeltildi
(config.py PEP 562); bu betik kalıntıyı siler. Canlı VPS beyni zaten temiz.

Yalnız TEST-İMZALI satırları siler (VPS canlı beyninde bulunmayanlar).
Çalıştır:  ! python3 ~/jarvis/ops/clean_test_pollution.py
"""
import os
import sqlite3

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "ops", "ghost.db")


def main():
    c = sqlite3.connect(DB)
    c.execute("PRAGMA journal_mode=WAL")
    q = [
        ("monitors", "name IN ('test','gurultu') OR url LIKE 'http://ornek%'"),
        ("schedules", "spec LIKE '%30 dak%' OR spec LIKE '%10dk%' OR name LIKE '%test%'"),
        ("goals", "title LIKE 'Test%' OR title LIKE 'Donuk%' OR title LIKE 'Tik%' "
                  "OR title LIKE 'CANLI TEST%'"),
        ("memories", "kind='test'"),
        ("entities", "name LIKE '%Test%' OR name IN "
                     "('UzakVarlık','BoşVarlık','SenkronTestVarlık')"),
        ("relations", "src LIKE '%Test%' OR dst LIKE '%Test%'"),
        ("events", "source IN ('test','testkaynak')"),
    ]
    for table, where in q:
        try:
            n = c.execute("DELETE FROM %s WHERE %s" % (table, where)).rowcount
            print("%-11s sildi: %d" % (table, n))
        except sqlite3.OperationalError as e:
            print("%-11s atlandı (%s)" % (table, e))
    c.commit()
    print("--- kalan sayılar ---")
    for t in ("monitors", "schedules", "goals", "memories", "entities",
              "relations", "events"):
        try:
            print(t, c.execute("SELECT COUNT(*) FROM " + t).fetchone()[0])
        except sqlite3.OperationalError:
            pass
    c.close()
    print("Bitti. Doğrulama: gerçek kayıtların (89 hafıza, 17 varlık) yerinde.")


if __name__ == "__main__":
    main()
