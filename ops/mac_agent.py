#!/usr/bin/env python3
"""Mac ucu — VPS'ten gelen komutu ANINDA çalıştırır (kuyruk beklemeden).

NEDEN: kuyruk yolu güvenilir ama yavaş — işçi 30 sn'de bir yokluyor, ölçülen
gidiş-dönüş 6-14 sn. İşin kendisi 0.1 sn sürüyor; zamanın %99'u bekleme.
Telefonda bu asimetriyi ters tünelle kırdık; bu, aynı kalıbın Mac'i.

KUYRUK KALKMIYOR — katman oldu:
  bu uç  → Mac açık ve tünel ayakta:      ~0.2 sn
  kuyruk → Mac uykuda / tünel kopuk:      uyanınca akar (iş kaybolmaz)
  ajan   → düşünmek gerekiyor:            dakikalar
Senkron bir uç yedek hattın yerini tutamaz: karşı taraf yoksa komut düşer.

GÜVENLİK:
- Yalnız 127.0.0.1'e bağlanır. Dışarıya açılan tek kapı, VPS'in localhost'una
  giden ters SSH tüneli (ai.jarvis.tunnel).
- Token şart. Tünelin ucu VPS'te localhost olduğu için oradaki HERHANGİ bir
  süreç bu porta vurabilir; token o yüzden var (derinlemesine savunma).
  Dosya: ~/.ghost_maclink_token (chmod 600).
- Komutlar Mac'te tam yetkiyle koşar — kuyruk işleri de öyle koşuyordu, yeni
  bir güven verilmiş olmuyor; yüzey genişliyor, o yüzden token + loopback.
"""
import base64
import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, "jarvis")
OPS = os.path.join(BASE, "ops")
sys.path.insert(0, OPS)
import mac_anahtar  # noqa: E402  — YubiKey kapısı (fiziksel düğme, 2026-09-25)
LOG = os.path.join(OPS, "mac_agent.log")
TOKEN_DOSYA = os.path.join(HOME, ".ghost_maclink_token")
PORT = 8765
AZAMI_SURE = 600          # tek komut için üst sınır
EKRAN = "/tmp/ghost_mac_ekran.png"


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%F %T ") + m + "\n")


# Mac tarafı sır koruması (Faz 2 / Mac turu, 2026-10-28): VPS tamamen ele geçirilip kapı atlatılsa bile bu kod
# Mac'te koşar. YALNIZ değer basan dar küme — Ghost'un günlük işinde hiçbiri yok (Sahip: güvenlik işimizi
# zorlaştırmasın). Ret Mac günlüğüne + Telegram'a (sır vekili üzerinden) düşer.
import re as _re  # noqa: E402
SIR_KOMUT = _re.compile(
    r"\bgh\s+auth\s+(token|status\b[^;&|\n]*(-t\b|--show-token))|"
    r"\bsecurity\s+(find-(generic|internet)-password\b(?![^;&|\n]*Claude Code-credentials)[^;&|\n]*\s-[a-z]*[wg]|"
    r"dump-keychain|export)\b|"     # Claude girişi (kota göstergesi, ghost/kota.py) bilinçli serbest
    r"\.ssh/(ghost_[a-z_]+|id_[a-z0-9]+)(?!\.pub)\b(?![\w.-])|"
    r"jarvis/(secrets\.env|\.env)\b(?!\S*\.example)|"
    r"\.ghost/yedek-sir(?:/|\b)")      # 4. tur K2: sır yedeği (düz Mac kopyası + gpg) hızlı hattan okunmaz


def _cut_yalniz_ad(m):
    p = m.group(0).split()
    ok = "-s" in p and _re.search(r"-d\s*['\"]?=", m.group(0)) and _re.search(r"(^|\s)-f\s*1(\s|$)", m.group(0))
    return " " if ok else m.group(0)


def sir_komutu(cmd):
    """Değer basabilecek sır erişimi mi? Serbest: anahtar ADI okumak (cut -s -d= -f1) ve ssh/scp'nin anahtarı -i ile
    KULLANMASI (içeriği basmaz)."""
    c = cmd or ""
    if not SIR_KOMUT.search(c):
        return False
    c = _re.sub(r"\b(ssh|scp)\b[^;&|\n]*?-i\s+\S*\.ssh/ghost_\w+", " ", c)
    c = _re.sub(r"\bcut\s+[^;&|\n]*", _cut_yalniz_ad, c)
    return bool(SIR_KOMUT.search(c))


def _uyar(metin):
    try:
        import urllib.parse
        import urllib.request
        sys.path.insert(0, BASE)
        from ghost import vekil
        sahip = ""
        for ad in (".env", "secrets.env"):
            try:
                for satir in open(os.path.join(BASE, ad), encoding="utf-8"):
                    if satir.startswith("TELEGRAM_OWNER_ID="):
                        sahip = satir.split("=", 1)[1].strip()
            except OSError:
                pass
        if sahip and vekil.aktif():
            urllib.request.urlopen(urllib.request.Request(
                vekil.url("telegram", "/sendMessage"),
                data=urllib.parse.urlencode({"chat_id": sahip, "text": metin[:3500]}).encode()), timeout=10).read()
    except Exception:
        pass


def token():
    try:
        return open(TOKEN_DOSYA, encoding="utf-8").read().strip()
    except OSError:
        return None


class Uc(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _yaz(self, kod, veri):
        govde = json.dumps(veri, ensure_ascii=False).encode()
        self.send_response(kod)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(govde)))
        self.end_headers()
        self.wfile.write(govde)

    def _yetkili(self):
        t = token()
        if not t:
            self._yaz(503, {"hata": "token dosyası yok"})
            return False
        if self.headers.get("X-Ghost-Token") != t:
            log("yetkisiz istek: " + self.path)
            self._yaz(403, {"hata": "token geçersiz"})
            return False
        return True

    def _govde(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8", "replace"))
        except ValueError:
            return {}

    def do_GET(self):
        if self.path != "/durum":
            return self._yaz(404, {"hata": "yok"})
        if not self._yetkili():
            return
        # /durum kapı KİLİTLİYKEN de cevap verir: VPS "Mac uyuyor" ile
        # "anahtar çıkarılmış"ı ayırt edebilsin. İş yapan uçlar (POST) kilitlenir.
        self._yaz(200, {"durum": "ayakta", "at": time.strftime("%F %T"),
                        "makine": os.uname().nodename,
                        "anahtar": mac_anahtar.durum()})

    def do_POST(self):
        if not self._yetkili():
            return
        if not mac_anahtar.acik():
            log("kilitli (YubiKey yok): " + self.path)
            return self._yaz(423, dict(mac_anahtar.KILIT))
        if self.path == "/calistir":
            v = self._govde()
            cmd = (v.get("cmd") or "").strip()
            if not cmd:
                return self._yaz(400, {"hata": "cmd boş"})
            if sir_komutu(cmd):
                log("SIR RED: " + cmd[:200])
                _uyar("🛡 Mac ajanı bir sır erişimini REDDETTİ (VPS'ten gelen komut):\n%s\n\nSen değilsen /freeze."
                      % cmd[:600])
                return self._yaz(200, {"rc": 126, "sn": 0, "cikti": (
                    "Mac ajanı: bu komut bir sırrın DEĞERİNİ basabilir (gh auth token / Anahtar Zinciri parolası / ssh özel "
                    "anahtarı / Mac sır dosyası) — reddedildi, sahibe bildirildi. Anahtarı KULLANMAK serbest (gh api, ssh -i, "
                    "vekil 127.0.0.1:8797); adlar için cut -s -d= -f1.")})
            sure = min(int(v.get("timeout") or 120), AZAMI_SURE)
            t0 = time.time()
            try:
                r = subprocess.run(["/bin/bash", "-lc", cmd],
                                   cwd=v.get("cwd") or BASE, capture_output=True,
                                   text=True, timeout=sure)
                cikti = (r.stdout or "")
                if (r.stderr or "").strip():
                    cikti += "\n[stderr]\n" + r.stderr
                rc = r.returncode
            except subprocess.TimeoutExpired:
                cikti, rc = "(zaman aşımı: %d sn)" % sure, -1
            except Exception as e:
                cikti, rc = "(çalıştırılamadı: %r)" % e, -1
            gecen = round(time.time() - t0, 2)
            log("calistir rc=%s %.2fs: %s" % (rc, gecen, cmd[:120]))
            return self._yaz(200, {"rc": rc, "sn": gecen, "cikti": cikti.strip()})

        if self.path == "/ekran":
            istek = self._govde()
            # screencapture TCC (Ekran Kaydı) izni ister. İzin YOKSA sessizce
            # boş dosya değil, açık hata dönmeli — "göremiyorum" ile "ekran
            # boş" birbirine karışmasın.
            try:
                os.path.exists(EKRAN) and os.remove(EKRAN)
            except OSError:
                pass
            r = subprocess.run(["/usr/sbin/screencapture", "-x", EKRAN],
                               capture_output=True, text=True, timeout=60)
            if r.returncode != 0 or not os.path.exists(EKRAN):
                log("ekran alınamadı: " + (r.stderr or "").strip()[:200])
                return self._yaz(500, {
                    "hata": "ekran alınamadı",
                    "detay": (r.stderr or "").strip()[:300],
                    "cozum": "Sistem Ayarları > Gizlilik ve Güvenlik > Ekran "
                             "Kaydı listesine /usr/bin/python3 eklenmeli, sonra "
                             "launchctl kickstart -k gui/$UID/ai.jarvis.maclink"})
            # Retina ekran 3024x1964 -> ~5 MB PNG, tünelden ~5 sn. Okumak için
            # o çözünürlük gerekmiyor: küçültülmüş JPEG ~10 kat küçük ve saniyenin
            # altında geliyor. Tam çözünürlük isteyen {"tam": true} der.
            v = istek
            if not v.get("tam"):
                boy = int(v.get("boy") or 1512)
                kucuk = EKRAN.replace(".png", "-k.jpg")
                r2 = subprocess.run(["/usr/bin/sips", "-Z", str(boy), "-s", "format",
                                     "jpeg", "-s", "formatOptions", "80",
                                     EKRAN, "--out", kucuk],
                                    capture_output=True, text=True, timeout=60)
                if r2.returncode == 0 and os.path.exists(kucuk):
                    ham = open(kucuk, "rb").read()
                    log("ekran alındı (küçültülmüş): %d bayt" % len(ham))
                    return self._yaz(200, {"bayt": len(ham), "bicim": "jpg",
                                           "png_b64": base64.b64encode(ham).decode()})
                log("sips başarısız, tam çözünürlük dönülüyor: "
                    + (r2.stderr or "").strip()[:120])
            ham = open(EKRAN, "rb").read()
            log("ekran alındı: %d bayt" % len(ham))
            return self._yaz(200, {"bayt": len(ham), "bicim": "png",
                                   "png_b64": base64.b64encode(ham).decode()})

        self._yaz(404, {"hata": "yok"})

    def log_message(self, *a):
        pass            # kendi log'umuz var; stderr'i kirletme


if __name__ == "__main__":
    if not token():
        log("BAŞLAMADI: token dosyası yok (%s)" % TOKEN_DOSYA)
        raise SystemExit(1)
    log("mac ucu dinliyor: 127.0.0.1:%d" % PORT)
    ThreadingHTTPServer(("127.0.0.1", PORT), Uc).serve_forever()
