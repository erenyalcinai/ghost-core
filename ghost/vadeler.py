"""Vadeler — tarihi olan yükümlülükler tek tabloda (Faz 1.2, 2026-10-29).

NEDEN (bütünsel denetim 05 #4): token dolumu, alan adı bitişi, marka bülteni, kill
ölçütü gibi tarihler hafıza dosyalarına ve dossier'a dağılmıştı; hiçbiri sabah
brifingine çıkmıyordu, Ghost yalnız Sahip sorunca hatırlıyordu. Tek tablo + "yaklaşan
14 gün" satırı: tarih yaklaşınca Ghost kendiliğinden söyler, geçince "gecikti" der.

Kayıt kişisel içerik taşımaz (ad + tarih + kısa not); ghost.db `vadeler` tablosu.
CLI: python3 -m ghost.vadeler liste|ekle "<ad>" <YYYY-MM-DD> ["not"] [--kaynak k]|
     kapat <id>|iptal <id>|yaklasan [gün]
"""
import datetime
import sqlite3
import sys

from ghost import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS vadeler(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  tarih TEXT NOT NULL,
  ad TEXT NOT NULL,
  notu TEXT,
  kaynak TEXT,
  durum TEXT NOT NULL DEFAULT 'acik',
  kapandi_ts TEXT
);
"""
DURUMLAR = ("acik", "kapandi", "iptal")
VARSAYILAN_GUN = 14


def _conn():
    c = sqlite3.connect(config.DB_PATH, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_SCHEMA)
    return c


def _simdi():
    return datetime.datetime.now().strftime("%F %T")


def _tarih(s):
    """'2026-11-09' ya da '30.11.2026' → date. Geçersizse ValueError."""
    s = str(s or "").strip()
    for bicim in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(s, bicim).date()
        except ValueError:
            continue
    raise ValueError("tarih biçimi: YYYY-MM-DD ya da GG.AA.YYYY (%r)" % s)


def ekle(ad, tarih, notu="", kaynak="sohbet"):
    """Vade ekle; aynı ad+tarih açıksa yeniden eklemez, mevcut id'yi döner."""
    ad = (ad or "").strip()
    if not ad:
        raise ValueError("ad boş")
    t = _tarih(tarih).isoformat()
    c = _conn()
    try:
        r = c.execute("SELECT id FROM vadeler WHERE ad=? AND tarih=? AND durum='acik'",
                      (ad, t)).fetchone()
        if r:
            return r[0]
        cur = c.execute("INSERT INTO vadeler(ts,tarih,ad,notu,kaynak) VALUES(?,?,?,?,?)",
                        (_simdi(), t, ad, notu or "", kaynak or ""))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def kapat(vid, durum="kapandi"):
    if durum not in DURUMLAR or durum == "acik":
        raise ValueError("durum: kapandi|iptal")
    c = _conn()
    try:
        n = c.execute("UPDATE vadeler SET durum=?, kapandi_ts=? WHERE id=? AND durum='acik'",
                      (durum, _simdi(), int(vid))).rowcount
        c.commit()
        return n == 1
    finally:
        c.close()


def liste(durum="acik"):
    c = _conn()
    try:
        q = "SELECT id,tarih,ad,notu,kaynak,durum FROM vadeler"
        args = ()
        if durum:
            q += " WHERE durum=?"
            args = (durum,)
        q += " ORDER BY tarih, id"
        return [dict(zip(("id", "tarih", "ad", "notu", "kaynak", "durum"), r))
                for r in c.execute(q, args)]
    finally:
        c.close()


def yaklasan(gun=VARSAYILAN_GUN, bugun=None):
    """Açık vadelerden bugün+gun içine düşenler ve geçmişte kalanlar (gecikti).
    Her kayda `kalan` (gün, negatif = gecikti) eklenir."""
    bugun = bugun or datetime.date.today()
    out = []
    for v in liste("acik"):
        kalan = (_tarih(v["tarih"]) - bugun).days
        if kalan <= gun:
            v["kalan"] = kalan
            out.append(v)
    return out


def metin(gun=VARSAYILAN_GUN, bugun=None):
    """Brifing/Nabız satırları; yaklaşan yoksa boş dize (mesaj atılmaz)."""
    rows = yaklasan(gun, bugun)
    if not rows:
        return ""
    satirlar = ["📅 Vadeler (%d gün):" % gun]
    for v in rows:
        k = v["kalan"]
        etiket = ("GECİKTİ %d gün" % -k) if k < 0 else "bugün" if k == 0 else "%d gün" % k
        notu = (" — " + v["notu"]) if v.get("notu") else ""
        satirlar.append("• %s (%s) %s%s" % (v["tarih"], etiket, v["ad"], notu))
    return "\n".join(satirlar)


def _main(argv):
    if not argv or argv[0] == "liste":
        durum = argv[1] if len(argv) > 1 else "acik"
        rows = liste(None if durum == "hepsi" else durum)
        if not rows:
            print("(kayıt yok)")
        for v in rows:
            print("%3d  %s  %-8s %s%s" % (v["id"], v["tarih"], v["durum"], v["ad"],
                                         (" — " + v["notu"]) if v["notu"] else ""))
        return 0
    if argv[0] == "ekle":
        if len(argv) < 3:
            print("kullanım: ekle \"<ad>\" <tarih> [\"not\"] [--kaynak k]")
            return 2
        kaynak = "sohbet"
        if "--kaynak" in argv:
            i = argv.index("--kaynak")
            kaynak = argv[i + 1]
            argv = argv[:i] + argv[i + 2:]
        vid = ekle(argv[1], argv[2], argv[3] if len(argv) > 3 else "", kaynak)
        print("vade #%d" % vid)
        return 0
    if argv[0] in ("kapat", "iptal"):
        ok = kapat(argv[1], "kapandi" if argv[0] == "kapat" else "iptal")
        print("tamam" if ok else "açık kayıt bulunamadı")
        return 0 if ok else 1
    if argv[0] == "yaklasan":
        gun = int(argv[1]) if len(argv) > 1 else VARSAYILAN_GUN
        m = metin(gun)
        if m:
            print(m)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
