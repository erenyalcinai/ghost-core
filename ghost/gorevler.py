"""Eller — Ghost'un sahibin telefonuna YAZDIĞI görev kuyruğu (Faz 6).

Faz 5 duyularla tek yön kurulmuştu: telefon anlatır, Ghost dinler. Bu modül ters
yönü açar. Ghost bir görev bırakır, uygulama onu iOS'un KENDİ sistemine yazar:
- `hatirlatici` → gerçek bir iOS Hatırlatıcı (alarmlı, Ghost kapalıyken bile çalar)
- `etkinlik`   → gerçek bir Takvim etkinliği
- `hatirlatici_sil` / `etkinlik_sil` → Ghost'un DAHA ÖNCE yazdığını kaldırır (2026-11-04, Sahip "sen
  hatırlatıcıları kaldıramıyor musun"). Yalnız bu kuyruktan `tamam` dönmüş, `yerel_id`'si bilinen kayıt
  silinebilir; uygulama kimlikle bulur (bulamazsa başlık+aynı dakika), sahibin elle kurduğuna dokunmaz.

Neden Telegram mesajı yetmiyor: Ghost'un kendi zamanlayıcısı (ghost/schedule.py)
mesaj atar — telefon sessizdeyse, uçak modundaysa ya da Sahip Telegram'a bakmıyorsa
kaybolur. iOS hatırlatıcısı işletim sisteminin alarmıdır; Ghost'a, VPS'e, ağa
bağlı değildir. İkisi farklı araçtır, biri ötekinin yerine geçmez.

Akış: `ekle()` → kuyrukta `bekliyor` → uygulama `GET /v1/gorevler` ile çeker,
cihazda uygular → `POST /v1/gorevler/<id>` ile sonucu yazar → Ghost sahibe
"kuruldu" der. Sonuç GERİ BİLDİRİLİR; "gönderdim" ile "telefona yazıldı" ayrı
şeylerdir ve karıştırılırsa Sahip olmayan bir hatırlatıcıya güvenir.

CEP CİHAZI (2026-11-23): aynı kuyruk, ikinci hedef. Küçük ekranlı el cihazı (cihaz kaydında tür `cep`) kendi
işlerini aynı uçtan uzun yoklamayla çeker; `hedef` sütunu iki dünyayı ayırır (telefon uygulaması bilmediği türü
hata diye tüketir → cep görevi telefona, telefon görevi cebe ASLA dönmez). Telefon yolundan farkları:
- EN ÇOK BİR KEZ teslim: görev verilirken `verildi` olur (`cep_al`); bağlantı o anda koparsa görev koşmaz,
  yeniden verilmez (kızılötesi iki kez gitmesin). Çağıran "verildi ama sonuç gelmedi"yi görür, kararı o verir.
- Son geçerlilik (`son`): süresi içinde alınmayan görev `suresi_doldu` olur (geç kalan "televizyonu aç" koşmaz).
- Sonuç çağırana döner (`cep_bekle` / `cep_sonuc`, büyük çıktı `cikti` sütununda); sahibine bildirim gitmez.
- Politika kodda (`cep_ekle`): FREEZE'de görev yok; özerk oturum yalnız okuma türlerini (`CEP_OKUMA`) ekler,
  sinyal/ses/ekran üreten araç yalnız sahibi isteyince.

LORA + KONUM (2026-11-23, firmware 1.4.0): `lora` (islem durum|dinle|gonder) ve `konum`. `lora durum|dinle` okumadır
(pasif alım); `lora gonder` SİNYAL üretir (ir_ham ile aynı sınıf) ve `konum` kişisel veridir → ikisi de yalnız sahibi
isteyince. Gönderim tavanları (alt bant, güç, tek gönderim süresi, saatlik görev döngüsü) cihazda gömülüdür; buradaki
kopya yalnız SIKILAŞTIRIR (erken ve açık hata + cihaz yeniden başlasa da süren sunucu sayımı), gevşetemez.
Konum sonucu en çok BİR kayıtta ve okunana dek durur (`cep_cikti_unut`); denetim kaydına koordinat girmez.

ADRESLEME (2026-11-24, Kokpit Watch hazırlığı): "cep" bir yetki kademesidir, tek bir cihaz değil — saat de cep türünde
eşlenir. Görev artık ALICISINI taşır (`alici` sütunu = cihaz kimliği) ve bir cihaz yalnız kendine adresli görevi alır.
Cihazın ne olduğu `ghost/cihaz_sinif.py` kaydındadır (cardputer | saat); hangi sınıf hangi türü anlar: `SINIF_TURLER`.
- `cep_ekle` alıcıyı kendisi seçer (`cep_alici_sec`): görevi anlayan sınıfta tek kayıtlı cihaz varsa o; birden çoksa
  yalnız biri yokluyorsa o, değilse açık hata (çağıran `alici=` / CLI `--cihaz` ile seçer).
- ADRESSİZ görev (alici NULL: yama öncesinden kalan kayıt ya da "eski" kipte eklenen) yalnız sınıfı o türü anlayan
  cihaza verilir; sınıfı kayıtsız cihaz adressiz görev ALAMAZ. Üç kip (`_sinif_durumu`): "kati" (kayıt en az bir kez
  yazılmış ya da birden çok cep cihazı eşli), "eski" (kayıt HİÇ yazılmamış ve en çok bir cep cihazı eşli: eski davranış,
  karışacak cihaz yok), "kapali" (kayıt ya da cihaz listesi okunamadı: adressiz görev kimseye verilmez, yeni görev
  kuyruklanmaz). İlke: bir cihaz anlamadığı görevi asla tüketmesin; emin değilken kapalı taraf.
- Yoklama damgası cihaz başına (`cep_yoklama(cihaz_id)`); sonucu yalnız görevin sahibi yazabilir (adresli görevde
  alıcı, adressiz görevde onu fiilen alan cihaz).
"""
import base64
import binascii
import json
import math
import os
import sqlite3
import threading
import time

from ghost import cihaz_sinif, config, durum, governance, kokpit

TURLER = ("hatirlatici", "etkinlik")                  # ekle() ile kurulanlar
SIL_TURU = {"hatirlatici": "hatirlatici_sil", "etkinlik": "etkinlik_sil"}   # sil_kuyrukla() ile kaldırılanlar
BEKLEYEN_TAVAN = 50        # tek çekimde uygulamaya verilecek en fazla görev
OMUR_GUN = 30              # bu yaştan eski BİTMİŞ görevler budanır

CEP_TURLER = ("bildir", "ekran", "durum", "wifi_tara", "ir_ham", "sd", "lora", "konum")   # cep_ekle() ile verilenler
CEP_OKUMA = ("durum", "wifi_tara")     # özerk oturumun da verebildiği (cihazda iz bırakmayan) türler (+ LORA_OKUMA işlemleri)
# Hangi cihaz sınıfı hangi görev türünü anlar. Saat bugün hiçbir cep görevini anlamaz (istemcisi bu ucu yoklamıyor);
# saate görev türü eklenecekse önce buraya yazılır, sonra istemciye.
SINIF_TURLER = {"cardputer": CEP_TURLER, "saat": ()}
YOKLAMA_CEVRIMICI_SN = 180  # bu süre içinde yoklamış cihaz "yokluyor" sayılır (cep.CEVRIMDISI_SN ile aynı eşik)
YOKLAMA_SAKLA_SN = 30 * 86400          # cihaz başına damga bu yaştan sonra budanır
CEP_OMUR_SN = 60           # varsayılan son geçerlilik: bu sürede alınmayan görev düşer
CEP_OMUR_TAVAN = 3600
CEP_BEKLE_TAVAN = 55       # uzun yoklama tavanı (nginx proxy_read_timeout 90 sn'nin altında)
CEP_ARALIK = 0.5           # uzun yoklamada kuyruğa bakma aralığı
CEP_METIN_TAVAN = 2000     # bildir/ekran metni
IR_KHZ = (30, 60)          # taşıyıcı frekans aralığı
IR_ADET_TAVAN = 1024       # ham süre sayısı
IR_TEK_TAVAN = 200000      # tek mark/space (µs)
IR_TOPLAM_TAVAN = 3000000  # bütün dizi (µs)
CIKTI_TAVAN = 32 * 1024    # saklanan sonuç nesnesi (JSON, bayt)
# "sd" türü: cihazdaki microSD kart (firmware 1.3.0, src/sdkart.h ile AYNI sınırlar; büyük dosya parça parça gider)
SD_ISLEM = ("bilgi", "liste", "oku", "yaz", "dizin", "sil", "adlandir")
SD_OKUMA = ("bilgi", "liste", "oku")
SD_OKU_TAVAN = 9000        # tek görevde okunan ham bayt
SD_YAZ_TAVAN = 6000        # tek görevde yazılan ham bayt
SD_YOL_TAVAN = 160         # yol, UTF-8 bayt
# "lora" türü: SX1262 (firmware 1.4.0, src/lora.h ile AYNI sınırlar). Kaynak ve kural: dossier/donanim/lora-gnss-rapor.md.
LORA_ISLEM = ("durum", "dinle", "gonder")
LORA_OKUMA = ("durum", "dinle")        # pasif: özerk oturum da verebilir. "gonder" sinyal üretir → yalnız sahibi isteyince
LORA_VARSAYILAN = {"mhz": 868.1, "bw": 125.0, "sf": 9, "cr": 5, "onek": 8, "sync": 0x12, "crc": True}
LORA_GUC_VARSAYILAN = 14               # dBm
# Bant genişliği: adı (kHz, cihaza giden) → gerçek değeri (Hz)
LORA_BW = {7.8: 7812.5, 10.4: 31250.0 / 3, 15.6: 15625.0, 20.8: 62500.0 / 3, 31.25: 31250.0, 41.7: 125000.0 / 3,
           62.5: 62500.0, 125.0: 125000.0, 250.0: 250000.0, 500.0: 500000.0}
LORA_ALIM_MHZ = (850.0, 930.0)         # dinleme aralığı (yayın değil)
LORA_SF, LORA_CR, LORA_ONEK, LORA_GUC = (7, 12), (5, 8), (6, 1024), (-9, 22)
LORA_DINLE_VARSAYILAN_MS = 10000
LORA_DINLE_TAVAN_MS = 60000            # SUNUCU tavanı (cihaz 120000'e dek kabul eder): dinlerken cihaz yoklamaz ve
                                       # yoklama damgası 180 sn'de "çevrimdışı" sayılır (cep.CEVRIMDISI_SN)
LORA_PAKET_TAVAN, LORA_BAYT_TAVAN = 32, 4096
LORA_VERI_TAVAN = 255                  # tek paket, bayt
LORA_HAVADA_TAVAN_MS = 4000            # tek gönderim
LORA_SAAT_SN = 3600                    # görev döngüsü penceresi
# Gönderime açık alt bantlar: (ad, alt MHz, üst MHz, güç tavanı dBm, saatte en çok havada kalma ms).
# BTK 2022/İK-SYD/245 Tablo 1; cihazdaki ALT_BANTLAR'ın KOPYASI (tests/test_cep_lora_konum.py ikisini karşılaştırır).
# Alarm alt bantları (868,6-868,7 / 869,2-869,4 / 869,65-869,7 MHz) bilerek yok: gönderime kapalı.
LORA_BANTLAR = (("863-865", 863.0, 865.0, 14, 3600), ("865-868", 865.0, 868.0, 14, 36000),
                ("868-868.6", 868.0, 868.6, 14, 36000), ("868.7-869.2", 868.7, 869.2, 14, 3600),
                ("869.4-869.65", 869.4, 869.65, 22, 360000), ("869.7-870", 869.7, 870.0, 14, 36000))
# Gönderimde en geniş bant genişliği (kHz), alt banda göre; listede olmayan alt bantta kanalın sığması yeter.
# 863-865 ve 865-868 MHz'de 250 / 500 kHz'in kurala uyduğu doğrulanamadı (dossier/donanim/lora-gnss-inceleme.md);
# cihazdaki ALT_BANTLAR'ın son sütununun KOPYASI (firmware 1.4.1). Dinleme bundan etkilenmez.
LORA_BW_TAVAN = {"863-865": 125.0, "865-868": 125.0}
# Özerk oturumun dinlemesi (inceleme K5): pasif alım okuma sayılır ama dinlerken cihaz yoklamaz (sahibinin 60 sn ömürlü
# görevi düşer) ve radyo sürekli alımda kalır (pil) → süre ve saatlik adet tavanı. Sahibinin oturumu etkilenmez.
LORA_OZERK_DINLE_MS = 15000
LORA_OZERK_DINLE_SAAT = 6              # son bir saatteki BÜTÜN dinleme görevleri sayılır (sahibinkiler dahil)
KONUM_BEKLE_MS = (500, 10000)          # "konum": GNSS alıcısını dinleme süresi
KONUM_BEKLE_VARSAYILAN_MS = 1500
YOKLAMA_AD = "cep_yoklama"  # durum katmanı: EN SON yoklayan cep cihazı {"cihaz","ts"} (eski biçim; adressiz dünya bunu okur)
YOKLAMALAR_AD = "cep_yoklamalar"       # durum katmanı: cihaz başına son yoklama {"<cihaz id>": ts}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kokpit_gorevler(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tur TEXT, veri TEXT, durum TEXT, sonuc TEXT, yerel_id TEXT,
    cihaz TEXT, ts TEXT, biten TEXT,
    hedef TEXT DEFAULT 'telefon', son REAL, verildi REAL, cikti TEXT);
"""
# 2026-11-23 göçü: eski tabloya eklenen sütunlar. hedef = telefon|cep (eski satır ve NULL = telefon),
# son = son geçerlilik (epoch; NULL = süresiz), verildi = cihaza teslim anı, cikti = cihazın döndürdüğü JSON.
# 2026-11-24: alici = cep görevinin gideceği cihaz kimliği (NULL = adressiz eski kayıt). `cihaz` sütunu ayrı şeydir:
# görevi fiilen ALAN / sonuçlandıran cihaz. Sürümlü kayıt semalar göç 3'te; buradaki satır taze veritabanı içindir.
_EK_SUTUN = (("hedef", "TEXT DEFAULT 'telefon'"), ("son", "REAL"), ("verildi", "REAL"), ("cikti", "TEXT"),
             ("alici", "TEXT"))
_SUTUN = "id,tur,veri,durum,sonuc,yerel_id,cihaz,ts,biten"
_CEP_SUTUN = _SUTUN + ",hedef,son,verildi,cikti,alici"


class HedefUyusmuyor(Exception):
    """Sonucu yazan cihazın türü görevin hedefi değil (telefon ↔ cep) ya da görev başka bir cihaza adresli.
    API bunu 403'e çevirir."""


def _db():
    c = sqlite3.connect(config.DB_PATH, timeout=10)
    c.executescript(_SCHEMA)
    var = {r[1] for r in c.execute("PRAGMA table_info(kokpit_gorevler)")}
    for ad, tip in _EK_SUTUN:
        if ad not in var:
            try:
                c.execute("ALTER TABLE kokpit_gorevler ADD COLUMN %s %s" % (ad, tip))
            except sqlite3.OperationalError:     # aynı anda başka süreç ekledi
                pass
    return c


def _suresi_dolanlar(c, simdi=None):
    """Son geçerliliği geçmiş bekleyenleri düşür. Önce salt okuma: düşecek görev yoksa yazma kilidi alınmaz
    (telefonun her çekiminde koşar)."""
    simdi = time.time() if simdi is None else simdi
    if c.execute("SELECT 1 FROM kokpit_gorevler WHERE durum='bekliyor' AND son IS NOT NULL AND son < ? LIMIT 1",
                 (simdi,)).fetchone():
        c.execute("UPDATE kokpit_gorevler SET durum='suresi_doldu', biten=? "
                  "WHERE durum='bekliyor' AND son IS NOT NULL AND son < ?", (time.strftime("%F %T"), simdi))
        c.commit()


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


def sil_kuyrukla(gid):
    """Ghost'un daha önce telefona YAZDIĞI hatırlatıcı/etkinliği kaldırma görevi → (id, hata).
    Kaynak görev `tamam` ve `yerel_id`'li olmalı — "gönderdim" değil "yazıldı" dediğimiz kayıt. Aynı kayıt için
    bekleyen silme varsa yenisi açılmaz. `ts` şimdi: uygulamanın GorevVeri'si ts'yi zorunlu çözer."""
    c = _db()
    r = c.execute("SELECT id,tur,veri,durum,sonuc,yerel_id,cihaz,ts,biten FROM kokpit_gorevler WHERE id=?",
                  (int(gid),)).fetchone()
    if not r:
        c.close()
        return None, "görev #%s yok" % gid
    g = _satir(r)
    if g["tur"] not in SIL_TURU:
        c.close()
        return None, "#%s silinebilir bir tür değil (%s)" % (gid, g["tur"])
    if g["durum"] == "silindi":
        c.close()
        return None, "#%s zaten silindi" % gid
    if g["durum"] != "tamam" or not g["yerel_id"]:
        c.close()
        return None, "#%s telefona yazılmamış (durum %s) — silinecek bir şey yok; bekliyorsa `iptal`" % (gid, g["durum"])
    for (v,) in c.execute("SELECT veri FROM kokpit_gorevler WHERE durum='bekliyor' AND tur=?", (SIL_TURU[g["tur"]],)):
        if json.loads(v or "{}").get("kaynak") == g["id"]:
            c.close()
            return None, "#%s için silme zaten kuyrukta" % gid
    k = g["veri"]
    veri = {"ad": k.get("ad", "?"), "yerel_id": g["yerel_id"], "kaynak": g["id"], "ts": time.time(),
            "hedef_ts": k.get("ts"), "ne_zaman_metin": k.get("ne_zaman_metin", "")}
    cur = c.execute("INSERT INTO kokpit_gorevler(tur, veri, durum, ts) VALUES(?,?, 'bekliyor', ?)",
                    (SIL_TURU[g["tur"]], json.dumps(veri, ensure_ascii=False), time.strftime("%F %T")))
    yeni = cur.lastrowid
    c.commit()
    c.close()
    governance.audit("gorevler", "sil", governance.REVERSIBLE, "%s #%d ← #%d: %s" % (
        SIL_TURU[g["tur"]], yeni, g["id"], veri["ad"][:60]))
    _durtu()
    return yeni, None


def _durtu():
    """Uygulamayı sessiz push'la uyandır — kuyrukta iş var. En iyi çaba: push
    gitmezse uygulama zaten öne gelince, SSE'de ve arka plan tazelemesinde çeker."""
    try:
        from ghost import apns
        apns.sessiz_herkese({"ghost": "gorev"})
    except Exception:
        pass


def bekleyenler(limit=BEKLEYEN_TAVAN, hedef="telefon"):
    """Bekleyen görevler, hedefe göre süzülü. Argümansız çağrı = yalnız telefon görevleri (uygulama cep
    türlerini tanımaz). Cep cihazı buradan ÇEKMEZ (bu okuma teslim saymaz) — onun yolu `cep_al`."""
    c = _db()
    _suresi_dolanlar(c)
    rows = c.execute("SELECT " + _SUTUN + " FROM kokpit_gorevler "
                     "WHERE durum='bekliyor' AND COALESCE(hedef,'telefon')=? ORDER BY id LIMIT ?",
                     (hedef, limit)).fetchall()
    c.close()
    return [_satir(r) for r in rows]


def bekleyen_sayisi(hedef="telefon"):
    c = _db()
    _suresi_dolanlar(c)
    n = c.execute("SELECT COUNT(*) FROM kokpit_gorevler WHERE durum='bekliyor' AND COALESCE(hedef,'telefon')=?",
                  (hedef,)).fetchone()[0]
    c.close()
    return n


def sonuclandir(gid, ok, mesaj="", yerel_id="", cihaz=None, hedef=None, cikti=None):
    """Uygulama görevi uyguladı (ya da uygulayamadı). Dönen: güncellenen görev ya da None.
    Yalnız 'bekliyor' durumundaki görev sonuçlanır — aynı görevin iki kez yazılması
    (iki cihaz, tekrar denemesi) böylece engellenir.

    `hedef` verilirse (sonucu yazan cihazın türü: telefon|cep) görevin hedefiyle karşılaştırılır; uyuşmazsa
    HedefUyusmuyor fırlar (telefon cep görevini, cep telefon görevini sonuçlandıramaz). Verilmezse denetim
    yok (eski çağıranlar). Cep görevi `bekliyor` ya da `verildi` durumundan sonuçlanır; `cikti` (cihazın
    döndürdüğü JSON nesnesi) saklanır, `yerel_id` kullanılmaz."""
    c = _db()
    r = c.execute("SELECT COALESCE(hedef,'telefon') FROM kokpit_gorevler WHERE id=?", (int(gid),)).fetchone()
    if not r:
        c.close()
        return None
    if hedef is not None and hedef != r[0]:
        c.close()
        raise HedefUyusmuyor("görev #%s hedefi %s, sonucu yazan %s" % (gid, r[0], hedef))
    if r[0] == "cep":
        c.close()
        return _cep_sonuclandir(gid, ok, mesaj, cihaz, cikti)
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
    if ok and g["tur"] in SIL_TURU.values() and g["veri"].get("kaynak"):
        c = _db()
        c.execute("UPDATE kokpit_gorevler SET durum='silindi' WHERE id=? AND durum='tamam'", (int(g["veri"]["kaynak"]),))
        c.commit()
        c.close()
    governance.audit("gorevler", "sonuc", governance.READ,
                     "#%s %s → %s %s" % (gid, g["tur"], g["durum"], (mesaj or "")[:80]))
    return g


def liste(limit=15, hedef="telefon"):
    """Son görevler (yeni önce). Varsayılan yalnız telefon: `/gorevler` ve `metin()` cep işlerini göstermez."""
    c = _db()
    rows = c.execute("SELECT " + _SUTUN + " FROM kokpit_gorevler WHERE COALESCE(hedef,'telefon')=? "
                     "ORDER BY id DESC LIMIT ?", (hedef, limit)).fetchall()
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


_SIMGE = {"bekliyor": "⏳", "tamam": "✅", "hata": "❌", "iptal": "🚫", "silindi": "🗑"}
_AD = {"hatirlatici": "hatırlatıcı", "etkinlik": "etkinlik",
       "hatirlatici_sil": "hatırlatıcı SİL", "etkinlik_sil": "etkinlik SİL"}


def metin(limit=10):
    g = liste(limit)
    if not g:
        return "📲 Telefona yazılmış görev yok. (Ghost hatırlatıcı/etkinlik kurabilir.)"
    s = ["📲 Telefon görevleri (son %d):" % len(g)]
    for x in g:
        v = x["veri"]
        s.append("%s #%d %s — %s @ %s%s" % (
            _SIMGE.get(x["durum"], "•"), x["id"], _AD.get(x["tur"], x["tur"]),
            v.get("ad", "?"), v.get("ne_zaman_metin", "?"),
            ("\n     " + x["sonuc"]) if x["durum"] == "hata" and x["sonuc"] else ""))
    return "\n".join(s)


def haber_ver(g):
    """Sonuç sahibe düşsün — 'gönderdim' ile 'telefona yazıldı' aynı şey değil."""
    if g.get("hedef") == "cep" or g.get("tur") in CEP_TURLER:
        return ""                     # cep görevinin sonucu çağırana döner; bildirim yok
    v = g["veri"]
    if g["tur"] in SIL_TURU.values():
        # Silme başarısı bildirilmez (Sahip 2026-10-29: "koştu" bildirimleri yoruyor) — sonucu sohbette Ghost söyler;
        # yalnız hata push'lanır.
        tur = "Hatırlatıcı" if g["tur"] == "hatirlatici_sil" else "Takvim etkinliği"
        if g["durum"] == "tamam":
            return "🗑 %s telefondan kaldırıldı: %s%s" % (tur, v.get("ad", "?"),
                                                          " (zaten yoktu)" if g.get("yerel_id") == "zaten-yok" else "")
        metin_ = "❌ %s telefondan KALDIRILAMADI: %s — %s" % (tur, v.get("ad", "?"), g.get("sonuc") or "sebep yok")
        try:
            from ghost import notify
            notify.telegram(metin_)
        except Exception:
            kokpit.outbox_yaz(metin_, "ghost")
        return metin_
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


# ---------------------------------------------------------------- CEP CİHAZI (uzaktan iş)
_nesil = {}                      # cihaz → son uzun yoklamanın sırası (süreç içi; API tek süreç, çok iş parçacığı)
_nesil_kilit = threading.Lock()


def _cikti_coz(s):
    if not s:
        return None
    try:
        return json.loads(s)
    except ValueError:
        return None


def _cikti_yaz(cikti):
    """Cihazın döndürdüğü nesne → saklanacak JSON metni (None = yok). Tavanı aşan çıktı saklanmaz; yerine
    `kesildi` işaretli küçük bir nesne yazılır (yarım JSON çağıranı yanıltır)."""
    if cikti is None:
        return None
    if not isinstance(cikti, dict):
        cikti = {"deger": cikti}
    try:
        s = json.dumps(cikti, ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps({"kesildi": True, "neden": "json değil"}, ensure_ascii=False)
    boyut = len(s.encode("utf-8"))
    if boyut > CIKTI_TAVAN:
        return json.dumps({"kesildi": True, "boyut": boyut, "tavan": CIKTI_TAVAN, "bas": s[:1000]}, ensure_ascii=False)
    return s


def _cep_satir(r):
    g = _satir(r[:9])
    g.update(hedef=r[9] or "telefon", son=r[10], verildi=r[11], cikti=_cikti_coz(r[12]), alici=r[13])
    return g


def _ir_coz(us):
    """'9000,4500,560' ya da sayı dizisi → ([int], hata). Sınırlar cihazın tamponuna ve tek gönderimin
    makul süresine göre; aşım sessizce kırpılmaz, açık hatayla reddedilir (yarım kod yanlış koddur)."""
    if isinstance(us, str):
        parcalar = [p.strip() for p in us.split(",")] if us.strip() else []
    elif isinstance(us, (list, tuple)):
        parcalar = list(us)
    else:
        return None, "us: virgülle ayrık mikro saniye dizisi bekleniyor (örnek '9000,4500,560')"
    if not parcalar:
        return None, "us boş: en az bir süre gerekli"
    if len(parcalar) > IR_ADET_TAVAN:
        return None, "us: en çok %d değer (verilen %d)" % (IR_ADET_TAVAN, len(parcalar))
    degerler = []
    for i, p in enumerate(parcalar, 1):
        if isinstance(p, str) and p.isascii() and p.isdigit():
            p = int(p)
        if isinstance(p, bool) or not isinstance(p, int):
            return None, "us: %d. değer tam sayı değil: %r" % (i, p)
        if not 1 <= p <= IR_TEK_TAVAN:
            return None, "us: %d. değer %d µs — tek değer 1..%d olmalı" % (i, p, IR_TEK_TAVAN)
        degerler.append(p)
    if sum(degerler) > IR_TOPLAM_TAVAN:
        return None, "us: toplam %d µs — en çok %d" % (sum(degerler), IR_TOPLAM_TAVAN)
    return degerler, None


def sd_yol_hatasi(yol):
    """Kart yolu denetimi (cihazdaki sdkart::yolDogrula ile aynı kural) → hata metni ya da None."""
    if not isinstance(yol, str) or not yol.startswith("/"):
        return "yol / ile başlamalı"
    if len(yol.encode("utf-8", "replace")) > SD_YOL_TAVAN:
        return "yol çok uzun (en çok %d bayt)" % SD_YOL_TAVAN
    if yol == "/":
        return None
    for parca in yol[1:].split("/"):
        if not parca:
            return "yolda boş parça (// ya da sonda /)"
        if parca in (".", ".."):
            return "yolda . ya da .. olamaz"
        if parca[-1] in " .":
            return "ad boşlukla ya da noktayla bitemez"
        if any(ord(ch) < 0x20 or ord(ch) == 0x7f or ch in '\\:*?"<>|' for ch in parca):
            return 'yolda geçersiz karakter (denetim karakteri ya da \\ : * ? " < > |)'
    return None


def _sd_sayi(veri, ad, enaz, encok):
    v = veri.get(ad, 0)
    if isinstance(v, bool) or not isinstance(v, int) or not enaz <= v <= encok:
        return None, "%s %d..%d arasında tam sayı olmalı: %r" % (ad, enaz, encok, v)
    return v, None


def _sd_dogrula(veri):
    islem = veri.get("islem")
    if islem not in SD_ISLEM:
        return None, "islem %s değil: %r" % ("|".join(SD_ISLEM), islem)
    if islem == "bilgi":
        return {"islem": islem}, None
    hata = sd_yol_hatasi(veri.get("yol"))
    if hata:
        return None, hata
    cikti = {"islem": islem, "yol": veri["yol"]}
    if islem in ("liste", "oku", "yaz"):
        cikti["bas"], hata = _sd_sayi(veri, "bas", 0, 0xFFFFFFFF)
        if hata:
            return None, hata
    if islem == "oku":
        cikti["adet"], hata = _sd_sayi(veri, "adet", 1, SD_OKU_TAVAN)
        if hata:
            return None, hata
    if islem == "yaz":
        if veri["yol"] == "/":
            return None, "dosya yolu gerekli"
        ham = veri.get("b64", "")
        if isinstance(ham, (bytes, bytearray)):
            ikili = bytes(ham)
        else:
            try:
                ikili = base64.b64decode(ham if isinstance(ham, str) else None, validate=True)
            except (binascii.Error, TypeError, ValueError):
                return None, "b64 geçerli base64 değil"
        if len(ikili) > SD_YAZ_TAVAN:
            return None, "tek görevde en çok %d bayt yazılır (verilen %d)" % (SD_YAZ_TAVAN, len(ikili))
        cikti["b64"] = base64.b64encode(ikili).decode("ascii")
    if islem in ("sil", "adlandir") and veri["yol"] == "/":
        return None, "kök silinemez / taşınamaz"
    if islem == "adlandir":
        hata = sd_yol_hatasi(veri.get("yeni"))
        if hata or veri.get("yeni") == "/":
            return None, "yeni: %s" % (hata or "kök olamaz")
        cikti["yeni"] = veri["yeni"]
    return cikti, None


def _tam(veri, ad, aralik, varsayilan=None):
    """Tam sayı alanı → (değer, hata). Alan yoksa `varsayilan` (None ise zorunlu)."""
    v = veri.get(ad, varsayilan)
    if isinstance(v, bool) or not isinstance(v, int) or not aralik[0] <= v <= aralik[1]:
        return None, "%s %d..%d arasında tam sayı olmalı: %r" % (ad, aralik[0], aralik[1], v)
    return v, None


def lora_bant(mhz, bw):
    """Kanal (mhz ± bw/2) hangi gönderim alt bandına TAM sığıyor → LORA_BANTLAR sırası ya da None.
    Hoşgörü YOK (firmware 1.4.1 altBantBul ile aynı kural; 1.4.0'daki 100 Hz'lik pay kenarı aşan kanalı kabul
    ediyordu): merkez frekans 100 Hz ızgarasına yuvarlanır (cihazda görev verisi 32 bit kayan noktayla okunur: ±30 Hz;
    cihaz da yuvarlanmış değeri denetler ve çipe onu yazar), yuvarlanmış kanal TAM sığmalı. 869,525 MHz / 250 kHz
    kenarlara tam oturur ve kabul edilir; 100 Hz ötesi reddedilir."""
    hz = LORA_BW.get(bw)
    if hz is None:
        return None
    try:
        f = round(mhz * 1e4) * 100
    except (OverflowError, ValueError, TypeError):
        return None
    for i, (_, alt, ust, _, _) in enumerate(LORA_BANTLAR):
        if 2 * f - hz >= 2 * round(alt * 1e6) and 2 * f + hz <= 2 * round(ust * 1e6):
            return i
    return None


def lora_havada_ms(ayar, n):
    """n baytlık paketin havada kalma süresi, ms (yukarı yuvarlanır). Semtech SX126x formülü, açık başlık; düşük hız
    iyileştirmesi simge süresi ≥ 16 ms ise açık. Cihazdaki lora::havadaMs ile aynı sonucu verir (tam sayı aritmetiği)."""
    pay_hz, payda_hz = {7.8: (15625, 2), 10.4: (31250, 3), 15.6: (15625, 1), 20.8: (62500, 3), 31.25: (31250, 1),
                        41.7: (125000, 3), 62.5: (62500, 1), 125.0: (125000, 1), 250.0: (250000, 1), 500.0: (500000, 1)}[ayar["bw"]]
    sf, cr = ayar["sf"], ayar["cr"]
    de = 1 if (1 << sf) * 1000 * payda_hz >= 16 * pay_hz else 0
    pay = 8 * n - 4 * sf + 28 + (16 if ayar["crc"] else 0)
    yuk = 8 + (-(-pay // (4 * (sf - 2 * de))) * cr if pay > 0 else 0)
    simge4 = 4 * ayar["onek"] + 17 + 4 * yuk
    ust, alt = simge4 * (1 << sf) * 250000 * payda_hz, 1000 * pay_hz
    return -(-ust // alt)


def _lora_ayar(veri, gonderim):
    """Radyo ayarlarını doğrula, eksikleri varsayılanla doldur → (ayar, hata). Cihaza hep AÇIK değerler gider."""
    a = {}
    for ad in ("mhz", "bw"):
        v = veri.get(ad, LORA_VARSAYILAN[ad])
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None, "%s sayı olmalı: %r" % (ad, v)
        a[ad] = float(v)
    yakin = [ad for ad in LORA_BW if abs(a["bw"] - ad) < 0.06]
    if not yakin:
        return None, "bw şunlardan biri olmalı (kHz): %s" % ", ".join("%g" % x for x in LORA_BW)
    a["bw"] = yakin[0]
    if not LORA_ALIM_MHZ[0] <= a["mhz"] <= LORA_ALIM_MHZ[1]:
        return None, "mhz %g..%g arasında olmalı: %r" % (LORA_ALIM_MHZ[0], LORA_ALIM_MHZ[1], a["mhz"])
    for ad, aralik in (("sf", LORA_SF), ("cr", LORA_CR), ("onek", LORA_ONEK), ("sync", (0, 255))):
        a[ad], hata = _tam(veri, ad, aralik, LORA_VARSAYILAN[ad])
        if hata:
            return None, hata
    crc = veri.get("crc", LORA_VARSAYILAN["crc"])
    if not isinstance(crc, bool) and crc not in (0, 1):
        return None, "crc true/false olmalı: %r" % (crc,)
    a["crc"] = bool(crc)
    if gonderim:
        a["guc"], hata = _tam(veri, "guc", LORA_GUC, LORA_GUC_VARSAYILAN)
        if hata:
            return None, hata
        bant = lora_bant(a["mhz"], a["bw"])
        if bant is None:
            return None, ("kanal gönderime açık alt banda sığmıyor (mhz ± bw/2 şunlardan birinin içinde olmalı: %s MHz)"
                          % ", ".join(b[0] for b in LORA_BANTLAR))
        bw_tavan = LORA_BW_TAVAN.get(LORA_BANTLAR[bant][0])
        if bw_tavan is not None and a["bw"] > bw_tavan + 0.06:
            return None, "bw bu alt bantta en çok %g kHz (%s MHz)" % (bw_tavan, LORA_BANTLAR[bant][0])
        if a["guc"] > LORA_BANTLAR[bant][3]:
            return None, "güç bu alt bantta en çok %d dBm (%s MHz)" % (LORA_BANTLAR[bant][3], LORA_BANTLAR[bant][0])
    return a, None


def _lora_hex(v):
    """Gönderilecek veri: bayt dizisi ya da onaltılık metin → (küçük harf hex, hata)."""
    if isinstance(v, (bytes, bytearray)):
        ham = bytes(v)
    elif isinstance(v, str):
        try:
            ham = bytes.fromhex(v) if v.isascii() and " " not in v else None
        except ValueError:
            ham = None
        if ham is None:
            return None, "hex onaltılık olmalı (boşluksuz, çift sayıda rakam)"
    else:
        return None, "hex onaltılık metin olmalı"
    if not 1 <= len(ham) <= LORA_VERI_TAVAN:
        return None, "veri 1..%d bayt olmalı (verilen %d)" % (LORA_VERI_TAVAN, len(ham))
    return ham.hex(), None


def _lora_kullanilan_ms(c, bant, simdi):
    """Son bir saatte bu alt bantta havada geçen süre, SUNUCU sayımı (cihazın kendi defteri ayrıca işler: firmware
    1.4.1'de yazılım sıfırlamasında korunur, güç kesilince bir saat gönderimi kilitler). Sonucu gelen görevde cihazın bildirdiği `sure_ms` (0 = verici açılmadı) sayılır; kuyrukta bekleyen ve
    cihaza verilip sonucu henüz gelmeyen görevde hesaplanan süre (temkinli: gitmiş sayılır)."""
    toplam = 0
    rows = c.execute("SELECT veri, durum, cikti FROM kokpit_gorevler WHERE hedef='cep' AND tur='lora' "
                     "AND (durum='bekliyor' OR verildi >= ?)", (simdi - LORA_SAAT_SN,)).fetchall()
    for veri_s, durum_, cikti_s in rows:
        try:
            v = json.loads(veri_s or "{}")
            if v.get("islem") != "gonder" or lora_bant(v["mhz"], v["bw"]) != bant:
                continue
            hesap = lora_havada_ms(v, len(v["hex"]) // 2)
        except (ValueError, KeyError, TypeError):
            continue
        cikti = _cikti_coz(cikti_s) if durum_ in ("tamam", "hata") else None
        bildirilen = cikti.get("sure_ms") if isinstance(cikti, dict) else None
        if isinstance(bildirilen, int) and not isinstance(bildirilen, bool) and 0 <= bildirilen <= LORA_HAVADA_TAVAN_MS:
            toplam += bildirilen
        elif durum_ in ("bekliyor", "verildi") or durum_ == "tamam":
            toplam += hesap                  # sonuç yok / okunamıyor: gitmiş say
    return toplam


def _lora_dogrula(veri, c=None, simdi=None):
    islem = veri.get("islem")
    if islem not in LORA_ISLEM:
        return None, "islem %s değil: %r" % ("|".join(LORA_ISLEM), islem)
    if islem == "durum":
        return {"islem": islem}, None
    ayar, hata = _lora_ayar(veri, islem == "gonder")
    if hata:
        return None, hata
    cikti = dict({"islem": islem}, **ayar)
    if islem == "dinle":
        for ad, aralik, varsayilan in (("sure_ms", (1, LORA_DINLE_TAVAN_MS), LORA_DINLE_VARSAYILAN_MS),
                                       ("paket", (1, LORA_PAKET_TAVAN), LORA_PAKET_TAVAN), ("bayt", (1, LORA_BAYT_TAVAN), LORA_BAYT_TAVAN)):
            cikti[ad], hata = _tam(veri, ad, aralik, varsayilan)
            if hata:
                return None, hata
        return cikti, None
    cikti["hex"], hata = _lora_hex(veri.get("hex"))
    if hata:
        return None, hata
    n = len(cikti["hex"]) // 2
    sure = lora_havada_ms(cikti, n)
    if sure > LORA_HAVADA_TAVAN_MS:
        return None, "paket havada çok uzun kalıyor (%d ms; en çok %d): sf, bw ya da uzunluğu küçült" % (sure, LORA_HAVADA_TAVAN_MS)
    if c is not None:
        bant = lora_bant(cikti["mhz"], cikti["bw"])
        ad, _, _, _, butce = LORA_BANTLAR[bant]
        kullanilan = _lora_kullanilan_ms(c, bant, time.time() if simdi is None else simdi)
        if kullanilan + sure > butce:
            return None, ("görev döngüsü dolu (%s MHz: son bir saatte %d ms, sınır %d ms, gereken %d ms; sunucu sayımı)"
                          % (ad, kullanilan, butce, sure))
    return cikti, None


def _lora_ozerk_dinle(c, veri, simdi=None):
    """Özerk oturumun dinleme isteği tavanların içinde mi → hata metni ya da None (bkz. LORA_OZERK_DINLE_MS)."""
    if veri.get("sure_ms", 0) > LORA_OZERK_DINLE_MS:
        return "özerk oturumda dinleme en çok %d ms (istenen %d): dinlerken cihaz başka görev almaz" % (
            LORA_OZERK_DINLE_MS, veri.get("sure_ms", 0))
    simdi = time.time() if simdi is None else simdi
    adet = 0
    for (veri_s,) in c.execute("SELECT veri FROM kokpit_gorevler WHERE hedef='cep' AND tur='lora' AND "
                               "(durum='bekliyor' OR verildi >= ?)", (simdi - LORA_SAAT_SN,)).fetchall():
        try:
            adet += json.loads(veri_s or "{}").get("islem") == "dinle"
        except (ValueError, AttributeError):
            continue
    if adet >= LORA_OZERK_DINLE_SAAT:
        return "özerk oturum saatte en çok %d dinleme ekleyebilir (son bir saatte %d dinleme var)" % (LORA_OZERK_DINLE_SAAT, adet)
    return None


def _cep_okuma_mi(tur, veri):
    """Özerk oturumun da verebildiği görev mi (cihazda iz bırakmayan, sinyal üretmeyen, kişisel veri döndürmeyen)."""
    if tur in CEP_OKUMA:
        return True
    return tur == "lora" and isinstance(veri, dict) and veri.get("islem") in LORA_OKUMA


def _cep_dogrula(tur, veri, c=None):
    """Türe göre `veri`yi doğrula ve cihazın beklediği biçime indir → (veri, hata). `c` (veritabanı bağlantısı)
    verilirse durum gerektiren denetimler de yapılır (lora gonder: sunucunun saatlik görev döngüsü sayımı)."""
    veri = dict(veri or {})
    if tur == "sd":
        return _sd_dogrula(veri)
    if tur == "lora":
        return _lora_dogrula(veri, c)
    if tur == "konum":
        bekle, hata = _tam(veri, "bekle_ms", KONUM_BEKLE_MS, KONUM_BEKLE_VARSAYILAN_MS)
        return ({"bekle_ms": bekle}, None) if not hata else (None, hata)
    if tur in ("bildir", "ekran"):
        metin_ = veri.get("metin")
        metin_ = metin_.strip() if isinstance(metin_, str) else ""
        if not metin_:
            return None, "metin boş"
        if len(metin_) > CEP_METIN_TAVAN:
            return None, "metin en çok %d karakter (verilen %d)" % (CEP_METIN_TAVAN, len(metin_))
        return ({"metin": metin_, "bip": bool(veri.get("bip"))} if tur == "bildir" else {"metin": metin_}), None
    if tur == "ir_ham":
        khz = veri.get("khz")
        if isinstance(khz, bool) or not isinstance(khz, int) or not IR_KHZ[0] <= khz <= IR_KHZ[1]:
            return None, "khz %d..%d arasında tam sayı olmalı: %r" % (IR_KHZ[0], IR_KHZ[1], khz)
        us, hata = _ir_coz(veri.get("us"))
        if hata:
            return None, hata
        return {"khz": khz, "us": ",".join(str(x) for x in us)}, None
    return {}, None                    # durum, wifi_tara: girdi yok


def _cep_ozet(tur, veri):
    if tur in ("bildir", "ekran"):
        return veri["metin"][:60].replace("\n", " ")
    if tur == "ir_ham":
        return "%d kHz n=%d" % (veri["khz"], veri["us"].count(",") + 1)
    if tur == "sd":
        return ("%s %s%s" % (veri["islem"], veri.get("yol", ""), " → " + veri["yeni"] if "yeni" in veri else "")).strip()[:120]
    if tur == "lora":                  # gönderilen veri denetim kaydına girmez (yalnız uzunluğu)
        if veri["islem"] == "dinle":
            return "dinle %g MHz %d ms" % (veri["mhz"], veri["sure_ms"])
        if veri["islem"] == "gonder":
            return "gonder %g MHz %d dBm n=%d" % (veri["mhz"], veri["guc"], len(veri["hex"]) // 2)
        return veri["islem"]
    return ""


def _cep_cihazlar():
    """Aktif cep türü cihazlar {id: ad} ya da None (cihaz listesi okunamadı: onay servisi düşük). Asla fırlatmaz."""
    try:
        return {str(c["id"]): str(c.get("ad") or "") for c in kokpit.cihazlar()
                if c.get("tur") == "cep" and c.get("durum", "aktif") == "aktif"}
    except Exception:
        return None


def _sinif_durumu(aktif=None):
    """Sınıf kaydının hâli → (kip, kayıt). Kip görev yönlendirmesinin ne kadar katı olacağını söyler:
      "kati"   kayıt en az bir kez yazılmış (içi boş olsa da) YA DA birden çok cep cihazı eşli → yalnız sınıfa göre
      "eski"   kayıt hiç yazılmamış VE en çok bir cep cihazı eşli → adressiz görev eski davranışla (karışacak cihaz yok)
      "kapali" kayıt okunamadı, ya da kayıt yokken cihaz listesi de okunamadı → adressiz görev YOK (emin değilken kapalı)
    `aktif`: elde varsa `_cep_cihazlar()` sonucu (ikinci kez sorulmasın)."""
    kayit, var = cihaz_sinif.yukle()
    if kayit is None:
        return "kapali", {}
    if var:
        return "kati", kayit
    aktif = _cep_cihazlar() if aktif is None else aktif
    if aktif is None:
        return "kapali", {}
    return ("eski", {}) if len(aktif) <= 1 else ("kati", {})


def cep_alici_sec(tur, secim=None):
    """Görevin alıcısını belirle → (cihaz kimliği | None, hata).
    `secim` (kimlik ya da ad) verilirse o cihaz: sınıfı kayıtlı OLMALI ve `tur`ü anlamalı. Verilmezse `tur`ü anlayan
    sınıftaki kayıtlı cihaz: tek ise o; birden çoksa yalnız biri son YOKLAMA_CEVRIMICI_SN içinde yoklamışsa o, değilse
    açık hata (tahmin edilmez). (None, None) = "eski" kip (bkz. `_sinif_durumu`) → adressiz görev."""
    aktif = _cep_cihazlar()
    kip, kayit = _sinif_durumu(aktif)
    if kip == "kapali":
        return None, "cihaz sınıfı kaydı okunamadı — görev kuyruklanmadı (yanlış cihaza gitmesin)"
    if secim is not None and str(secim).strip():
        s = str(secim).strip()
        if aktif is not None:
            if s in aktif:
                cid = s
            else:
                uyan = [i for i, ad in aktif.items() if ad.lower() == s.lower()]
                if len(uyan) > 1:
                    return None, "'%s' adında %d cep cihazı var (%s) — kimlikle seç" % (s, len(uyan), ", ".join(sorted(uyan)))
                if not uyan:
                    return None, "'%s' adında ya da kimliğinde aktif cep cihazı yok" % s
                cid = uyan[0]
        elif s in kayit:
            cid = s                                   # cihaz listesi okunamadı: yalnız sınıfı kayıtlı kimlik kabul
        else:
            return None, "cihaz listesi okunamadı ve '%s' sınıfı kayıtlı bir kimlik değil" % s
        sinif = kayit.get(cid)
        if sinif is None:
            return None, ("cihaz %s sınıfı kayıtlı değil — önce `python3 -m ghost.cep sinif %s cardputer|saat`" % (cid, cid))
        if tur not in SINIF_TURLER.get(sinif, ()):
            return None, "cihaz %s (%s) '%s' görevini anlamaz" % (cid, sinif, tur)
        return cid, None
    if kip == "eski":
        return None, None
    aday = sorted(i for i, sinif in kayit.items()
                  if tur in SINIF_TURLER.get(sinif, ()) and (aktif is None or i in aktif))
    if len(aday) == 1:
        return aday[0], None
    if not aday:
        return None, "'%s' görevini anlayan, sınıfı kayıtlı aktif cihaz yok (`python3 -m ghost.cep cihazlar`)" % tur
    yoklayan = [i for i in aday if (cep_yoklama(i) or (float("inf"),))[0] <= YOKLAMA_CEVRIMICI_SN]
    if len(yoklayan) == 1:
        return yoklayan[0], None
    return None, "'%s' görevini anlayan %d cihaz var (%s) — --cihaz ile seç" % (tur, len(aday), ", ".join(aday))


def cep_ekle(tur, veri=None, omur_sn=CEP_OMUR_SN, alici=None):
    """Cep cihazına görev bırak → (id, hata). Görev `omur_sn` içinde alınmazsa düşer (geç koşmaz).
    Görev alıcısına adreslenir (`alici`: cihaz kimliği ya da adı; verilmezse `cep_alici_sec` seçer).
    POLİTİKA burada, çağırana bırakılmaz: FREEZE'de görev yok; özerk oturum (zamanlayıcı/nöbetçi/hedef işi)
    yalnız CEP_OKUMA türlerini ekleyebilir — sinyal, ses ve ekran üreten araç yalnız sahibi isteyince.
    Push dürtüsü yok (cihaz uzun yoklamayla zaten bekliyor)."""
    if tur not in CEP_TURLER:
        return None, "tür %s değil: %s" % ("|".join(CEP_TURLER), tur)
    if governance.is_frozen():
        return None, "FREEZE aktif — cep cihazına görev verilmez"
    if os.environ.get("GHOST_OZERK") == "1" and not _cep_okuma_mi(tur, veri):
        ne = "lora %s" % veri.get("islem") if tur == "lora" and isinstance(veri, dict) else tur
        governance.audit("gorevler", "cep_ret", governance.READ, "özerk oturum %s istedi" % ne)
        return None, "%s yalnız sahibi isteyince: özerk oturum cep cihazına yalnız okuma görevi (%s) verebilir" % (
            ne, "|".join(CEP_OKUMA + tuple("lora " + i for i in LORA_OKUMA)))
    c = _db()
    try:
        veri, hata = _cep_dogrula(tur, veri, c)
        if not hata and os.environ.get("GHOST_OZERK") == "1" and tur == "lora" and veri.get("islem") == "dinle":
            hata = _lora_ozerk_dinle(c, veri)
    finally:
        c.close()
    if hata:
        return None, hata
    try:
        omur = float(omur_sn)
    except (TypeError, ValueError):
        return None, "ömür sayı değil: %r" % (omur_sn,)
    if not 0 < omur <= CEP_OMUR_TAVAN:
        return None, "ömür 0..%d sn arasında olmalı: %r" % (CEP_OMUR_TAVAN, omur_sn)
    alici, hata = cep_alici_sec(tur, alici)
    if hata:
        return None, hata
    c = _db()
    if tur == "konum":     # kişisel veri: yeni istekle birlikte önceki konum sonuçları silinir (en çok bir kayıt durur)
        c.execute("UPDATE kokpit_gorevler SET cikti=NULL WHERE hedef='cep' AND tur='konum' AND cikti IS NOT NULL")
    cur = c.execute("INSERT INTO kokpit_gorevler(tur, veri, durum, ts, hedef, son, alici) "
                    "VALUES(?,?, 'bekliyor', ?, 'cep', ?, ?)",
                    (tur, json.dumps(veri, ensure_ascii=False), time.strftime("%F %T"), time.time() + omur, alici))
    gid = cur.lastrowid
    c.commit()
    c.close()
    if tur == "sd":        # kart: okuma READ, yazma REVERSIBLE, silme geri alınmaz
        sinif = (governance.READ if veri["islem"] in SD_OKUMA else
                 governance.PERMANENT if veri["islem"] == "sil" else governance.REVERSIBLE)
    elif tur == "konum":   # hiçbir şeyi değiştirmez (READ) ama kişisel veri: özerk oturuma yukarıda kapalı
        sinif = governance.READ
    else:
        sinif = governance.READ if _cep_okuma_mi(tur, veri) else governance.REVERSIBLE
    governance.audit("gorevler", "cep_ekle", sinif, ("cep %s #%d %s%s" % (
        tur, gid, _cep_ozet(tur, veri), " → %s" % alici if alici else "")).rstrip())
    return gid, None


def _adressiz_alabilir(cihaz_id, tur, kip, kayit):
    """Adressiz (alici NULL) görev bu cihaza verilebilir mi (`kip`, `kayit`: `_sinif_durumu`). "eski" kipte herkes alır
    (tek cep cihazı, kayıt hiç yazılmamış); "kapali" kipte kimse; "kati" kipte yalnız sınıfı kayıtlı ve o türü anlayan
    cihaz. Kayıtsız cihaz (yeni eşlenmiş saat) ALAMAZ."""
    if kip == "eski":
        return True
    if kip != "kati":
        return False
    return tur in SINIF_TURLER.get(kayit.get(cihaz_id), ())


def cep_al(cihaz_id, limit=1):
    """Bekleyen cep görevlerini ATOMİK al → [{"id","tur","veri","son"}] (en eski önce). Alınan görev aynı
    işlemde `verildi` olur: iki eşzamanlı çağrıdan yalnız biri alır, ikinci kez verilmez (en çok bir kez teslim).
    FREEZE'de görev verilmez. Kuyruk boşken yazma kilidi alınmaz (uzun yoklama yarım saniyede bir sorar).
    Cihaz yalnız KENDİNE adresli görevi alır; adressiz görev için bkz. `_adressiz_alabilir`."""
    if governance.is_frozen():
        return []
    cihaz_id = str(cihaz_id)
    alinan = []
    c = _db()
    try:
        if not c.execute("SELECT 1 FROM kokpit_gorevler WHERE durum='bekliyor' AND hedef='cep' "
                         "AND (alici=? OR alici IS NULL) LIMIT 1", (cihaz_id,)).fetchone():
            return []
        # Sınıf kaydı yalnız ADRESSİZ aday varken ve yazma kilidi alınmadan ÖNCE okunur (kayıt ayrı bağlantıyla
        # okunur; kilit içinden okumak bekleyen bir göçle kilitlenebilirdi). Adresli görev kayda hiç bakmaz.
        kip, kayit = "kapali", {}
        if c.execute("SELECT 1 FROM kokpit_gorevler WHERE durum='bekliyor' AND hedef='cep' AND alici IS NULL "
                     "LIMIT 1").fetchone():
            kip, kayit = _sinif_durumu()
        adet = max(1, int(limit))
        simdi = time.time()
        c.isolation_level = None             # işlem elle: BEGIN IMMEDIATE okuma ile yazma arasına kimseyi sokmaz
        c.execute("BEGIN IMMEDIATE")
        try:
            c.execute("UPDATE kokpit_gorevler SET durum='suresi_doldu', biten=? "
                      "WHERE durum='bekliyor' AND son IS NOT NULL AND son < ?", (time.strftime("%F %T"), simdi))
            rows = c.execute("SELECT id,tur,veri,son,alici FROM kokpit_gorevler WHERE durum='bekliyor' AND hedef='cep' "
                             "AND (alici=? OR alici IS NULL) ORDER BY id", (cihaz_id,)).fetchall()
            rows = [r for r in rows if r[4] is not None or _adressiz_alabilir(cihaz_id, r[1], kip, kayit)][:adet]
            for r in rows:
                cur = c.execute("UPDATE kokpit_gorevler SET durum='verildi', verildi=?, cihaz=? "
                                "WHERE id=? AND durum='bekliyor'", (simdi, cihaz_id, r[0]))
                if cur.rowcount:
                    alinan.append({"id": r[0], "tur": r[1], "veri": json.loads(r[2] or "{}"), "son": r[3]})
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    finally:
        c.close()
    for g in alinan:
        governance.audit("gorevler", "cep_verildi", governance.READ, "#%d %s → %s" % (g["id"], g["tur"], cihaz_id))
    return alinan


def _yoklama_yaz(cihaz_id):
    simdi = time.time()
    try:
        durum.yaz(YOKLAMA_AD, {"cihaz": cihaz_id, "ts": simdi})

        def _g(eski):
            d = {k: v for k, v in (eski.items() if isinstance(eski, dict) else ())
                 if isinstance(v, (int, float)) and simdi - v < YOKLAMA_SAKLA_SN}
            d[str(cihaz_id)] = simdi
            return d
        durum.guncelle(YOKLAMALAR_AD, _g, {})
    except Exception:
        pass                              # damga yazılamadı diye teslim durmaz; çağıran "çevrimdışı" görür


def cep_bekle_al(cihaz_id, bekle_sn=0):
    """Cihazın uzun yoklaması: görev varsa hemen, yoksa en çok `bekle_sn` (tavan CEP_BEKLE_TAVAN) bekleyip
    döner. Her çağrı cihazın 'son yoklama' damgasını tazeler (`cep_yoklama`). FREEZE'de görev verilmez;
    süre yine beklenir (cihaz sıkı döngüye girmesin).
    Aynı cihaz yeni bir yoklama açarsa eskisi hemen boş döner: eski bağlantı büyük olasılıkla ölüdür
    (kablosuz ağ koptu, cihaz yeniden bağlandı) ve görevi ölü bağlantıya vermek onu kaybetmek demektir."""
    try:
        bekle = float(bekle_sn or 0)
    except (TypeError, ValueError):
        bekle = 0.0
    bekle = max(0.0, min(bekle, CEP_BEKLE_TAVAN)) if bekle == bekle else 0.0     # NaN → 0
    with _nesil_kilit:
        nesil = _nesil[cihaz_id] = _nesil.get(cihaz_id, 0) + 1
    _yoklama_yaz(cihaz_id)
    bitis = time.time() + bekle
    while True:
        if _nesil.get(cihaz_id) != nesil:
            return []
        g = cep_al(cihaz_id)
        if g or time.time() >= bitis:
            if not g and bekle:
                _yoklama_yaz(cihaz_id)    # uzun bekleyişin sonunda damga yaşlanmış görünmesin
            return g
        time.sleep(CEP_ARALIK)


def cep_yoklama(cihaz_id=None):
    """Son yoklama → (yaş sn, cihaz kimliği) ya da hiç yoklamadıysa None.
    `cihaz_id` verilirse O cihazın damgası (başka cihazın yoklaması onu çevrimiçi göstermez); verilmezse en son
    yoklayan cihaz, hangisi olursa (adressiz dünya ve eski çağıranlar)."""
    try:
        d = durum.oku(YOKLAMA_AD)
        if cihaz_id is None:
            return max(0.0, time.time() - float(d["ts"])), d.get("cihaz")
        cid = str(cihaz_id)
        ts = (durum.oku(YOKLAMALAR_AD) or {}).get(cid)
        if ts is None and d and d.get("cihaz") == cid:
            ts = d["ts"]                  # yama öncesinden kalan tek damga bu cihazınsa geçerli
        return (max(0.0, time.time() - float(ts)), cid) if ts is not None else None
    except Exception:
        return None


def cep_yoklamalar():
    """Cihaz başına son yoklama yaşı {cihaz kimliği: yaş sn} (eski tek damga dahil). Asla fırlatmaz."""
    cikti, simdi = {}, time.time()
    try:
        d = durum.oku(YOKLAMA_AD)
        if d and d.get("cihaz"):
            cikti[str(d["cihaz"])] = max(0.0, simdi - float(d["ts"]))
    except Exception:
        pass
    try:
        for cid, ts in (durum.oku(YOKLAMALAR_AD) or {}).items():
            cikti[str(cid)] = max(0.0, simdi - float(ts))
    except Exception:
        pass
    return cikti


KONUM_SAKLA_SN = 120          # konum sonucu, istek verildikten bu kadar sonra gelirse saklanmaz (bekleyen istemci kalmamıştır)


def _yas_sn(ts):
    """'%F %T' damgasının yaşı (sn); okunamazsa çok eski say (temkinli: saklama)."""
    try:
        return time.time() - time.mktime(time.strptime(str(ts), "%Y-%m-%d %H:%M:%S"))
    except (ValueError, TypeError, OverflowError):
        return float("inf")


def _cep_sonuclandir(gid, ok, mesaj, cihaz, cikti):
    c = _db()
    r = c.execute("SELECT alici, durum, cihaz, tur FROM kokpit_gorevler WHERE id=? AND hedef='cep'", (int(gid),)).fetchone()
    # Sonucu yalnız görevin sahibi yazar: adresli görevde alıcı; adressiz görevde onu fiilen ALAN cihaz (inceleme
    # bulgu 6: `verildi` durumundaki adressiz görevi başka bir cep cihazı sonuçlandırabiliyordu).
    sahip = r and (r[0] or (r[2] if r[1] == "verildi" else None))
    if sahip and cihaz is not None and str(cihaz) != sahip:
        c.close()
        raise HedefUyusmuyor("görev #%s sahibi %s, sonucu yazan %s" % (gid, sahip, cihaz))
    if r and not sahip and r[1] == "bekliyor" and cihaz is not None:
        # Adressiz ve henüz ALINMAMIŞ görev (ikinci inceleme, 2026-11-26): sahibi yok diye herkes yazabiliyordu —
        # saat, alamayacağı bekleyen görevi kimliğini tahmin edip "tamam"/"hata" diye TÜKETEBİLİYOR ve uydurma çıktı
        # bırakabiliyordu. Sonucu yalnız o görevi ALABİLECEK cihaz yazar (kural `cep_al` ile aynı).
        c.close()                         # sınıf kaydı ayrı bağlantıyla okunur (bkz. cep_al'daki not)
        kip, kayit = _sinif_durumu()
        if not _adressiz_alabilir(str(cihaz), r[3], kip, kayit):
            raise HedefUyusmuyor("görev #%s adressiz ve %s onu alamaz (sonucunu da yazamaz)" % (gid, cihaz))
        c = _db()
    cur = c.execute("UPDATE kokpit_gorevler SET durum=?, sonuc=?, cikti=?, cihaz=COALESCE(?, cihaz), biten=? "
                    "WHERE id=? AND hedef='cep' AND durum IN ('bekliyor','verildi')",
                    ("tamam" if ok else "hata", str(mesaj or "")[:300], _cikti_yaz(cikti), cihaz,
                     time.strftime("%F %T"), int(gid)))
    c.commit()
    if not cur.rowcount:
        c.close()
        return None
    r = c.execute("SELECT " + _CEP_SUTUN + " FROM kokpit_gorevler WHERE id=?", (int(gid),)).fetchone()
    c.close()
    g = _cep_satir(r)
    if g["tur"] == "konum" and g.get("cikti") is not None and _yas_sn(g.get("ts")) > KONUM_SAKLA_SN:
        # Geç gelen konum: isteyen istemci beklemeyi çoktan bıraktı, okuyacak kimse yok → hiç saklanmaz.
        cep_cikti_unut(gid)
        g["cikti"] = None
    governance.audit("gorevler", "cep_sonuc", governance.READ,
                     "#%s %s → %s %s" % (gid, g["tur"], g["durum"], g["sonuc"][:80]))
    return g


def cep_sonuc(gid):
    """Cep görevinin güncel hâli (çıktı çözülmüş) ya da None (yok / cep görevi değil)."""
    c = _db()
    _suresi_dolanlar(c)
    r = c.execute("SELECT " + _CEP_SUTUN + " FROM kokpit_gorevler WHERE id=? AND hedef='cep'", (int(gid),)).fetchone()
    c.close()
    return _cep_satir(r) if r else None


def cep_cikti_unut(gid):
    """Cep görevinin saklanan çıktısını sil (durum ve kısa mesaj kalır) → silindi mi. Kişisel veri taşıyan sonuç
    (konum) çağırana verildikten sonra veritabanında ve yedeklerinde durmasın diye."""
    c = _db()
    cur = c.execute("UPDATE kokpit_gorevler SET cikti=NULL WHERE id=? AND hedef='cep' AND cikti IS NOT NULL", (int(gid),))
    c.commit()
    c.close()
    return bool(cur.rowcount)


def cep_bekle(gid, sn=75):
    """Sonuç gelene ya da `sn` dolana dek bekle → görev (durum: tamam|hata|bekliyor|verildi|suresi_doldu|iptal)
    ya da None. `verildi` = cihaz aldı ama sonuç yazmadı (koşmuş da olabilir); `suresi_doldu` = cihaz hiç almadı."""
    bitis = time.time() + max(0.0, float(sn or 0))
    while True:
        g = cep_sonuc(gid)
        if g is None or g["durum"] not in ("bekliyor", "verildi") or time.time() >= bitis:
            return g
        time.sleep(0.25)


def cep_liste(limit=15):
    c = _db()
    _suresi_dolanlar(c)
    rows = c.execute("SELECT " + _CEP_SUTUN + " FROM kokpit_gorevler WHERE hedef='cep' ORDER BY id DESC LIMIT ?",
                     (limit,)).fetchall()
    c.close()
    return [_cep_satir(r) for r in rows]


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
    elif cmd == "sil" and len(argv) > 1:
        kod = 0
        for n in argv[1:]:
            yeni, hata_ = sil_kuyrukla(n)
            if hata_:
                print("HATA #%s: %s" % (n, hata_)); kod = 1
            else:
                print("kuyruğa girdi: #%d (#%s'i kaldır) — telefon çekince silinecek (sonucu /gorevler)" % (yeni, n))
        return kod
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
