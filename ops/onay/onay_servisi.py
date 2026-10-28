#!/usr/bin/python3 -I
"""Ghost onay servisi — onay kaydı, Kokpit cihaz anahtarları ve karar kuyruğu AYRI KULLANICIDA.

NEDEN VAR (2026-10-28, bağımsız denetim K1/K2/Y4, Faz 1 madde 10): onay kaydı
(ops/sahne_onaylar.json), Kokpit cihaz anahtarları (ghost.db kokpit_devices.key_hex) ve karar
kuyruğu (ops/kokpit_kararlar/) modelle AYNI `ghost` kullanıcısındaydı. Model dosyayı yazıp,
anahtarı okuyup imzalayıp ya da karar dosyası bırakıp kendi "gerçek onayını" üretebiliyordu.

Bu süreç `ghost-onay` kullanıcısıyla koşar; durumu /var/lib/ghost-onay (700) altında, kodu
root sahipli /usr/local/lib/ghost-onay/ altındadır. `ghost` (model dahil) yalnız Unix soketinden
(/run/ghost-onay/onay.sock) soru sorar. Onay YALNIZ iki kanıtla yazılır:
  1. Kokpit: imzalı isteğin kendisi (başlıklar + gövde). İmzayı BU süreç, yalnız kendisinin
     bildiği cihaz anahtarıyla doğrular; ghost-api yalnız ileticidir.
  2. Telegram: isteği yapan sürecin PID'i (SO_PEERCRED) ghost-bridge.service'in MainPID'i
     olmalı ve gönderen uid sahip olmalı. Model köprünün çocuğudur; ptrace_scope=1 ataya
     dokunamaz. Eşleme kodu da yalnız köprüden istenir ve koda YALNIZ Telegram'dan sahibe
     gider — çağırana dönmez, dökümde görünmez.
Tam izolasyon değildir (köprü kodu hâlâ ghost'un yazabildiği yerde; değiştirip yeniden
başlatmak çok adımlı ve kapının gözündedir). Deneyim değişmez.

Protokol: tek bağlantı = tek JSON satırı istek → tek JSON satırı cevap. {"op": ..., ...}.
Bağımlılık yok (stdlib); repodan import YOK — bilinçli, root sahipli kopya kendi başına koşar.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import socketserver
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid

SURUM = 2
JETON_OMUR_SN = 60      # op_kokpit'in verdiği inbox imza jetonu (paylaşım/ilet) bu kadar yaşar, tek kullanımlık
DURUM_DIZIN = "/var/lib/ghost-onay"
SOKET = "/run/ghost-onay/onay.sock"
ONAY_OMUR_SN = 24 * 3600
KART_OMUR_SN = 7 * 86400
KARAR_OMUR_SN = 3600
PENCERE_SN = 300          # Kokpit imza zaman penceresi (ghost/kokpit.py ile aynı)
KOD_OMRU_SN = 600
ESLESME_DENEME_TAVANI = 5
ISTEK_TAVAN = 2 * 1024 * 1024
KOPRU_BIRIMI = "ghost-bridge.service"
INBOX_OMUR_SN = 600                       # imzalı inbox jetonu bu kadar geçerli (API yazar, köprü okur)

EID_DESEN = re.compile(r"^onay-[A-Za-z0-9_-]{1,48}$")
EYLEM_DESEN = re.compile(r"^\[Sahne eylem: ([^\]]+)\]\s*(.*)$", re.S)
# ghost/sahne.py _ONAY_KESIN'in aynısı: mesajın TAMAMI kısa bir evet olmalı.
ONAY_KESIN = re.compile(r"^\W*(onayla|onaylıyorum|onay|evet|tamam|olur|ok|okey|yap|devam|uygula|yayınla)"
                        r"(\s+(onaylıyorum|yap|gönder|uygula|devam))?[\s.!👍✅]*$", re.I)
ONAY_YOLLARI = ("/v1/eylem", "/v1/mesaj")

SCHEMA = """
CREATE TABLE IF NOT EXISTS onaylar(
    eid TEXT PRIMARY KEY, olusturma REAL, onizleme_sha TEXT, onizleme TEXT, kirpildi INTEGER,
    acik INTEGER, ts REAL, kanal TEXT, tuketildi INTEGER DEFAULT 0, tuketim_ts REAL);
CREATE TABLE IF NOT EXISTS kararlar(pid TEXT PRIMARY KEY, ts REAL, cihaz TEXT, tuketildi INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS cihazlar(
    id TEXT PRIMARY KEY, ad TEXT, key_hex TEXT, durum TEXT, kod TEXT, kod_son REAL,
    created TEXT, last_seen TEXT);
CREATE TABLE IF NOT EXISTS nonces(nonce TEXT PRIMARY KEY, ts REAL);
CREATE TABLE IF NOT EXISTS sayac(ad TEXT PRIMARY KEY, n INTEGER, ts REAL);
CREATE TABLE IF NOT EXISTS gunluk(ts REAL, op TEXT, pid INTEGER, uid INTEGER, ayrinti TEXT);
CREATE TABLE IF NOT EXISTS inbox_tuketilen(imza TEXT PRIMARY KEY, ts REAL);
"""
KART_TURLERI = ("genel", "mail", "kapi")
HOOK_BIRIMI = "ghost-hook.service"       # kural tetiklerini sohbete düşüren ikinci güvenilir süreç (sistem birimi)


def komut_ozeti(komut):
    """ghost/sahne.py komut_ozeti ve kapı _komut_ozeti ile AYNI (repodan import yok)."""
    k = re.sub(r"\s*(?:;\s*:|&&\s*:|#)?\s*\bonay-[0-9a-f]{16}\b", "", komut or "")
    k = re.sub(r"\s+", " ", k).strip().rstrip(";").strip()
    return hashlib.sha256(k.encode("utf-8")).hexdigest()[:16]


_KART_KOMUT = re.compile(r"\AKomut:\n(.*?)\n\nKomut özeti: [0-9a-f]{16}\Z", re.S)


def _now():
    return time.strftime("%F %T")


def _systemd_mainpid(birim):
    try:
        out = subprocess.run(["/usr/bin/systemctl", "show", "-p", "MainPID", "--value", birim],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return int(out or 0)
    except Exception:
        return 0


def _telegram(token, chat_id, metin):
    if not (token and chat_id):
        return False
    try:
        veri = urllib.parse.urlencode({"chat_id": chat_id, "text": metin}).encode()
        urllib.request.urlopen(urllib.request.Request(
            "https://api.telegram.org/bot%s/sendMessage" % token, data=veri), timeout=20).read()
        return True
    except Exception:
        return False


# 6. tur (2026-10-29): FREEZE ortak konumu — köprü bayrak durumunu /run/ghost-freeze/durum'a (1/0) aynalar (ghost/governance.py).
# FREEZE'de onay kaydı/tüketimi/kart açma/tetik imzası durur: gerçek acil durdurmada onaylı iş de yürümez, mail gitmez.
FREEZE_YOL = "/run/ghost-freeze/durum"      # içerik "1" = durdurma aktif
FREEZE_KAPALI = frozenset({"sahip_onayi", "tuket", "komut_tuket", "karar_tuket", "kart_ac", "tetik_imzala"})


def _dondu(yol):
    try:
        with open(yol, encoding="utf-8") as f:
            return f.read(1) == "1"
    except OSError:
        return False


class Onay:
    """Durum + kurallar. Soketten bağımsız (testler doğrudan isle() çağırır)."""

    def __init__(self, dizin, kopru_pid=None, telegram=None, freeze_yol=None):
        self.dizin = dizin
        self.freeze_yol = freeze_yol or FREEZE_YOL
        self.kilit = threading.Lock()
        self.db = sqlite3.connect(os.path.join(dizin, "onay.db"), timeout=10, check_same_thread=False)
        self.db.executescript(SCHEMA)
        var = {r[1] for r in self.db.execute("PRAGMA table_info(onaylar)")}
        if "tur" not in var:
            self.db.execute("ALTER TABLE onaylar ADD COLUMN tur TEXT")      # SURUM 2: kart türü
        if "baglam" not in var:
            self.db.execute("ALTER TABLE onaylar ADD COLUMN baglam TEXT")   # SURUM 2: kapı komut özeti
        self.db.commit()
        self.ayar = self._ayar_oku()
        self._kopru_pid = kopru_pid or (lambda: _systemd_mainpid(self.ayar.get("kopru_birimi") or KOPRU_BIRIMI))
        self._telegram = telegram or (lambda m: _telegram(self.ayar.get("telegram_token"),
                                                          self.ayar.get("sahip_id"), m))
        self.inbox_key = self._inbox_key()
        self.inbox_jetonlar = {}      # jeton → (cihaz, ts); yalnız geçerli cihaz imzasında üretilir (op_kokpit)

    def _inbox_key(self):
        """Kokpit inbox imza anahtarı — /var/lib/ghost-onay altında (ghost/model okuyamaz). İlk koşuda üretilir."""
        yol = os.path.join(self.dizin, "inbox.key")
        try:
            with open(yol, "rb") as f:
                k = f.read().strip()
            if len(k) >= 32:
                return k
        except OSError:
            pass
        k = secrets.token_hex(32).encode()
        try:
            fd = os.open(yol, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(k)
        except OSError:
            pass
        return k

    def _ayar_oku(self):
        try:
            with open(os.path.join(self.dizin, "ayar.json"), encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _gunluk(self, op, peer, ayrinti=""):
        self.db.execute("INSERT INTO gunluk VALUES(?,?,?,?,?)",
                        (time.time(), op, peer[0], peer[1], str(ayrinti)[:300]))
        self.db.execute("DELETE FROM gunluk WHERE ts < ?", (time.time() - 90 * 86400,))

    # ------------------------------------------------------------ giriş
    def isle(self, istek, peer):
        """istek: dict, peer: (pid, uid, gid) → cevap dict. Asla fırlatmaz."""
        izinli = set(self.ayar.get("izinli_uid") or []) | {0, os.getuid()}
        if peer[1] not in izinli:
            return {"ok": False, "hata": "izinsiz kullanıcı"}
        op = str(istek.get("op") or "")
        fn = getattr(self, "op_" + op, None) if re.fullmatch(r"[a-z_]{2,30}", op) else None
        if not fn:
            return {"ok": False, "hata": "bilinmeyen op"}
        if op in FREEZE_KAPALI and _dondu(self.freeze_yol):
            return {"ok": False, "hata": "FREEZE: onay işlemleri durdu (/unfreeze ile aç)", "tuketildi": False, "eid": None}
        with self.kilit:
            try:
                r = fn(istek, peer)
                self.db.commit()
                return r
            except Exception as e:
                self.db.rollback()
                return {"ok": False, "hata": "%s: %s" % (type(e).__name__, str(e)[:200])}

    def _kopruden_mi(self, peer):
        return peer[0] > 1 and peer[0] == self._kopru_pid()

    def _inbox_imza(self, metin, ts):
        msg = ("%s\n%d" % (hashlib.sha256((metin or "").encode("utf-8")).hexdigest(), int(ts))).encode()
        return hmac.new(self.inbox_key, msg, hashlib.sha256).hexdigest()

    def op_inbox_dogrula(self, i, peer):
        """Köprü: bu inbox mesajı imzalı (device-authenticated API'den) mi? Yalnız köprü ana süreci sorar."""
        if not self._kopruden_mi(peer):
            return {"ok": False, "hata": "yalnız köprü ana süreci"}
        try:
            ts = int(i.get("ts") or 0)
        except (TypeError, ValueError):
            return {"ok": True, "gecerli": False}
        imza = str(i.get("imza") or "")
        ok = bool(imza and 0 < time.time() - ts < INBOX_OMUR_SN
                  and hmac.compare_digest(imza, self._inbox_imza(str(i.get("metin") or ""), ts)))
        if ok:
            # Tek kullanımlık (2. tur bulgu 5): aynı imza 10 dk içinde ikinci kez geçmesin (/unfreeze tekrar oynatma).
            self.db.execute("DELETE FROM inbox_tuketilen WHERE ts < ?", (time.time() - 2 * INBOX_OMUR_SN,))
            try:
                self.db.execute("INSERT INTO inbox_tuketilen(imza, ts) VALUES(?,?)", (imza, time.time()))
            except sqlite3.IntegrityError:
                return {"ok": True, "gecerli": False, "neden": "imza zaten tüketildi"}
        return {"ok": True, "gecerli": ok}

    # ------------------------------------------------------------ onay kaydı
    def op_ping(self, i, peer):
        return {"ok": True, "surum": SURUM}

    def op_kart_ac(self, i, peer):
        """Onay kartı açıldı: sahibe gösterilen önizleme saklanır. İLK kayıt kilitlenir —
        aynı id ikinci kez açılıp önizlemesi değiştirilemez."""
        eid = str(i.get("eid") or "")
        if not EID_DESEN.match(eid):
            return {"ok": False, "hata": "eid biçimi"}
        onizleme = str(i.get("onizleme") or "")
        tur = str(i.get("tur") or "genel")
        baglam = None
        if tur not in KART_TURLERI:
            return {"ok": False, "hata": "kart türü: %s" % "|".join(KART_TURLERI)}
        if tur == "kapi":
            # 4. tur K1: bağlam ÇAĞIRANDAN alınmaz — sahibe gösterilen önizlemedeki "Komut:" bloğundan servis
            # kendisi hesaplar. Önizleme ile koşacak komut ayrışamaz.
            m = _KART_KOMUT.match(onizleme)
            if not m:
                return {"ok": False, "hata": "kapi kartı önizlemesi 'Komut:\\n…\\n\\nKomut özeti: …' biçiminde olmalı"}
            baglam = komut_ozeti(m.group(1))
            if i.get("baglam") and str(i["baglam"]) != baglam:
                return {"ok": False, "hata": "bağlam önizlemedeki komutla uyuşmuyor"}
        if self.db.execute("SELECT 1 FROM onaylar WHERE eid=?", (eid,)).fetchone():
            return {"ok": False, "hata": "kart zaten açılmış"}
        self.db.execute("DELETE FROM onaylar WHERE olusturma < ?", (time.time() - KART_OMUR_SN,))
        self.db.execute("INSERT INTO onaylar(eid, olusturma, onizleme_sha, onizleme, kirpildi, acik, tur, baglam) "
                        "VALUES(?,?,?,?,?,1,?,?)",
                        (eid, time.time(), hashlib.sha256(onizleme.encode("utf-8")).hexdigest(),
                         onizleme[:20000], 1 if len(onizleme) > 8000 else 0, tur, baglam))
        return {"ok": True}

    def _guvenilir_surec(self, peer):
        """Köprü ana süreci ya da ghost-hook sistem birimi (cgroup root'un; ghost oraya süreç koyamaz)."""
        if self._kopruden_mi(peer):
            return True
        try:
            with open("/proc/%d/cgroup" % peer[0], encoding="ascii") as f:
                return ("/" + HOOK_BIRIMI) in f.read()
        except OSError:
            return False

    def op_tetik_imzala(self, i, peer):
        """Kural tetiği sohbete düşerken köprü/hook imzalatır; imzasız inbox kaydı köprüde tam yetkili sohbete
        girmez (özerk işe gider). Model (köprü çocuğu) bu imzayı alamaz."""
        if not self._guvenilir_surec(peer):
            self._gunluk("red-tetik-imza", peer)
            return {"ok": False, "hata": "yalnız köprü/hook"}
        ts = int(time.time())
        return {"ok": True, "imza": self._inbox_imza(str(i.get("metin") or "").strip(), ts), "ts": ts}

    def op_kartlar_esitle(self, i, peer):
        """Sahnede artık görünmeyen açık kartlar kapanır (düz 'evet' yalnız GÖRÜNEN tek karta).
        Yalnız kapatır, açmaz/onaylamaz. 4. tur K1: yalnız köprü ana süreci — model görünen kartı kapatıp
        düz "evet"i gizli karta yönlendiremesin."""
        if not self._kopruden_mi(peer):
            return {"ok": False, "hata": "yalnız köprü ana süreci"}
        acik = {str(x) for x in (i.get("acik") or [])}
        rows = self.db.execute("SELECT eid FROM onaylar WHERE acik=1 AND ts IS NULL").fetchall()
        n = 0
        for (eid,) in rows:
            if eid not in acik:
                self.db.execute("UPDATE onaylar SET acik=0 WHERE eid=?", (eid,))
                n += 1
        return {"ok": True, "kapanan": n}

    def op_kart_kapat(self, i, peer):
        if not self._kopruden_mi(peer):
            return {"ok": False, "hata": "yalnız köprü ana süreci"}
        self.db.execute("UPDATE onaylar SET acik=0 WHERE eid=? AND ts IS NULL", (str(i.get("eid") or ""),))
        return {"ok": True}

    def _onayla(self, eid, kanal, peer):
        r = self.db.execute("SELECT ts, tuketildi FROM onaylar WHERE eid=?", (eid,)).fetchone()
        if r and r[1]:
            return None                    # tüketilmiş onay ikinci "onayla" ile yeniden açılmaz
        if r:
            self.db.execute("UPDATE onaylar SET ts=?, kanal=?, acik=0 WHERE eid=?", (time.time(), kanal, eid))
        else:                              # kartı servise bildirilmemiş (eski kart) → önizlemesiz onay
            self.db.execute("INSERT INTO onaylar(eid, olusturma, kirpildi, acik, ts, kanal) VALUES(?,?,0,0,?,?)",
                            (eid, time.time(), time.time(), kanal))
        self._gunluk("onay", peer, "%s %s" % (eid, kanal))
        return eid

    def _metinden(self, metin, kanal, peer):
        m = (metin or "").strip()
        e = EYLEM_DESEN.match(m)
        if e:
            eid = e.group(1).strip()
            if EID_DESEN.match(eid) and e.group(2).strip().lower() == "onayla":
                return self._onayla(eid, kanal, peer)
            return None
        if ONAY_KESIN.match(m):
            rows = self.db.execute("SELECT eid FROM onaylar WHERE acik=1 AND ts IS NULL AND olusturma > ?",
                                   (time.time() - ONAY_OMUR_SN,)).fetchall()
            if len(rows) == 1:             # birden çok açık kart varsa düz "evet" hiçbirini onaylamaz
                return self._onayla(rows[0][0], kanal, peer)
        return None

    def op_sahip_onayi(self, i, peer):
        """Telegram kanalı: yalnız köprünün ana süreci, yalnız sahibin uid'iyle."""
        if not self._kopruden_mi(peer):
            self._gunluk("red-kopru-degil", peer, str(i.get("metin") or "")[:80])
            return {"ok": False, "hata": "yalnız köprü ana süreci"}
        sahip = self.ayar.get("sahip_id")
        if not sahip or str(i.get("uid")) != str(sahip):
            return {"ok": False, "hata": "sahip değil"}
        return {"ok": True, "eid": self._metinden(str(i.get("metin") or ""), "telegram", peer)}

    def _gecerli_satir(self, eid):
        return self.db.execute("SELECT ts, tuketildi, onizleme_sha, kirpildi, tur FROM onaylar WHERE eid=?",
                               (str(eid or ""),)).fetchone()

    def op_gecerli(self, i, peer):
        r = self._gecerli_satir(i.get("eid"))
        ok = bool(r and r[0] and not r[1] and time.time() - r[0] < ONAY_OMUR_SN)
        return {"ok": True, "gecerli": ok}

    def op_tuket(self, i, peer):
        """Geçerliyse tüket (atomik). SURUM 2 (2026-10-28, 2. tur bulgu 1): onay İŞE bağlı — tüketen taraf kart
        TÜRÜNÜ ve sahibe gösterilen önizlemenin ÖZETİNİ söylemek zorunda; ikisi de karttakiyle eşleşmeli ve
        önizleme kırpılmamış olmalı. Tüketen (vekil SMTP, kapı) içeriği önizlemeyle kendisi karşılaştırır."""
        eid = str(i.get("eid") or "")
        r = self._gecerli_satir(eid)
        if not (r and r[0] and not r[1] and time.time() - r[0] < ONAY_OMUR_SN):
            return {"ok": True, "tuketildi": False, "neden": "onay yok"}
        sha, tur = i.get("onizleme_sha"), i.get("tur")
        if not sha or not tur:
            return {"ok": True, "tuketildi": False, "neden": "tür ve önizleme özeti zorunlu"}
        if (r[4] or "genel") != tur:
            return {"ok": True, "tuketildi": False, "neden": "kart türü farklı (%s)" % (r[4] or "genel")}
        if r[3] or not r[2] or not hmac.compare_digest(str(r[2]), str(sha)):
            return {"ok": True, "tuketildi": False, "neden": "önizleme eşleşmiyor"}
        self.db.execute("UPDATE onaylar SET tuketildi=1, tuketim_ts=? WHERE eid=?", (time.time(), eid))
        self._gunluk("tuket", peer, eid)
        return {"ok": True, "tuketildi": True}

    def op_komut_tuket(self, i, peer):
        """Kapı istisnası (2. tur bulgu 1): onay 'kapi' türünde, bağlamı (komut özeti) eşleşen, geçerli ve
        tüketilmemiş mi? Atomik tüket. Bir komut için verilen onay başka komutta geçmez."""
        eid = str(i.get("eid") or "")
        baglam = str(i.get("baglam") or "")
        r = self.db.execute("SELECT ts, tuketildi, tur, baglam FROM onaylar WHERE eid=?", (eid,)).fetchone()
        if not (r and r[0] and not r[1] and time.time() - r[0] < ONAY_OMUR_SN):
            return {"ok": True, "tuketildi": False, "neden": "onay yok"}
        if r[2] != "kapi" or not baglam or r[3] != baglam:
            self._gunluk("komut-tuket-red", peer, "%s tur=%s" % (eid, r[2]))
            return {"ok": True, "tuketildi": False, "neden": "komut bu onaya ait değil"}
        self.db.execute("UPDATE onaylar SET tuketildi=1, tuketim_ts=? WHERE eid=?", (time.time(), eid))
        self._gunluk("komut-tuket", peer, eid)
        return {"ok": True, "tuketildi": True}

    def op_az_once_tuketildi(self, i, peer):
        r = self.db.execute("SELECT tuketildi, tuketim_ts FROM onaylar WHERE eid=?",
                            (str(i.get("eid") or ""),)).fetchone()
        pencere = min(float(i.get("pencere_sn") or 120), 600)
        return {"ok": True, "evet": bool(r and r[0] and time.time() - float(r[1] or 0) < pencere)}

    def op_onizleme(self, i, peer):
        r = self.db.execute("SELECT onizleme, onizleme_sha, kirpildi, tur, ts, tuketildi FROM onaylar WHERE eid=?",
                            (str(i.get("eid") or ""),)).fetchone()
        return {"ok": True, "onizleme": r[0] if r else None, "sha": r[1] if r else None,
                "kirpildi": bool(r[2]) if r else None, "tur": (r[3] or "genel") if r else None,
                "gecerli": bool(r and r[4] and not r[5] and time.time() - r[4] < ONAY_OMUR_SN)}

    # ------------------------------------------------------------ Kokpit kimliği
    def op_kokpit(self, i, peer):
        """İmzalı Kokpit isteğini doğrula; onay/karar yolundaysa kaydı da burada yaz."""
        h = i.get("h") or {}
        did, ts, nonce, sig = (str(h.get(k) or "") for k in ("device", "ts", "nonce", "sig"))
        method = str(i.get("method") or "GET").upper()
        path = str(i.get("path") or "")
        govde = None
        if i.get("body_b64") is not None:
            govde = base64.b64decode(i["body_b64"])
            body_sha = hashlib.sha256(govde).hexdigest()
        else:
            body_sha = str(i.get("body_sha") or hashlib.sha256(b"").hexdigest())
        if not (did and ts and nonce and sig):
            return {"ok": True, "cihaz": None, "neden": "baslik"}
        try:
            tsf = float(ts)
        except ValueError:
            return {"ok": True, "cihaz": None, "neden": "ts-bicim"}
        if not (8 <= len(nonce) <= 64):
            return {"ok": True, "cihaz": None, "neden": "nonce-bicim"}
        row = self.db.execute("SELECT id, ad, key_hex FROM cihazlar WHERE id=? AND durum='aktif'", (did,)).fetchone()
        if not row or not row[2]:
            return {"ok": True, "cihaz": None, "neden": "cihaz"}
        msg = "%s\n%s\n%s\n%s\n%s" % (ts, nonce, method, path, body_sha)
        bekl = hmac.new(bytes.fromhex(row[2]), msg.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(bekl, sig.lower()):
            return {"ok": True, "cihaz": None, "neden": "imza"}
        if abs(time.time() - tsf) > PENCERE_SN:
            return {"ok": True, "cihaz": None, "neden": "saat"}
        neden = "ok"
        self.db.execute("DELETE FROM nonces WHERE ts < ?", (time.time() - 2 * PENCERE_SN,))
        try:
            self.db.execute("INSERT INTO nonces(nonce, ts) VALUES(?,?)", (did + ":" + nonce, time.time()))
        except sqlite3.IntegrityError:
            if method != "GET":
                return {"ok": True, "cihaz": None, "neden": "tekrar"}
            neden = "tekrar-get"
        self.db.execute("UPDATE cihazlar SET last_seen=? WHERE id=?", (_now(), did))
        cevap = {"ok": True, "cihaz": {"id": row[0], "ad": row[1]}, "neden": neden, "onay": None, "karar": None}
        if method == "POST" and neden == "ok":
            # 2026-10-28 (Codex sonrası bulgu): Kokpit paylaşımı (/v1/paylas) ve Masa "ilet" gelen kutusu kaydının
            # METNİ istek gövdesinde yok (sunucu dosya yolu/kırıntı ekler) → imzasız kalıyor, köprü özerk işe
            # yolluyordu; sahibin belgeli sorusu cevapsız kaldı. Jeton: geçerli cihaz imzasında bir kez verilir,
            # ghost-api metni kurup op_inbox_imzala ile imzalatır. Model geçerli cihaz imzası üretemediği için
            # jeton da alamaz; tek kullanımlık, JETON_OMUR_SN.
            simdi = time.time()
            for j, (_c, jts) in list(self.inbox_jetonlar.items()):
                if simdi - jts > JETON_OMUR_SN:
                    del self.inbox_jetonlar[j]
            jeton = secrets.token_hex(16)
            self.inbox_jetonlar[jeton] = (row[0], simdi)
            cevap["cihaz"]["inbox_jeton"] = jeton
        if method == "POST" and neden == "ok" and govde is not None:
            yol = urllib.parse.urlparse(path).path
            try:
                veri = json.loads(govde.decode("utf-8") or "{}")
            except Exception:
                veri = {}
            if isinstance(veri, dict):
                if yol == "/v1/eylem" and not veri.get("alanlar"):
                    eid = str(veri.get("id") or "").strip()[:64]
                    if EID_DESEN.match(eid) and str(veri.get("veri") or "").strip().lower() == "onayla":
                        cevap["onay"] = self._onayla(eid, "kokpit-eylem", peer)
                elif yol == "/v1/mesaj":
                    cevap["onay"] = self._metinden(str(veri.get("metin") or ""), "kokpit-mesaj", peer)
                    # 2. tur bulgu 6: inbox imzası CİHAZ İMZASI doğrulandığı anda burada üretilir — çağıranın
                    # kimliğine (ghost-api) güvenilmez; model geçerli cihaz imzası olmadan jeton alamaz.
                    its = int(time.time())
                    cevap["cihaz"].update(inbox_imza=self._inbox_imza(str(veri.get("metin") or "").strip(), its),
                                          inbox_ts=its)
                elif yol.startswith("/v1/kararlar/") and veri.get("karar") == "onay":
                    pid = yol.rsplit("/", 1)[1]
                    if 4 <= len(pid) <= 16:
                        self.db.execute("INSERT OR REPLACE INTO kararlar(pid, ts, cihaz, tuketildi) VALUES(?,?,?,0)",
                                        (pid, time.time(), did))
                        self._gunluk("karar", peer, "%s %s" % (pid, did))
                        cevap["karar"] = pid
        return cevap

    def op_inbox_imzala(self, i, peer):
        """Paylaşım/Masa-ilet kaydını imzala — yalnız op_kokpit'in aynı cihaz isteğinde verdiği jetonla (tek
        kullanımlık, JETON_OMUR_SN). Jetonsuz/bayat çağrı imza alamaz: imza üretimi hâlâ cihaz imzasına bağlı."""
        jeton = str(i.get("jeton") or "")
        kayit = self.inbox_jetonlar.pop(jeton, None) if jeton else None
        if not kayit or time.time() - kayit[1] > JETON_OMUR_SN:
            self._gunluk("red-inbox-jeton", peer)
            return {"ok": False, "hata": "jeton yok ya da süresi geçti"}
        ts = int(time.time())
        return {"ok": True, "imza": self._inbox_imza(str(i.get("metin") or "").strip(), ts), "ts": ts}

    def op_karar_tuket(self, i, peer):
        """Kokpit'ten gelen 'onay' kararı imzalı istekle kaydedilmiş mi? (tek kullanımlık)"""
        pid = str(i.get("pid") or "")
        r = self.db.execute("SELECT ts, tuketildi FROM kararlar WHERE pid=?", (pid,)).fetchone()
        if not (r and not r[1] and time.time() - r[0] < KARAR_OMUR_SN):
            return {"ok": True, "gecerli": False}
        self.db.execute("UPDATE kararlar SET tuketildi=1 WHERE pid=?", (pid,))
        self.db.execute("DELETE FROM kararlar WHERE ts < ?", (time.time() - 7 * 86400,))
        return {"ok": True, "gecerli": True}

    # ------------------------------------------------------------ eşleme / cihazlar
    def _sayac(self, ad, sifirla=False):
        if sifirla:
            self.db.execute("DELETE FROM sayac WHERE ad=?", (ad,))
            return 0
        r = self.db.execute("SELECT n FROM sayac WHERE ad=?", (ad,)).fetchone()
        n = (r[0] if r else 0) + 1
        self.db.execute("INSERT OR REPLACE INTO sayac VALUES(?,?,?)", (ad, n, time.time()))
        return n

    def op_kod_uret(self, i, peer):
        """Eşleme kodu yalnız köprü ana sürecinden istenir ve YALNIZ Telegram'dan sahibe gider."""
        if not self._kopruden_mi(peer):
            self._gunluk("red-kod", peer)
            return {"ok": False, "hata": "yalnız köprü ana süreci"}
        kod = "%06d" % secrets.randbelow(10 ** 6)
        ad = str(i.get("ad") or "")[:60]
        self._sayac("eslesme", sifirla=True)
        self.db.execute("DELETE FROM cihazlar WHERE durum='bekliyor'")
        self.db.execute("INSERT INTO cihazlar(id, ad, key_hex, durum, kod, kod_son, created) VALUES(?,?,?,?,?,?,?)",
                        (uuid.uuid4().hex[:12], ad, "", "bekliyor", kod, time.time() + KOD_OMRU_SN, _now()))
        self._gunluk("kod", peer, ad)
        gitti = self._telegram("📱 Kokpit eşleme kodu (10 dk, tek kullanım): %s\n"
                               "Uygulamadaki Eşleme ekranına gir." % kod)
        return {"ok": True, "gonderildi": bool(gitti)}

    def op_esle(self, i, peer):
        kod = str(i.get("kod") or "").strip()
        ad = str(i.get("ad") or "")[:60]
        if not kod:
            return {"ok": True, "cihaz": None}
        row = self.db.execute("SELECT id, kod_son FROM cihazlar WHERE durum='bekliyor' AND kod=?", (kod,)).fetchone()
        if not row or time.time() > (row[1] or 0):
            bekleyen = self.db.execute("SELECT COUNT(*) FROM cihazlar WHERE durum='bekliyor'").fetchone()[0]
            if bekleyen and self._sayac("eslesme") >= ESLESME_DENEME_TAVANI:
                self.db.execute("DELETE FROM cihazlar WHERE durum='bekliyor'")
                self._sayac("eslesme", sifirla=True)
                self._telegram("⚠️ Kokpit eşleme: %d yanlış kod üst üste — bekleyen eşleme kodu İPTAL edildi. "
                               "Sen değilsen biri kod deniyor; sen isen /cihaz ile yeni kod al." % ESLESME_DENEME_TAVANI)
            return {"ok": True, "cihaz": None}
        self._sayac("eslesme", sifirla=True)
        key = secrets.token_hex(32)
        self.db.execute("UPDATE cihazlar SET key_hex=?, durum='aktif', kod=NULL, kod_son=NULL, "
                        "ad=COALESCE(NULLIF(?, ''), ad), last_seen=? WHERE id=?", (key, ad, _now(), row[0]))
        self._gunluk("esle", peer, "%s %s" % (row[0], ad))
        self._telegram("📱 Kokpit'e yeni cihaz eşlendi: %s (%s). Sen değilsen hemen /freeze ve haber ver."
                       % (ad or "?", row[0]))
        r = self.db.execute("SELECT ad, created FROM cihazlar WHERE id=?", (row[0],)).fetchone()
        return {"ok": True, "cihaz": {"id": row[0], "key": key, "ad": r[0], "created": r[1]}}

    def op_iptal(self, i, peer):
        if not self._kopruden_mi(peer):          # 4. tur: cihaz iptali (onay kanalını kesmek) yalnız köprüden (/cihaz_iptal)
            return {"ok": False, "hata": "yalnız köprü ana süreci"}
        did = i.get("id")
        if did:
            n = self.db.execute("UPDATE cihazlar SET durum='iptal', key_hex='' WHERE id=?", (str(did),)).rowcount
        else:
            n = self.db.execute("UPDATE cihazlar SET durum='iptal', key_hex='' "
                                "WHERE durum IN ('aktif','bekliyor')").rowcount
        self._gunluk("iptal", peer, did or "hepsi")
        return {"ok": True, "n": n}

    def op_cihazlar(self, i, peer):
        rows = self.db.execute("SELECT id, ad, durum, created, last_seen FROM cihazlar").fetchall()
        return {"ok": True, "cihazlar": [dict(zip(("id", "ad", "durum", "created", "last_seen"), r)) for r in rows]}

    def op_sahip(self, i, peer):
        return {"ok": True, "id": self.ayar.get("sahip_id")}

    def op_durum(self, i, peer):
        # 6. tur: FREEZE görünür olsun
        say = lambda q: self.db.execute(q).fetchone()[0]  # noqa: E731
        return {"ok": True, "surum": SURUM, "freeze": _dondu(self.freeze_yol),
                "aktif_cihaz": say("SELECT COUNT(*) FROM cihazlar WHERE durum='aktif'"),
                "acik_kart": say("SELECT COUNT(*) FROM onaylar WHERE acik=1 AND ts IS NULL"),
                "gecerli_onay": say("SELECT COUNT(*) FROM onaylar WHERE ts IS NOT NULL AND tuketildi=0"),
                "sahip": bool(self.ayar.get("sahip_id")), "telegram": bool(self.ayar.get("telegram_token"))}

    def ice_aktar(self, cihazlar):
        """Kurulumda (root → runuser ghost-onay) ghost.db'deki aktif cihazları taşır. Soketten çağrılamaz."""
        n = 0
        with self.kilit:
            for c in cihazlar:
                if c.get("durum") == "aktif" and re.fullmatch(r"[0-9a-f]{64}", str(c.get("key_hex") or "")):
                    self.db.execute("INSERT OR REPLACE INTO cihazlar(id, ad, key_hex, durum, created, last_seen) "
                                    "VALUES(?,?,?,?,?,?)", (c["id"], c.get("ad") or "", c["key_hex"], "aktif",
                                                            c.get("created"), c.get("last_seen")))
                    n += 1
            self.db.commit()
        return n


# ---------------------------------------------------------------- soket
class _Isleyici(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            cred = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            peer = struct.unpack("3i", cred)
            satir = self.rfile.readline(ISTEK_TAVAN + 1)
            if len(satir) > ISTEK_TAVAN:
                cevap = {"ok": False, "hata": "istek çok büyük"}
            else:
                istek = json.loads(satir.decode("utf-8") or "{}")
                cevap = self.server.onay.isle(istek if isinstance(istek, dict) else {}, peer)
        except Exception as e:
            cevap = {"ok": False, "hata": "istek: %s" % type(e).__name__}
        try:
            self.wfile.write((json.dumps(cevap, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            pass


class Sunucu(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, yol, onay):
        try:
            os.unlink(yol)
        except FileNotFoundError:
            pass
        super().__init__(yol, _Isleyici)
        os.chmod(yol, 0o666)          # bağlanan herkes; yetki SO_PEERCRED uid ile süzülür
        self.onay = onay


def main(argv):
    dizin, soket = DURUM_DIZIN, SOKET
    if argv[:1] == ["ice-aktar"]:
        cihazlar = json.load(sys.stdin)
        print("aktarıldı:", Onay(dizin).ice_aktar(cihazlar))
        return 0
    if argv[:1] == ["durum"]:
        print(json.dumps(Onay(dizin).isle({"op": "durum"}, (0, 0, 0)), ensure_ascii=False))
        return 0
    os.umask(0o077)
    srv = Sunucu(soket, Onay(dizin))
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
