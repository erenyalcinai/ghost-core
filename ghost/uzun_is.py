"""Uzun ufuklu iş (Faz 2.6, 2026-10-30 — bütünsel denetim 07 Ö10): saatler süren işi kontrol noktalı adımlara böl.

NEDEN: "AppA'in 7 açık Sentry hatasını tek tek çöz", "App B 1.1 regresyon turu" gibi işler bugün tek `/job`
(1800 sn tavan) ya da sahibin başında kalmasıyla yürüyor; yarım kalan işin nerede kaldığı yalnız dökümde.
TASARIM: iş = başlık + 2-12 adımlık plan (ghost.db `uzun_is`). Her adım AYRI bir iş olarak olay veriyoluna
(`bus.istek`, kind `uzun-is:<id>:<n>`) düşer; köprü onu sıradan özerk iş gibi koşar (ÖZERK kalkan + kota
penceresi + ikinci göz AYNEN geçerli). Adım oturumu bitince `python3 -m ghost.uzun_is adim <id> <n> tamam|soru|hata
"<metin>"` ile rapor verir; `tamam` bir sonraki adımı kuyruklar (zincir kendini sürer), `soru`/`hata` işi DURDURUR
ve sahibe TEK butonlu soru (Devam / Adımı atla / İptal) gider — cevap gelene dek hiçbir adım koşmaz.
Durum kartı: dossier/uzun_is/<id>.md → Masa "Ghost" kartına yayınlanır (aynı kaynak → güncellenir).
BÜTÇE: toplam süre (varsayılan 4 sa, adım süreleri toplanır) ve adım tavanı; dolunca durur, sorar.
BEKÇİ (`tick`, selfcheck 15 dk): adım işi rapor vermeden hata/iptal/denetimde takıldıysa → durdur + soru;
"bitti" ama rapor yoksa (≥5 dk) → adım tamam sayılır, zincir sürer; FREEZE → koşan/soran işler iptal.
YÖNETİŞİM: özerk oturum (GHOST_OZERK=1) uzun iş BAŞLATAMAZ (Sahip başlatır, sohbet oturumu kaydeder); adımlar
özerk kipte koştuğu için geri alınmaz eylem (deploy/push/silme/para) YAPAMAZ — adım ÖNERİLEN EYLEM bloğu yazar
(onay-komut kartı açılır) ve `soru` ile durur. Adım başlamadan `aktif <id> <n>` sorar: iptal/duran işin adımı
kuyruktan geç çıkarsa hiçbir şey yapmadan çıkar.
CLI: python3 -m ghost.uzun_is baslat "<başlık>" "adım1|adım2|…" [--detay "…"] [--butce-sa 4] · liste · goster <id> ·
adim <id> <n> tamam|soru|hata|atla ["metin"] · devam <id> ["sahibin cevabı"] · atla <id> · iptal <id> ["neden"] ·
aktif <id> <n> · tick · kart <id>
"""
import glob
import json
import os
import sqlite3
import sys
import time

from ghost import config, governance

KIND = "uzun-is:%d:%d"
ADIM_TAVAN = 12
BUTCE_SA_VARSAYILAN = 4.0
RAPOR_BEKLE_SN = 300          # iş "bitti" ama adım raporu gelmedi → bu kadar bekle, sonra tamam say
DENEME_TAVAN = 3              # aynı adım en çok bu kadar koşar (devam ile)
SECENEKLER = ("Devam", "Adımı atla", "İptal")
AKTIF_DURUMLAR = ("kosuyor", "soru")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS uzun_is(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  baslik TEXT NOT NULL,
  detay TEXT NOT NULL DEFAULT '',
  durum TEXT NOT NULL DEFAULT 'kosuyor',      -- kosuyor|soru|bitti|iptal
  adimlar TEXT NOT NULL DEFAULT '[]',
  butce_sn INTEGER NOT NULL DEFAULT 14400,
  kullanilan_sn INTEGER NOT NULL DEFAULT 0,
  masa_id INTEGER,
  soru TEXT NOT NULL DEFAULT '',
  cevaplar TEXT NOT NULL DEFAULT '[]',
  notu TEXT NOT NULL DEFAULT '',
  created TEXT NOT NULL,
  updated TEXT NOT NULL
);
"""

# Testlerin ve dış bağlamın değiştirebildiği kancalar (None = gerçek organ)
_kuyruk = None      # (prompt, kind) -> olay id
_sor = None         # (soru, secenekler) -> döküm id
_bildir = None      # (metin) -> None
_yayinla = None     # (yol) -> masa id


class UzunIsHata(Exception):
    pass


def _conn():
    c = sqlite3.connect(config.DB_PATH, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_SCHEMA)
    return c


def _now():
    return time.strftime("%F %T")


def _kart_dizini():
    return os.path.join(config.BASE, "dossier", "uzun_is")


# ---------- kayıt ----------
_SEC = "SELECT id, baslik, detay, durum, adimlar, butce_sn, kullanilan_sn, masa_id, soru, cevaplar, notu, created, updated FROM uzun_is"


def _satir(r):
    d = dict(zip(("id", "baslik", "detay", "durum", "adimlar", "butce_sn", "kullanilan_sn", "masa_id", "soru",
                  "cevaplar", "notu", "created", "updated"), r))
    d["adimlar"] = json.loads(d["adimlar"] or "[]")
    d["cevaplar"] = json.loads(d["cevaplar"] or "[]")
    return d


def oku(uid):
    c = _conn()
    try:
        r = c.execute(_SEC + " WHERE id=?", (int(uid),)).fetchone()
    finally:
        c.close()
    if not r:
        raise UzunIsHata("uzun iş #%s yok" % uid)
    d = _satir(r)
    d["id"] = int(d["id"])
    return d


def liste(hepsi=False):
    c = _conn()
    try:
        q = _SEC + ("" if hepsi else " WHERE durum IN ('kosuyor','soru')") + " ORDER BY id"
        return [_satir(r) for r in c.execute(q).fetchall()]
    finally:
        c.close()


def _yaz(is_, **kw):
    is_.update(kw)
    is_["updated"] = _now()
    c = _conn()
    try:
        c.execute("UPDATE uzun_is SET durum=?, adimlar=?, kullanilan_sn=?, masa_id=?, soru=?, cevaplar=?, notu=?, "
                  "updated=? WHERE id=?",
                  (is_["durum"], json.dumps(is_["adimlar"], ensure_ascii=False), int(is_["kullanilan_sn"]),
                   is_.get("masa_id"), is_.get("soru") or "", json.dumps(is_["cevaplar"], ensure_ascii=False),
                   is_.get("notu") or "", is_["updated"], is_["id"]))
        c.commit()
    finally:
        c.close()
    _kart_yaz(is_)
    return is_


def adimlari_ayristir(adimlar):
    if isinstance(adimlar, str):
        adimlar = [a for a in adimlar.split("|")]
    out = [str(a).strip() for a in (adimlar or []) if str(a).strip()]
    if not out:
        raise UzunIsHata("en az bir adım gerekli (\"adım1|adım2|…\")")
    if len(out) > ADIM_TAVAN:
        raise UzunIsHata("en çok %d adım (verilen %d) — işi böl" % (ADIM_TAVAN, len(out)))
    return out


def baslat(baslik, adimlar, detay="", butce_sa=BUTCE_SA_VARSAYILAN):
    """Yeni uzun iş: kayıt + plan kartı + ilk adım kuyruğa. Özerk oturum ve FREEZE başlatamaz."""
    if os.environ.get("GHOST_OZERK") == "1":
        raise UzunIsHata("özerk oturum uzun iş başlatamaz — Sahip başlatır (sohbet oturumu kaydeder)")
    if governance.is_frozen():
        raise UzunIsHata("FREEZE açık — uzun iş başlatılmaz")
    baslik = (baslik or "").strip()
    if not baslik:
        raise UzunIsHata("başlık gerekli")
    adlar = adimlari_ayristir(adimlar)
    butce_sn = int(float(butce_sa) * 3600)
    if butce_sn <= 0:
        raise UzunIsHata("bütçe saat > 0 olmalı")
    plan = [{"n": i + 1, "ad": ad, "durum": "acik", "jid": None, "ozet": "", "bas": 0, "bit": 0, "sure_sn": 0,
             "deneme": 0} for i, ad in enumerate(adlar)]
    c = _conn()
    try:
        cur = c.execute("INSERT INTO uzun_is(baslik, detay, durum, adimlar, butce_sn, created, updated) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (baslik[:200], (detay or "").strip()[:2000], "kosuyor", json.dumps(plan, ensure_ascii=False),
                         butce_sn, _now(), _now()))
        c.commit()
        uid = cur.lastrowid
    finally:
        c.close()
    governance.audit("uzun_is", "baslat", governance.REVERSIBLE,
                     "#%d %s (%d adım, bütçe %.1f sa)" % (uid, baslik[:80], len(plan), butce_sa))
    is_ = oku(uid)
    _kuyrukla(is_, 1)
    return uid


# ---------- adım istemi ----------
def _plan_metni(is_):
    isaret = {"acik": " ", "kosuyor": ">", "tamam": "x", "hata": "!", "atlandi": "-"}
    satirlar = []
    for a in is_["adimlar"]:
        s = "  %d. [%s] %s" % (a["n"], isaret.get(a["durum"], "?"), a["ad"])
        if a.get("ozet") and a["durum"] in ("tamam", "hata"):
            s += " — " + a["ozet"].strip().splitlines()[0][:160]
        satirlar.append(s)
    return "\n".join(satirlar)


def istem(is_, n):
    a = is_["adimlar"][n - 1]
    toplam = len(is_["adimlar"])
    onceki = [x for x in is_["adimlar"] if x["durum"] == "tamam" and x.get("ozet")]
    onceki_metin = "\n".join("  %d. %s: %s" % (x["n"], x["ad"], x["ozet"].strip()[:600]) for x in onceki) or "  (yok)"
    cevaplar = "\n".join("  - %s" % c for c in is_["cevaplar"][-5:]) or "  (yok)"
    kalan_dk = max(0, int(is_["butce_sn"]) - int(is_["kullanilan_sn"])) // 60
    return (
        "UZUN İŞ #%d — adım %d/%d: %s\n"
        "Başlık: %s\n%s"
        "PLAN:\n%s\n"
        "ÖNCEKİ ADIM ÖZETLERİ:\n%s\n"
        "SAHİBİN CEVAPLARI (bu işe dair):\n%s\n"
        "Kalan süre bütçesi: %d dk · bu adım %d. deneme.\n"
        "KURALLAR:\n"
        "- ÖNCE `python3 -m ghost.uzun_is aktif %d %d` koş; \"aktif değil\" derse HİÇBİR ŞEY yapma, tek satırla çık.\n"
        "- YALNIZ bu adımı yap; sonraki adımlara geçme, planı değiştirme.\n"
        "- Bu adım özerk kipte koşar: ghost/ ve ops/ koduna yazamaz, commit/push/deploy/silme/para YAPAMAZ. Böyle bir "
        "şey gerekiyorsa yapma: çıktına ÖNERİLEN EYLEM bloğu yaz (başlık / komut: / neden:) ve "
        "`python3 -m ghost.uzun_is adim %d %d soru \"<tek soru>\"` koş — iş durur, Sahip karar verir.\n"
        "- Rapor/tablo/diff çıktın varsa `dossier/uzun_is/%d-adim%d.md` dosyasına yaz.\n"
        "- BİTİNCE MUTLAKA (son komutun bu olsun): `python3 -m ghost.uzun_is adim %d %d tamam \"<3-5 satır: ne yaptın, "
        "somut çıktı, sonraki adım için not>\"` — bu çağrı sonraki adımı başlatır; çağırmazsan zincir durur.\n"
        "- Adım yapılamıyorsa: `python3 -m ghost.uzun_is adim %d %d hata \"<neden>\"`.\n"
        "Sohbet çıktın kısa olsun (telefonda okunur); ayrıntı dosyada."
        % (is_["id"], n, toplam, a["ad"], is_["baslik"],
           ("Detay: %s\n" % is_["detay"]) if is_.get("detay") else "",
           _plan_metni(is_), onceki_metin, cevaplar, kalan_dk, int(a.get("deneme") or 0) + 1,
           is_["id"], n, is_["id"], n, is_["id"], n, is_["id"], n, is_["id"], n))


def _kuyrukla(is_, n):
    a = is_["adimlar"][n - 1]
    if a.get("deneme", 0) >= DENEME_TAVAN:
        a["durum"] = "hata"
        return _dur(is_, "Adım %d (%s) %d kez koştu, hâlâ bitmedi — atlayalım mı?" % (n, a["ad"], a["deneme"]))
    prompt = istem(is_, n)
    kind = KIND % (is_["id"], n)
    # Sınıflandırma istemin KURALLAR metnine (deploy/push sözcükleri) değil, adımın kendisine bakar: adım adı
    # "deploy et" diyorsa köprü onay butonu açar (PERMANENT adım = onay kartı), "hataları incele" diyorsa açmaz.
    sinif_metni = "%s — %s" % (is_["baslik"], a["ad"])
    if _kuyruk is not None:
        ev = _kuyruk(prompt, kind, sinif_metni)
    else:
        from ghost import bus
        ev = bus.istek(prompt, kind, "uzun_is", classify=sinif_metni)
    a.update(durum="kosuyor", bas=int(time.time()), bit=0, deneme=int(a.get("deneme") or 0) + 1, olay=ev, jid=None)
    is_["durum"] = "kosuyor"
    is_["soru"] = ""
    governance.audit("uzun_is", "adim.kuyruk", governance.READ, "#%d adım %d/%d: %s" % (is_["id"], n, len(is_["adimlar"]), a["ad"][:80]))
    return _yaz(is_)


def _acik_adim(is_):
    for a in is_["adimlar"]:
        if a["durum"] == "acik":
            return a["n"]
    return None


def _kosan_adim(is_):
    for a in is_["adimlar"]:
        if a["durum"] in ("kosuyor", "hata"):
            return a["n"]
    return None


def _ilerle(is_):
    """Sıradaki açık adımı kuyrukla; yoksa bitir; bütçe/FREEZE durdurur."""
    if governance.is_frozen():
        return _iptal_et(is_, "FREEZE")
    n = _acik_adim(is_)
    if n is None:
        return _bitir(is_)
    if int(is_["kullanilan_sn"]) >= int(is_["butce_sn"]):
        return _dur(is_, "Süre bütçesi doldu (%d dk / %d dk); %d adım kaldı. Devam = aynı bütçe bir daha." %
                    (int(is_["kullanilan_sn"]) // 60, int(is_["butce_sn"]) // 60,
                     sum(1 for a in is_["adimlar"] if a["durum"] == "acik")))
    return _kuyrukla(is_, n)


def _dur(is_, soru):
    """Hata/onay/bütçe noktası: iş durur, sahibe TEK butonlu soru (kapalı küme)."""
    n = _kosan_adim(is_) or _acik_adim(is_) or len(is_["adimlar"])
    is_["durum"] = "soru"
    is_["soru"] = soru[:600]
    _yaz(is_)
    metin = ("🧭 Uzun iş #%d (%s) durdu — adım %d/%d: %s.\n%s" %
             (is_["id"], is_["baslik"][:80], n, len(is_["adimlar"]), is_["adimlar"][n - 1]["ad"][:80], soru[:600]))
    try:
        if _sor is not None:
            _sor(metin, list(SECENEKLER))
        else:
            from ghost import sahne
            sahne.sor(metin, list(SECENEKLER))
    except Exception:
        pass
    governance.audit("uzun_is", "dur", governance.READ, "#%d adım %d: %s" % (is_["id"], n, soru[:120]))
    return is_


def _bitir(is_):
    is_["durum"] = "bitti"
    is_["soru"] = ""
    _yaz(is_)
    tamam = sum(1 for a in is_["adimlar"] if a["durum"] == "tamam")
    atlanan = sum(1 for a in is_["adimlar"] if a["durum"] == "atlandi")
    metin = "🧭 Uzun iş #%d bitti: %s — %d adım tamam%s, %d dk. Durum kartı Masa'da (Ghost)." % (
        is_["id"], is_["baslik"][:80], tamam, (", %d atlandı" % atlanan) if atlanan else "", int(is_["kullanilan_sn"]) // 60)
    _bildir_metin(metin)
    governance.audit("uzun_is", "bitti", governance.READ, "#%d %d/%d adım, %d sn" % (is_["id"], tamam, len(is_["adimlar"]), is_["kullanilan_sn"]))
    return is_


def _iptal_et(is_, neden=""):
    is_["durum"] = "iptal"
    is_["notu"] = ("iptal: %s" % neden)[:300]
    is_["soru"] = ""
    for a in is_["adimlar"]:
        if a["durum"] == "kosuyor":
            a["durum"] = "hata"
            a["ozet"] = a.get("ozet") or "(iptal edildi)"
    _yaz(is_)
    governance.audit("uzun_is", "iptal", governance.REVERSIBLE, "#%d %s" % (is_["id"], neden[:120]))
    return is_


def _bildir_metin(metin):
    try:
        if _bildir is not None:
            _bildir(metin)
        else:
            from ghost import notify
            notify.telegram(metin)
    except Exception:
        pass


# ---------- adım raporu (adım oturumu çağırır) ----------
def adim(uid, n, sonuc, metin=""):
    """Adım oturumunun raporu: tamam → sonraki adım · soru/hata → dur · atla → atlanır, sonraki adım."""
    is_ = oku(uid)
    n = int(n)
    if not 1 <= n <= len(is_["adimlar"]):
        raise UzunIsHata("adım %d yok (1-%d)" % (n, len(is_["adimlar"])))
    if is_["durum"] not in AKTIF_DURUMLAR:
        raise UzunIsHata("uzun iş #%d %s — adım raporu alınmaz" % (is_["id"], is_["durum"]))
    a = is_["adimlar"][n - 1]
    if a["durum"] in ("tamam", "atlandi"):
        raise UzunIsHata("adım %d zaten %s" % (n, a["durum"]))
    metin = (metin or "").strip()
    simdi = int(time.time())
    if a.get("bas") and not a.get("bit"):
        a["sure_sn"] = int(a.get("sure_sn") or 0) + max(0, simdi - int(a["bas"]))
        a["bit"] = simdi
        is_["kullanilan_sn"] = int(is_["kullanilan_sn"]) + max(0, simdi - int(a["bas"]))
    if sonuc == "tamam":
        a["durum"] = "tamam"
        a["ozet"] = metin[:1500] or "(özet verilmedi)"
        governance.audit("uzun_is", "adim.tamam", governance.READ, "#%d adım %d (%d sn)" % (is_["id"], n, a["sure_sn"]))
        return _ilerle(is_)
    if sonuc == "atla":
        a["durum"] = "atlandi"
        a["ozet"] = metin[:500] or "(atlandı)"
        return _ilerle(is_)
    if sonuc == "hata":
        a["durum"] = "hata"
        a["ozet"] = metin[:1500] or "(neden verilmedi)"
        return _dur(is_, "Adım %d hata verdi: %s" % (n, metin[:400] or "neden verilmedi"))
    if sonuc == "soru":
        a["durum"] = "hata"      # tekrar koşacak; cevap istemine girer
        a["ozet"] = ("soru: " + metin)[:1500]
        return _dur(is_, metin[:500] or "Adım bir karar istiyor.")
    raise UzunIsHata("sonuç tamam|soru|hata|atla olmalı")


def devam(uid, cevap=""):
    """Sahip 'Devam' dedi (ya da düz metinle cevapladı): duran adım cevapla yeniden koşar."""
    is_ = oku(uid)
    if is_["durum"] != "soru":
        raise UzunIsHata("uzun iş #%d soruda değil (%s)" % (is_["id"], is_["durum"]))
    cevap = (cevap or "").strip()
    if cevap:
        is_["cevaplar"].append("[adım %s] %s" % (_kosan_adim(is_) or "-", cevap[:500]))
    if "bütçe" in is_["soru"].lower() and "doldu" in is_["soru"].lower():
        is_["butce_sn"] = int(is_["kullanilan_sn"]) + int(is_["butce_sn"])   # aynı bütçe bir daha
        c = _conn()
        try:
            c.execute("UPDATE uzun_is SET butce_sn=? WHERE id=?", (is_["butce_sn"], is_["id"]))
            c.commit()
        finally:
            c.close()
    n = _kosan_adim(is_)
    governance.audit("uzun_is", "devam", governance.REVERSIBLE, "#%d adım %s%s" % (is_["id"], n, (": " + cevap[:80]) if cevap else ""))
    if n is None:
        return _ilerle(is_)
    is_["adimlar"][n - 1]["durum"] = "acik"
    return _kuyrukla(is_, n)


def atla(uid, neden=""):
    is_ = oku(uid)
    if is_["durum"] not in AKTIF_DURUMLAR:
        raise UzunIsHata("uzun iş #%d %s" % (is_["id"], is_["durum"]))
    n = _kosan_adim(is_)
    if n is None:
        raise UzunIsHata("atlanacak duran adım yok")
    a = is_["adimlar"][n - 1]
    a["durum"] = "atlandi"
    a["ozet"] = (neden or "Sahip atladı")[:500]
    governance.audit("uzun_is", "atla", governance.REVERSIBLE, "#%d adım %d" % (is_["id"], n))
    return _ilerle(is_)


def iptal(uid, neden=""):
    is_ = oku(uid)
    if is_["durum"] not in AKTIF_DURUMLAR:
        raise UzunIsHata("uzun iş #%d zaten %s" % (is_["id"], is_["durum"]))
    return _iptal_et(is_, neden or "Sahip iptal etti")


def aktif(uid, n):
    """Adım oturumu başlarken sorar: (True, '') ya da (False, neden). İptal/duran işin geç çıkan adımı boşa koşmasın."""
    try:
        is_ = oku(uid)
    except UzunIsHata as e:
        return False, str(e)
    n = int(n)
    if is_["durum"] != "kosuyor":
        return False, "uzun iş #%d %s" % (is_["id"], is_["durum"])
    if not 1 <= n <= len(is_["adimlar"]) or is_["adimlar"][n - 1]["durum"] != "kosuyor":
        return False, "adım %d şu an koşmuyor" % n
    return True, ""


# ---------- bekçi ----------
def _isler_oku():
    """ops/jobs/*.json → {kind: (jid, kayıt)} (aynı kind'ın en yeni işi)."""
    out = {}
    for yol in glob.glob(os.path.join(config.OPS, "jobs", "*.json")):
        try:
            jid = int(os.path.splitext(os.path.basename(yol))[0])
            with open(yol, encoding="utf-8") as f:
                j = json.load(f)
        except (ValueError, OSError, json.JSONDecodeError):
            continue
        k = j.get("kind") or ""
        if k.startswith("uzun-is:") and (k not in out or out[k][0] < jid):
            out[k] = (jid, j)
    return out


def _ts(s):
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return 0


def tick(simdi=None):
    """Selfcheck bekçisi: rapor vermeyen adım işlerini toparlar; FREEZE'de aktif işleri iptal eder. Yapılanlar listesi."""
    simdi = simdi or time.time()
    yapilan = []
    aktifler = liste()
    if not aktifler:
        return yapilan
    if governance.is_frozen():
        for is_ in aktifler:
            _iptal_et(is_, "FREEZE")
            yapilan.append("#%d iptal (FREEZE)" % is_["id"])
        return yapilan
    isler = _isler_oku()
    for is_ in aktifler:
        if is_["durum"] != "kosuyor":
            continue
        n = _kosan_adim(is_)
        if n is None:
            continue
        a = is_["adimlar"][n - 1]
        if a["durum"] != "kosuyor":
            continue
        kayit = isler.get(KIND % (is_["id"], n))
        if not kayit:
            continue                      # henüz işe dönüşmedi (kota ertelemesi / kuyruk) → bekle
        jid, j = kayit
        if a.get("jid") != jid:
            a["jid"] = jid
            _yaz(is_)
        st = j.get("status") or ""
        if st in ("hata", "iptal", "denetimde takıldı"):
            a["durum"] = "hata"
            a["ozet"] = ("iş #%d %s: %s" % (jid, st, (j.get("hata") or "")[:300])).strip()
            bit = _ts(j.get("ended")) or simdi
            a["bit"] = int(bit)
            a["sure_sn"] = int(a.get("sure_sn") or 0) + max(0, int(bit - (a.get("bas") or bit)))
            is_["kullanilan_sn"] = int(is_["kullanilan_sn"]) + max(0, int(bit - (a.get("bas") or bit)))
            _dur(is_, "Adım %d'in işi (#%d) %s%s" % (n, jid, st, (": " + j["hata"][:300]) if j.get("hata") else ""))
            yapilan.append("#%d adım %d → soru (iş #%d %s)" % (is_["id"], n, jid, st))
        elif st == "bitti" and simdi - (_ts(j.get("ended")) or simdi) >= RAPOR_BEKLE_SN:
            a["bit"] = int(_ts(j.get("ended")) or simdi)
            a["sure_sn"] = int(a.get("sure_sn") or 0) + max(0, a["bit"] - int(a.get("bas") or a["bit"]))
            is_["kullanilan_sn"] = int(is_["kullanilan_sn"]) + max(0, a["bit"] - int(a.get("bas") or a["bit"]))
            a["durum"] = "tamam"
            a["ozet"] = "(adım raporu yok — iş #%d bitti%s)" % (jid, (", ikinci göz: " + j["verify"]) if j.get("verify") else "")
            _ilerle(is_)
            yapilan.append("#%d adım %d → tamam (rapor yok, iş #%d)" % (is_["id"], n, jid))
    return yapilan


# ---------- durum kartı ----------
def kart_metni(is_):
    simge = {"kosuyor": "🏃", "soru": "❓", "bitti": "✅", "iptal": "🛑"}
    satirlar = ["# 🧭 Uzun iş #%d · %s" % (is_["id"], is_["baslik"]), "",
                "Durum: %s %s · adım %d/%d · süre %d dk / bütçe %d dk · başladı %s · güncelleme %s" % (
                    simge.get(is_["durum"], ""), is_["durum"],
                    sum(1 for a in is_["adimlar"] if a["durum"] in ("tamam", "atlandi")), len(is_["adimlar"]),
                    int(is_["kullanilan_sn"]) // 60, int(is_["butce_sn"]) // 60, is_["created"], is_["updated"])]
    if is_.get("detay"):
        satirlar += ["", "Detay: " + is_["detay"]]
    if is_["durum"] == "soru" and is_.get("soru"):
        satirlar += ["", "**Bekleyen soru:** " + is_["soru"]]
    if is_["durum"] == "iptal" and is_.get("notu"):
        satirlar += ["", is_["notu"]]
    satirlar += ["", "| # | Adım | Durum | Süre | İş | Özet |", "|---|---|---|---|---|---|"]
    for a in is_["adimlar"]:
        ozet = (a.get("ozet") or "").strip().replace("\n", " ").replace("|", "/")[:200]
        dosya = os.path.join(_kart_dizini(), "%d-adim%d.md" % (is_["id"], a["n"]))
        if os.path.isfile(dosya):
            ozet += " (dossier/uzun_is/%d-adim%d.md)" % (is_["id"], a["n"])
        satirlar.append("| %d | %s | %s | %s | %s | %s |" % (
            a["n"], a["ad"].replace("|", "/"), a["durum"], ("%d dk" % (int(a.get("sure_sn") or 0) // 60)) if a.get("sure_sn") else "",
            ("#%d" % a["jid"]) if a.get("jid") else "", ozet))
    if is_["cevaplar"]:
        satirlar += ["", "sahibin cevapları:"] + ["- " + c for c in is_["cevaplar"]]
    return "\n".join(satirlar) + "\n"


def _kart_yaz(is_):
    """dossier/uzun_is/<id>.md yaz + Masa 'Ghost' kartına yayınla (aynı kaynak → günceller). Sessiz başarısız."""
    try:
        d = _kart_dizini()
        os.makedirs(d, exist_ok=True)
        yol = os.path.join(d, "%d.md" % is_["id"])
        with open(yol, "w", encoding="utf-8") as f:
            f.write(kart_metni(is_))
        if _yayinla is not None:
            kid = _yayinla(yol)
        else:
            from ghost import masa
            kid, _ = masa.yayinla(yol, "Ghost")
        if kid and kid != is_.get("masa_id"):
            is_["masa_id"] = kid
            c = _conn()
            try:
                c.execute("UPDATE uzun_is SET masa_id=? WHERE id=?", (kid, is_["id"]))
                c.commit()
            finally:
                c.close()
    except Exception:
        pass


# ---------- CLI ----------
def _ozet_satir(is_):
    n = sum(1 for a in is_["adimlar"] if a["durum"] in ("tamam", "atlandi"))
    return "#%d [%s] %s — adım %d/%d, %d dk%s" % (
        is_["id"], is_["durum"], is_["baslik"][:70], n, len(is_["adimlar"]), int(is_["kullanilan_sn"]) // 60,
        (" · soru: " + is_["soru"][:100]) if is_["durum"] == "soru" else "")


def _secenek(argv, ad, varsayilan=None):
    if ad in argv:
        i = argv.index(ad)
        if i + 1 < len(argv):
            v = argv[i + 1]
            del argv[i:i + 2]
            return v
    return varsayilan


def _main(argv):
    argv = list(argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("\n".join(__doc__.strip().splitlines()[-3:]))
        return 0
    cmd, args = argv[0], argv[1:]
    try:
        if cmd == "baslat":
            detay = _secenek(args, "--detay", "")
            butce = float(_secenek(args, "--butce-sa", BUTCE_SA_VARSAYILAN))
            if len(args) < 2:
                raise UzunIsHata('kullanım: baslat "<başlık>" "adım1|adım2|…" [--detay "…"] [--butce-sa 4]')
            uid = baslat(args[0], args[1], detay=detay, butce_sa=butce)
            is_ = oku(uid)
            print("🧭 Uzun iş #%d başladı: %s (%d adım, bütçe %.1f sa). Adım 1 kuyrukta." % (is_["id"], is_["baslik"], len(is_["adimlar"]), butce))
            print(_plan_metni(is_))
        elif cmd == "liste":
            rows = liste(hepsi="--hepsi" in args)
            print("\n".join(_ozet_satir(r) for r in rows) or "(aktif uzun iş yok)")
        elif cmd == "goster":
            print(kart_metni(oku(args[0])))
        elif cmd == "adim":
            if len(args) < 3:
                raise UzunIsHata('kullanım: adim <id> <n> tamam|soru|hata|atla ["metin"]')
            is_ = adim(args[0], args[1], args[2], " ".join(args[3:]))
            print(_ozet_satir(is_))
        elif cmd == "devam":
            print(_ozet_satir(devam(args[0], " ".join(args[1:]))))
        elif cmd == "atla":
            print(_ozet_satir(atla(args[0], " ".join(args[1:]))))
        elif cmd == "iptal":
            print(_ozet_satir(iptal(args[0], " ".join(args[1:]))))
        elif cmd == "aktif":
            ok, neden = aktif(args[0], args[1])
            print("aktif" if ok else "aktif değil: %s" % neden)
            return 0 if ok else 3
        elif cmd == "tick":
            y = tick()
            print("\n".join(y) or "(değişiklik yok)")
        elif cmd == "kart":
            is_ = oku(args[0])
            _kart_yaz(is_)
            print("kart yazıldı: dossier/uzun_is/%d.md (Masa #%s)" % (is_["id"], is_.get("masa_id")))
        else:
            raise UzunIsHata("bilinmeyen komut: %s" % cmd)
    except UzunIsHata as e:
        print("hata: %s" % e)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
