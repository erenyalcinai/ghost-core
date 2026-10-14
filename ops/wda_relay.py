#!/usr/bin/env python3
"""WDA rölesi — MAC'TE çalışır. Telefonu USB'ye bağımlı olmaktan çıkarır.

ESKİ RAY: `ios forward 8101 8100` — Mac'in 8101'ini usbmuxd üzerinden telefonun
8100'üne bağlardı. Yani telefon KABLOYLA takılı olmak zorundaydı; kablo/porta
bir şey olursa (2026-08-29: usbmuxd "no such device", telefon şarj oluyor ama
veri hattı yok) VPS telefonu tamamen kaybederdi.

YENİ RAY: WDA zaten telefonun Wi-Fi adresinde dinliyor (wda.log'daki
`ServerURLHere->http://<ip>:8100<-`). Bu röle Mac'in 127.0.0.1:8101'ini
dinler ve trafiği o adrese taşır. Kablo yalnız iki şey için gerekir kalır:
şarj ve WDA'nın yeniden başlatılması (xcodebuild -destination id=UDID).

Hedefi kendisi bulur, sırayla:
  1. GHOST_WDA_HOST ortam değişkeni (elle sabitlemek istersen)
  2. son çalışan adres (~/ghost-ios/wda_host)   ← DHCP değişse de çoğu zaman aynı
  3. ~/ghost-ios/wda.log içindeki ServerURLHere satırı (WDA her başlayışta yazar)
  4. USB yedeği: `ios forward 8102 8100` → 127.0.0.1:8102
  5. son çare: yerel /24 taraması (telefon DHCP'de IP değiştirmişse)
Her aday `GET /status` ile DOĞRULANIR — açık port değil, cevap veren WDA aranır.

PORT SÖZLEŞMESİ: 8101 bizim (röle), 8100 telefonun kendi portu, yerel 8100 Mac'te
BOŞ bırakılır (mobilecli orayı kendi yönetir). USB yedeği 8102'yi kullanır.
9100 da bizim: WDA'nın gömülü MJPEG ekran yayınını (telefon :9100) aynı düzenle
taşır — phone.py `ekran` kareyi buradan çeker (~9 sn'lik /screenshot yerine).
MJPEG yalnız Wi-Fi hedefinde çalışır; USB yedeğinde bu port kapalı kalır.
GÜVENLİK: yalnız 127.0.0.1'de dinler. Telefonun WDA'sı zaten LAN'a açık; bu röle
o kapıyı genişletmez, VPS'e giden ters SSH tüneline bağlar.

launchd: ai.jarvis.wdarelay (KeepAlive). Log: ~/ghost-ios/wda_relay.log
"""
import json
import os
import re
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HOME = os.path.expanduser("~")
IOS_DIR = os.path.join(HOME, "ghost-ios")
IOS_BIN = os.path.join(IOS_DIR, "bin", "ios")
WDA_LOG = os.path.join(IOS_DIR, "wda.log")
CACHE = os.path.join(IOS_DIR, "wda_host")
LOG = os.path.join(IOS_DIR, "wda_relay.log")
UDID = "00008110-000000000000000A"

DINLE = ("127.0.0.1", 8101)
TELEFON_PORT = 8100
USB_PORT = 8102          # yerel USB yedeği; 8100 mobilecli'nin, İŞGAL ETME
MJPEG_DINLE = ("127.0.0.1", 9100)
MJPEG_PORT = 9100        # WDA'nın gömülü MJPEG yayını (telefonda, komut portundan ayrı)
TAZE_SN = 30             # doğrulanmış hedef bu süre boyunca tekrar sorulmaz
TARAMA_ARA_SN = 120      # /24 taraması en fazla bu sıklıkta

_kilit = threading.Lock()
_hedef = None            # ("ip", port)
_hedef_ts = 0.0
_usb = None              # ios forward alt süreci
_son_tarama = 0.0


# ---- YubiKey kapısı (2026-09-26, Sahip kararı): telefon da kapının arkasında.
# Röle ~/jarvis/ops'tan koşar, mac_anahtar oradan gelir. Modül yoksa ama seri
# dosyası varsa fail-closed — "kapı kodu eksik" diye telefon açık kalmasın.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import mac_anahtar
except ImportError:              # pragma: no cover — yalnız Mac'te kopya eksikse
    mac_anahtar = None

_KILIT_GOVDE = json.dumps({
    "value": {"error": "locked",
              "message": "YubiKey takili degil (sahibin fiziksel dugmesi)"},
    "hata": "Mac kilitli: YubiKey takili degil — anahtari tak",
}, ensure_ascii=False).encode()


def _kapi_acik():
    if mac_anahtar is not None:
        return mac_anahtar.acik()
    return not os.path.exists(os.path.join(HOME, ".ghost", "anahtar_seri"))


def _kilitli_yaz(sock):
    """Kilitliyken WDA istemcisine (phone.py) duzgun HTTP 423 dondur."""
    try:
        sock.sendall(b"HTTP/1.1 423 Locked\r\n"
                     b"Content-Type: application/json; charset=utf-8\r\n"
                     b"Content-Length: %d\r\nConnection: close\r\n\r\n"
                     % len(_KILIT_GOVDE) + _KILIT_GOVDE)
    except OSError:
        pass


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%F %T ") + msg + "\n")
    except OSError:
        pass


def _canli(host, port, zaman=3):
    """Bu adreste GERÇEKTEN WDA var mı? Açık port yetmez, cevabı okuruz."""
    try:
        with urllib.request.urlopen("http://%s:%d/status" % (host, port),
                                    timeout=zaman) as r:
            d = json.load(r)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return False
    return d.get("state") == "success" or bool((d.get("value") or {}).get("ready"))


def _logdan_ip():
    """wda.log'daki son ServerURLHere satırı. WDA her başlayışta günceller."""
    try:
        with open(WDA_LOG, encoding="utf-8", errors="replace") as f:
            metin = f.read()
    except OSError:
        return None
    bulunan = re.findall(r"ServerURLHere->http://([0-9.]+):(\d+)<-", metin)
    if not bulunan:
        return None
    ip, port = bulunan[-1]
    return ip, int(port)


def _onbellek_oku():
    try:
        ip, port = open(CACHE, encoding="utf-8").read().strip().split(":")
        return ip, int(port)
    except (OSError, ValueError):
        return None


def _onbellek_yaz(hedef):
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            f.write("%s:%d" % hedef)
    except OSError:
        pass


def _usb_yedegi():
    """USB hattı varsa `ios forward 8102 8100` ile yerel bir uç aç."""
    global _usb
    if not os.path.exists(IOS_BIN):
        return None
    if _usb is None or _usb.poll() is not None:
        try:
            r = subprocess.run([IOS_BIN, "list"], capture_output=True,
                               text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return None
        if UDID not in r.stdout:
            return None          # cihaz kabloda yok; süreç açıp çöp bırakma
        try:
            _usb = subprocess.Popen(
                [IOS_BIN, "forward", str(USB_PORT), str(TELEFON_PORT)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            return None
        time.sleep(1.5)
        log("USB yedeği açıldı (pid %s, %d→%d)" % (_usb.pid, USB_PORT, TELEFON_PORT))
    return ("127.0.0.1", USB_PORT)


def _tarama():
    """Yerel /24'te 8100'de cevap veren WDA ara (DHCP IP değiştirdiyse)."""
    global _son_tarama
    if time.time() - _son_tarama < TARAMA_ARA_SN:
        return None
    _son_tarama = time.time()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 53))
        onek = s.getsockname()[0].rsplit(".", 1)[0]
        s.close()
    except OSError:
        return None
    bulundu = []
    def dene(n):
        ip = "%s.%d" % (onek, n)
        try:                      # önce ucuz TCP denemesi, sonra doğrulama
            with socket.create_connection((ip, TELEFON_PORT), timeout=0.6):
                pass
        except OSError:
            return
        if _canli(ip, TELEFON_PORT, zaman=3):
            bulundu.append(ip)
    isler = [threading.Thread(target=dene, args=(n,)) for n in range(1, 255)]
    for t in isler:
        t.start()
    for t in isler:
        t.join()
    if bulundu:
        log("tarama buldu: %s" % bulundu[0])
        return (bulundu[0], TELEFON_PORT)
    return None


def hedef_bul(zorla=False):
    """Doğrulanmış (ip, port). Bulunan adres TAZE_SN boyunca yeniden sorulmaz."""
    global _hedef, _hedef_ts
    with _kilit:
        if not zorla and _hedef and time.time() - _hedef_ts < TAZE_SN:
            return _hedef
        adaylar = []
        elle = os.environ.get("GHOST_WDA_HOST")
        if elle:
            ip, _, p = elle.partition(":")
            adaylar.append((ip, int(p or TELEFON_PORT)))
        for kaynak in (_onbellek_oku(), _logdan_ip()):
            if kaynak and kaynak not in adaylar:
                adaylar.append(kaynak)
        for aday in adaylar:
            if _canli(*aday):
                if _hedef != aday:
                    log("hedef: %s:%d" % aday)
                _hedef, _hedef_ts = aday, time.time()
                _onbellek_yaz(aday)
                return aday
        for yedek in (_usb_yedegi(), _tarama()):
            if yedek and _canli(*yedek):
                if _hedef != yedek:
                    log("hedef (yedek): %s:%d" % yedek)
                _hedef, _hedef_ts = yedek, time.time()
                if yedek[0] != "127.0.0.1":
                    _onbellek_yaz(yedek)
                return yedek
        _hedef, _hedef_ts = None, time.time()
        return None


def _akit(kaynak, hedef):
    try:
        while True:
            veri = kaynak.recv(65536)
            if not veri:
                break
            hedef.sendall(veri)
    except OSError:
        pass
    finally:
        for s in (kaynak, hedef):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


class Role(socketserver.BaseRequestHandler):
    PORT = None   # None → hedefin kendi portu; sayı → hedefin IP'sinde o port (MJPEG)

    def _cevir(self, hedef):
        """hedef_bul çıktısını bu rölenin portuna çevir; olmuyorsa None."""
        if hedef is None:
            return None
        if self.PORT is None:
            return hedef
        if hedef[0] == "127.0.0.1":
            # USB yedeği: Mac'in 9100'ü bu rölenin KENDİSİ — bağlanmak sonsuz
            # döngü olur. MJPEG yalnız Wi-Fi hedefinde var.
            return None
        return (hedef[0], self.PORT)

    def handle(self):
        if not _kapi_acik():
            _kilitli_yaz(self.request)
            self.request.close()
            return
        hedef = self._cevir(hedef_bul())
        if hedef is None:
            hedef = self._cevir(hedef_bul(zorla=True))
        if hedef is None:
            log("hedef yok (port %s) — WDA uygun yolda bulunamadı" % (self.PORT or "wda"))
            self.request.close()
            return
        try:
            uc = socket.create_connection(hedef, timeout=10)
        except OSError as e:
            log("bağlanamadı %s:%d (%s) — hedef yenileniyor" % (hedef[0], hedef[1], e))
            yeni = self._cevir(hedef_bul(zorla=True))
            if not yeni or yeni == hedef:
                self.request.close()
                return
            try:
                uc = socket.create_connection(yeni, timeout=10)
            except OSError:
                self.request.close()
                return
        t = threading.Thread(target=_akit, args=(uc, self.request), daemon=True)
        t.start()
        _akit(self.request, uc)
        t.join(timeout=5)
        uc.close()


# ---------------------------------------------------------------- hafif MJPEG (2026-09-06)
# WDA'nın yayını tam çözünürlük (1124×2436, ~110 KB/kare, ~10 kare/sn = 1+ MB/sn). Mac'in
# Wi-Fi uplink'i bunu taşıyamayınca tünelin keepalive'ları kuyrukta ölüyor, Kokpit'te kartı
# büyütmek tüneli düşürüyordu. WDA'nın mjpegScalingFactor/Quality ayarları bu sürümde ETKİSİZ
# (kalite 10 ile 100 aynı boy). Bu yüzden küçültme burada, Mac'te, sips ile: uzun kenar
# HAFIF_BOY px, kalite HAFIF_KALITE, en çok HAFIF_FPS kare/sn → ~15 KB/kare, ~45 KB/sn.
# Tam boy isteyen (phone.py: koordinat için piksel gerekir) `GET /?ham=1` ile ham yayını alır.
HAFIF_BOY = int(os.environ.get("WDA_MJPEG_BOY", "700"))
HAFIF_KALITE = int(os.environ.get("WDA_MJPEG_KALITE", "45"))
HAFIF_FPS = float(os.environ.get("WDA_MJPEG_FPS", "3"))
_KARE_BASLIK = re.compile(rb"Content-Length:\s*(\d+)\r\n\r\n", re.I)


def _kucult(jpeg):
    """sips ile küçült; her hatada orijinali döndür (yayın hiç kesilmesin)."""
    yol = os.path.join("/tmp", "wda_relay_%d_%d.jpg" % (os.getpid(), threading.get_ident()))
    try:
        with open(yol, "wb") as f:
            f.write(jpeg)
        subprocess.run(["sips", "-Z", str(HAFIF_BOY), "-s", "format", "jpeg",
                        "-s", "formatOptions", str(HAFIF_KALITE), yol, "--out", yol],
                       capture_output=True, timeout=5)
        with open(yol, "rb") as f:
            kucuk = f.read()
        return kucuk if kucuk[:2] == b"\xff\xd8" else jpeg
    except Exception:
        return jpeg
    finally:
        try:
            os.unlink(yol)
        except OSError:
            pass


def _kareler(oku):
    """MJPEG bayt akışından (oku() → bytes|b'') kareleri (jpeg) sırayla ver."""
    buf = b""
    while True:
        m = _KARE_BASLIK.search(buf)
        if not m:
            p = oku()
            if not p:
                return
            buf += p
            if len(buf) > 8 * 1024 * 1024:   # başlık hiç gelmiyorsa şişme
                buf = buf[-65536:]
            continue
        n = int(m.group(1))
        while len(buf) < m.end() + n:
            p = oku()
            if not p:
                return
            buf += p
        yield buf[m.end():m.end() + n]
        buf = buf[m.end() + n:]


def _hafif_yayin(uc, istemci, sinir=None):
    """Telefon yayınını (uc) küçültüp istemciye MJPEG olarak yaz. `sinir`: test için kare tavanı."""
    istemci.sendall(b"HTTP/1.0 200 OK\r\nContent-Type: multipart/x-mixed-replace; "
                    b"boundary=--BoundaryString\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\n")
    son = 0.0
    sayi = 0
    for kare in _kareler(lambda: uc.recv(65536)):
        simdi = time.time()
        if simdi - son < 1.0 / HAFIF_FPS:
            continue                                     # kare düşür: tünele hız sınırı
        son = simdi
        k = _kucult(kare)
        istemci.sendall(b"--BoundaryString\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(k))
        istemci.sendall(k + b"\r\n")
        sayi += 1
        if sinir and sayi >= sinir:
            return sayi
    return sayi


class MjpegRole(Role):
    PORT = MJPEG_PORT

    def handle(self):
        if not _kapi_acik():
            self.request.close()
            return
        # İstemcinin isteğini oku: `?ham` geçiyorsa ham geçiş (Role.handle), yoksa hafif kip.
        self.request.settimeout(5)
        try:
            istek = self.request.recv(4096)
        except OSError:
            self.request.close()
            return
        self.request.settimeout(None)
        if b"ham" in istek.split(b"\r\n", 1)[0]:
            self._ham(istek)
            return
        hedef = self._cevir(hedef_bul()) or self._cevir(hedef_bul(zorla=True))
        if hedef is None:
            log("hedef yok (port %s) — WDA uygun yolda bulunamadı" % self.PORT)
            self.request.close()
            return
        try:
            uc = socket.create_connection(hedef, timeout=10)
            uc.sendall(b"GET / HTTP/1.0\r\n\r\n")
            uc.settimeout(15)
            _hafif_yayin(uc, self.request)
        except OSError:
            pass
        finally:
            for s in (uc if "uc" in locals() else None, self.request):
                try:
                    s and s.close()
                except OSError:
                    pass

    def _ham(self, istek):
        hedef = self._cevir(hedef_bul()) or self._cevir(hedef_bul(zorla=True))
        if hedef is None:
            self.request.close()
            return
        try:
            uc = socket.create_connection(hedef, timeout=10)
        except OSError:
            self.request.close()
            return
        uc.sendall(istek)
        t = threading.Thread(target=_akit, args=(uc, self.request), daemon=True)
        t.start()
        _akit(self.request, uc)
        t.join(timeout=5)
        uc.close()


class Sunucu(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    try:
        open(LOG, "w").close()          # her başlangıçta sıfırla, sınırsız büyümesin
    except OSError:
        pass
    log("röle başlıyor: %s:%d (wda) + %s:%d (mjpeg)" % (DINLE + MJPEG_DINLE))
    ilk = hedef_bul(zorla=True)
    log("ilk hedef: %s" % ("%s:%d" % ilk if ilk else "YOK (bağlantıda aranacak)"))
    mjpeg = Sunucu(MJPEG_DINLE, MjpegRole)
    threading.Thread(target=mjpeg.serve_forever, daemon=True).start()
    with Sunucu(DINLE, Role) as srv:
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
