"""Deploy → etki kartı — her sürüm/deploy'dan 24 saat ve 7 gün sonra ölçü kıyası (Faz 1.8, 2026-10-29).

NEDEN (bütünsel denetim 07 Ö9, 08 #3; kaldıraç 2, 2026-08-12): sürüm çıkıyor, site yayınlanıyor, ama
"ne değişti?" sorusunu kimse sayıyla cevaplamıyordu. Kayıt: `surumler` tablosu (ürün, etiket, taban
ölçüleri); selfcheck tick'i 24 sa ve 7 gün dolunca aynı ölçüleri yeniden okur, farkı tabloya çevirir,
Masa'da ürünün kartına yayınlar (dossier/etki/<ürün>-<etiket>.md). Ölçü kaynağı `ops/biz_state.json`
(sabah dijesti günlük yazar; ASC/D1/PostHog/Sentry). sahibe mesaj gitmez; tablo Masa'da durur.

Tetik: (a) sohbette deploy/sürüm sonrası REFLEKS `python3 -m ghost.etki kaydet <urun> "<etiket>" ["not"]`
(b) biz_digest ASC sürüm durumu READY_FOR_SALE'e dönünce kendiliğinden (`otomatik`). Ürünler: appa,
appb, site:<ad> (siteler yalnız PostHog toplamıyla, kaba)."""
import json
import os
import sqlite3
import sys
import time

from ghost import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS surumler(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  ts_epoch REAL NOT NULL,
  urun TEXT NOT NULL,
  etiket TEXT NOT NULL,
  notu TEXT,
  taban TEXT NOT NULL,
  k24 TEXT, k7 TEXT,
  durum TEXT NOT NULL DEFAULT 'bekliyor'
);
"""
OLCULER = {
    "appa": ("asc_state", "asc_builds", "asc_testers", "d1_users", "d1_paid", "ph_events_24h", "ph_users_24h", "sentry_open"),
    "appb": ("appb_state", "appb_reviews", "appb_puan7", "appb_indirme_gun", "appb_iap_gun", "appb_ciro_gun", "sentry_open"),
}
SITE_OLCULER = ("ph_events_24h", "ph_users_24h")
KART = {"appa": "AppA", "appb": "App B"}
ETIKETLER = {"asc_state": "sürüm durumu", "asc_builds": "build", "asc_testers": "tester", "d1_users": "kullanıcı",
             "d1_paid": "ücretli", "ph_events_24h": "PostHog olay/24s", "ph_users_24h": "PostHog kullanıcı/24s",
             "sentry_open": "açık hata", "appb_state": "sürüm durumu", "appb_reviews": "yorum", "appb_puan7": "7g puan",
             "appb_indirme_gun": "günlük indirme", "appb_iap_gun": "günlük IAP", "appb_ciro_gun": "günlük ciro"}
K24_SN, K7_SN = 24 * 3600, 7 * 86400


def _conn():
    c = sqlite3.connect(config.DB_PATH, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_SCHEMA)
    return c


def olculer_oku(urun, okuyucu=None):
    """Ürünün ölçü anlık görüntüsü: {anahtar: değer}. okuyucu() → biz_state metrics (testler için)."""
    if okuyucu is None:
        def okuyucu():
            try:
                from ghost import durum     # Faz 1.9: biz_state durum tablosunda
                d = durum.tasi("biz_state", os.path.join(config.OPS, "biz_state.json"), {}) or {}
                return d.get("metrics", {}) or {}
            except Exception:
                return {}
    m = okuyucu()
    anahtarlar = OLCULER.get(urun) or (SITE_OLCULER if urun.startswith("site:") else ())
    return {k: m[k] for k in anahtarlar if k in m}


def kaydet(urun, etiket, notu="", okuyucu=None, simdi=None):
    """Sürüm/deploy kaydı; aynı ürün+etiket açıksa yeniden eklemez. id döner."""
    simdi = simdi or time.time()
    urun = (urun or "").strip().lower()
    etiket = (etiket or "").strip()
    if not urun or not etiket:
        raise ValueError("ürün ve etiket gerekli")
    c = _conn()
    try:
        r = c.execute("SELECT id FROM surumler WHERE urun=? AND etiket=?", (urun, etiket)).fetchone()
        if r:
            return r[0]
        taban = olculer_oku(urun, okuyucu)
        cur = c.execute("INSERT INTO surumler(ts,ts_epoch,urun,etiket,notu,taban) VALUES(?,?,?,?,?,?)",
                        (time.strftime("%F %T", time.localtime(simdi)), simdi, urun, etiket, notu or "",
                         json.dumps(taban, ensure_ascii=False)))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def otomatik(onceki, simdi_m, simdi=None):
    """biz_digest: ASC sürüm durumu READY_FOR_SALE'e dönen ürün için kayıt (etiket = tarih). Açılan id'ler."""
    ids = []
    for urun, anahtar in (("appa", "asc_state"), ("appb", "appb_state")):
        yeni, eski = simdi_m.get(anahtar), (onceki or {}).get(anahtar)
        if yeni == "READY_FOR_SALE" and eski and eski != yeni:
            try:
                ids.append(kaydet(urun, "mağaza %s" % time.strftime("%F", time.localtime(simdi or time.time())),
                                  "ASC: %s → READY_FOR_SALE" % eski, okuyucu=lambda: simdi_m, simdi=simdi))
            except Exception:
                continue
    return ids


def _fark(taban, simdi_m):
    satirlar = []
    for k, v0 in taban.items():
        v1 = simdi_m.get(k)
        ad = ETIKETLER.get(k, k)
        if v1 is None:
            satirlar.append((ad, v0, "—", "ölçülemedi"))
            continue
        try:
            d = float(v1) - float(v0)
            isaret = ("+%g" % d) if d > 0 else ("%g" % d) if d < 0 else "0"
        except (TypeError, ValueError):
            isaret = "değişti" if v1 != v0 else "aynı"
        satirlar.append((ad, v0, v1, isaret))
    return satirlar


def rapor(kayit):
    """Markdown tablo: taban → 24 sa → 7 gün."""
    taban = json.loads(kayit["taban"] or "{}")
    k24 = json.loads(kayit["k24"] or "{}") if kayit.get("k24") else None
    k7 = json.loads(kayit["k7"] or "{}") if kayit.get("k7") else None
    out = ["# Etki — %s · %s" % (KART.get(kayit["urun"], kayit["urun"]), kayit["etiket"]), "",
           "Kayıt: %s%s" % (kayit["ts"], (" — " + kayit["notu"]) if kayit.get("notu") else ""), "",
           "| Ölçü | Taban | 24 sa | Δ24 | 7 gün | Δ7 |", "|---|---|---|---|---|---|"]
    if not taban:
        out.append("| (taban ölçüsü yok — dijest o gün ölçmemişti) | | | | | |")
    f24 = {a: (v1, d) for a, _v0, v1, d in _fark(taban, k24)} if k24 else {}
    f7 = {a: (v1, d) for a, _v0, v1, d in _fark(taban, k7)} if k7 else {}
    for k, v0 in taban.items():
        ad = ETIKETLER.get(k, k)
        a24 = f24.get(ad, ("…", ""))
        a7 = f7.get(ad, ("…", ""))
        out.append("| %s | %s | %s | %s | %s | %s |" % (ad, v0, a24[0], a24[1], a7[0], a7[1]))
    out.append("")
    out.append("Kaynak: ops/biz_state.json (sabah dijesti). Δ = kayıt anına göre fark; '…' henüz dolmadı.")
    return "\n".join(out)


def _yayinla(kayit, metin):
    dizin = os.path.join(config.BASE, "dossier", "etki")
    os.makedirs(dizin, exist_ok=True)
    guvenli = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in "%s-%s" % (kayit["urun"], kayit["etiket"]))
    yol = os.path.join(dizin, guvenli + ".md")
    with open(yol, "w", encoding="utf-8") as f:
        f.write(metin)
    try:
        from ghost import masa
        kart = KART.get(kayit["urun"], "Raporlar")
        masa.yayinla(yol, kart, "Etki · %s" % kayit["etiket"])
    except Exception:
        pass
    return yol


def liste():
    c = _conn()
    try:
        return [dict(zip(("id", "ts", "ts_epoch", "urun", "etiket", "notu", "taban", "k24", "k7", "durum"), r))
                for r in c.execute("SELECT id,ts,ts_epoch,urun,etiket,notu,taban,k24,k7,durum FROM surumler ORDER BY id DESC")]
    finally:
        c.close()


def tick(simdi=None, okuyucu=None, yayinla=None):
    """selfcheck: vadesi gelen 24 sa / 7 gün ölçümlerini doldur, raporu Masa'ya yayınla. Dokunulan id'ler."""
    simdi = simdi or time.time()
    dokunulan = []
    yayinla = yayinla or _yayinla
    for k in liste():
        if k["durum"] == "tamam":
            continue
        yas = simdi - float(k["ts_epoch"])
        degisti = False
        if not k["k24"] and yas >= K24_SN:
            k["k24"] = json.dumps(olculer_oku(k["urun"], okuyucu), ensure_ascii=False)
            degisti = True
        if not k["k7"] and yas >= K7_SN:
            k["k7"] = json.dumps(olculer_oku(k["urun"], okuyucu), ensure_ascii=False)
            k["durum"] = "tamam"
            degisti = True
        if not degisti:
            continue
        c = _conn()
        try:
            c.execute("UPDATE surumler SET k24=?, k7=?, durum=? WHERE id=?", (k["k24"], k["k7"], k["durum"], k["id"]))
            c.commit()
        finally:
            c.close()
        try:
            yayinla(k, rapor(k))
        except Exception:
            pass
        dokunulan.append(k["id"])
    return dokunulan


def _main(argv):
    if not argv or argv[0] == "liste":
        for k in liste():
            print("#%d %s %s · %s [%s]%s" % (k["id"], k["ts"][:16], k["urun"], k["etiket"], k["durum"],
                                             " 24s✓" if k["k24"] else "", ) + (" 7g✓" if k["k7"] else ""))
        return 0
    if argv[0] == "kaydet":
        if len(argv) < 3:
            print('kullanım: kaydet <appa|appb|site:ad> "<etiket>" ["not"]')
            return 2
        print("sürüm #%d" % kaydet(argv[1], argv[2], argv[3] if len(argv) > 3 else ""))
        return 0
    if argv[0] == "rapor":
        k = next((x for x in liste() if x["id"] == int(argv[1])), None)
        print(rapor(k) if k else "yok")
        return 0
    if argv[0] == "tick":
        print("dokunulan:", tick())
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
