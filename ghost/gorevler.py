"""Eller — Ghost'un sahibin telefonuna YAZDIĞI görev kuyruğu (Faz 6).

Faz 5 duyularla tek yön kurulmuştu: telefon anlatır, Ghost dinler. Bu modül ters
yönü açar. Ghost bir görev bırakır, uygulama onu iOS'un KENDİ sistemine yazar:
- `hatirlatici` → gerçek bir iOS Hatırlatıcı (alarmlı, Ghost kapalıyken bile çalar)
- `etkinlik`   → gerçek bir Takvim etkinliği

Neden Telegram mesajı yetmiyor: Ghost'un kendi zamanlayıcısı (ghost/schedule.py)
mesaj atar — telefon sessizdeyse, uçak modundaysa ya da Sahip Telegram'a bakmıyorsa
kaybolur. iOS hatırlatıcısı işletim sisteminin alarmıdır; Ghost'a, VPS'e, ağa
bağlı değildir. İkisi farklı araçtır, biri ötekinin yerine geçmez.

Akış: `ekle()` → kuyrukta `bekliyor` → uygulama `GET /v1/gorevler` ile çeker,
cihazda uygular → `POST /v1/gorevler/<id>` ile sonucu yazar → Ghost sahibe
"kuruldu" der. Sonuç GERİ BİLDİRİLİR; "gönderdim" ile "telefona yazıldı" ayrı
şeylerdir ve karıştırılırsa Sahip olmayan bir hatırlatıcıya güvenir.
"""
import json
import sqlite3
import time

from ghost import config, governance, kokpit

TURLER = ("hatirlatici", "etkinlik")
BEKLEYEN_TAVAN = 50        # tek çekimde uygulamaya verilecek en fazla görev
OMUR_GUN = 30              # bu yaştan eski BİTMİŞ görevler budanır

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kokpit_gorevler(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tur TEXT, veri TEXT, durum TEXT, sonuc TEXT, yerel_id TEXT,
    cihaz TEXT, ts TEXT, biten TEXT);
"""


def _db():
    c = sqlite3.connect(config.DB_PATH, timeout=10)
    c.executescript(_SCHEMA)
    return c


def _satir(r):
    return {"id": r[0], "tur": r[1], "veri": json.loads(r[2] or "{}"), "durum": r[3],
            "sonuc": r[4], "yerel_id": r[5], "cihaz": r[6], "ts": r[7], "biten": r[8]}


def _zaman(metin):
    """'yarın 10:00' · '30dk' · '2026-09-05 14:30' → epoch. Anlaşılmazsa None."""
    s = (metin or "").strip()
    if not s:
        return None
    for kalip in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return time.mktime(time.strptime(s, kalip))
        except ValueError:
            pass
    try:
        from ghost import schedule
        p = schedule.parse_spec(s)
        if p:
            return p[1]
    except Exception:
        pass
    return None


def ekle(tur, veri, sessiz=False):
    """Görev kuyruğa gir → (id, hata). Zaman doğrulaması BURADA yapılır: geçmişe
    hatırlatıcı kurmak sessizce hiçbir şey yapmaz, en kötü hata türü budur."""
    if tur not in TURLER:
        return None, "tür %s değil: %s" % ("|".join(TURLER), tur)
    veri = dict(veri or {})
    ad = (veri.get("ad") or "").strip()
    if not ad:
        return None, "başlık boş"
    veri["ad"] = ad[:200]

    ts = _zaman(veri.get("ne_zaman") or veri.get("bas"))
    if ts is None:
        return None, "zaman anlaşılmadı: %r (örnek: 'yarın 10:00', '30dk', '2026-09-05 14:30')" % (
            veri.get("ne_zaman") or veri.get("bas"))
    if ts < time.time() - 60:
        return None, "geçmiş bir zaman (%s) — hatırlatıcı hiç çalmazdı" % time.strftime("%F %H:%M", time.localtime(ts))
    veri["ts"] = ts
    veri["ne_zaman_metin"] = time.strftime("%F %H:%M", time.localtime(ts))
    if tur == "etkinlik":
        bit = _zaman(veri.get("bit")) if veri.get("bit") else None
        veri["bit_ts"] = bit if (bit and bit > ts) else ts + 3600

    c = _db()
    cur = c.execute("INSERT INTO kokpit_gorevler(tur, veri, durum, ts) VALUES(?,?, 'bekliyor', ?)",
                    (tur, json.dumps(veri, ensure_ascii=False), time.strftime("%F %T")))
    gid = cur.lastrowid
    c.commit()
    c.close()
    governance.audit("gorevler", "ekle", governance.REVERSIBLE,
                     "%s #%d: %s @ %s" % (tur, gid, veri["ad"][:60], veri["ne_zaman_metin"]))
    if not sessiz:
        _durtu()
    return gid, None


def _durtu():
    """Uygulamayı sessiz push'la uyandır — kuyrukta iş var. En iyi çaba: push
    gitmezse uygulama zaten öne gelince, SSE'de ve arka plan tazelemesinde çeker."""
    try:
        from ghost import apns
        apns.sessiz_herkese({"ghost": "gorev"})
    except Exception:
        pass


def bekleyenler(limit=BEKLEYEN_TAVAN):
    c = _db()
    rows = c.execute("SELECT id,tur,veri,durum,sonuc,yerel_id,cihaz,ts,biten FROM kokpit_gorevler "
                     "WHERE durum='bekliyor' ORDER BY id LIMIT ?", (limit,)).fetchall()
    c.close()
    return [_satir(r) for r in rows]


def bekleyen_sayisi():
    c = _db()
    n = c.execute("SELECT COUNT(*) FROM kokpit_gorevler WHERE durum='bekliyor'").fetchone()[0]
    c.close()
    return n


def sonuclandir(gid, ok, mesaj="", yerel_id="", cihaz=None):
    """Uygulama görevi uyguladı (ya da uygulayamadı). Dönen: güncellenen görev ya da None.
    Yalnız 'bekliyor' durumundaki görev sonuçlanır — aynı görevin iki kez yazılması
    (iki cihaz, tekrar denemesi) böylece engellenir."""
    c = _db()
    cur = c.execute("UPDATE kokpit_gorevler SET durum=?, sonuc=?, yerel_id=?, cihaz=?, biten=? "
                    "WHERE id=? AND durum='bekliyor'",
                    ("tamam" if ok else "hata", (mesaj or "")[:300], (yerel_id or "")[:80],
                     cihaz, time.strftime("%F %T"), int(gid)))
    c.commit()
    if not cur.rowcount:
        c.close()
        return None
    r = c.execute("SELECT id,tur,veri,durum,sonuc,yerel_id,cihaz,ts,biten FROM kokpit_gorevler "
                  "WHERE id=?", (int(gid),)).fetchone()
    c.close()
    g = _satir(r)
    governance.audit("gorevler", "sonuc", governance.READ,
                     "#%s %s → %s %s" % (gid, g["tur"], g["durum"], (mesaj or "")[:80]))
    return g


def liste(limit=15):
    c = _db()
    rows = c.execute("SELECT id,tur,veri,durum,sonuc,yerel_id,cihaz,ts,biten FROM kokpit_gorevler "
                     "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    return [_satir(r) for r in rows]


def iptal(gid):
    c = _db()
    cur = c.execute("UPDATE kokpit_gorevler SET durum='iptal', biten=? WHERE id=? AND durum='bekliyor'",
                    (time.strftime("%F %T"), int(gid)))
    c.commit()
    c.close()
    return bool(cur.rowcount)


def buda():
    c = _db()
    c.execute("DELETE FROM kokpit_gorevler WHERE durum!='bekliyor' AND ts < ?",
              (time.strftime("%F %T", time.localtime(time.time() - OMUR_GUN * 86400)),))
    c.commit()
    c.close()


_SIMGE = {"bekliyor": "⏳", "tamam": "✅", "hata": "❌", "iptal": "🚫"}


def metin(limit=10):
    g = liste(limit)
    if not g:
        return "📲 Telefona yazılmış görev yok. (Ghost hatırlatıcı/etkinlik kurabilir.)"
    s = ["📲 Telefon görevleri (son %d):" % len(g)]
    for x in g:
        v = x["veri"]
        s.append("%s #%d %s — %s @ %s%s" % (
            _SIMGE.get(x["durum"], "•"), x["id"],
            "hatırlatıcı" if x["tur"] == "hatirlatici" else "etkinlik",
            v.get("ad", "?"), v.get("ne_zaman_metin", "?"),
            ("\n     " + x["sonuc"]) if x["durum"] == "hata" and x["sonuc"] else ""))
    return "\n".join(s)


def haber_ver(g):
    """Sonuç sahibe düşsün — 'gönderdim' ile 'telefona yazıldı' aynı şey değil."""
    v = g["veri"]
    tur = "Hatırlatıcı" if g["tur"] == "hatirlatici" else "Takvim etkinliği"
    if g["durum"] == "tamam":
        metin_ = "📲 %s telefonuna yazıldı: %s — %s" % (tur, v.get("ad", "?"), v.get("ne_zaman_metin", "?"))
    else:
        metin_ = "❌ %s telefona YAZILAMADI: %s — %s" % (tur, v.get("ad", "?"), g.get("sonuc") or "sebep yok")
    try:
        from ghost import notify
        notify.telegram(metin_)      # Telegram + kokpit dökümü + alarmsa push
    except Exception:
        kokpit.outbox_yaz(metin_, "ghost")
    return metin_


# ---------------------------------------------------------------- CLI
def main(argv):
    cmd = argv[0] if argv else "liste"
    if cmd in ("hatirlatici", "hatirlat"):
        if len(argv) < 3:
            print('kullanım: hatirlatici "<başlık>" "<ne zaman>" ["not"]'); return 2
        gid, hata = ekle("hatirlatici", {"ad": argv[1], "ne_zaman": argv[2],
                                         "not": argv[3] if len(argv) > 3 else ""})
    elif cmd == "etkinlik":
        if len(argv) < 3:
            print('kullanım: etkinlik "<başlık>" "<başlangıç>" ["bitiş"] ["yer"]'); return 2
        gid, hata = ekle("etkinlik", {"ad": argv[1], "bas": argv[2],
                                      "bit": argv[3] if len(argv) > 3 else "",
                                      "yer": argv[4] if len(argv) > 4 else ""})
    elif cmd == "iptal" and len(argv) > 1:
        print("iptal edildi" if iptal(argv[1]) else "bulunamadı ya da zaten bitmiş"); return 0
    elif cmd == "json":
        print(json.dumps(liste(30), ensure_ascii=False, indent=1)); return 0
    else:
        print(metin(20)); return 0
    if hata:
        print("HATA:", hata); return 1
    print("kuyruğa girdi: #%d — telefon uygulaması çekince yazılacak (sonucu /gorevler)" % gid)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
