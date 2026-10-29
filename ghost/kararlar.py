"""Kararlar defteri — sahibin kararları tek tabloda: geçerli / rafta / geri alındı (Faz 1.1, 2026-10-29).

NEDEN (bütünsel denetim 05 #1): "tekrar önerme" kararları ~40 hafıza dosyasına, CLAUDE.md'ye
ve dossier'a dağılmış; Ghost rafa kaldırılmış bir fikri yeniden önerip uyarı yiyordu. Defter kararı konuyla birlikte tutar; tur başında konuyla eşleşen 0-3 satır
bağlama girer ("[rafta]" etiketi tekrar önermeyi kaynağında keser).

SONUÇ DEFTERİ DEĞİL: 2026-10-26'da sökülen sonuç defteri iş SONUÇLARINI ve sahibin tepkisini
tutuyordu. Bu defter sahibin KARARLARINI tutar (yol haritası §8, K5). Ayrımı Sahip onaylamadan
toplu çıkarım (hafıza/CLAUDE.md/dossier'dan ~186 aday) yapılmaz; tek tek `ekle` serbest.

Kayıt: konu (kısa başlık), karar (bir-iki cümle), durum, tarih (karar günü), kaynak
(hafıza dosyası / dossier yolu / "sohbet"), etiket (virgüllü). Kişi yargısı yazılmaz.
CLI: python3 -m ghost.kararlar liste [durum|hepsi] | ekle "<konu>" "<karar>" [--durum rafta]
     [--tarih YYYY-MM-DD] [--kaynak k] [--etiket a,b] | ara "<sorgu>" | durum <id> <gecerli|rafta|
     geri_alindi|iptal> ["not"] | say
"""
import datetime
import sqlite3
import sys

from ghost import config, memory

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kararlar(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  tarih TEXT NOT NULL,
  konu TEXT NOT NULL,
  karar TEXT NOT NULL,
  durum TEXT NOT NULL DEFAULT 'gecerli',
  kaynak TEXT,
  etiket TEXT,
  notu TEXT,
  guncellendi TEXT
);
"""
_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS kararlar_fts
USING fts5(konu, karar, etiket, content='kararlar', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS kararlar_ai AFTER INSERT ON kararlar BEGIN
  INSERT INTO kararlar_fts(rowid, konu, karar, etiket) VALUES (new.id, new.konu, new.karar, new.etiket);
END;
CREATE TRIGGER IF NOT EXISTS kararlar_ad AFTER DELETE ON kararlar BEGIN
  INSERT INTO kararlar_fts(kararlar_fts, rowid, konu, karar, etiket)
  VALUES('delete', old.id, old.konu, old.karar, old.etiket);
END;
CREATE TRIGGER IF NOT EXISTS kararlar_au AFTER UPDATE ON kararlar BEGIN
  INSERT INTO kararlar_fts(kararlar_fts, rowid, konu, karar, etiket)
  VALUES('delete', old.id, old.konu, old.karar, old.etiket);
  INSERT INTO kararlar_fts(rowid, konu, karar, etiket) VALUES (new.id, new.konu, new.karar, new.etiket);
END;
"""
DURUMLAR = ("gecerli", "rafta", "geri_alindi", "iptal")
ETIKET = {"gecerli": "", "rafta": "[rafta] ", "geri_alindi": "[geri alındı] ", "iptal": "[iptal] "}
_ALANLAR = ("id", "tarih", "konu", "karar", "durum", "kaynak", "etiket", "notu")


def _conn():
    c = sqlite3.connect(config.DB_PATH, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_SCHEMA)
    fts = True
    try:
        c.executescript(_FTS)
    except sqlite3.OperationalError:
        fts = False
    return c, fts


def _simdi():
    return datetime.datetime.now().strftime("%F %T")


def ekle(konu, karar, durum="gecerli", tarih=None, kaynak="sohbet", etiket="", notu=""):
    """Karar ekle; aynı konu+karar varsa (durumu ne olursa olsun) yeniden eklemez, id'sini döner."""
    konu, karar = (konu or "").strip(), (karar or "").strip()
    if not konu or not karar:
        raise ValueError("konu ve karar boş olamaz")
    if durum not in DURUMLAR:
        raise ValueError("durum: %s" % "|".join(DURUMLAR))
    tarih = tarih or datetime.date.today().isoformat()
    datetime.date.fromisoformat(tarih)   # biçim denetimi
    c, _ = _conn()
    try:
        r = c.execute("SELECT id FROM kararlar WHERE konu=? AND karar=?", (konu, karar)).fetchone()
        if r:
            return r[0]
        cur = c.execute(
            "INSERT INTO kararlar(ts,tarih,konu,karar,durum,kaynak,etiket,notu) VALUES(?,?,?,?,?,?,?,?)",
            (_simdi(), tarih, konu, karar, durum, kaynak or "", etiket or "", notu or ""))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def durum_degistir(kid, durum, notu=""):
    if durum not in DURUMLAR:
        raise ValueError("durum: %s" % "|".join(DURUMLAR))
    c, _ = _conn()
    try:
        n = c.execute("UPDATE kararlar SET durum=?, notu=CASE WHEN ?='' THEN notu ELSE ? END, "
                      "guncellendi=? WHERE id=?", (durum, notu or "", notu or "", _simdi(), int(kid))).rowcount
        c.commit()
        return n == 1
    finally:
        c.close()


def liste(durum=None):
    c, _ = _conn()
    try:
        q = "SELECT %s FROM kararlar" % ",".join(_ALANLAR)
        args = ()
        if durum:
            q += " WHERE durum=?"
            args = (durum,)
        q += " ORDER BY tarih DESC, id DESC"
        return [dict(zip(_ALANLAR, r)) for r in c.execute(q, args)]
    finally:
        c.close()


def say():
    c, _ = _conn()
    try:
        out = {d: 0 for d in DURUMLAR}
        for d, n in c.execute("SELECT durum, COUNT(*) FROM kararlar GROUP BY durum"):
            out[d] = n
        out["toplam"] = sum(out[d] for d in DURUMLAR)
        return out
    finally:
        c.close()


def ara(sorgu, k=5):
    """Anahtar terimlerle FTS (AND, sonra OR); FTS yoksa LIKE. İptal edilenler hariç değil —
    'iptal' de bir karardır ve tekrar önermeyi keser."""
    terimler = memory.anahtar_terimler(sorgu)
    if not terimler:
        return []
    c, fts = _conn()
    try:
        cols = ",".join("k." + a for a in _ALANLAR)
        if fts:
            parcalar = [memory._fts_terim(t) for t in terimler]
            for baglac in (" AND ", " OR "):
                q = baglac.join(parcalar)
                try:
                    rows = c.execute(
                        "SELECT %s FROM kararlar_fts f JOIN kararlar k ON k.id=f.rowid "
                        "WHERE kararlar_fts MATCH ? ORDER BY bm25(kararlar_fts) LIMIT ?" % cols,
                        (q, k)).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                if rows:
                    return [dict(zip(_ALANLAR, r)) for r in rows]
            return []
        kosul = " OR ".join(["lower(k.konu||' '||k.karar||' '||k.etiket) LIKE ?"] * len(terimler))
        rows = c.execute("SELECT %s FROM kararlar k WHERE %s ORDER BY k.tarih DESC LIMIT ?" % (cols, kosul),
                         tuple("%%%s%%" % memory.kok(t) for t in terimler) + (k,)).fetchall()
        return [dict(zip(_ALANLAR, r)) for r in rows]
    finally:
        c.close()


def satir(r):
    return "- %s%s: %s (%s)" % (ETIKET.get(r["durum"], ""), r["konu"], r["karar"], r["tarih"])


def baglam_notu(mesaj, k=3):
    """Tur başında bağlama girecek 0-3 satır; eşleşme yoksa boş dize. Yalnız AND eşleşmesi
    (OR gürültü getirir — hafıza isabet denetimindeki dersin aynısı)."""
    terimler = memory.anahtar_terimler(mesaj)
    if len(terimler) < 1:
        return ""
    c, fts = _conn()
    try:
        if not fts:
            return ""
        q = " AND ".join(memory._fts_terim(t) for t in terimler)
        try:
            rows = c.execute(
                "SELECT %s FROM kararlar_fts f JOIN kararlar k ON k.id=f.rowid "
                "WHERE kararlar_fts MATCH ? ORDER BY bm25(kararlar_fts) LIMIT ?"
                % ",".join("k." + a for a in _ALANLAR), (q, k * 3)).fetchall()
        except sqlite3.OperationalError:
            return ""
        if not rows and len(terimler) >= 3:
            # Gevşet: en belirleyici (uzun) iki terim yeter — "appa için tekrar testflight açalım mı"
            uzun = sorted(terimler, key=len, reverse=True)[:2]
            q = " AND ".join(memory._fts_terim(t) for t in uzun)
            try:
                rows = c.execute(
                    "SELECT %s FROM kararlar_fts f JOIN kararlar k ON k.id=f.rowid "
                    "WHERE kararlar_fts MATCH ? ORDER BY bm25(kararlar_fts) LIMIT ?"
                    % ",".join("k." + a for a in _ALANLAR), (q, k * 3)).fetchall()
            except sqlite3.OperationalError:
                rows = []
        # Rafta/iptal/geri alındı önce: "tekrar önerme" hatasını kesen satırlar bm25'e yenilmesin (2026-10-29).
        # Raftakiler ayrıca gevşek (OR) eşleşmeyle de aranır: "AppA BEKLEMEDE" kaydında 'testflight' geçmiyordu,
        # AND sorusu onu kaçırıyordu; rafta kümesi küçük (≈50), OR orada gürültü üretmez.
        try:
            q_or = " OR ".join(memory._fts_terim(t) for t in terimler)
            rafta = c.execute(
                "SELECT %s FROM kararlar_fts f JOIN kararlar k ON k.id=f.rowid WHERE kararlar_fts MATCH ? "
                "AND k.durum IN ('rafta','iptal','geri_alindi') ORDER BY k.tarih DESC, bm25(kararlar_fts) LIMIT ?"
                % ",".join("k." + a for a in _ALANLAR), (q_or, k)).fetchall()
        except sqlite3.OperationalError:
            rafta = []
        gorulen, birlesik = set(), []
        for r in list(rafta) + list(rows):
            if r[0] not in gorulen:
                gorulen.add(r[0])
                birlesik.append(r)
        kayitlar = [dict(zip(_ALANLAR, r)) for r in birlesik]
        kayitlar.sort(key=lambda r: 0 if r["durum"] in ("rafta", "iptal", "geri_alindi") else 1)
        return "\n".join(satir(r) for r in kayitlar[:k])
    finally:
        c.close()


def _secenek(argv, ad, varsayilan=None):
    if ad in argv:
        i = argv.index(ad)
        deger = argv[i + 1]
        del argv[i:i + 2]
        return deger
    return varsayilan


def _main(argv):
    argv = list(argv)
    if not argv or argv[0] == "liste":
        durum = argv[1] if len(argv) > 1 else None
        rows = liste(None if durum in (None, "hepsi") else durum)
        if not rows:
            print("(kayıt yok)")
        for r in rows:
            print("%3d  %s  %-11s %s: %s" % (r["id"], r["tarih"], r["durum"], r["konu"], r["karar"]))
        return 0
    if argv[0] == "ekle":
        durum = _secenek(argv, "--durum", "gecerli")
        tarih = _secenek(argv, "--tarih")
        kaynak = _secenek(argv, "--kaynak", "sohbet")
        etiket = _secenek(argv, "--etiket", "")
        if len(argv) < 3:
            print('kullanım: ekle "<konu>" "<karar>" [--durum rafta] [--tarih YYYY-MM-DD] [--kaynak k] [--etiket a,b]')
            return 2
        print("karar #%d" % ekle(argv[1], argv[2], durum, tarih, kaynak, etiket))
        return 0
    if argv[0] == "ara":
        rows = ara(" ".join(argv[1:]))
        print("\n".join(satir(r) for r in rows) if rows else "(eşleşme yok)")
        return 0
    if argv[0] == "durum":
        ok = durum_degistir(argv[1], argv[2], argv[3] if len(argv) > 3 else "")
        print("tamam" if ok else "kayıt bulunamadı")
        return 0 if ok else 1
    if argv[0] == "say":
        s = say()
        print("toplam %d · geçerli %d · rafta %d · geri alındı %d · iptal %d"
              % (s["toplam"], s["gecerli"], s["rafta"], s["geri_alindi"], s["iptal"]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
