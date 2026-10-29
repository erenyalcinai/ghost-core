"""Ghost Kokpit — iOS uygulamasının SUNUCU tarafı yapıştırıcısı.

NEDEN VAR (2026-09-02, Sahip kararı — docs/kokpit.md): Ghost'un tek ağzı
Telegram'dı ve köprü (ops/telegram_bridge.py) `send()`/inline buton/`chat_id`
etrafında kurulu. İkinci bir kanal eklemek için köprüyü yeniden yazmak yerine
üç ince ray açıyoruz; köprü tek yazar kalır, uygulama bunlara bakar:

  1. OUTBOX  — Ghost'un söylediği HER ŞEY (Telegram'a giden `send()` çağrıları)
     ve sahibin yazdığı her şey `kokpit_outbox` tablosuna da düşer. Uygulama
     bunu birleşik döküm olarak okur. Butonlu satırlar karar kartıdır.
  2. INBOX   — uygulamadan gelen mesajlar ops/kokpit_inbox/*.json (atomik
     claim: .proc'a taşı → işle → sil; ops/requests kalıbının aynısı).
     Köprü kendi döngüsünde alır ve Telegram'dan gelmiş gibi işler.
  3. KARARLAR — uygulamadan gelen onay/ret ops/kokpit_kararlar/*.json.
     Köprü `resolve_pending` ile çözer; pending.json'a İKİNCİ YAZAR GİRMEZ
     (yarış yok).

Kimlik: cihaz başına 32 baytlık anahtar + istek imzası (HMAC-SHA256, zaman
penceresi 300 sn, nonce tekrar reddi). Eşleme tek kullanımlık 6 haneli kodla
(/cihaz komutu Telegram'dan). İptal /cihaz-iptal — Telegram düşse bile SSH'tan
`python3 -m ghost.kokpit iptal` ile kapatılır.
"""
import hashlib
import hmac
import itertools
import json
import os
import secrets
import sqlite3
import sys
import time
import uuid

from ghost import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS kokpit_outbox(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, kaynak TEXT, metin TEXT, butonlar TEXT);
CREATE TABLE IF NOT EXISTS kokpit_devices(
    id TEXT PRIMARY KEY, ad TEXT, key_hex TEXT, durum TEXT,
    kod TEXT, kod_son REAL, created TEXT, last_seen TEXT);
CREATE TABLE IF NOT EXISTS kokpit_nonces(nonce TEXT PRIMARY KEY, ts REAL);
"""
_EK_SUTUNLAR = (("apns_token", "TEXT"), ("apns_env", "TEXT"),   # Faz 2 (push)
                ("la_start_token", "TEXT"))                       # Live Activity push-to-start
_EK_OUTBOX = (("medya", "TEXT"),    # Faz 4: Ghost'un/sahibin görseli, videosu, sesi, belgesi (JSON)
              ("kanal", "TEXT"),    # Faz 1.13 (2026-10-29): satır hangi kanaldan (kokpit|telegram|…) — teslim ölçüsü
              ("gecici", "INTEGER"),   # Faz 2.7: 1 = ilk satır (geçici balon), sonuç gelince 0 + metin güncellenir
              ("guncelleme", "REAL"))  # Faz 2.7: son güncelleme anı (epoch); istemci `gts` ile yalnız değişenleri çeker
MEDYA_DIZIN = "kokpit_medya"        # ops/ altında; kaynak dosya buraya KOPYALANIR (özgünü silinebilir)
MEDYA_TAVAN = 200 * 1024 * 1024     # dizin bu boyutu aşınca en eski dosyalar budanır
SCHEMA += """
CREATE TABLE IF NOT EXISTS kokpit_etkinlik(
    jid INTEGER, device_id TEXT, token TEXT, env TEXT, ts TEXT,
    PRIMARY KEY (jid, device_id));
"""
PENCERE_SN = 300          # imza zaman penceresi
KOD_OMRU_SN = 600         # eşleme kodu ömrü
OUTBOX_TUT = 5000         # döküm tavanı (eski satırlar budanır)


def _db():
    c = sqlite3.connect(config.DB_PATH, timeout=10)
    c.executescript(SCHEMA)
    var = {r[1] for r in c.execute("PRAGMA table_info(kokpit_devices)")}
    for ad, tip in _EK_SUTUNLAR:
        if ad not in var:
            c.execute("ALTER TABLE kokpit_devices ADD COLUMN %s %s" % (ad, tip))
    var = {r[1] for r in c.execute("PRAGMA table_info(kokpit_outbox)")}
    for ad, tip in _EK_OUTBOX:
        if ad not in var:
            c.execute("ALTER TABLE kokpit_outbox ADD COLUMN %s %s" % (ad, tip))
    return c


def _now():
    return time.strftime("%F %T")


def _dir(ad):
    p = os.path.join(config.OPS, ad)
    os.makedirs(p, exist_ok=True)
    return p


# ---------------------------------------------------------------- OUTBOX
def _butonlar_json(butonlar):
    """Köprü biçimi [[(etiket, veri), ...]] → uygulama biçimi."""
    if not butonlar:
        return None
    rows = []
    for row in butonlar:
        r = []
        for b in row:
            if isinstance(b, dict):
                r.append({"etiket": b.get("etiket", ""), "veri": b.get("veri", "")})
            else:
                r.append({"etiket": b[0], "veri": b[1]})
        rows.append(r)
    return json.dumps(rows, ensure_ascii=False)


_MEDYA_TUR = {".jpg": "gorsel", ".jpeg": "gorsel", ".png": "gorsel", ".gif": "gorsel", ".webp": "gorsel",
              ".heic": "gorsel", ".mp4": "video", ".mov": "video", ".m4v": "video",
              ".mp3": "ses", ".m4a": "ses", ".wav": "ses", ".oga": "ses", ".ogg": "ses", ".opus": "ses"}
_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
         ".webp": "image/webp", ".heic": "image/heic", ".mp4": "video/mp4", ".mov": "video/quicktime",
         ".m4v": "video/x-m4v", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav",
         ".oga": "audio/ogg", ".ogg": "audio/ogg", ".opus": "audio/ogg", ".pdf": "application/pdf",
         ".txt": "text/plain", ".md": "text/markdown", ".json": "application/json", ".html": "text/html",
         ".csv": "text/csv", ".log": "text/plain"}


def medya_tur(ad):
    return _MEDYA_TUR.get(os.path.splitext(ad or "")[1].lower(), "belge")


def medya_mime(ad):
    return _MIME.get(os.path.splitext(ad or "")[1].lower(), "application/octet-stream")


def _medya_buda(d):
    """Dizin tavanı aşınca en eski dosyaları sil (dökümde satır kalır, dosya 404 olur)."""
    try:
        dosyalar = sorted((os.path.getmtime(os.path.join(d, f)), os.path.join(d, f))
                          for f in os.listdir(d))
        toplam = sum(os.path.getsize(p) for _, p in dosyalar)
        while toplam > MEDYA_TAVAN and dosyalar:
            _, p = dosyalar.pop(0)
            toplam -= os.path.getsize(p)
            os.remove(p)
    except OSError:
        pass


def medya_sakla(yol, ad=None, tur=None, kopyala=True):
    """Bir dosyayı döküm medyası yap → {"tur","ad","boyut","mime","yol"} ya da None.
    kopyala=True: ops/kokpit_medya/ altına kopyalanır (özgün dosya geçici olabilir:
    send_voice mp3'ü siler, Mac ekran görüntüleri /tmp'de). Asla fırlatmaz."""
    try:
        if not yol or not os.path.isfile(yol):
            return None
        ad = ad or os.path.basename(yol)
        hedef = yol
        if kopyala:
            d = _dir(MEDYA_DIZIN)
            guvenli = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in ad)[-80:] or "dosya"
            hedef = os.path.join(d, "%s-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"),
                                                  os.urandom(3).hex(), guvenli))
            import shutil
            shutil.copyfile(yol, hedef)
            _medya_buda(d)
        return {"tur": tur or medya_tur(ad), "ad": ad, "boyut": os.path.getsize(hedef),
                "mime": medya_mime(ad), "yol": hedef}
    except Exception:
        return None


def outbox_yaz(metin, kaynak="ghost", butonlar=None, medya=None, kanal=None, gecici=False):
    """Döküme bir satır ekle. Asla fırlatmaz — köprünün send()'ini düşürmesin.
    medya: dosya yolu (kopyalanır) ya da medya_sakla() sözlüğü. kanal: verilmezse channels.son_kanal()."""
    if kanal is None:
        try:
            from ghost import channels as _ch
            kanal = _ch.son_kanal() or ""
        except Exception:
            kanal = ""
    if kaynak == "sahip" and metin:
        try:  # cevap hangi kanaldan/biçimde gelirse gelsin sahnedeki soru kapanır (tek huni);
            # buton cevabı id'li, düz metin sahnedeki bekleyen butona göre (sahne.cozumle_serbest)
            from ghost import sahne
            if metin.startswith("[Sahne eylem:"):
                sahne.cozumle_metinden(metin)
            else:
                sahne.cozumle_serbest(metin)
        except Exception:
            pass
    try:
        if isinstance(medya, str):
            medya = medya_sakla(medya)
        c = _db()
        cur = c.execute(
            "INSERT INTO kokpit_outbox(ts, kaynak, metin, butonlar, medya, kanal, gecici) VALUES(?,?,?,?,?,?,?)",
            (_now(), kaynak, metin or "", _butonlar_json(butonlar),
             json.dumps(medya, ensure_ascii=False) if medya else None, kanal or "", 1 if gecici else 0))
        rid = cur.lastrowid
        if rid % 200 == 0:  # ara sıra buda
            c.execute("DELETE FROM kokpit_outbox WHERE id < ?", (rid - OUTBOX_TUT,))
        c.commit()
        c.close()
        return rid
    except Exception:
        return None


def outbox_guncelle(rid, metin, butonlar=None):
    """Faz 2.7: var olan döküm satırının metnini (ve butonlarını) değiştir — ilk satır balonu sonuçla dolar.
    gecici→0, guncelleme=şimdi; istemci aynı id'yi yerinde değiştirir. → True/False. Asla fırlatmaz."""
    try:
        c = _db()
        n = c.execute("UPDATE kokpit_outbox SET metin=?, butonlar=COALESCE(?, butonlar), gecici=0, guncelleme=? WHERE id=?",
                      (metin or "", _butonlar_json(butonlar) if butonlar else None, time.time(), int(rid))).rowcount
        c.commit()
        c.close()
        return n > 0
    except Exception:
        return False


def outbox_guncellenenler(since_ts, limit=20):
    """guncelleme > since_ts olan satırlar (artan) — SSE/yoklama bunları aynı `mesaj` olayıyla yeniden verir."""
    if not since_ts:
        return []
    c = _db()
    rows = c.execute("SELECT id, ts, kaynak, metin, butonlar, medya, gecici FROM kokpit_outbox WHERE guncelleme > ? "
                     "ORDER BY guncelleme LIMIT ?", (float(since_ts), int(limit))).fetchall()
    c.close()
    return [_satir(*r) for r in rows]


def outbox_son_guncelleme():
    c = _db()
    r = c.execute("SELECT COALESCE(MAX(guncelleme),0) FROM kokpit_outbox").fetchone()[0]
    c.close()
    return float(r or 0)


def _satir(i, ts, kaynak, metin, but, med, gecici=0):
    m = None
    if med:
        try:
            m = json.loads(med)
            m.pop("yol", None)        # sunucu yolu istemciye ÇIKMAZ; dosya /v1/medya/<id> ile gelir
        except Exception:
            m = None
    return {"id": i, "ts": ts, "kaynak": kaynak, "metin": metin,
            "butonlar": json.loads(but) if but else None, "medya": m, "gecici": bool(gecici)}


def outbox_oku(since=0, limit=50, once=None):
    """id > since (artan). since < 0 → EN SON `limit` satır (uygulama açılışı: 5000
    satırlık dökümü baştan yüklemesin). once verilirse id < once olan son `limit`
    satır ("daha eski" sayfası). Her durumda artan sırada döner."""
    c = _db()
    sel = "SELECT id, ts, kaynak, metin, butonlar, medya, gecici FROM kokpit_outbox "
    if once is not None:
        rows = c.execute(sel + "WHERE id < ? ORDER BY id DESC LIMIT ?",
                         (int(once), int(limit))).fetchall()[::-1]
    elif int(since) < 0:
        rows = c.execute(sel + "ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()[::-1]
    else:
        rows = c.execute(sel + "WHERE id > ? ORDER BY id LIMIT ?",
                         (int(since), int(limit))).fetchall()
    c.close()
    return [_satir(*r) for r in rows]


def medya_yol(outbox_id):
    """Döküm satırının medya dosyası → (yol, mime, ad) ya da None. Yol DB'den gelir,
    istemciden değil (path traversal yok)."""
    c = _db()
    r = c.execute("SELECT medya FROM kokpit_outbox WHERE id=?", (int(outbox_id),)).fetchone()
    c.close()
    if not r or not r[0]:
        return None
    try:
        m = json.loads(r[0])
    except Exception:
        return None
    yol = m.get("yol")
    if not yol or not os.path.isfile(yol):
        return None
    return yol, m.get("mime") or medya_mime(m.get("ad")), m.get("ad") or os.path.basename(yol)


def outbox_son():
    c = _db()
    r = c.execute("SELECT COALESCE(MAX(id),0) FROM kokpit_outbox").fetchone()[0]
    c.close()
    return r


def outbox_bekle(since=0, limit=50, bekle=0):
    """Uzun yoklama: yeni satır gelene ya da `bekle` sn dolana kadar tut."""
    son = time.time() + max(0, min(float(bekle or 0), 60))
    while True:
        rows = outbox_oku(since, limit)
        if rows or time.time() >= son:
            return rows
        time.sleep(0.5)


# ---------------------------------------------------------------- INBOX / KARARLAR
_SIRA = itertools.count()


def _kuyrukla(dizin, veri, onek):
    """Dosya adı sıralanabilir: ns zaman + süreç içi sayaç → aynı anda gelen iki
    mesajın sırası korunur (testte yakalandı: aynı ms'de sıra rastgeleydi)."""
    d = _dir(dizin)
    fn = "%s_%019d_%06d_%s.json" % (onek, time.time_ns(), next(_SIRA) % 10 ** 6,
                                   uuid.uuid4().hex[:4])
    tmp = os.path.join(d, fn + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(d, fn))
    return fn


def _al(dizin):
    """Kuyruktaki dosyaları sahiplen (→ .proc), oku, sil; listeyi döndür.
    Yetim .proc (600 sn) kurtarılır — ops/requests kalıbı."""
    d = os.path.join(config.OPS, dizin)
    if not os.path.isdir(d):
        return []
    out = []
    for fn in sorted(os.listdir(d)):
        p = os.path.join(d, fn)
        if fn.endswith(".proc"):
            try:
                if time.time() - os.path.getmtime(p) > 600:
                    os.replace(p, p[:-5])
            except OSError:
                pass
            continue
        if not fn.endswith(".json"):
            continue
        proc = p + ".proc"
        try:
            os.replace(p, proc)
        except OSError:
            continue
        try:
            out.append(json.load(open(proc, encoding="utf-8")))
        except Exception:
            pass
        try:
            os.remove(proc)
        except OSError:
            pass
    return out


def mesaj_kuyrukla(metin, cihaz=None, sesli=False, yol=None, tur=None, ad=None, not_=None,
                   grup=None, sira=None, toplam=None, imza=None):
    """Uygulamadan gelen mesaj. sesli: mikrofonla söylendi (cevap da sesli olur — Telegram
    sesli mesajıyla aynı). yol/tur/ad/not_: paylaşılan görsel/belge (köprü Telegram
    foto/dosya yoluyla aynı istemi kurar). grup/sira/toplam: aynı gönderideki çoklu
    dosya (2026-09-04) — köprü grubu TEK sohbet turu olarak işler, N ayrı tur değil."""
    v = {"metin": metin, "cihaz": cihaz, "ts": _now()}
    if imza:       # onay servisinin cihaz imzasını doğrularken verdiği jeton (imza, ts) — köprü slash komutta arar
        v["imza"], v["imza_ts"] = imza
    if sesli:
        v["sesli"] = True
    if yol:
        v.update({"yol": yol, "tur": tur or medya_tur(ad or yol), "ad": ad or os.path.basename(yol),
                  "not": not_ or ""})
        if grup:
            v.update({"grup": str(grup)[:40], "sira": int(sira or 1), "toplam": max(1, int(toplam or 1))})
    return _kuyrukla("kokpit_inbox", v, "msg")


def inbox_al():
    return _al("kokpit_inbox")


def karar_kuyrukla(pid, onay, cihaz=None):
    return _kuyrukla("kokpit_kararlar",
                     {"pid": pid, "onay": bool(onay), "cihaz": cihaz, "ts": _now()}, "karar")


def kararlar_al():
    return _al("kokpit_kararlar")


def bekleyen_kararlar():
    """Köprünün pending.json'unu SALT OKU (yazar köprüdür)."""
    from ghost import governance
    p = os.path.join(config.OPS, "pending.json")
    try:
        pend = json.load(open(p, encoding="utf-8"))
    except Exception:
        return []
    out = []
    for pid, v in pend.items():
        text = v.get("text", "")
        goster = text
        it = v.get("intent")
        if isinstance(it, dict) and it.get("preview"):
            goster = it["preview"]          # Telegram'daki buton mesajıyla aynı metin
        elif v.get("kind") == "mail" and text.startswith("MAIL|"):
            parca = text[5:].split("|", 2)
            if len(parca) == 3:
                goster = "✉️ Kime: %s\nKonu: %s\n\n%s" % tuple(x.strip() for x in parca)
        out.append({"id": pid, "sinif": governance.classify(text),
                    "tur": v.get("kind", ""), "metin": goster[:1000],
                    "ts": v.get("ts", "")})
    return out


# ---------------------------------------------------------------- CİHAZLAR / KİMLİK
def kod_uret(ad=None):
    """Tek kullanımlık 6 haneli eşleme kodu (10 dk). Eski bekleyen kodlar silinir.
    Onay servisi kuruluysa (Faz 1) kod servis tarafından üretilir ve YALNIZ Telegram'dan sahibe
    gider; burada None döner (kod dökümde/oturumda görünmez — model okuyup eşleyemesin)."""
    from ghost import onay
    if onay.servis_modu():
        onay.kod_uret(ad)
        return None
    kod = "%06d" % secrets.randbelow(10 ** 6)
    _hata_say(sifirla=True)
    c = _db()
    c.execute("DELETE FROM kokpit_devices WHERE durum='bekliyor'")
    c.execute("INSERT INTO kokpit_devices(id, ad, key_hex, durum, kod, kod_son, created) "
              "VALUES(?,?,?,?,?,?,?)",
              (uuid.uuid4().hex[:12], ad or "", "", "bekliyor", kod,
               time.time() + KOD_OMRU_SN, _now()))
    c.commit()
    c.close()
    return kod


ESLESME_DENEME_TAVANI = 5   # bir kod ömründe bu kadar yanlış → kod iptal + sahibe uyarı


def _hata_dosyasi():
    return os.path.join(config.OPS, "eslesme_hata.json")


def _hata_say(sifirla=False):
    """Yanlış kod sayacı (dosyada; API süreci yeniden başlasa da kalır)."""
    yol = _hata_dosyasi()
    if sifirla:
        try:
            os.remove(yol)
        except OSError:
            pass
        return 0
    try:
        with open(yol, encoding="utf-8") as f:
            n = int(json.load(f).get("n", 0))
    except (OSError, ValueError):
        n = 0
    n += 1
    with open(yol, "w", encoding="utf-8") as f:
        json.dump({"n": n, "ts": time.time()}, f)
    return n


def _uyar(metin):
    """Sahibe Telegram uyarısı (ops/send_telegram.py); testte sessiz."""
    if os.environ.get("GHOST_TEST"):
        return
    try:
        import subprocess
        import sys
        subprocess.Popen([sys.executable, os.path.join(config.BASE, "ops", "send_telegram.py")],
                         stdin=subprocess.PIPE).communicate(metin.encode(), timeout=20)
    except Exception:
        pass


def esle(kod, ad=None):
    """Kod doğruysa cihazı etkinleştir → (device_id, key_hex); değilse None.
    Kod ömründe ESLESME_DENEME_TAVANI yanlış deneme → bekleyen kod iptal, sahibe uyarı
    (6 hane × 10 dk × paralel istek = kaba kuvvet penceresi; tavan bunu kapatır)."""
    kod = (kod or "").strip()
    if not kod:
        return None
    from ghost import onay
    if onay.servis_modu():
        r = onay.esle(kod, ad)
        if not r:
            return None
        c = _db()       # meta satır (APNs/Live Activity token'ları burada); anahtar ghost.db'ye YAZILMAZ
        c.execute("INSERT OR REPLACE INTO kokpit_devices(id, ad, key_hex, durum, created, last_seen) "
                  "VALUES(?,?,'','aktif',?,?)", (r["id"], r.get("ad") or ad or "", r.get("created") or _now(), _now()))
        c.commit()
        c.close()
        return r["id"], r["key"]
    c = _db()
    row = c.execute("SELECT id, kod_son FROM kokpit_devices WHERE durum='bekliyor' AND kod=?",
                    (kod,)).fetchone()
    if not row or time.time() > (row[1] or 0):
        bekleyen = c.execute("SELECT COUNT(*) FROM kokpit_devices WHERE durum='bekliyor'").fetchone()[0]
        if bekleyen and _hata_say() >= ESLESME_DENEME_TAVANI:
            c.execute("DELETE FROM kokpit_devices WHERE durum='bekliyor'")
            c.commit()
            _hata_say(sifirla=True)
            _uyar("⚠️ Kokpit eşleme: %d yanlış kod üst üste — bekleyen eşleme kodu İPTAL edildi. "
                  "Sen değilsen biri kod deniyor; sen isen /eslesme ile yeni kod al."
                  % ESLESME_DENEME_TAVANI)
        c.close()
        return None
    _hata_say(sifirla=True)
    key = secrets.token_hex(32)
    c.execute("UPDATE kokpit_devices SET key_hex=?, durum='aktif', kod=NULL, kod_son=NULL, "
              "ad=COALESCE(NULLIF(?, ''), ad), last_seen=? WHERE id=?",
              (key, ad or "", _now(), row[0]))
    c.commit()
    c.close()
    _uyar("📱 Kokpit'e yeni cihaz eşlendi: %s (%s). Sen değilsen hemen /freeze ve haber ver." % (ad or "?", row[0]))
    return row[0], key


def cihaz_ekle(ad):
    """Eşleme kodsuz doğrudan aktif cihaz (derleme zamanı anahtarı için). Onay servisi kuruluysa
    KAPALI: anahtar yalnız eşlemeyle, servis içinde doğar."""
    from ghost import onay
    if onay.servis_modu():
        raise RuntimeError("onay servisi kurulu: cihaz yalnız /cihaz eşleme koduyla eklenir")
    key = secrets.token_hex(32)
    did = uuid.uuid4().hex[:12]
    c = _db()
    c.execute("INSERT INTO kokpit_devices(id, ad, key_hex, durum, created) VALUES(?,?,?,?,?)",
              (did, ad, key, "aktif", _now()))
    c.commit()
    c.close()
    _uyar("📱 Kokpit'e eşleme kodsuz cihaz eklendi: %s (%s). Sen değilsen hemen /freeze ve haber ver." % (ad, did))
    return did, key


def cihazlar():
    from ghost import onay
    if onay.servis_modu():
        try:
            return onay.cihazlar()
        except onay.ServisHatasi:
            pass
    c = _db()
    rows = c.execute("SELECT id, ad, durum, created, last_seen FROM kokpit_devices").fetchall()
    c.close()
    return [dict(zip(("id", "ad", "durum", "created", "last_seen"), r)) for r in rows]


def apns_kaydet(device_id, token, env="development"):
    """Uygulama her açılışta token'ını bildirir (değişebilir)."""
    token = (token or "").strip().lower()
    if not (16 <= len(token) <= 200) or any(ch not in "0123456789abcdef" for ch in token):
        return False
    env = "production" if env == "production" else "development"
    c = _db()
    n = c.execute("UPDATE kokpit_devices SET apns_token=?, apns_env=? WHERE id=? AND durum='aktif'",
                  (token, env, device_id)).rowcount
    if n:
        # Bir fiziksel telefon = bir token. Aynı token eski (yeniden eşlenmiş) kayıtlarda
        # kalırsa her push o kadar kez çalar (2026-09-07: 5 aktif kayıt, aynı token, 5 bildirim).
        c.execute("UPDATE kokpit_devices SET apns_token=NULL WHERE apns_token=? AND id!=?",
                  (token, device_id))
    c.commit()
    c.close()
    return n > 0


def _hex_token(t):
    t = (t or "").strip().lower()
    return t if (16 <= len(t) <= 200 and all(ch in "0123456789abcdef" for ch in t)) else None


def la_token_kaydet(device_id, tur, token, jid=None):
    """tur='start' → cihaz kolonu; tur='update' → (jid, cihaz) satırı."""
    token = _hex_token(token)
    if not token:
        return False
    c = _db()
    if tur == "start":
        n = c.execute("UPDATE kokpit_devices SET la_start_token=? WHERE id=? AND durum='aktif'",
                      (token, device_id)).rowcount
    elif tur == "update" and jid is not None:
        env = (c.execute("SELECT apns_env FROM kokpit_devices WHERE id=?", (device_id,)).fetchone() or [None])[0]
        c.execute("INSERT OR REPLACE INTO kokpit_etkinlik(jid, device_id, token, env, ts) VALUES(?,?,?,?,?)",
                  (int(jid), device_id, token, env or "development", _now()))
        n = 1
    else:
        n = 0
    c.commit()
    c.close()
    return n > 0


def la_start_tokenlar():
    c = _db()
    rows = c.execute("SELECT id, la_start_token, apns_env FROM kokpit_devices "
                     "WHERE durum='aktif' AND la_start_token IS NOT NULL AND la_start_token != ''").fetchall()
    c.close()
    return [{"id": r[0], "token": r[1], "env": r[2] or "development"} for r in rows]


def la_start_sil(device_id):
    c = _db()
    c.execute("UPDATE kokpit_devices SET la_start_token=NULL WHERE id=?", (device_id,))
    c.commit()
    c.close()


def la_update_tokenlar(jid):
    c = _db()
    rows = c.execute("SELECT device_id, token, env FROM kokpit_etkinlik WHERE jid=?", (int(jid),)).fetchall()
    c.close()
    return [{"device_id": r[0], "token": r[1], "env": r[2] or "development"} for r in rows]


def la_update_sil(jid):
    c = _db()
    c.execute("DELETE FROM kokpit_etkinlik WHERE jid=?", (int(jid),))
    c.commit()
    c.close()


def apns_sil(device_id):
    c = _db()
    c.execute("UPDATE kokpit_devices SET apns_token=NULL WHERE id=?", (device_id,))
    c.commit()
    c.close()


def apns_cihazlar():
    c = _db()
    rows = c.execute("SELECT id, ad, apns_token, apns_env FROM kokpit_devices "
                     "WHERE durum='aktif' AND apns_token IS NOT NULL AND apns_token != '' "
                     "ORDER BY COALESCE(last_seen, '') DESC").fetchall()
    c.close()
    out, gorulen = [], set()          # aynı token bir kez: en son görülen kayıt kazanır
    for r in rows:
        if r[2] in gorulen:
            continue
        gorulen.add(r[2])
        out.append(dict(zip(("id", "ad", "apns_token", "apns_env"), r)))
    return out


def iptal(device_id=None):
    """Cihaz(lar)ı kapat. id yoksa HEPSİ — acil kapı. Servis kuruluysa anahtar orada da silinir."""
    from ghost import onay
    if onay.servis_modu():
        onay.iptal(device_id)
    c = _db()
    if device_id:
        n = c.execute("UPDATE kokpit_devices SET durum='iptal', key_hex='', apns_token=NULL WHERE id=?",
                      (device_id,)).rowcount
    else:
        n = c.execute("UPDATE kokpit_devices SET durum='iptal', key_hex='', apns_token=NULL "
                      "WHERE durum IN ('aktif','bekliyor')").rowcount
    c.commit()
    c.close()
    return n


def imza(key_hex, ts, nonce, method, path, body=b""):
    msg = "%s\n%s\n%s\n%s\n%s" % (ts, nonce, method.upper(), path,
                                  hashlib.sha256(body or b"").hexdigest())
    return hmac.new(bytes.fromhex(key_hex), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def dogrula_neden(headers, method, path, body=b""):
    """İmzalı isteği doğrula → (cihaz kaydı | None, neden).
    neden: ok · tekrar-get · baslik · ts-bicim · nonce-bicim · cihaz · imza · saat · tekrar.
    headers: küçük-harf anahtarlı dict ya da .get destekleyen nesne.

    NEDEN AYRIŞIYOR (2026-09-07): iOS URLSession bağlantı koptuğunda AYNI isteği (aynı
    nonce ile) ikinci kez yollar — 25 sn'lik outbox uzun yoklamasında sık. Eski kod her
    reddi tek bir None'a indiriyordu, API 401 basıyordu, uygulama da her 401'de eşleme
    ekranına düşüyordu: bir gecede 4 eşleme, 5 aktif kayıt, 5 kat bildirim. 401 yalnız
    GERÇEK kimlik hatası olmalı (cihaz yok / imza tutmuyor). GET tekrarı idempotent →
    kabul edilir; POST tekrarı reddedilir ama 'tekrar' diye ayrışır. Saat penceresi de
    kimlik hatası değildir ('saat'). İmza doğrulanmadan saat/tekrar bilgisi verilmez."""
    from ghost import onay
    if onay.servis_modu():
        # Faz 1 (2026-10-28): cihaz anahtarları onay servisinde (ghost-onay kullanıcısı). İmzayı servis
        # doğrular; onay/karar yolundaysa (eylem, mesaj, kararlar) kaydı da imzayla aynı adımda yazar.
        cihaz, neden = onay.kokpit_dogrula(headers, method, path, body)
        if cihaz:
            try:
                c = _db()
                c.execute("UPDATE kokpit_devices SET last_seen=? WHERE id=?", (_now(), cihaz["id"]))
                c.commit()
                c.close()
            except Exception:
                pass
        return cihaz, neden
    g = lambda k: headers.get(k) or headers.get(k.lower()) or ""  # noqa: E731
    did, ts, nonce, sig = g("X-Ghost-Device"), g("X-Ghost-Ts"), g("X-Ghost-Nonce"), g("X-Ghost-Sig")
    if not (did and ts and nonce and sig):
        return None, "baslik"
    try:
        tsf = float(ts)
    except ValueError:
        return None, "ts-bicim"
    if not (8 <= len(nonce) <= 64):
        return None, "nonce-bicim"
    c = _db()
    row = c.execute("SELECT id, ad, key_hex FROM kokpit_devices WHERE id=? AND durum='aktif'",
                    (did,)).fetchone()
    if not row or not row[2]:
        c.close()
        return None, "cihaz"
    bekl = imza(row[2], ts, nonce, method, path, body)
    if not hmac.compare_digest(bekl, sig.lower()):
        c.close()
        return None, "imza"
    if abs(time.time() - tsf) > PENCERE_SN:
        c.close()
        return None, "saat"
    neden = "ok"
    c.execute("DELETE FROM kokpit_nonces WHERE ts < ?", (time.time() - 2 * PENCERE_SN,))
    try:
        c.execute("INSERT INTO kokpit_nonces(nonce, ts) VALUES(?,?)", (did + ":" + nonce, time.time()))
    except sqlite3.IntegrityError:
        if method.upper() != "GET":      # yazan isteğin tekrarı: oynatma koruması
            c.close()
            return None, "tekrar"
        neden = "tekrar-get"             # okuyan isteğin bire bir tekrarı: zararsız
    c.execute("UPDATE kokpit_devices SET last_seen=? WHERE id=?", (_now(), did))
    c.commit()
    c.close()
    return {"id": row[0], "ad": row[1]}, neden


def dogrula(headers, method, path, body=b""):
    """İmzalı isteği doğrula → cihaz kaydı (dict) ya da None (neden için dogrula_neden)."""
    return dogrula_neden(headers, method, path, body)[0]


# ---------------------------------------------------------------- CLI
def main(argv):
    cmd = argv[0] if argv else "cihazlar"
    if cmd in ("kod", "ekle"):
        # 2026-10-28 bağımsız denetim K2: oturum CLI'den aktif cihaz anahtarı (ya da eşleme kodu) üretip
        # imzalı /v1/eylem ile kendi "gerçek onayını" yazabiliyordu. Eşleme yalnız sahibin /eslesme komutuyla.
        print("kapalı: eşleme kodu yalnız sahibin Telegram/Kokpit /eslesme komutuyla üretilir.", file=sys.stderr)
        return 2
    elif cmd == "cihazlar":
        for c in cihazlar():
            print(json.dumps(c, ensure_ascii=False))
    elif cmd == "iptal":
        print("iptal:", iptal(argv[1] if len(argv) > 1 else None))
    elif cmd == "outbox":
        for r in outbox_oku(int(argv[1]) if len(argv) > 1 else -1, 20):
            print(json.dumps(r, ensure_ascii=False))
    elif cmd == "medya" and len(argv) > 1:
        print(outbox_yaz(argv[2] if len(argv) > 2 else "", "ghost", medya=argv[1]))
    else:
        print("kullanım: cihazlar | iptal [id] | outbox [since] | medya <dosya> [açıklama]")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
