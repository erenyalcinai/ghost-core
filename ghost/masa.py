"""Masa — Ghost ile sahibin ORTAK, KALICI içerik alanı (Kokpit "Masa" sekmesi).

KARAR (Sahip, 2026-10-04): Sahne "fikir olarak güzel ama gereksiz kullanılıyor; orayı App B gibi
klasör/kart alanına, ikimizin ortak dosya ve benim de anlayabileceğim içerik alanına çevirelim.
Sistemi bozmasın, güçsüzleştirmesin; ben dosyalara müdahale edersem bir şeyi bozacak yapıda olmasın."

MİMARİ İLKE — Masa bir YAYIN KATMANIDIR, çalışma ağacı değil:
  * Ghost'un kanonik dosyaları (dossier/, playbooks/, kod) Masa'da DURMAZ; Ghost oraya ANLIK
    GÖRÜNTÜ yayınlar (`yayinla`) ve kaynağını işaret eder (`kaynak`). Masa'daki hiçbir kayıt
    otomasyon tarafından gerçek olarak OKUNMAZ, içe alınmaz, çalıştırılmaz. Sahip Masa'da ne
    yaparsa yapsın (silse, bozsa, yanlış klasöre taşısa) sistem etkilenmez — en kötü ihtimalle
    Ghost aynı dosyayı yeniden yayınlar.
  * sahibin eklediği şeyler (not, fotoğraf, belge, klasör) Masa'da yaşar; Ghost onları GİRDİ
    olarak görür (sahibin paylaşımı gibi): `yeni` ile okur, sohbet bağlamına tek satır düşer.
  * Depo: ghost.db `masa_kart` (ağaç: ebeveyn) + `ops/masa_dosya/` (kopya; budanmaz, Masa
    kalıcıdır). Her değişiklik `seq` artırır; uygulama uzun yoklamayla yeniler.

App B modeli: kart = kap. Türler: klasor · not (markdown) · rapor (Ghost'un yayınladığı
markdown; uygulama MarkdownMetin ile çizer) · sahne (Sahne bileşenleri JSON; BilesenView çizer) ·
gorsel · dosya · baglanti. Kök = Masa. `durum`: masa | dolap (biten/bekleyen; silme değil depo).

İZİN KURALLARI (yapı bozulmaz):
  sahip  : ekler (her tür); kendi kayıtlarını düzenler/siler; HER kaydı adlandırır, taşır, dolaba
          kaldırır / geri alır. Ghost kaydını "sil" → DOLAP (içerik kaybolmaz). sabit kök
          klasörler (Ghost'un açtığı AppA, App B…) silinmez, dolaba kalkmaz; adı değişebilir.
  ghost : her şeyi yapar; sahibin kaydının içeriğini düzenlemek / silmek için `zorla=True`
          (CLI `--sahip-istedi`) — Sahip söylemeden sahibin notuna dokunma.

CLI (sohbetten):
    python3 -m ghost.masa agac                          # tüm ağaç (dolap hariç)
    python3 -m ghost.masa oku <id>                      # tek kayıt, tam içerik
    python3 -m ghost.masa yayinla <dosya> <kart> [ad]   # repo dosyasını karta yayınla (aynı kaynak → günceller)
    python3 -m ghost.masa not <kart> "<markdown>"       # Ghost notu
    python3 -m ghost.masa dosya <yol> <kart> [ad]       # görsel/belge kopyala
    python3 -m ghost.masa ekle klasor <kart|kok> <ad>   # alt klasör
    python3 -m ghost.masa tasi <id> <kart|kok> · dolap <id> · geri <id> · adlandir <id> <ad>
    python3 -m ghost.masa sil <id> [--sahip-istedi]
    python3 -m ghost.masa yeni [--okundu]               # sahibin Ghost'un henüz görmediği değişiklikleri
    python3 -m ghost.masa hazirla                       # kök klasörleri aç (bir kez; tekrarı zararsız)
    python3 -m ghost.masa durum
"""
import json
import os
import shutil
import sqlite3
import sys
import time

from ghost import config, sahip

TURLER = ("klasor", "not", "rapor", "sahne", "gorsel", "dosya", "baglanti")
YAZARLAR = ("ghost", "sahip")
AD_TAVAN = 120
ICERIK_TAVAN = 400_000          # markdown/JSON karakter
DOSYA_DIZIN = "masa_dosya"      # ops/ altında; kaynak KOPYALANIR, budanmaz
DOSYA_UYARI = 500 * 1024 * 1024
ONIZLEME = 160
KOK_KLASORLER = sahip.masa_kok()   # ops/sahip.json "masa_kok" (açık çekirdek: koddan çıktı)

SCHEMA = """
CREATE TABLE IF NOT EXISTS masa_kart(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ebeveyn INTEGER,
    tur TEXT NOT NULL,
    ad TEXT NOT NULL,
    icerik TEXT,
    dosya TEXT, mime TEXT, boyut INTEGER,
    kaynak TEXT,
    yazar TEXT NOT NULL,
    sira INTEGER NOT NULL DEFAULT 0,
    durum TEXT NOT NULL DEFAULT 'masa',
    sabit INTEGER NOT NULL DEFAULT 0,
    ts TEXT, guncelleme TEXT, guncelleyen TEXT,
    gorulme TEXT);
CREATE INDEX IF NOT EXISTS masa_kart_ebeveyn ON masa_kart(ebeveyn, durum);
CREATE TABLE IF NOT EXISTS masa_meta(k TEXT PRIMARY KEY, v TEXT);
"""


class MasaHata(Exception):
    """Kullanıcıya gösterilebilir hata (400). İzin hatası için `yetki=True`."""
    def __init__(self, mesaj, yetki=False):
        super().__init__(mesaj)
        self.yetki = yetki


def _db():
    c = sqlite3.connect(config.DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def _now():
    return time.strftime("%F %T")


def _dir():
    p = os.path.join(config.OPS, DOSYA_DIZIN)
    os.makedirs(p, exist_ok=True)
    return p


def _seq_artir(c):
    c.execute("INSERT INTO masa_meta(k, v) VALUES('seq', '1') "
              "ON CONFLICT(k) DO UPDATE SET v = CAST(CAST(v AS INTEGER) + 1 AS TEXT)")


def seq():
    c = _db()
    try:
        r = c.execute("SELECT v FROM masa_meta WHERE k='seq'").fetchone()
        return int(r[0]) if r else 0
    finally:
        c.close()


def _meta(c, k, v=None):
    if v is None:
        r = c.execute("SELECT v FROM masa_meta WHERE k=?", (k,)).fetchone()
        return r[0] if r else None
    c.execute("INSERT INTO masa_meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


def _kayit(r, icerik=True):
    if r is None:
        return None
    d = dict(r)
    d["sabit"] = bool(d.get("sabit"))
    if not icerik:
        ic = d.pop("icerik", None) or ""
        d["onizleme"] = "" if d["tur"] == "sahne" else ic.strip().replace("\n", " ")[:ONIZLEME]
    d.pop("dosya", None)      # sunucu yolu istemciye ÇIKMAZ; dosya /v1/masa/<id>/dosya ile gelir
    return d


# ---------------------------------------------------------------- okuma
def oku(kid, icerik=True):
    c = _db()
    try:
        return _kayit(c.execute("SELECT * FROM masa_kart WHERE id=?", (int(kid),)).fetchone(), icerik)
    finally:
        c.close()


def dosya_yol(kid):
    """(yol, mime, ad) ya da None — API /v1/masa/<id>/dosya."""
    c = _db()
    try:
        r = c.execute("SELECT dosya, mime, ad FROM masa_kart WHERE id=?", (int(kid),)).fetchone()
    finally:
        c.close()
    if not r or not r["dosya"] or not os.path.isfile(r["dosya"]):
        return None
    return r["dosya"], r["mime"] or "application/octet-stream", r["ad"]


def cocuklar(ebeveyn=None, durum="masa"):
    c = _db()
    try:
        if ebeveyn is None:
            rows = c.execute("SELECT * FROM masa_kart WHERE ebeveyn IS NULL AND durum=? ORDER BY sira, id",
                             (durum,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM masa_kart WHERE ebeveyn=? AND durum=? ORDER BY sira, id",
                             (int(ebeveyn), durum)).fetchall()
        return [_kayit(r, icerik=False) for r in rows]
    finally:
        c.close()


def agac(durum="masa"):
    """Düz liste (içerik yerine önizleme) + her klasör için çocuk sayısı/son güncelleme.
    Uygulama tek istekte alır; dolap ayrı (durum='dolap')."""
    c = _db()
    try:
        rows = c.execute("SELECT * FROM masa_kart WHERE durum=? ORDER BY ebeveyn, sira, id", (durum,)).fetchall()
        out = [_kayit(r, icerik=False) for r in rows]
        sayac, son = {}, {}
        for k in out:
            e = k["ebeveyn"]
            sayac[e] = sayac.get(e, 0) + 1
            g = k.get("guncelleme") or k.get("ts") or ""
            if g > son.get(e, ""):
                son[e] = g
        for k in out:
            if k["tur"] == "klasor":
                k["cocuk_sayisi"] = sayac.get(k["id"], 0)
                k["son_icerik"] = son.get(k["id"], "")
        return out
    finally:
        c.close()


def yol(kid):
    """Kök → kayıt kırıntısı [{id, ad}]."""
    c = _db()
    try:
        out, cur, guard = [], int(kid), 0
        while cur is not None and guard < 32:
            r = c.execute("SELECT id, ad, ebeveyn FROM masa_kart WHERE id=?", (cur,)).fetchone()
            if not r:
                break
            out.insert(0, {"id": r["id"], "ad": r["ad"]})
            cur = r["ebeveyn"]
            guard += 1
        return out
    finally:
        c.close()


def bekle(since, sn=0):
    son = time.time() + max(0, min(float(sn or 0), 60))
    while True:
        s = seq()
        if s != int(since) or time.time() >= son:
            return s
        time.sleep(0.5)


# ---------------------------------------------------------------- yardımcılar
def _klasor_dogrula(c, ebeveyn):
    if ebeveyn is None:
        return None
    r = c.execute("SELECT id, tur, durum FROM masa_kart WHERE id=?", (int(ebeveyn),)).fetchone()
    if not r:
        raise MasaHata("hedef kart yok (#%s)" % ebeveyn)
    if r["tur"] != "klasor":
        raise MasaHata("hedef bir klasör değil (#%s %s)" % (ebeveyn, r["tur"]))
    return int(r["id"])


def _sonraki_sira(c, ebeveyn):
    if ebeveyn is None:
        r = c.execute("SELECT MAX(sira) FROM masa_kart WHERE ebeveyn IS NULL").fetchone()
    else:
        r = c.execute("SELECT MAX(sira) FROM masa_kart WHERE ebeveyn=?", (ebeveyn,)).fetchone()
    return int(r[0] or 0) + 1


def klasor_bul(ad_veya_id, olustur=False, yazar="ghost"):
    """'AppA' | '12' | 'kok' → klasör id (None = kök). Kök klasör adı büyük/küçük harfsiz
    eşleşir; yoksa ve olustur=True ise Ghost açar."""
    s = str(ad_veya_id or "").strip()
    if s.lower() in ("", "kok", "kök", "masa", "/"):
        return None
    if s.isdigit():
        c = _db()
        try:
            r = c.execute("SELECT id FROM masa_kart WHERE id=? AND tur='klasor'", (int(s),)).fetchone()
        finally:
            c.close()
        if not r:
            raise MasaHata("klasör yok (#%s)" % s)
        return int(r["id"])
    c = _db()
    try:
        r = c.execute("SELECT id FROM masa_kart WHERE tur='klasor' AND lower(ad)=lower(?) "
                      "ORDER BY (ebeveyn IS NULL) DESC, id LIMIT 1", (s,)).fetchone()
    finally:
        c.close()
    if r:
        return int(r["id"])
    if not olustur:
        raise MasaHata("klasör yok: %r (ekle klasor kok \"%s\" ile aç)" % (s, s))
    return kart_ekle("klasor", s, None, yazar=yazar)


def _ad_temizle(ad, varsayilan="Adsız"):
    a = " ".join(str(ad or "").split())[:AD_TAVAN]
    return a or varsayilan


# ---------------------------------------------------------------- yazma
def kart_ekle(tur, ad, ebeveyn=None, icerik=None, yazar="ghost", kaynak=None,
              dosya=None, mime=None, boyut=None, sabit=False, c=None):
    if tur not in TURLER:
        raise MasaHata("bilinmeyen tür %r" % tur)
    if yazar not in YAZARLAR:
        raise MasaHata("yazar ghost|sahip")
    if icerik is not None and len(icerik) > ICERIK_TAVAN:
        raise MasaHata("içerik çok büyük (>%d karakter)" % ICERIK_TAVAN)
    if tur in ("not", "rapor", "baglanti") and not (icerik or "").strip():
        raise MasaHata("%s için içerik boş" % tur)
    kendi = c is None
    c = c or _db()
    try:
        e = _klasor_dogrula(c, ebeveyn)
        now = _now()
        cur = c.execute(
            "INSERT INTO masa_kart(ebeveyn, tur, ad, icerik, dosya, mime, boyut, kaynak, yazar, sira, durum, "
            "sabit, ts, guncelleme, guncelleyen, gorulme) VALUES(?,?,?,?,?,?,?,?,?,?,'masa',?,?,?,?,?)",
            (e, tur, _ad_temizle(ad, {"not": "Not", "klasor": "Klasör"}.get(tur, "Adsız")), icerik, dosya,
             mime, boyut, kaynak, yazar, _sonraki_sira(c, e), 1 if sabit else 0, now, now, yazar,
             now if yazar == "ghost" else None))
        _seq_artir(c)
        if kendi:
            c.commit()
        return cur.lastrowid
    finally:
        if kendi:
            c.close()


def dosya_ekle(ad, veri, ebeveyn=None, yazar="sahip", kaynak=None, mime=None):
    """Ham bayt → ops/masa_dosya/ kopyası + gorsel/dosya kaydı."""
    from ghost import kokpit
    if not veri:
        raise MasaHata("dosya boş")
    ad = _ad_temizle(ad, "dosya")
    guvenli = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in ad)[-80:] or "dosya"
    hedef = os.path.join(_dir(), "%s-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), os.urandom(3).hex(), guvenli))
    with open(hedef, "wb") as f:
        f.write(veri)
    tur = "gorsel" if kokpit.medya_tur(ad) == "gorsel" else "dosya"
    return kart_ekle(tur, ad, ebeveyn, yazar=yazar, kaynak=kaynak, dosya=hedef,
                     mime=mime or kokpit.medya_mime(ad), boyut=len(veri))


def dosya_kopyala(yol_, ebeveyn=None, ad=None, yazar="ghost", kaynak=None):
    if not os.path.isfile(yol_):
        raise MasaHata("dosya yok: %s" % yol_)
    with open(yol_, "rb") as f:
        veri = f.read()
    return dosya_ekle(ad or os.path.basename(yol_), veri, ebeveyn, yazar=yazar,
                      kaynak=kaynak or _gorece(yol_))


def _gorece(p):
    p = os.path.abspath(p)
    base = os.path.abspath(config.BASE)
    return os.path.relpath(p, base) if p.startswith(base + os.sep) else p


def yayinla(yol_, kart, ad=None):
    """Ghost bir repo dosyasını karta yayınlar — ANLIK GÖRÜNTÜ. Aynı klasörde aynı `kaynak`lı
    Ghost kaydı varsa YENİLENİR (çift kopya olmaz); Sahip o kaydı taşımışsa yeni yerinde yenilenir.
    .md/.txt → rapor (markdown); diğerleri → dosya/görsel kopyası. → (id, 'yeni'|'guncel')"""
    if not os.path.isfile(yol_):
        raise MasaHata("dosya yok: %s" % yol_)
    e = klasor_bul(kart, olustur=True)
    kaynak = _gorece(yol_)
    c = _db()
    try:
        r = c.execute("SELECT id, ebeveyn FROM masa_kart WHERE kaynak=? AND yazar='ghost' AND tur IN "
                      "('rapor','dosya','gorsel') ORDER BY (ebeveyn IS ?) DESC, id DESC LIMIT 1",
                      (kaynak, e)).fetchone()
    finally:
        c.close()
    metin_mi = yol_.lower().endswith((".md", ".txt", ".markdown"))
    if not ad:
        ad = _baslik_cikar(yol_) if metin_mi else os.path.basename(yol_)
    if metin_mi:
        with open(yol_, encoding="utf-8", errors="replace") as f:
            icerik = f.read()
        if len(icerik) > ICERIK_TAVAN:
            icerik = icerik[:ICERIK_TAVAN] + "\n\n…(kesildi; tamamı %s)" % kaynak
        if r:
            c = _db()
            try:
                c.execute("UPDATE masa_kart SET icerik=?, ad=?, guncelleme=?, guncelleyen='ghost', durum='masa' "
                          "WHERE id=?", (icerik, _ad_temizle(ad), _now(), r["id"]))
                _seq_artir(c)
                c.commit()
            finally:
                c.close()
            return int(r["id"]), "guncel"
        return kart_ekle("rapor", ad, e, icerik=icerik, yazar="ghost", kaynak=kaynak), "yeni"
    if r:   # ikili dosya: eskisini sil, yenisini aynı yere koy
        sil(r["id"], "ghost")
        kid = dosya_kopyala(yol_, r["ebeveyn"], ad=ad, kaynak=kaynak)
        return kid, "guncel"
    return dosya_kopyala(yol_, e, ad=ad, kaynak=kaynak), "yeni"


def _baslik_cikar(yol_):
    try:
        with open(yol_, encoding="utf-8", errors="replace") as f:
            for _ in range(20):
                s = f.readline()
                if not s:
                    break
                if s.startswith("#"):
                    return s.lstrip("#").strip()[:AD_TAVAN]
    except OSError:
        pass
    return os.path.splitext(os.path.basename(yol_))[0]


def sahne_kaydet(bilesenler, baslik, kart, kaynak=None):
    """Sahne çıktısını karta kalıcı kayıt yapar (uygulama BilesenView ile çizer)."""
    e = klasor_bul(kart, olustur=True)
    return kart_ekle("sahne", baslik or "Sahne", e, icerik=json.dumps(bilesenler, ensure_ascii=False),
                     yazar="ghost", kaynak=kaynak)


def not_ekle(kart, metin, yazar="ghost", ad=None):
    e = kart if (kart is None or isinstance(kart, int)) else klasor_bul(kart, olustur=(yazar == "ghost"))
    ad = ad or (metin.strip().splitlines()[0].lstrip("#").strip()[:60] if metin.strip() else "Not")
    return kart_ekle("not", ad, e, icerik=metin, yazar=yazar)


# ---------------------------------------------------------------- izin + düzenleme
def _al(c, kid):
    r = c.execute("SELECT * FROM masa_kart WHERE id=?", (int(kid),)).fetchone()
    if not r:
        raise MasaHata("kayıt yok (#%s)" % kid)
    return r


def _izin(r, yazar, eylem, zorla=False):
    """Kuralların tek yeri. Hata fırlatır ya da (gerekirse değişmiş) eylemi döndürür."""
    if yazar not in YAZARLAR:
        raise MasaHata("yazar ghost|sahip")
    sahibi = r["yazar"] == yazar
    if yazar == "sahip":
        if eylem == "duzenle" and not sahibi:
            raise MasaHata("Ghost'un yayını düzenlenmez — yanına not ekle ya da Ghost'a ilet", yetki=True)
        if eylem in ("dolap", "sil") and r["sabit"]:
            raise MasaHata("kök klasör dolaba kalkmaz/silinmez (adını değiştirebilirsin)", yetki=True)
        if eylem == "tasi" and r["sabit"]:
            raise MasaHata("kök klasör taşınmaz", yetki=True)
        if eylem == "sil" and not sahibi:
            return "dolap"      # Ghost kaydını Sahip silemez; dolaba kalkar, içerik durur
        return eylem
    # ghost
    if eylem in ("duzenle", "sil") and not sahibi and not zorla:
        raise MasaHata("sahibin kaydı: düzenlemek/silmek için Sahip istemiş olmalı (--sahip-istedi)", yetki=True)
    return eylem


def _dokun(c, kid, yazar):
    c.execute("UPDATE masa_kart SET guncelleme=?, guncelleyen=?, gorulme=CASE WHEN ?='ghost' THEN ? ELSE NULL END "
              "WHERE id=?", (_now(), yazar, yazar, _now(), int(kid)))
    _seq_artir(c)


def duzenle(kid, yazar, icerik=None, ad=None, zorla=False):
    c = _db()
    try:
        r = _al(c, kid)
        if icerik is not None:
            _izin(r, yazar, "duzenle", zorla)
            if r["tur"] not in ("not", "rapor", "baglanti"):
                raise MasaHata("%s içeriği düzenlenmez" % r["tur"])
            if len(icerik) > ICERIK_TAVAN:
                raise MasaHata("içerik çok büyük")
            if not icerik.strip():
                raise MasaHata("içerik boş")
            c.execute("UPDATE masa_kart SET icerik=? WHERE id=?", (icerik, r["id"]))
        if ad is not None:
            _izin(r, yazar, "adlandir", zorla)
            c.execute("UPDATE masa_kart SET ad=? WHERE id=?", (_ad_temizle(ad, r["ad"]), r["id"]))
        _dokun(c, r["id"], yazar)
        c.commit()
        return True
    finally:
        c.close()


def _soyundan_mi(c, aday, kok):
    """aday, kok'un alt ağacında mı (ya da kendisi mi)?"""
    cur, guard = aday, 0
    while cur is not None and guard < 64:
        if int(cur) == int(kok):
            return True
        r = c.execute("SELECT ebeveyn FROM masa_kart WHERE id=?", (cur,)).fetchone()
        cur = r["ebeveyn"] if r else None
        guard += 1
    return False


def tasi(kid, yeni_ebeveyn, yazar, sira=None):
    """Yeniden ebeveynle (kök = None). Döngü yasak; hedef klasör olmalı."""
    c = _db()
    try:
        r = _al(c, kid)
        _izin(r, yazar, "tasi")
        e = _klasor_dogrula(c, yeni_ebeveyn)
        if e is not None and _soyundan_mi(c, e, r["id"]):
            raise MasaHata("bir klasör kendi içine taşınmaz")
        s = int(sira) if sira is not None else _sonraki_sira(c, e)
        c.execute("UPDATE masa_kart SET ebeveyn=?, sira=? WHERE id=?", (e, s, r["id"]))
        _dokun(c, r["id"], yazar)
        c.commit()
        return True
    finally:
        c.close()


def sirala(kid, sira, yazar):
    c = _db()
    try:
        r = _al(c, kid)
        c.execute("UPDATE masa_kart SET sira=? WHERE id=?", (int(sira), r["id"]))
        _dokun(c, r["id"], yazar)
        c.commit()
        return True
    finally:
        c.close()


def _durum(kid, yazar, durum):
    c = _db()
    try:
        r = _al(c, kid)
        _izin(r, yazar, "dolap" if durum == "dolap" else "geri")
        c.execute("UPDATE masa_kart SET durum=? WHERE id=?", (durum, r["id"]))
        _dokun(c, r["id"], yazar)
        c.commit()
        return True
    finally:
        c.close()


def dolap(kid, yazar):
    return _durum(kid, yazar, "dolap")


def geri(kid, yazar):
    return _durum(kid, yazar, "masa")


def _alt_agac(c, kid):
    out, kuyruk = [], [int(kid)]
    while kuyruk:
        cur = kuyruk.pop()
        out.append(cur)
        kuyruk += [r["id"] for r in c.execute("SELECT id FROM masa_kart WHERE ebeveyn=?", (cur,))]
    return out


def sil(kid, yazar, zorla=False):
    """Kalıcı silme. Sahip: yalnız kendi kaydı (klasörse alt ağacın tamamı kendi olmalı), Ghost
    kaydı → dolap. Ghost: kendi kaydı serbest; sahibin kaydı için zorla. → 'silindi'|'dolap'"""
    c = _db()
    try:
        r = _al(c, kid)
        eylem = _izin(r, yazar, "sil", zorla)
        if eylem == "dolap":
            c.execute("UPDATE masa_kart SET durum='dolap' WHERE id=?", (r["id"],))
            _dokun(c, r["id"], yazar)
            c.commit()
            return "dolap"
        ids = _alt_agac(c, r["id"])
        if not zorla:
            baskasi = c.execute("SELECT COUNT(*) FROM masa_kart WHERE id IN (%s) AND yazar!=?" %
                                ",".join("?" * len(ids)), ids + [yazar]).fetchone()[0]
            if baskasi:
                raise MasaHata("klasörde %s'in kayıtları var; önce onları taşı ya da dolaba kaldır"
                               % ("Ghost" if yazar == "sahip" else "Sahip"), yetki=True)
        for d in c.execute("SELECT dosya FROM masa_kart WHERE id IN (%s) AND dosya IS NOT NULL" %
                           ",".join("?" * len(ids)), ids).fetchall():
            try:
                if d["dosya"] and os.path.isfile(d["dosya"]) and os.path.dirname(d["dosya"]) == _dir():
                    os.remove(d["dosya"])
            except OSError:
                pass
        c.execute("DELETE FROM masa_kart WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)
        _seq_artir(c)
        c.commit()
        return "silindi"
    finally:
        c.close()


# ---------------------------------------------------------------- Ghost'a akış
def yeni(okundu=False):
    """sahibin Ghost'un henüz görmediği kayıtları/değişiklikleri (gorulme NULL, guncelleyen sahip)."""
    c = _db()
    try:
        rows = c.execute("SELECT * FROM masa_kart WHERE guncelleyen='sahip' AND gorulme IS NULL "
                         "ORDER BY guncelleme, id").fetchall()
        out = [_kayit(r) for r in rows]
        for k in out:
            k["yol"] = " › ".join(x["ad"] for x in yol(k["id"])[:-1]) or "Masa"
        if okundu and rows:
            c.execute("UPDATE masa_kart SET gorulme=? WHERE guncelleyen='sahip' AND gorulme IS NULL", (_now(),))
            c.commit()
        return out
    finally:
        c.close()


def baglam_satiri():
    """Sohbet turuna iliştirilen tek satır (ghost/duyular.satir gibi). Yoksa boş."""
    try:
        y = yeni()
    except Exception:
        return ""
    if not y:
        return ""
    ornek = "; ".join("%s › %s (%s)" % (k["yol"], k["ad"], k["tur"]) for k in y[:3])
    return ("[MASA: sahibin Masa'da senin henüz bakmadığın %d değişikliği var: %s%s. "
            "İlgiliyse `python3 -m ghost.masa yeni --okundu` ile oku; ilgisizse yok say.]\n"
            % (len(y), ornek, "…" if len(y) > 3 else ""))


def ilet(kid, cihaz=None):
    """Sahip 'Ghost'a ilet' dedi: kayıt sohbete Sahip mesajı olarak düşer (paylaşım rayıyla aynı)."""
    from ghost import kokpit
    k = oku(kid)
    if not k:
        raise MasaHata("kayıt yok")
    kirinti = " › ".join(x["ad"] for x in yol(kid))
    if k["tur"] in ("gorsel", "dosya"):
        d = dosya_yol(kid)
        if not d:
            raise MasaHata("dosya bulunamadı")
        metin = "[Masa › %s] %s" % (kirinti, k["ad"])
        kokpit.outbox_yaz(metin, "sahip", medya=kokpit.medya_sakla(d[0], ad=k["ad"], kopyala=False))
        kokpit.mesaj_kuyrukla("Masa'daki şu kaydı iletiyorum: %s\n%s" % (kirinti, d[0]), cihaz,
                              yol=d[0], tur=k["tur"], ad=k["ad"], not_="Masa › " + kirinti)
    else:
        govde = (k.get("icerik") or "")[:6000]
        if k["tur"] == "sahne":
            govde = "(Sahne kaydı; `python3 -m ghost.masa oku %d`)" % k["id"]
        metin = "[Masa › %s]\n%s" % (kirinti, govde)
        kokpit.outbox_yaz(metin, "sahip")
        kokpit.mesaj_kuyrukla(metin, cihaz)
    c = _db()
    try:
        c.execute("UPDATE masa_kart SET gorulme=? WHERE id=?", (_now(), int(kid)))
        c.commit()
    finally:
        c.close()
    return metin


# ---------------------------------------------------------------- kurulum + durum
def hazirla():
    """Kök klasörleri aç (sabit) ve eski Share Extension paylaşımlarını bir kez içe al."""
    acilan = []
    c = _db()
    try:
        for ad in KOK_KLASORLER:
            r = c.execute("SELECT id FROM masa_kart WHERE tur='klasor' AND ebeveyn IS NULL AND lower(ad)=lower(?)",
                          (ad,)).fetchone()
            if not r:
                kart_ekle("klasor", ad, None, yazar="ghost", sabit=True, c=c)
                acilan.append(ad)
        c.commit()
        yapildi = _meta(c, "paylasim_ice_alindi")
        hedef = c.execute("SELECT id FROM masa_kart WHERE tur='klasor' AND ebeveyn IS NULL AND ad='Paylaşımlar'"
                          ).fetchone()
    finally:
        c.close()
    ice = 0
    if not yapildi:
        ice = _paylasimlari_ice_al(hedef["id"] if hedef else None)
        c = _db()
        try:
            _meta(c, "paylasim_ice_alindi", _now())
            c.commit()
        finally:
            c.close()
    return {"acilan": acilan, "paylasim_ice_alinan": ice}


def _paylasimlari_ice_al(hedef):
    """ops/kokpit_paylasim/ (Share Extension, 2026-09) → Paylaşımlar klasörü; sahibin kaydı,
    o gün Ghost görmüştü → gorulme dolu (yeni listesine düşmez). Her dosya kendi bağlantısıyla
    (uzun açık yazma işlemi başka bağlantıyı kilitlemesin)."""
    d = os.path.join(config.OPS, "kokpit_paylasim")
    if hedef is None or not os.path.isdir(d):
        return 0
    ice = 0
    for fn in sorted(os.listdir(d)):
        p = os.path.join(d, fn)
        if not os.path.isfile(p):
            continue
        ad = fn.split("-", 3)[-1] if fn.count("-") >= 3 else fn   # 20260903-205058-bf6649-foto.jpg → foto.jpg
        try:
            with open(p, "rb") as f:
                veri = f.read()
            kid = dosya_ekle(ad, veri, hedef, yazar="sahip", kaynak="paylaşım " + fn)
        except Exception:
            continue
        ts = ("%s-%s-%s %s:%s:%s" % (fn[0:4], fn[4:6], fn[6:8], fn[9:11], fn[11:13], fn[13:15])
              if fn[:8].isdigit() and fn[8:9] == "-" else _now())
        c = _db()
        try:
            c.execute("UPDATE masa_kart SET gorulme=?, ts=?, guncelleme=? WHERE id=?", (_now(), ts, ts, kid))
            c.commit()
        finally:
            c.close()
        ice += 1
    return ice


def durum():
    c = _db()
    try:
        n = c.execute("SELECT durum, tur, yazar, COUNT(*) FROM masa_kart GROUP BY durum, tur, yazar").fetchall()
        boyut = 0
        d = os.path.join(config.OPS, DOSYA_DIZIN)
        if os.path.isdir(d):
            boyut = sum(os.path.getsize(os.path.join(d, f)) for f in os.listdir(d))
        return {"seq": seq(), "sayim": [dict(durum=r[0], tur=r[1], yazar=r[2], n=r[3]) for r in n],
                "dosya_mb": round(boyut / 1048576, 1), "dosya_uyari": boyut > DOSYA_UYARI,
                "yeni": len(yeni())}
    finally:
        c.close()


# ---------------------------------------------------------------- CLI
def _agac_yaz(ebeveyn=None, girinti=0, durum_="masa"):
    for k in cocuklar(ebeveyn, durum_):
        ek = ""
        if k["tur"] == "klasor":
            ek = ""
        elif k.get("onizleme"):
            ek = " — " + k["onizleme"][:70]
        print("%s#%d %s[%s] %s%s  (%s, %s)" % ("  " * girinti, k["id"], "📁 " if k["tur"] == "klasor" else "",
                                             k["tur"], k["ad"], ek, k["yazar"], (k.get("guncelleme") or "")[:16]))
        if k["tur"] == "klasor":
            _agac_yaz(k["id"], girinti + 1, durum_)


def main(argv):
    cmd = argv[0] if argv else "agac"
    try:
        if cmd == "agac":
            print("Masa (seq %d)" % seq())
            _agac_yaz(None, 0, "dolap" if "--dolap" in argv else "masa")
        elif cmd == "oku":
            k = oku(argv[1])
            if not k:
                print("yok"); return 1
            print(json.dumps(k, ensure_ascii=False, indent=1))
        elif cmd == "yayinla":
            kid, ne = yayinla(argv[1], argv[2], argv[3] if len(argv) > 3 else None)
            print("masa #%d %s (%s → %s)" % (kid, ne, _gorece(argv[1]), argv[2]))
        elif cmd == "not":
            print("masa not #%d" % not_ekle(argv[1], argv[2], "ghost"))
        elif cmd == "dosya":
            e = klasor_bul(argv[2], olustur=True)
            print("masa #%d" % dosya_kopyala(argv[1], e, ad=argv[3] if len(argv) > 3 else None))
        elif cmd == "ekle":
            if argv[1] == "klasor":
                print("masa klasör #%d" % kart_ekle("klasor", argv[3], klasor_bul(argv[2]), yazar="ghost"))
            elif argv[1] == "baglanti":
                print("masa bağlantı #%d" % kart_ekle("baglanti", argv[4] if len(argv) > 4 else argv[3],
                                                     klasor_bul(argv[2], olustur=True), icerik=argv[3], yazar="ghost"))
            else:
                print("ekle klasor <kart|kok> <ad> | ekle baglanti <kart> <url> [ad]"); return 2
        elif cmd == "tasi":
            tasi(argv[1], klasor_bul(argv[2]), "ghost"); print("taşındı")
        elif cmd == "dolap":
            dolap(argv[1], "ghost"); print("dolaba kaldırıldı")
        elif cmd == "geri":
            geri(argv[1], "ghost"); print("masaya alındı")
        elif cmd == "adlandir":
            duzenle(argv[1], "ghost", ad=argv[2]); print("adlandırıldı")
        elif cmd == "sil":
            print(sil(argv[1], "ghost", zorla="--sahip-istedi" in argv))
        elif cmd == "yeni":
            y = yeni(okundu="--okundu" in argv)
            if not y:
                print("sahipten yeni değişiklik yok")
            for k in y:
                print("#%d [%s] %s › %s  (%s)" % (k["id"], k["tur"], k["yol"], k["ad"], (k.get("guncelleme") or "")[:16]))
                if k["tur"] in ("not", "baglanti", "rapor"):
                    print("   " + (k.get("icerik") or "")[:1500].replace("\n", "\n   "))
                elif k["tur"] in ("gorsel", "dosya"):
                    d = dosya_yol(k["id"])
                    print("   dosya: %s" % (d[0] if d else "?"))
        elif cmd == "hazirla":
            print(json.dumps(hazirla(), ensure_ascii=False))
        elif cmd == "durum":
            print(json.dumps(durum(), ensure_ascii=False, indent=1))
        else:
            print(__doc__); return 2
    except MasaHata as e:
        print("masa hatası: %s" % e); return 1
    except IndexError:
        print(__doc__); return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
