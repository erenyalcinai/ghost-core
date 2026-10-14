#!/usr/bin/env python3
"""Ghost veri okuyucu (Mac) — Tam Disk Erişimi'ne sahip TEK süreç.

Mac'te `~/.ghost/veri_okuyucu.py` olarak koşar (launchd `ai.jarvis.veri`, sarmalayıcı
`~/.ghost/bin/ghost-veri`). Yalnız 127.0.0.1:8766, `X-Ghost-Token` şart, her istek
günlüğe düşer (`~/.ghost/log/veri.log`; içerik değil, uç+parametre).

SALT OKUNUR: tüm veritabanları `mode=ro` URI ile açılır, hiçbir uç sahibin verisine yazmaz
(tek yazma: yedek veritabanlarının ~/.ghost/yedek/_db/ altındaki kendi kopyası).
2026-09-19'daki Safari/Mail kapsam dışı sözünü Sahip 2026-09-28'de kaldırdı.
Dosyalar (iCloud Drive) 2026-09-28'de Sahip kararıyla TAMAMEN eklendi ("dosyaları ekle iyi
yol ile tamamen"); yine salt okunur, kök `~/Library/Mobile Documents` dışına çıkılmaz.

Uçlar (hepsi GET):
  /durum                         hangi depo okunabiliyor
  /sema?<depo>                   tablo/kolon adları (veri yok)
  /notlar?son=20&q=&uzunluk=400  Apple Notes listesi (gövde çözülmüş, kırpık)
  /notlar?id=<pk>                tek not, tam gövde
  /mesajlar?son=30&kisi=&q=      iMessage/SMS (attributedBody çözülmüş)
  /fotograflar?son=20&gun=       Fotoğraflar meta (tarih, yer, boyut, tür)
  /foto?uuid=<uuid>&boy=kucuk|orta|asil   tek fotoğraf, base64 JPEG (JSON)
  /dosyalar?yol=&q=&son=100&derin=0&sirala=ad|tarih
                                 iCloud Drive / Dosyalar (~/Library/Mobile Documents):
                                 klasör listesi; derin=1 alt klasörlere iner; q ada göre
                                 süzer; yer tutucu (.X.icloud) "bulutta": true olarak gelir
  /dosya?yol=<göreli>            tek dosya: metinse "metin", değilse "b64"; buluttaysa
                                 önce brctl download ile indirir; tavan DOSYA_TAVAN (25 MB)
                                 (ikisinde de kok=icloud|whatsapp; whatsapp = medya kabı)
  /whatsapp?son=50               sohbet listesi (ad, grup mu, okunmamış, son mesaj)
  /whatsapp?sohbet=<pk|ad parçası>&son=30&q=   bir sohbetin mesajları (yeniden eskiye)
  /whatsapp?q=metin&son=30       tüm sohbetlerde metin araması
  (+ bas=YYYY-MM-DD[ HH:MM] & bit=... tarih aralığı; ikisi de yerel saat)
  /mesajlar?kaynak=yedek ... , /whatsapp?kaynak=yedek ...
                                 aynı uçlar, veri Mac yerine iPhone YEDEĞİNDEN (tam geçmiş)
  /yedek                         yedek durumu (cihaz, tarih, bitti mi, hangi alanlar var)
  /yedek?liste=1&domain=&q=      Manifest'te dosya ara (domain/relativePath)
  /aramalar?son=30&kisi=         arama geçmişi (yedek; rehberden ad çözümlü)
  /rehber?q=&son=50              kişiler (yedek)
  /safari?son=30&q=              Safari geçmişi (yedek)
  /dosya?kok=yedek&yol=<domain>/<relativePath>   yedekten tek dosya (WhatsApp medyası dahil)
  /mail?son=30&q=&kimden=&hesap=&kutu=&bas=&bit=  Mac Mail (Envelope Index, canlı): liste
                                 (Sahip 2026-09-28 "gmaili de okuyabil"; hesap= adres/sunucu parçası,
                                 kutu= INBOX|Sent|... ; q konu+gönderen+özet)
  /mail?id=<ROWID>&uzunluk=4000  tek mail: başlıklar + düz metin gövde + ek adları (.emlx)
  /mail?hesaplar=1               hesaplar ve kutular (mesaj sayılı)

Ayrıştırıcılar (`not_govdesi`, `mesaj_metni`, `_pb_walk`) saf fonksiyondur; VPS'teki
`tests/test_mac_veri.py` bunları sentetik veriyle sınar. Sunucu yalnız __main__'de kalkar.
"""
import base64
import gzip
import json
import os
import plistlib
import re
import stat
import sqlite3
import subprocess
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME = os.path.expanduser("~")
PORT = 8766
# YubiKey kapısı (2026-09-25): modül ~/.ghost ya da ~/jarvis/ops'tan gelir. Bu dosya
# ~/.ghost'a KOPYALANARAK koşar; modül bulunamazsa ama seri dosyası varsa
# KAPALI kal (fail-closed) — "kapı kodu eksik" diye veri sızmasın.
for _p in (os.path.join(HOME, ".ghost"), os.path.join(HOME, "jarvis", "ops")):
    _p not in sys.path and sys.path.insert(0, _p)
try:
    import mac_anahtar
except ImportError:  # pragma: no cover — yalnız Mac'te kopya eksikse
    mac_anahtar = None


def _kapi_acik():
    if mac_anahtar is not None:
        return mac_anahtar.acik()
    return not os.path.exists(os.path.join(HOME, ".ghost", "anahtar_seri"))


TOKEN_DOSYA = os.path.join(HOME, ".ghost_maclink_token")
LOG = os.path.join(HOME, ".ghost", "log", "veri.log")
FOTO_KUTUPHANE = os.path.join(HOME, "Pictures/Photos Library.photoslibrary")
DEPOLAR = {
    "notlar": os.path.join(HOME, "Library/Group Containers/group.com.apple.notes/NoteStore.sqlite"),
    "mesajlar": os.path.join(HOME, "Library/Messages/chat.db"),
    "fotograflar": os.path.join(FOTO_KUTUPHANE, "database/Photos.sqlite"),
    "whatsapp": os.path.join(HOME, "Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite"),
}
CORE_DATA_EPOCH = 978307200  # 2001-01-01 UTC
SON_TAVAN = 200
# Dosyalar (iCloud Drive). Kök: tüm iCloud kapları; com~apple~CloudDocs = "iCloud Drive"
# (Masaüstü/Belgeler senkrondaysa onun içinde), iCloud~<bundle> = uygulama klasörleri.
DOSYA_KOK = os.path.join(HOME, "Library/Mobile Documents")
WHATSAPP_KAP = os.path.join(HOME, "Library/Group Containers/group.net.whatsapp.WhatsApp.shared")
# /dosyalar ve /dosya için seçilebilir kökler (kok=icloud|whatsapp); varsayılan icloud.
KOKLER = {"icloud": DOSYA_KOK, "whatsapp": WHATSAPP_KAP}
DOSYA_TAVAN = 25 * 1024 * 1024
DOSYA_GEZI_TAVAN = 40000      # derin taramada ziyaret edilen girdi sayısı
DOSYA_GEZI_SANIYE = 20
# 2026-09-19'da Safari+Mail kapları kapsam dışıydı; 2026-09-28 Sahip kaldırdı ("safari ve maili de
# kısıtlama"). Boş demet: hiçbir kök kap gizlenmez.
KAPSAM_DISI_KAP = ()
METIN_UZANTI = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml", ".toml",
                ".ini", ".cfg", ".log", ".py", ".js", ".ts", ".swift", ".dart", ".html", ".htm",
                ".css", ".xml", ".sh", ".rtf", ".tex", ".sql", ".env", ".plist", ".srt", ".vtt"}


def log(m):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%F %T ") + m + "\n")


def token():
    try:
        return open(TOKEN_DOSYA, encoding="utf-8").read().strip()
    except OSError:
        return ""


def _ac(yol, dondurulmus=False):
    c = sqlite3.connect("file:%s?mode=ro%s" % (urllib.parse.quote(yol), "&immutable=1" if dondurulmus else ""),
                        uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def dene(yol):
    try:
        c = _ac(yol)
        c.execute("select name from sqlite_master limit 1").fetchone()
        c.close()
        return "ok"
    except Exception as e:
        s = str(e)
        return "denied" if "authorization" in s or "unable to open" in s else "hata: %s" % e


def _ls(p):
    try:
        os.listdir(p)
        return True
    except OSError:
        return False


def _tarih(cd):
    """Core Data saniyesi (2001 epoch) → ISO yerel saat. Nanosaniye (Mesajlar) da gelir."""
    if cd is None:
        return None
    try:
        cd = float(cd)
    except (TypeError, ValueError):
        return None
    if cd > 1e11:  # Mesajlar: nanosaniye
        cd = cd / 1e9
    if cd <= 0:
        return None
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(cd + CORE_DATA_EPOCH))


def _sayi(v, alt, ust, varsayilan):
    try:
        v = int(v)
    except (TypeError, ValueError):
        return varsayilan
    return max(alt, min(ust, v))


# ---------------- protobuf yürüyücüsü (bağımlılıksız) ----------------

def _varint(buf, i):
    r = 0
    s = 0
    while True:
        b = buf[i]
        i += 1
        r |= (b & 0x7F) << s
        if not b & 0x80:
            return r, i
        s += 7


def _pb_walk(buf):
    """Alanları (no, tür, değer) olarak döndürür; length-delimited değer bytes."""
    i = 0
    out = []
    n = len(buf)
    while i < n:
        try:
            key, i = _varint(buf, i)
        except IndexError:
            break
        no, tur = key >> 3, key & 7
        if tur == 0:
            v, i = _varint(buf, i)
        elif tur == 1:
            v, i = buf[i:i + 8], i + 8
        elif tur == 2:
            ln, i = _varint(buf, i)
            v, i = buf[i:i + ln], i + ln
        elif tur == 5:
            v, i = buf[i:i + 4], i + 4
        else:
            break
        out.append((no, tur, v))
    return out


def _pb_alan(buf, no):
    for n, t, v in _pb_walk(buf):
        if n == no and t == 2:
            return v
    return None


def not_govdesi(zdata):
    """ZICNOTEDATA.ZDATA (gzip'li protobuf) → düz metin.
    Yol: Document(2) → Note(3) → note_text(2). Bulamazsa en uzun okunur parça."""
    if not zdata:
        return ""
    try:
        ham = gzip.decompress(zdata) if zdata[:2] == b"\x1f\x8b" else bytes(zdata)
    except Exception:
        return ""
    try:
        doc = _pb_alan(ham, 2)
        note = _pb_alan(doc, 3) if doc else None
        txt = _pb_alan(note, 2) if note else None
        if txt:
            return txt.decode("utf-8", "replace").replace("￼", "[ek]")
    except Exception:
        pass
    parcalar = re.findall(rb"[\x20-\x7e\xc2-\xf4][\x20-\x7e\x80-\xbf\xc2-\xf4\n]{3,}", ham)
    if not parcalar:
        return ""
    return max(parcalar, key=len).decode("utf-8", "replace")


def mesaj_metni(text, attributed):
    """message.text boşsa attributedBody (typedstream) içinden NSString çıkar."""
    if text:
        return text
    if not attributed:
        return ""
    b = bytes(attributed)
    i = b.find(b"NSString")
    if i < 0:
        return ""
    p = i + len(b"NSString") + 5  # \x01\x94\x84\x01\x2b
    if p >= len(b):
        return ""
    if b[p] == 0x81:
        ln = int.from_bytes(b[p + 1:p + 3], "little")
        p += 3
    elif b[p] == 0x82:
        ln = int.from_bytes(b[p + 1:p + 5], "little")
        p += 5
    else:
        ln = b[p]
        p += 1
    return b[p:p + ln].decode("utf-8", "replace").replace("￼", "[ek]")


# ---------------- sorgular ----------------

def notlar(son=20, q="", uzunluk=400, pk=None):
    c = _ac(DEPOLAR["notlar"])
    sql = """SELECT n.Z_PK AS pk, n.ZTITLE1 AS baslik, n.ZSNIPPET AS ozet,
                    n.ZMODIFICATIONDATE1 AS degisti,
                    COALESCE(n.ZCREATIONDATE1, n.ZCREATIONDATE2, n.ZCREATIONDATE3, n.ZCREATIONDATE) AS olustu,
                    f.ZTITLE2 AS klasor, f.ZFOLDERTYPE AS klasor_turu,
                    n.ZISPASSWORDPROTECTED AS kilitli, d.ZDATA AS zdata
             FROM ZICCLOUDSYNCINGOBJECT n
             LEFT JOIN ZICCLOUDSYNCINGOBJECT f ON f.Z_PK = n.ZFOLDER
             LEFT JOIN ZICNOTEDATA d ON d.ZNOTE = n.Z_PK
             WHERE n.ZTITLE1 IS NOT NULL AND COALESCE(n.ZMARKEDFORDELETION, 0) = 0
               AND COALESCE(f.ZFOLDERTYPE, 0) <> 1"""
    arg = []
    if pk is not None:
        sql += " AND n.Z_PK = ?"
        arg.append(int(pk))
    if q:
        sql += " AND (n.ZTITLE1 LIKE ? OR n.ZSNIPPET LIKE ?)"
        arg += ["%" + q + "%"] * 2
    sql += " ORDER BY n.ZMODIFICATIONDATE1 DESC LIMIT ?"
    arg.append(SON_TAVAN if pk is not None else son)
    out = []
    for r in c.execute(sql, arg):
        govde = "" if r["kilitli"] else not_govdesi(r["zdata"])
        if pk is None and len(govde) > uzunluk:
            govde = govde[:uzunluk] + "…"
        out.append({"id": r["pk"], "baslik": r["baslik"], "klasor": r["klasor"],
                    "degisti": _tarih(r["degisti"]), "olustu": _tarih(r["olustu"]),
                    "kilitli": bool(r["kilitli"]), "govde": govde})
    c.close()
    return out


def mesajlar(son=30, kisi="", q="", kaynak=""):
    yedekten = kaynak == "yedek"
    c = _ac(yedek_db("mesajlar") if yedekten else DEPOLAR["mesajlar"])
    adlar = rehber_haritasi() if yedekten else {}
    sql = """SELECT m.ROWID AS id, m.date AS tarih, m.is_from_me AS benden, m.text AS text,
                    m.attributedBody AS ab, m.cache_has_attachments AS ekli,
                    h.id AS kimden, c.chat_identifier AS sohbet, c.display_name AS grup,
                    m.service AS servis
             FROM message m
             LEFT JOIN handle h ON h.ROWID = m.handle_id
             LEFT JOIN chat_message_join j ON j.message_id = m.ROWID
             LEFT JOIN chat c ON c.ROWID = j.chat_id
             WHERE m.item_type = 0 AND m.associated_message_type = 0"""
    arg = []
    kisi_ad = bool(kisi) and not re.sub(r"\D", "", kisi) and adlar   # ada göre süz (rehber gerek)
    if kisi and not kisi_ad:
        sql += " AND (h.id LIKE ? OR c.chat_identifier LIKE ? OR c.display_name LIKE ?)"
        arg += ["%" + kisi + "%"] * 3
    if q:
        sql += " AND m.text LIKE ?"
        arg.append("%" + q + "%")
    sql += " ORDER BY m.date DESC LIMIT ?"
    arg.append(son * 20 if kisi_ad else son)
    out = []
    for r in c.execute(sql, arg):
        metin = mesaj_metni(r["text"], r["ab"])
        if q and not r["text"] and q.lower() not in metin.lower():
            continue
        kimden = r["kimden"] or "?"
        sohbet = r["grup"] or r["sohbet"]
        if adlar:
            kimden = adlar.get(_tel_anahtar(kimden), kimden)
            if sohbet and not r["grup"]:
                sohbet = adlar.get(_tel_anahtar(sohbet), sohbet)
        if kisi_ad and kisi.lower() not in (kimden + " " + (sohbet or "")).lower():
            continue
        out.append({"id": r["id"], "tarih": _tarih(r["tarih"]), "benden": bool(r["benden"]),
                    "kimden": "ben" if r["benden"] else kimden,
                    "sohbet": sohbet, "servis": r["servis"],
                    "ekli": bool(r["ekli"]), "metin": metin})
        if len(out) >= son:
            break
    c.close()
    return out


def fotograflar(son=20, gun=None):
    c = _ac(DEPOLAR["fotograflar"])
    sql = """SELECT a.ZUUID AS uuid, a.ZFILENAME AS dosya, a.ZDATECREATED AS tarih,
                    a.ZLATITUDE AS lat, a.ZLONGITUDE AS lon, a.ZWIDTH AS w, a.ZHEIGHT AS h,
                    a.ZKIND AS tur, a.ZFAVORITE AS favori, a.ZDIRECTORY AS dizin,
                    aa.ZORIGINALFILENAME AS ozgun_ad, aa.ZTITLE AS baslik
             FROM ZASSET a
             LEFT JOIN ZADDITIONALASSETATTRIBUTES aa ON aa.ZASSET = a.Z_PK
             WHERE COALESCE(a.ZTRASHEDSTATE, 0) = 0 AND COALESCE(a.ZHIDDEN, 0) = 0"""
    arg = []
    if gun:
        sql += " AND a.ZDATECREATED >= ?"
        arg.append(time.time() - CORE_DATA_EPOCH - int(gun) * 86400)
    sql += " ORDER BY a.ZDATECREATED DESC LIMIT ?"
    arg.append(son)
    out = []
    for r in c.execute(sql, arg):
        konum = None
        if r["lat"] is not None and r["lat"] > -90 and r["lon"] is not None and r["lon"] > -180:
            konum = [round(r["lat"], 5), round(r["lon"], 5)]
        out.append({"uuid": r["uuid"], "tarih": _tarih(r["tarih"]), "dosya": r["ozgun_ad"] or r["dosya"],
                    "tur": "video" if r["tur"] == 1 else "foto", "boyut": [r["w"], r["h"]],
                    "konum": konum, "favori": bool(r["favori"]), "baslik": r["baslik"],
                    "asil_var": os.path.exists(_asil_yol(r["uuid"], r["dosya"], r["dizin"]))})
    c.close()
    return out


def _asil_yol(uuid, dosya, dizin):
    return os.path.join(FOTO_KUTUPHANE, "originals", str(dizin or (uuid or "0")[0]), dosya or "")


def foto(uuid, boy="orta"):
    """Tek fotoğraf → JPEG base64. asil: orijinal dosya; orta: ≤1280px; kucuk: ≤400px.
    Orijinal yoksa (iCloud 'depolamayı optimize et') türev dizininden en büyük JPEG."""
    if not re.fullmatch(r"[0-9A-Fa-f-]{36}", uuid or ""):
        return {"hata": "uuid biçimi"}
    c = _ac(DEPOLAR["fotograflar"])
    r = c.execute("SELECT ZFILENAME AS dosya, ZDIRECTORY AS dizin FROM ZASSET WHERE ZUUID = ?",
                  (uuid,)).fetchone()
    c.close()
    if not r:
        return {"hata": "yok"}
    kaynak = _asil_yol(uuid, r["dosya"], r["dizin"])
    kaynak_turu = "asil"
    if not os.path.exists(kaynak):
        turev_dizin = os.path.join(FOTO_KUTUPHANE, "resources", "derivatives", uuid[0])
        adaylar = []
        if os.path.isdir(turev_dizin):
            for ad in os.listdir(turev_dizin):
                if ad.startswith(uuid) and ad.lower().endswith((".jpeg", ".jpg", ".heic")):
                    p = os.path.join(turev_dizin, ad)
                    adaylar.append((os.path.getsize(p), p))
        if not adaylar:
            return {"hata": "dosya yok (orijinal iCloud'da, türev de yok)"}
        kaynak = max(adaylar)[1]
        kaynak_turu = "turev"
    hedef = "/tmp/ghost_foto_%s.jpg" % uuid
    en = {"kucuk": "400", "orta": "1280"}.get(boy)
    cmd = ["/usr/bin/sips", "-s", "format", "jpeg", "-s", "formatOptions", "80"]
    if en:
        cmd += ["-Z", en]
    cmd += [kaynak, "--out", hedef]
    r2 = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r2.returncode != 0 or not os.path.exists(hedef):
        return {"hata": "sips: " + (r2.stderr or "").strip()[:200]}
    with open(hedef, "rb") as f:
        ham = f.read()
    os.unlink(hedef)
    return {"uuid": uuid, "kaynak": kaynak_turu, "bayt": len(ham),
            "jpeg_b64": base64.b64encode(ham).decode()}


# ---------------- Dosyalar (iCloud Drive) ----------------

def dosya_kap_adi(ad):
    """Kök kap adını insanca çevir: com~apple~CloudDocs → iCloud Drive, iCloud~md~obsidian → obsidian."""
    if ad == "com~apple~CloudDocs":
        return "iCloud Drive"
    if ad.startswith("iCloud~"):
        return ad[len("iCloud~"):].split("~")[-1] + " (uygulama)"
    return ad


def dosya_yolu(rel, kok=None):
    """Göreli yolu kök altına çöz; kök dışına çıkan (.. ya da symlink) ValueError."""
    kok = os.path.realpath(kok or DOSYA_KOK)
    rel = (rel or "").strip().strip("/")
    if rel in ("", "."):
        return kok
    tam = os.path.realpath(os.path.join(kok, rel))
    if tam != kok and not tam.startswith(kok + os.sep):
        raise ValueError("yol kök dışında")
    if KAPSAM_DISI_KAP and tam != kok and os.path.relpath(tam, kok).split(os.sep)[0] in KAPSAM_DISI_KAP:
        raise ValueError("kapsam dışı kap")
    return tam


def _yer_tutucu(ad):
    """'.Rapor.pdf.icloud' → 'Rapor.pdf' (iCloud'da duran, diskte olmayan dosya); değilse None."""
    if ad.startswith(".") and ad.endswith(".icloud") and len(ad) > 8:
        return ad[1:-7]
    return None


def _girdi(dizin, ad, kok):
    gercek = _yer_tutucu(ad)
    bulutta = gercek is not None
    if not bulutta:
        if ad.startswith(".") or ad == "Icon\r":
            return None
        gercek = ad
    p = os.path.join(dizin, ad)
    try:
        st = os.stat(p)
    except OSError:
        return None
    klasor = stat.S_ISDIR(st.st_mode) and not bulutta
    bayt = None if klasor else st.st_size
    if bulutta:
        try:
            with open(p, "rb") as f:
                bayt = int(plistlib.load(f).get("NSURLFileSizeKey", 0)) or None
        except Exception:
            bayt = None
    rel = os.path.relpath(os.path.join(dizin, gercek), kok)
    g = {"ad": gercek, "yol": rel, "klasor": klasor, "bayt": bayt,
         "degisti": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)), "bulutta": bulutta,
         "_t": st.st_mtime}
    if dizin == kok:
        g["kap"] = dosya_kap_adi(gercek)
    return g


def dosyalar(yol="", q="", son=100, derin=False, sirala="ad", kok=None):
    kok = os.path.realpath(kok or DOSYA_KOK)
    baslangic = dosya_yolu(yol, kok)
    if not os.path.isdir(baslangic):
        return {"hata": "klasör değil ya da yok"}
    q = (q or "").lower()
    out, ziyaret, t0, kesildi = [], 0, time.time(), False
    for dizin, altlar, dosyalar_ in os.walk(baslangic):
        altlar[:] = sorted(a for a in altlar if not a.startswith("."))
        for ad in sorted(altlar + dosyalar_):
            ziyaret += 1
            g = _girdi(dizin, ad, kok)
            if g is None or (dizin == kok and g["ad"] in KAPSAM_DISI_KAP):
                continue
            if q and q not in g["ad"].lower():
                continue
            out.append(g)
            if not (derin or q) and len(out) >= son:
                break
        if not derin and not q:
            break
        if ziyaret >= DOSYA_GEZI_TAVAN or time.time() - t0 > DOSYA_GEZI_SANIYE:
            kesildi = True
            break
    if sirala == "tarih":
        out.sort(key=lambda g: -g["_t"])
    else:
        out.sort(key=lambda g: (not g["klasor"], g["ad"].lower()))
    kesildi = kesildi or len(out) > son
    out = out[:son]
    for g in out:
        g.pop("_t", None)
    return {"yol": os.path.relpath(baslangic, kok) if baslangic != kok else "", "girdi": out,
            "kesildi": kesildi, "ziyaret": ziyaret}


SF_DATALESS = 0x40000000  # <sys/stat.h>: içeriği iCloud'da, diskte yalnız kaydı olan dosya


def _bosaltilmis(tam):
    """macOS 'dataless' dosya mı? (yer tutucu .X.icloud DEĞİL; ls'te normal görünür, boyutu doğru,
    ama open()/read() EDEADLK (errno 11) verir — 2026-09-30'da 19 MB WhatsApp zip'iyle yaşandı)."""
    try:
        return bool(getattr(os.stat(tam), "st_flags", 0) & SF_DATALESS)
    except OSError:
        return False


def _indir(tam, bekle=45):
    """iCloud yer tutucusunu / boşaltılmış dosyayı gerçek dosyaya çevir (brctl download), gelmesini bekle."""
    subprocess.run(["/usr/bin/brctl", "download", tam], capture_output=True, text=True, timeout=30)
    t0 = time.time()
    while time.time() - t0 < bekle:
        if os.path.isfile(tam) and not _bosaltilmis(tam):
            return True
        time.sleep(1)
    return False


def _materyalize_ac():
    """Sürecin dataless dosyaları okurken içeriği indirmesine izin ver (setiopolicy_np).
    Kapalıysa çekirdek okumaya EDEADLK döner. Başarısız olursa sessiz: _indir yedeği var."""
    try:
        import ctypes
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        # IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES=17, IOPOL_SCOPE_PROCESS=0, ..._ON=1
        return libc.setiopolicy_np(17, 0, 1) == 0
    except Exception:
        return False


def dosya(yol, kok=None):
    kok = os.path.realpath(kok or DOSYA_KOK)
    tam = dosya_yolu(yol, kok)
    if tam == kok:
        return {"hata": "dosya yolu ver"}
    if os.path.isdir(tam):
        return {"hata": "klasör (paket olabilir: .pages/.numbers/.key); /dosyalar ile listele"}
    if not os.path.exists(tam):
        yt = os.path.join(os.path.dirname(tam), "." + os.path.basename(tam) + ".icloud")
        if not os.path.exists(yt):
            return {"hata": "yok"}
        if not _indir(tam):
            return {"hata": "iCloud'dan inmedi (ağ? büyük dosya?) — tekrar dene"}
    bayt = os.path.getsize(tam)
    if bayt > DOSYA_TAVAN:
        return {"hata": "dosya %d bayt, tavan %d" % (bayt, DOSYA_TAVAN), "bayt": bayt}
    if _bosaltilmis(tam) and not _indir(tam):
        return {"hata": "iCloud'dan inmedi (içerik bulutta; ağ? büyük dosya?) — tekrar dene"}
    try:
        with open(tam, "rb") as f:
            ham = f.read()
    except OSError as e:
        if e.errno != 11:  # EDEADLK: dataless dosya, materyalizasyon kapalı
            raise
        if not _indir(tam):
            return {"hata": "iCloud'dan inmedi (EDEADLK; içerik bulutta) — tekrar dene"}
        with open(tam, "rb") as f:
            ham = f.read()
    uz = os.path.splitext(tam)[1].lower()
    out = {"yol": os.path.relpath(tam, kok), "bayt": bayt, "uzanti": uz,
           "degisti": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(tam)))}
    metin = None
    if uz in METIN_UZANTI or (uz == "" and b"\x00" not in ham[:4096]):
        try:
            metin = ham.decode("utf-8")
        except UnicodeDecodeError:
            metin = None
    if metin is not None:
        out.update(tur="metin", metin=metin)
    else:
        out.update(tur="ikili", b64=base64.b64encode(ham).decode())
    return out


# ---------------- WhatsApp (Mac uygulaması, 2026-09-28) ----------------
# Sahip Mac'e WhatsApp kurup telefonuna bağladı; ChatStorage.sqlite düz SQLite (iOS şeması).

WA_TUR = {0: "metin", 1: "görsel", 2: "video", 3: "ses", 4: "kişi", 5: "konum", 6: "sistem",
          7: "bağlantı", 8: "belge", 10: "arama", 11: "gif", 14: "silindi", 15: "çıkartma",
          38: "anket"}


def _wa_db(kaynak):
    return yedek_db("whatsapp") if kaynak == "yedek" else DEPOLAR["whatsapp"]


def wa_sohbetler(son=50, kaynak=""):
    c = _ac(_wa_db(kaynak))
    out = []
    for r in c.execute("""SELECT Z_PK AS pk, ZPARTNERNAME AS ad, ZCONTACTJID AS jid, ZSESSIONTYPE AS tur,
                                 ZUNREADCOUNT AS okunmamis, ZMESSAGECOUNTER AS sayi, ZARCHIVED AS arsiv,
                                 ZLASTMESSAGEDATE AS son, ZLASTMESSAGETEXT AS son_metin
                          FROM ZWACHATSESSION
                          WHERE COALESCE(ZREMOVED, 0) = 0 AND COALESCE(ZHIDDEN, 0) = 0
                            AND COALESCE(ZCONTACTJID, '') NOT LIKE 'status@%'
                          ORDER BY CASE WHEN ZLASTMESSAGEDATE > 100000000000 THEN ZLASTMESSAGEDATE / 1000000000.0
                                        ELSE COALESCE(ZLASTMESSAGEDATE, 0) END DESC LIMIT ?""", (son,)):
        cd = float(r["son"] or 0)
        cd = cd / 1e9 if cd > 1e11 else cd   # bozuk kayıtlar nanosaniye ölçeğinde geliyor
        out.append({"id": r["pk"], "ad": r["ad"], "jid": r["jid"], "grup": r["tur"] == 1,
                    "okunmamis": r["okunmamis"] or 0, "mesaj": r["sayi"] or 0, "arsiv": bool(r["arsiv"]),
                    "son": _tarih(cd) if cd > 31536000 else None, "son_metin": _wa_son_metin(c, r["pk"], r["son_metin"])})
    c.close()
    return out


def _wa_son_metin(c, pk, ham):
    """Sohbetin son mesaj önizlemesi. ZLASTMESSAGETEXT Mac sürümünde protobuf BLOB geliyor (JSON'a base64
    çöpü düşüyordu, 2026-09-28); metni ZWAMESSAGE'dan al, medyaysa türünü yaz, en son ham metne düş."""
    r = c.execute("SELECT ZTEXT, ZMESSAGETYPE FROM ZWAMESSAGE WHERE ZCHATSESSION = ? "
                  "ORDER BY ZMESSAGEDATE DESC LIMIT 1", (pk,)).fetchone()
    if r is not None:
        if isinstance(r[0], str) and r[0].strip():
            return r[0]
        if r[1] is not None:
            return "[%s]" % WA_TUR.get(r[1], "tür%s" % r[1])
    return ham if isinstance(ham, str) else ""


def _cd_sn(iso):
    """'2026-07-02' ya da '2026-07-02 14:30' (yerel) → Core Data saniyesi; boş/bozuk → None."""
    if not iso:
        return None
    for bicim in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return time.mktime(time.strptime(iso.strip(), bicim)) - CORE_DATA_EPOCH
        except ValueError:
            continue
    return None


def wa_mesajlar(sohbet="", q="", son=30, bas="", bit="", kaynak=""):
    c = _ac(_wa_db(kaynak))
    sql = """SELECT m.Z_PK AS id, m.ZMESSAGEDATE AS tarih, m.ZISFROMME AS benden, m.ZTEXT AS metin,
                    m.ZMESSAGETYPE AS tur, m.ZPUSHNAME AS gonderen, m.ZFROMJID AS kimden,
                    s.ZPARTNERNAME AS sohbet, s.Z_PK AS sohbet_id, s.ZSESSIONTYPE AS stur,
                    md.ZMEDIALOCALPATH AS medya, md.ZTITLE AS baslik, md.ZFILESIZE AS medya_bayt,
                    COALESCE(md.ZTHUMBNAILLOCALPATH, md.ZXMPPTHUMBPATH) AS onizleme
             FROM ZWAMESSAGE m
             JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION
             LEFT JOIN ZWAMEDIAITEM md ON md.Z_PK = m.ZMEDIAITEM
             WHERE 1=1"""
    arg = []
    if sohbet:
        if str(sohbet).isdigit():
            sql += " AND s.Z_PK = ?"; arg.append(int(sohbet))
        else:
            sql += " AND s.ZPARTNERNAME LIKE ?"; arg.append("%" + sohbet + "%")
    if q:
        sql += " AND m.ZTEXT LIKE ?"; arg.append("%" + q + "%")
    b1, b2 = _cd_sn(bas), _cd_sn(bit)
    if b1 is not None:
        sql += " AND m.ZMESSAGEDATE >= ?"; arg.append(b1)
    if b2 is not None:
        sql += " AND m.ZMESSAGEDATE < ?"; arg.append(b2 + (86400 if len(bit.strip()) <= 10 else 0))
    sql += " ORDER BY m.ZMESSAGEDATE DESC LIMIT ?"; arg.append(son)
    out = []
    for r in c.execute(sql, arg):
        if r["benden"]:
            kim = "ben"
        elif r["stur"] == 1:   # grup: gönderen üye
            kim = r["gonderen"] or (r["kimden"] or "").split("@")[0] or "?"
        else:                  # birebir: sohbetin adı (push name bozuk/base64 gelebiliyor)
            kim = r["sohbet"] or r["gonderen"] or (r["kimden"] or "").split("@")[0]
        out.append({"id": r["id"], "tarih": _tarih(r["tarih"]), "sohbet": r["sohbet"], "sohbet_id": r["sohbet_id"],
                    "grup": r["stur"] == 1, "kim": kim, "tur": WA_TUR.get(r["tur"], "tür%s" % r["tur"]),
                    "metin": r["metin"] or "", "medya": r["medya"], "baslik": r["baslik"],
                    # DB'de "Media/..." yazar, diskte "Message/Media/..." — /dosya?kok=whatsapp'a hazır ver
                    "onizleme": ("Message/" + r["onizleme"]) if (r["onizleme"] or "").startswith("Media/") else r["onizleme"]})
    c.close()
    return out


# ---------------- iPhone yedeği (idevicebackup2/Finder, 2026-09-28) ----------------
# Sahip kararı 2026-09-28 ("finderı bağla"): telefonun yerel yedeği ~/.ghost/yedek/<udid>/ altında.
# Yedek anlık görüntüdür (canlı değil); Manifest.db (düz SQLite) domain+relativePath → fileID (sha1),
# dosya <udid>/<fileID[:2]>/<fileID>. Veritabanları (+ -wal/-shm) _db/ altına kopyalanıp checkpoint
# edilir; kaynak yedek dosyasına yazılmaz. Aynı kararla Safari ve Mail kısıtı da kalktı.

YEDEK_KOK = os.path.join(HOME, ".ghost", "yedek")
YEDEK_ALAN = {   # ad → (domain, relativePath) — yedek içindeki veritabanları
    "mesajlar": ("HomeDomain", "Library/SMS/sms.db"),
    "aramalar": ("HomeDomain", "Library/CallHistoryDB/CallHistory.storedata"),
    "rehber": ("HomeDomain", "Library/AddressBook/AddressBook.sqlitedb"),
    "safari": ("HomeDomain", "Library/Safari/History.db"),
    "whatsapp": ("AppDomainGroup-group.net.whatsapp.WhatsApp.shared", "ChatStorage.sqlite"),
}
ARAMA_TUR = {1: "telefon", 8: "facetime video", 16: "facetime ses"}
# iOS 26 yedeğinde arama geçmişi ve Safari geçmişi YOK (iCloud'la eşitlenen veriyi telefon yedeğe
# koymuyor; canlı 2026-09-28: yalnız CallHistorySyncHelper.plist ve Safari çerezleri var). Aynı veri
# iCloud üzerinden Mac'in kendi veritabanlarında: oraya düşülür (kopya + checkpoint, kaynağa yazılmaz).
MAC_YEDEK_YOL = {
    "aramalar": os.path.join(HOME, "Library/Application Support/CallHistoryDB/CallHistory.storedata"),
    "safari": os.path.join(HOME, "Library/Safari/History.db"),
}


def _ac_manifest(dizin):
    """Manifest.db WAL kipinde gelir ve yanında -wal/-shm yok; salt okunur açılış 'unable to open'
    verir (2026-09-28 canlı). immutable=1: kilit/shm istemez, dosya değişmez varsayar."""
    return _ac(os.path.join(dizin, "Manifest.db"), dondurulmus=True)


def yedek_dizini(kok=None):
    """En taze Manifest.db'ye sahip yedek dizini (udid). Yoksa None."""
    kok = kok or YEDEK_KOK
    aday = []
    try:
        for ad in os.listdir(kok):
            m = os.path.join(kok, ad, "Manifest.db")
            if not ad.startswith("_") and os.path.isfile(m):
                aday.append((os.path.getmtime(m), os.path.join(kok, ad)))
    except OSError:
        return None
    return max(aday)[1] if aday else None


def yedek_dosya(domain, rel, dizin=None):
    """Manifest'ten (domain, relativePath) → yedekteki gerçek dosya yolu; yoksa None."""
    dizin = dizin or yedek_dizini()
    if not dizin:
        return None
    c = _ac_manifest(dizin)
    r = c.execute("SELECT fileID FROM Files WHERE domain = ? AND relativePath = ? AND flags = 1",
                  (domain, rel)).fetchone()
    c.close()
    if not r:
        return None
    p = os.path.join(dizin, r[0][:2], r[0])
    return p if os.path.isfile(p) else None


def yedek_db(ad, dizin=None):
    """Yedekteki veritabanını (+ wal/shm) _db/<ad>.sqlite'a kopyala (kaynak yeniyse), checkpoint et, yolu ver.
    Yedekte yoksa ve Mac'te iCloud kopyası varsa (MAC_YEDEK_YOL) oradan kopyalar (_db/<ad>_mac.sqlite)."""
    domain, rel = YEDEK_ALAN[ad]
    dizin = dizin or yedek_dizini()
    kaynak = yedek_dosya(domain, rel, dizin)
    if kaynak:
        ekler = {ek: yedek_dosya(domain, rel + ek, dizin) for ek in ("-wal", "-shm")}
        return _kopya_db(ad, kaynak, ekler)
    mac_yol = MAC_YEDEK_YOL.get(ad)
    if mac_yol and os.path.isfile(mac_yol):
        return _kopya_db(ad + "_mac", mac_yol, {ek: (mac_yol + ek if os.path.isfile(mac_yol + ek) else None)
                                               for ek in ("-wal", "-shm")})
    raise ValueError("yedekte yok: %s (yedek %s)%s" % (rel, "var" if dizin else "alınmamış",
                     "; Mac kopyası da yok: %s" % mac_yol if mac_yol else ""))


def _kopya_db(ad, kaynak, ekler):
    onbellek = os.path.join(YEDEK_KOK, "_db")
    os.makedirs(onbellek, exist_ok=True)
    hedef = os.path.join(onbellek, ad + ".sqlite")
    en_yeni = max([os.path.getmtime(kaynak)] + [os.path.getmtime(k) for k in ekler.values() if k])
    if not os.path.exists(hedef) or os.path.getmtime(hedef) < en_yeni or os.path.getsize(hedef) == 0:
        import shutil
        for ek in ("", "-wal", "-shm"):
            k = kaynak if not ek else ekler.get(ek)
            if k:
                shutil.copyfile(k, hedef + ek)
            elif os.path.exists(hedef + ek):
                os.unlink(hedef + ek)
        try:
            c = sqlite3.connect(hedef, timeout=5)
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.execute("PRAGMA journal_mode=DELETE")
            c.close()
        except sqlite3.DatabaseError:
            pass
        os.utime(hedef, None)
    return hedef


def yedek_durum():
    dizin = yedek_dizini()
    if not dizin:
        return {"var": False, "kok": YEDEK_KOK}
    out = {"var": True, "udid": os.path.basename(dizin)}
    for ad, alan in (("Status.plist", ("SnapshotState", "Date", "IsFullBackup", "Version")),
                     ("Info.plist", ("Device Name", "Product Version", "Product Type", "Last Backup Date"))):
        try:
            with open(os.path.join(dizin, ad), "rb") as f:
                pl = plistlib.load(f)
            for k in alan:
                v = pl.get(k)
                if hasattr(v, "strftime"):   # plist tarihi UTC (saat dilimsiz) gelir → yerel saat
                    import calendar
                    v = time.strftime("%Y-%m-%d %H:%M", time.localtime(calendar.timegm(v.timetuple())))
                out[k.lower().replace(" ", "_")] = v
        except Exception:
            pass
    try:
        c = _ac_manifest(dizin)
        out["dosya_sayisi"] = c.execute("SELECT count(*) FROM Files WHERE flags = 1").fetchone()[0]
        c.close()
    except Exception as e:
        out["dosya_sayisi"] = "hata: %s" % e
    out["alanlar"] = {ad: (True if yedek_dosya(d, r, dizin) else
                           ("mac" if os.path.isfile(MAC_YEDEK_YOL.get(ad, "")) else False))
                      for ad, (d, r) in YEDEK_ALAN.items()}
    out["bitti"] = out.get("snapshotstate") == "finished"
    return out


def _tel_anahtar(s):
    """Telefon/e-posta → rehber eşleme anahtarı: rakamların son 10'u; e-postaysa küçük harf."""
    s = (s or "").strip()
    if "@" in s and not s.replace("+", "").isdigit():
        return s.lower()
    r = re.sub(r"\D", "", s)
    return r[-10:] if len(r) >= 10 else r


def rehber_haritasi(dizin=None):
    """Yedekteki rehber → {tel/eposta anahtarı: ad}. Yedek/rehber yoksa boş."""
    try:
        c = _ac(yedek_db("rehber", dizin))
    except Exception:
        return {}
    h = {}
    for r in c.execute("""SELECT p.First, p.Last, p.Organization, v.value FROM ABMultiValue v
                          JOIN ABPerson p ON p.ROWID = v.record_id WHERE v.property IN (3, 4)"""):
        ad = " ".join(x for x in (r[0], r[1]) if x) or (r[2] or "")
        k = _tel_anahtar(r[3])
        if ad and k and k not in h:
            h[k] = ad
    c.close()
    return h


def rehber(q="", son=50, dizin=None):
    c = _ac(yedek_db("rehber", dizin))
    sql = """SELECT p.ROWID AS id, p.First AS ad, p.Last AS soyad, p.Organization AS kurum,
                    group_concat(CASE WHEN v.property = 3 THEN v.value END, ' | ') AS tel,
                    group_concat(CASE WHEN v.property = 4 THEN v.value END, ' | ') AS eposta
             FROM ABPerson p LEFT JOIN ABMultiValue v ON v.record_id = p.ROWID AND v.property IN (3, 4)"""
    arg = []
    if q:
        sql += " WHERE p.First LIKE ? OR p.Last LIKE ? OR p.Organization LIKE ? OR v.value LIKE ?"
        arg += ["%" + q + "%"] * 4
    sql += " GROUP BY p.ROWID ORDER BY p.Last, p.First LIMIT ?"
    arg.append(son)
    out = [{"id": r["id"], "ad": " ".join(x for x in (r["ad"], r["soyad"]) if x) or (r["kurum"] or "?"),
            "kurum": r["kurum"], "tel": [t for t in (r["tel"] or "").split(" | ") if t],
            "eposta": [e for e in (r["eposta"] or "").split(" | ") if e]} for r in c.execute(sql, arg)]
    c.close()
    return out


def aramalar(son=30, kisi="", dizin=None):
    c = _ac(yedek_db("aramalar", dizin))
    adlar = rehber_haritasi(dizin)
    sql = """SELECT Z_PK AS id, ZADDRESS AS adres, ZDATE AS tarih, ZDURATION AS sure, ZORIGINATED AS benden,
                    ZANSWERED AS cevaplandi, ZCALLTYPE AS tur, ZSERVICE_PROVIDER AS servis
             FROM ZCALLRECORD"""
    arg = []
    rakam = re.sub(r"\D", "", kisi or "")
    if rakam:                       # numara: SQL'de süz (son 7 hane)
        sql += " WHERE ZADDRESS LIKE ?"
        arg.append("%" + rakam[-7:] + "%")
    sql += " ORDER BY ZDATE DESC LIMIT ?"
    arg.append(son * 20 if kisi and not rakam else son)   # ad: rehber çözümünden sonra süz
    out = []
    for r in c.execute(sql, arg):
        adres = r["adres"] if isinstance(r["adres"], str) else (bytes(r["adres"]).decode("utf-8", "replace") if r["adres"] else "")
        ad = adlar.get(_tel_anahtar(adres))
        if kisi and not rakam and kisi.lower() not in (ad or "").lower():
            continue
        servis = (r["servis"] or "").lower()
        out.append({"id": r["id"], "tarih": _tarih(r["tarih"]), "kim": ad or adres, "numara": adres,
                    "benden": bool(r["benden"]), "cevaplandi": bool(r["cevaplandi"]),
                    "sure_sn": int(r["sure"] or 0),
                    "tur": "whatsapp" if "whatsapp" in servis else ARAMA_TUR.get(r["tur"], "tür%s" % r["tur"])})
        if len(out) >= son:
            break
    c.close()
    return out


def safari(son=30, q="", dizin=None):
    c = _ac(yedek_db("safari", dizin))
    sql = """SELECT v.id AS id, i.url AS url, v.title AS baslik, v.visit_time AS tarih, i.visit_count AS ziyaret
             FROM history_visits v JOIN history_items i ON i.id = v.history_item"""
    arg = []
    if q:
        sql += " WHERE i.url LIKE ? OR v.title LIKE ?"
        arg += ["%" + q + "%"] * 2
    sql += " ORDER BY v.visit_time DESC LIMIT ?"
    arg.append(son)
    out = [{"id": r["id"], "tarih": _tarih(r["tarih"]), "baslik": r["baslik"], "url": r["url"],
            "ziyaret": r["ziyaret"]} for r in c.execute(sql, arg)]
    c.close()
    return out


def yedek_dosya_oku(yol, dizin=None):
    """/dosya?kok=yedek&yol=<domain>/<relativePath> → yedekteki dosya (metin ya da b64)."""
    yol = (yol or "").strip("/")
    if "/" not in yol:
        return {"hata": "yol '<domain>/<relativePath>' biçiminde olmalı (ör. HomeDomain/Library/SMS/sms.db)"}
    domain, rel = yol.split("/", 1)
    tam = yedek_dosya(domain, rel, dizin)
    if not tam:
        return {"hata": "yedekte yok"}
    bayt = os.path.getsize(tam)
    if bayt > DOSYA_TAVAN:
        return {"hata": "dosya %d bayt, tavan %d" % (bayt, DOSYA_TAVAN), "bayt": bayt}
    with open(tam, "rb") as f:
        ham = f.read()
    uz = os.path.splitext(rel)[1].lower()
    out = {"yol": yol, "bayt": bayt, "uzanti": uz,
           "degisti": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(tam)))}
    if uz in METIN_UZANTI:
        try:
            out.update(tur="metin", metin=ham.decode("utf-8")); return out
        except UnicodeDecodeError:
            pass
    out.update(tur="ikili", b64=base64.b64encode(ham).decode())
    return out


def yedek_liste(domain="", q="", son=100, dizin=None):
    """Manifest'te dosya ara: domain süzgeci + ad parçası. Çıktı domain/relativePath satırları."""
    dizin = dizin or yedek_dizini()
    if not dizin:
        return {"hata": "yedek yok"}
    c = _ac_manifest(dizin)
    sql = "SELECT domain, relativePath FROM Files WHERE flags = 1"
    arg = []
    if domain:
        sql += " AND domain LIKE ?"; arg.append("%" + domain + "%")
    if q:
        sql += " AND relativePath LIKE ?"; arg.append("%" + q + "%")
    sql += " ORDER BY domain, relativePath LIMIT ?"; arg.append(son)
    out = [r[0] + "/" + r[1] for r in c.execute(sql, arg)]
    alanlar = None
    if not domain and not q:
        alanlar = [r[0] for r in c.execute("SELECT DISTINCT domain FROM Files ORDER BY domain")]
    c.close()
    return {"girdi": out, "domainler": alanlar}


# ---------------- HTTP ----------------

# ---- Mac Mail (2026-09-28, Sahip "gmaili de okuyabil") ----
# Mail.app'in tüm hesapları (Gmail, Hotmail, iCloud...) tek dizinde: ~/Library/Mail/V10.
# Zarf dizini "MailData/Envelope Index" (sqlite, WAL) konu/gönderen/tarih/kutu tutar;
# gövde .emlx dosyalarında (ilk satır bayt sayısı + RFC822 + plist kuyruk). Notes gibi
# doğrudan salt-okunur açılır (kopya yok: dosya yüzlerce MB olabilir).
MAIL_KOK = os.path.join(HOME, "Library/Mail")
MAIL_GOVDE_TAVAN = 20000


def mail_dizini(kok=None):
    kok = kok or MAIL_KOK
    for v in ("V10", "V9", "V8", "V7", "V6"):
        p = os.path.join(kok, v)
        if os.path.isdir(p):
            return p
    raise ValueError("Mac Mail dizini yok: %s (Mail.app hiç açılmamış ya da izin yok)" % kok)


def _mail_ac(kok=None):
    yol = os.path.join(mail_dizini(kok), "MailData", "Envelope Index")
    if not os.path.isfile(yol):
        # Teşhis: Mail.app'te hesap yoksa V10 boş kalır. Ne var ne yok, hata metnine yaz.
        kok = kok or MAIL_KOK
        agac = {}
        for a in sorted(os.listdir(kok))[:20]:
            t = os.path.join(kok, a)
            agac[a] = sorted(os.listdir(t))[:20] if os.path.isdir(t) else "dosya"
        raise ValueError("Envelope Index yok: %s — Mail.app'te hesap tanımlı değil olabilir; dizin: %s" % (yol, agac))
    try:
        c = _ac(yol)
        c.execute("select 1 from messages limit 1").fetchone()
        return c
    except sqlite3.OperationalError:
        # WAL/shm kilidi: kopyalayıp dondurulmuş aç (Manifest.db ile aynı çare)
        return _ac(_kopya_db("mail", yol, {ek: (yol + ek if os.path.isfile(yol + ek) else None)
                                          for ek in ("-wal", "-shm")}))


def _kolonlar(c, tablo):
    try:
        return [r[1] for r in c.execute("pragma table_info('%s')" % tablo)]
    except sqlite3.Error:
        return []


def _kutu_coz(url):
    """imap://owner%40example.com@imap.gmail.com/INBOX → ("owner@example.com@imap.gmail.com", "INBOX")."""
    if not url:
        return "", ""
    u = urllib.parse.urlsplit(url)
    hesap = urllib.parse.unquote(u.netloc)
    kutu = urllib.parse.unquote(u.path).strip("/")
    if u.scheme in ("local", "file") and not hesap:
        hesap = "yerel"
    return hesap, kutu


def _mail_tarih(t):
    if t is None:
        return None
    try:
        t = float(t)
    except (TypeError, ValueError):
        return None
    if t > 1e11:   # bazı sürümler nanosaniye
        t /= 1e9
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def _mail_sorgu(c):
    """Sürüme göre değişen kolonları tolere eden temel SELECT + kolon adları."""
    mk = _kolonlar(c, "messages")
    tarih = "date_received" if "date_received" in mk else ("date_sent" if "date_sent" in mk else "ROWID")
    ozet = "m.snippet" if "snippet" in mk else ("m.summary" if "summary" in mk else "NULL")
    silinmis = " AND m.deleted = 0" if "deleted" in mk else ""
    ad = "a.comment" if "comment" in _kolonlar(c, "addresses") else "NULL"
    sql = ("SELECT m.ROWID AS id, s.subject AS konu, a.address AS kimden_adres, %s AS kimden_ad, "
           "m.%s AS tarih, %s AS okundu, %s AS bayrak, %s AS bayt, mb.url AS kutu_url, %s AS ozet "
           "FROM messages m LEFT JOIN subjects s ON s.ROWID = m.subject "
           "LEFT JOIN addresses a ON a.ROWID = m.sender "
           "LEFT JOIN mailboxes mb ON mb.ROWID = m.mailbox WHERE 1=1%s"
           % (ad, tarih, "m.read" if "read" in mk else "NULL", "m.flagged" if "flagged" in mk else "NULL",
              "m.size" if "size" in mk else "NULL", ozet, silinmis))
    return sql, tarih, ozet, ad


def _mail_satir(r):
    hesap, kutu = _kutu_coz(r["kutu_url"])
    kimden = r["kimden_ad"] or r["kimden_adres"] or ""
    if r["kimden_ad"] and r["kimden_adres"] and r["kimden_ad"] != r["kimden_adres"]:
        kimden = "%s <%s>" % (r["kimden_ad"], r["kimden_adres"])
    return {"id": r["id"], "tarih": _mail_tarih(r["tarih"]), "kimden": kimden, "konu": r["konu"] or "",
            "hesap": hesap, "kutu": kutu, "okundu": bool(r["okundu"]) if r["okundu"] is not None else None,
            "bayrak": bool(r["bayrak"]) if r["bayrak"] is not None else None, "bayt": r["bayt"],
            "ozet": (r["ozet"] or "")[:200] or None}


def _zaman_esik(m):
    """'YYYY-MM-DD[ HH:MM]' (yerel) → unix saniye; boş/bozuksa None."""
    m = (m or "").strip()
    for bicim in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return time.mktime(time.strptime(m, bicim))
        except ValueError:
            pass
    return None


def mail_liste(son=30, q="", kimden="", hesap="", kutu="", bas="", bit="", kok=None):
    c = _mail_ac(kok)
    sql, tarih, ozet, ad = _mail_sorgu(c)
    arg = []
    if q:
        sql += " AND (s.subject LIKE ? OR a.address LIKE ? OR %s LIKE ? OR %s LIKE ?)" % (ad, ozet)
        arg += ["%" + q + "%"] * 4
    if kimden:
        sql += " AND (a.address LIKE ? OR %s LIKE ?)" % ad
        arg += ["%" + kimden + "%"] * 2
    if hesap:
        sql += " AND mb.url LIKE ?"
        arg.append("%" + hesap.replace("@", "%40") + "%")
    if kutu:
        sql += " AND mb.url LIKE ?"
        arg.append("%/" + kutu + "%")
    e = _zaman_esik(bas)
    if e is not None:
        sql += " AND m.%s >= ?" % tarih; arg.append(e)
    e = _zaman_esik(bit)
    if e is not None:
        sql += " AND m.%s <= ?" % tarih; arg.append(e + (86399 if len((bit or "").strip()) <= 10 else 0))
    sql += " ORDER BY m.%s DESC LIMIT ?" % tarih
    arg.append(son)
    out = [_mail_satir(r) for r in c.execute(sql, arg)]
    c.close()
    return out


def mail_hesaplar(kok=None):
    """Kutular (url) → hesap/kutu ve mesaj sayısı; Accounts.plist varsa hesap adları da."""
    c = _mail_ac(kok)
    say = {}
    for r in c.execute("SELECT mb.url AS url, COUNT(m.ROWID) AS n FROM mailboxes mb "
                       "LEFT JOIN messages m ON m.mailbox = mb.ROWID GROUP BY mb.url"):
        h, k = _kutu_coz(r["url"])
        say.setdefault(h, {"hesap": h, "toplam": 0, "kutular": {}})
        say[h]["toplam"] += r["n"]
        if r["n"]:
            say[h]["kutular"][k] = r["n"]
    c.close()
    adlar = []
    p = os.path.join(mail_dizini(kok), "MailData", "Accounts.plist")
    if os.path.isfile(p):
        try:
            with open(p, "rb") as f:
                for a in (plistlib.load(f).get("MailAccounts") or []):
                    adlar.append({"ad": a.get("AccountName"), "adresler": a.get("EmailAddresses") or [],
                                  "tur": a.get("AccountType") or a.get("TypeName"), "yol": a.get("AccountPath")})
        except Exception as e:  # noqa: BLE001
            adlar.append({"hata": "Accounts.plist: %s" % e})
    return {"hesaplar": sorted(say.values(), key=lambda x: -x["toplam"]), "tanimlar": adlar}


def _emlx_bul(mid, kok=None):
    """<id>.emlx ya da <id>.partial.emlx dosyasını bul. Önce Apple'ın dizin kuralı
    (son 3 basamak atılır, kalan basamaklar tersten klasör olur), sonra glob."""
    import glob
    d = mail_dizini(kok)
    s = str(int(mid))
    adaylar = []
    on = s[:-3]
    for dizin_parcasi in ({"/".join(reversed(on)), "/".join(on)} if on else {""}):
        for ek in (".emlx", ".partial.emlx"):
            adaylar.append(os.path.join(d, "*", "*.mbox", "*", "Data", dizin_parcasi, "Messages", s + ek))
    for kalip in adaylar:
        for yol in glob.glob(kalip):
            return yol
    for kalip in (os.path.join(d, "*", "**", "Messages", s + ".emlx"),
                  os.path.join(d, "*", "**", "Messages", s + ".partial.emlx")):
        for yol in glob.glob(kalip, recursive=True):
            return yol
    return None


def _baslik(m, ad):
    from email.header import decode_header, make_header
    v = m.get(ad, "")
    try:
        return str(make_header(decode_header(v))) if v else ""
    except Exception:  # noqa: BLE001
        return v


def _mail_metin(m):
    """MIME → düz metin (text/plain öncelikli, yoksa HTML soyulur) + ek adları."""
    plains, htmls, ekler = [], [], []
    for part in m.walk():
        ct = part.get_content_type()
        if part.get_filename():
            ekler.append(part.get_filename()); continue
        if part.get_content_maintype() == "multipart":
            continue
        try:
            govde = part.get_payload(decode=True)
            metin = govde.decode(part.get_content_charset() or "utf-8", "replace") if govde else ""
        except Exception:  # noqa: BLE001
            continue
        if ct == "text/plain":
            plains.append(metin)
        elif ct == "text/html":
            htmls.append(metin)
    if plains:
        metin = "\n".join(plains)
    else:
        h = "\n".join(htmls)
        h = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", h)
        h = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>|</h\d>|</blockquote>", "\n", h)
        h = re.sub(r"(?i)</t[dh]>", " ", h)
        h = re.sub(r"<[^>]+>", "", h)
        import html as _html
        metin = _html.unescape(h)
    metin = re.sub(r"[ \t\r\f\v]+", " ", metin)
    metin = "\n".join(l.strip() for l in metin.splitlines())
    metin = re.sub(r"\n{3,}", "\n\n", metin).strip()
    return metin, ekler


def mail_oku(mid, uzunluk=4000, kok=None):
    try:
        mid = int(mid)
    except (TypeError, ValueError):
        return {"hata": "id sayı olmalı"}
    yol = _emlx_bul(mid, kok)
    if not yol:
        return {"hata": "emlx bulunamadı (mail Mac'e inmemiş olabilir; Mail.app'te açınca iner)", "id": mid}
    with open(yol, "rb") as f:
        ham = f.read()
    ilk, _, geri = ham.partition(b"\n")
    try:
        n = int(ilk.strip()); mesaj = geri[:n]
    except ValueError:
        mesaj = geri
    import email as _email
    m = _email.message_from_bytes(mesaj)
    metin, ekler = _mail_metin(m)
    uzunluk = max(200, min(int(uzunluk or 4000), MAIL_GOVDE_TAVAN))
    return {"id": mid, "yol": os.path.relpath(yol, mail_dizini(kok)), "kismi": yol.endswith(".partial.emlx"),
            "kimden": _baslik(m, "From"), "kime": _baslik(m, "To"), "cc": _baslik(m, "Cc") or None,
            "tarih": _baslik(m, "Date"), "konu": _baslik(m, "Subject"), "ekler": ekler,
            "uzunluk": len(metin), "metin": metin[:uzunluk] + ("…" if len(metin) > uzunluk else "")}


class Uc(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        p = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if not token() or self.headers.get("X-Ghost-Token", "") != token():
            log("403 %s" % u.path)
            return self._json(403, {"hata": "token"})
        if not _kapi_acik():
            log("423 %s (YubiKey yok)" % u.path)
            return self._json(423, {"hata": "Mac kilitli: YubiKey takılı değil", "kapi": "kilitli"})
        t0 = time.time()
        try:
            if u.path == "/durum":
                d = {k: dene(v) for k, v in DEPOLAR.items()}
                d["mail_dizini"] = "ok" if _ls(os.path.join(HOME, "Library/Mail")) else "denied"
                try:
                    c = _mail_ac(); d["mail_sayi"] = c.execute("select count(*) from messages").fetchone()[0]; c.close()
                except Exception as e:  # noqa: BLE001
                    d["mail_sayi"] = "hata: %s" % e
                d["dosyalar"] = "ok" if _ls(DOSYA_KOK) else "denied"
                if d["dosyalar"] == "ok":
                    d["dosyalar_kap"] = [dosya_kap_adi(a) for a in sorted(os.listdir(DOSYA_KOK))
                                         if not a.startswith(".") and a not in KAPSAM_DISI_KAP]
                for ad, sql in (("notlar", "select count(*) from ZICCLOUDSYNCINGOBJECT where ZTITLE1 is not null"),
                                ("mesajlar", "select count(*) from message"),
                                ("fotograflar", "select count(*) from ZASSET"),
                                ("whatsapp", "select count(*) from ZWAMESSAGE")):
                    if d.get(ad) == "ok":
                        try:
                            c = _ac(DEPOLAR[ad]); d[ad + "_sayi"] = c.execute(sql).fetchone()[0]; c.close()
                        except Exception as e:
                            d[ad + "_sayi"] = "hata: %s" % e
                try:
                    y = yedek_durum()
                    d["yedek"] = ("%s · %s · %s" % (y.get("device_name"), y.get("date"), "bitti" if y["bitti"] else "SÜRÜYOR")
                                  if y["var"] else "yok")
                except Exception as e:
                    d["yedek"] = "hata: %s" % e
                out = d
            elif u.path == "/sema":
                yol = DEPOLAR.get(u.query)
                if not yol:
                    return self._json(404, {"hata": "depo yok"})
                c = _ac(yol)
                out = {t[0]: [r[1] for r in c.execute("pragma table_info('%s')" % t[0])]
                       for t in c.execute("select name from sqlite_master where type='table' order by name")}
                c.close()
            elif u.path == "/notlar":
                out = notlar(_sayi(p.get("son"), 1, SON_TAVAN, 20), p.get("q", ""),
                             _sayi(p.get("uzunluk"), 40, 20000, 400), p.get("id"))
            elif u.path == "/mesajlar":
                out = mesajlar(_sayi(p.get("son"), 1, SON_TAVAN, 30), p.get("kisi", ""), p.get("q", ""),
                               p.get("kaynak", ""))
            elif u.path == "/yedek":
                if p.get("liste"):
                    out = yedek_liste(p.get("domain", ""), p.get("q", ""), _sayi(p.get("son"), 1, 2000, 100))
                else:
                    out = yedek_durum()
            elif u.path == "/aramalar":
                out = aramalar(_sayi(p.get("son"), 1, SON_TAVAN, 30), p.get("kisi", ""))
            elif u.path == "/rehber":
                out = rehber(p.get("q", ""), _sayi(p.get("son"), 1, 2000, 50))
            elif u.path == "/safari":
                out = safari(_sayi(p.get("son"), 1, SON_TAVAN, 30), p.get("q", ""))
            elif u.path == "/fotograflar":
                out = fotograflar(_sayi(p.get("son"), 1, SON_TAVAN, 20), _sayi(p.get("gun"), 1, 3650, 0) or None)
            elif u.path == "/foto":
                out = foto(p.get("uuid", ""), p.get("boy", "orta"))
            elif u.path == "/dosyalar":
                out = dosyalar(p.get("yol", ""), p.get("q", ""), _sayi(p.get("son"), 1, 2000, 100),
                               p.get("derin") in ("1", "true"), p.get("sirala", "ad"), KOKLER.get(p.get("kok", "icloud")))
            elif u.path == "/dosya":
                if p.get("kok") == "yedek":
                    out = yedek_dosya_oku(p.get("yol", ""))
                else:
                    out = dosya(p.get("yol", ""), KOKLER.get(p.get("kok", "icloud")))
            elif u.path == "/mail":
                if p.get("id"):
                    out = mail_oku(p.get("id"), _sayi(p.get("uzunluk"), 200, MAIL_GOVDE_TAVAN, 4000))
                elif p.get("hesaplar"):
                    out = mail_hesaplar()
                else:
                    out = mail_liste(_sayi(p.get("son"), 1, SON_TAVAN, 30), p.get("q", ""), p.get("kimden", ""),
                                     p.get("hesap", ""), p.get("kutu", ""), p.get("bas", ""), p.get("bit", ""))
            elif u.path == "/whatsapp":
                if p.get("sohbet") or p.get("q") or p.get("bas") or p.get("bit"):
                    out = wa_mesajlar(p.get("sohbet", ""), p.get("q", ""), _sayi(p.get("son"), 1, SON_TAVAN, 30),
                                      p.get("bas", ""), p.get("bit", ""), p.get("kaynak", ""))
                else:
                    out = wa_sohbetler(_sayi(p.get("son"), 1, SON_TAVAN, 50), p.get("kaynak", ""))
            else:
                log("404 %s" % u.path)
                return self._json(404, {"hata": "yok"})
        except ValueError as e:
            log("400 %s %s: %r" % (u.path, u.query, e))
            return self._json(400, {"hata": str(e)})
        except Exception as e:
            log("500 %s %s: %r" % (u.path, u.query, e))
            return self._json(500, {"hata": str(e)})
        n = len(out) if isinstance(out, list) else (len(out.get("girdi", [])) if isinstance(out, dict) and "girdi" in out else 1)
        log("%s %s → %d kayıt %.2fs" % (u.path, u.query[:80], n, time.time() - t0))
        self._json(200, out)


if __name__ == "__main__":
    log("veri okuyucu dinliyor 127.0.0.1:%d (dataless materyalize: %s)" % (PORT, _materyalize_ac()))
    ThreadingHTTPServer(("127.0.0.1", PORT), Uc).serve_forever()
