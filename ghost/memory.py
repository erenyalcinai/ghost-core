"""Hafıza Sarayı çekirdeği — SQLite (+FTS5 varsa) kalıcı bellek.

remember(): her öğrenme/olay tek kayıt. recall(): tam-metin arama.
FTS5 yoksa LIKE'a düşer (VPS/Mac stok Python farkları için).

GERİ ÇAĞIRMA KADEMESİ (2026-10-29, bütünsel denetim 05-hafiza-zihin §1/§6/Ek B):
eski recall sorgu kelimelerini OR'layıp eşiksiz döndürüyordu → "AppA için bugün
ne yapalım" 3 bayat iş logu getiriyordu, doğal dil soruları 0/10 ıskalıyordu.
Yeni yol: anahtar terimler (3+ harf, Türkçe durak listesi dışı) → FTS AND →
boşsa NEAR/5 → boşsa yalnız AYIRT EDİCİ (kayıtların ≤%25'inde geçen) en seyrek 2
terim OR; bm25 sıralar; iş/test/devir kayıtları dışarıda (parametreyle açılır);
özdeş metin tekilleşir; hiç eşleşme yoksa BOŞ liste. `baglam_notu()`
sohbet turuna girecek ≤600 karakterlik metni üretir — eşik altı → "" (gürültü
cevapsızlıktan beterdir; duyular ilkesi). Terim çıkarımı (`anahtar_terimler`)
arsiv_ara'nın md/dossier/Masa kelime-kesişimi için de TEK kaynaktır.
"""
import re
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


# ---- anahtar terimler (recall + arsiv_ara ortak) ----
# Durak liste: soru kalıbı/bağlaç/zamir + "sahip"/"ghost" (hemen her kayıtta geçer,
# AND kademesini boşa düşürür). Küçük harf, Türkçe karakterli; _tr() ile eşlenir.
DURAK = frozenset("""
ve için ile ne nasıl neden niye nerede nereden nereye hangi kim kaç mi mı mu mü
bu şu da de ki ya ama fakat veya yani gibi kadar daha çok en var yok bugün yarın
şimdi son olan oldu olur olacak olmuş nedir neydi neler durum durumu durumda
hakkında ilgili ben sen biz siz bana sana bize bunu şunu onu bunun şunun onun
lütfen bir iki hep hiç mıydı miydi muydu müydü musun misin mısın müsün değil
evet hayır peki acaba zaman bakalım yapalım yapmalı biraz bak söyle anlat
hatırla hatırlıyor hatırlıyorsun nasıldı kimdir nerde selam merhaba nasılsın
iyi teşekkür tamam olsun ise eğer çünkü hem her tüm bütün bazı başka diğer aynı
böyle şöyle öyle sonra önce artık yine hâlâ hala henüz zaten sadece yalnız bile
dedi diyor dediği demiş mesela örneğin şey şeyi şeyler şeyin bilgi bilgisi kısaca
sahip ghost the and for what how is are was with about
""".split())
_KELIME = re.compile(r"[\w.]+", re.UNICODE)
# Eşik: AND/NEAR tam eşleşmedir, eşik yok. OR kademesi gevşektir → yalnız AYIRT EDİCİ terim
# girer: kayıtların ≤%25'inde geçen (taban 3 kayıt). Sayısal bm25 eşiği denendi ve atıldı:
# FTS5, kayıtların yarısından çoğunda geçen terimin idf'sini ~0'a kırpar, küçük korpusta her
# terim öyle görünür → doğru kayıt düşüyordu. bm25 yalnız SIRALAMA için kalır.
OR_PAY = 0.25
OR_TABAN = 3
HARIC_TURLER = ("iş", "test", "devir")   # iş günlüğü/test kaçağı/devir notu — bilgi değil


_BUYUK = re.compile(r"\b[A-ZÇĞİÖŞÜ0-9.]{2,}\b")


def _tr(s):
    """Türkçe-doğru küçük harf: İ→i; I→ı (Taşra) — ama TAMAMI büyük kısaltmada
    (IAP, ISNIC, CLAUDE) I→i, çünkü FTS unicode61 de öyle katlar. casefold tek
    başına I→i, İ→"i̇" (iki karakter) yapar."""
    s = _BUYUK.sub(lambda m: m.group().replace("I", "i"), str(s or ""))
    return s.replace("I", "ı").replace("İ", "i").casefold()


def anahtar_terimler(metin, en_fazla=8):
    """Mesajdan sıralı, tekrarsız anahtar terimler: 3+ harf, durak dışı, apostrof
    eki atılmış ("Yalco'de" → "yalco"); noktalı kimlikler korunur ("sahip.kim",
    "appb.full"); 4 haneden kısa salt sayı atılır."""
    out = []
    for ham in _KELIME.findall(_tr(metin).replace("’", "'")):
        t = ham.split("'")[0].strip(".")
        if len(t) < 3 or t in DURAK:
            continue
        if t.isdigit() and len(t) < 4:
            continue
        if t not in out:
            out.append(t)
    return out[:en_fazla]


def kok(terim):
    """Kaba Türkçe kök (önek eşleşmesi için): ≤4 olduğu gibi, 5 → son 1 atılır,
    6-8 → son 2, 9+ → son 3; noktalı kimlik dokunulmaz. "kararı"→"kara", "portta"→"port",
    "çalışıyor"→"çalışı", "tescili"→"tesci". Fazla kesme yanlış eşleşme getirir ama
    sıralama idf ile düzelir; az kesme ise eki farklı kelimeyi hiç bulamaz."""
    n = len(terim)
    if "." in terim or n <= 4:
        return terim
    return terim[:-1] if n == 5 else terim[:-2] if n <= 8 else terim[:-3]


def _fts_terim(terim):
    """FTS5 parçası: noktalı kimlik tırnaklı ifade ("sahip kim"), diğerleri kök+önek."""
    if "." in terim:
        return '"%s"' % terim.replace(".", " ").replace('"', "")
    return '"%s"*' % kok(terim).replace('"', "")


def _recall_fts(c, terimler, k, haric):
    """AND → NEAR/5 → ayırt edici en seyrek 2 terim OR kademesi; bm25 sırası; özdeş metin
    tekilleşir; [(ts, kind, text)]."""
    parcalar = [_fts_terim(t) for t in terimler]
    adaylar = [" AND ".join(parcalar)]
    yer = ",".join("?" * len(haric))
    tur_kosul = ("AND m.kind NOT IN (%s) " % yer) if haric else ""
    if len(parcalar) > 1:
        adaylar.append("NEAR(%s, 5)" % " ".join(parcalar))
        toplam = c.execute("SELECT count(*) FROM memories m WHERE m.deleted=0 " + tur_kosul, list(haric)).fetchone()[0]
        tavan = max(OR_TABAN, OR_PAY * toplam)
        sayim = []
        for p in parcalar:
            try:
                n = c.execute("SELECT count(*) FROM memories_fts f JOIN memories m ON m.id=f.rowid "
                              "WHERE memories_fts MATCH ? AND m.deleted=0 " + tur_kosul, [p] + list(haric)).fetchone()[0]
            except sqlite3.OperationalError:
                continue
            if 0 < n <= tavan:
                sayim.append((n, p))
        seyrek = [p for _, p in sorted(sayim)[:2]]
        if seyrek:
            adaylar.append(" OR ".join(seyrek))
    sql = ("SELECT m.ts, m.kind, m.text FROM memories_fts f JOIN memories m ON m.id = f.rowid "
           "WHERE memories_fts MATCH ? AND m.deleted=0 " + tur_kosul + "ORDER BY bm25(memories_fts) LIMIT ?")
    for q in adaylar:
        try:
            rows = c.execute(sql, [q] + list(haric) + [k * 4]).fetchall()
        except sqlite3.OperationalError:
            continue
        secim, gorulen = [], set()
        for ts, kind, text in rows:
            imza = " ".join(_tr(text).split())
            if imza in gorulen:   # özdeş metin (test kaçağı kopyaları)
                continue
            gorulen.add(imza)
            secim.append((ts, kind, text))
            if len(secim) >= k:
                break
        if secim:
            return secim
    return []


def recall(query, k=5, haric=HARIC_TURLER):
    """[(ts, kind, text)] — en alakalı k kayıt. Kademe: anahtar terimler → FTS AND →
    NEAR/5 → ayırt edici en seyrek 2 terim OR (bm25 sırası); hiç eşleşme yoksa []. `haric` türleri
    (varsayılan iş/test/devir) dışarıda; `haric=()` hepsini açar. Terim çıkmayan
    sorgu ("selam nasılsın") → []. FTS yoksa LIKE AND zinciri."""
    query = (query or "").strip()
    if not query:
        return []
    terimler = anahtar_terimler(query)
    if not terimler:
        return []
    haric = tuple(haric or ())
    c, fts = _conn()
    try:
        if fts:
            rows = _recall_fts(c, terimler, k, haric)
            if rows:
                return rows
            return []
        kos = " AND ".join("text LIKE ?" for _ in terimler)
        yer = ",".join("?" * len(haric))
        return c.execute(
            "SELECT ts, kind, text FROM memories WHERE %s AND deleted=0 %s ORDER BY id DESC LIMIT ?"
            % (kos, ("AND kind NOT IN (%s)" % yer) if haric else ""),
            ["%" + kok(t) + "%" for t in terimler] + list(haric) + [k]).fetchall()
    finally:
        c.close()


def baglam_notu(mesaj, k=3, en_fazla=600):
    """Sohbet turuna enjekte edilecek hafıza notu (≤en_fazla karakter) ya da "".
    Eşik altı / terimsiz mesaj / eşleşme yok → "" — köprü o zaman HİÇ enjekte etmez
    (eski "gereksizse yok say" bloğu her mesaja gürültü iliştiriyordu)."""
    try:
        rows = recall(mesaj, k=k)
    except Exception:  # noqa: BLE001 — DB kilidi/yokluğu sohbeti düşürmesin
        rows = []
    if not rows:
        return ""
    bas = "[Ghost kalıcı hafızasından ilgili kayıtlar:\n"
    son = "]"
    satirlar = []
    kalan = en_fazla - len(bas) - len(son)
    for ts, kind, text in rows:
        metin = " ".join(str(text).split())
        satir = "- [%s %s] %s" % (kind, str(ts)[:10], metin)
        if len(satir) + 1 > kalan:
            if kalan < 40:   # anlamlı bir parça sığmıyor
                break
            satir = satir[:kalan - 2] + "…"
        satirlar.append(satir)
        kalan -= len(satir) + 1
    if not satirlar:
        return ""
    return bas + "\n".join(satirlar) + son


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
