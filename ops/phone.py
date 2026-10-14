#!/usr/bin/env python3
"""Telefonu VPS'ten DOĞRUDAN sür — aradaki ajan olmadan.

ESKİ ZİNCİR: Ghost(VPS) → prompt → Mac'te ikinci bir Claude → mobilecli →
telefon. Her dokunuş için bir ajan daha düşünüyordu; dakikalar ve token, üstelik
ekranı ancak onun ANLATTIĞI kadar görebiliyordum.

YENİ ZİNCİR: Mac'ten VPS'e ters SSH tüneli (ai.jarvis.tunnel) WDA'nın ucunu
VPS'in localhost'una açar; bu betik oraya HTTP konuşur. İkinci ajan devreden
çıkar, ekranı kendim görürüm.

MAC'TEKİ UÇ ARTIK USB DEĞİL (2026-08-29): 8101'i ops/wda_relay.py rölesi
(ai.jarvis.wdarelay) dinler ve telefonun Wi-Fi'daki WDA adresine taşır. Kablo
yalnız ŞARJ için gerekir; usbmuxd cihazı düşürse bile telefon sürülebilir.

PORT SÖZLEŞMESİ: 8101 bizim (röle), yerel 8100 mobilecli'nin — işgal edilirse
telefon mobilecli'den sürülemez hale gelir. Tünel de 8101→8101.

GÜVENLİK: uç yalnız 127.0.0.1'de. VPS'te ufw yalnız 80/443'ü Cloudflare'a açar; 0.0.0.0'a açmak
telefonu internete açmak olurdu.

KOORDİNAT TUZAĞI: WDA "point" ister (375×812), ekran görüntüsü ise PİKSEL
(1125×2436, ×3). Ekran görüntüsünden okunan koordinat doğrudan verilirse
dokunuş ekranın dışına düşer. `--px` bayrağı çevirir.

Kullanım:
    python3 ops/phone.py durum
    python3 ops/phone.py ekran [dosya] [--gonder "açıklama"]
    python3 ops/phone.py boyut
    python3 ops/phone.py ana
    python3 ops/phone.py ac com.apple.Preferences
    python3 ops/phone.py dokun 187 406 [--px]
    python3 ops/phone.py yaz "metin"
    python3 ops/phone.py kaydir 187 700 187 200 [süre]
    python3 ops/phone.py basili x y [--px] [süre]     # uzun basma (bağlam menüsü)
"""
import base64
import json
import os
import re
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPS = os.path.join(BASE, "ops")
WDA = os.environ.get("GHOST_WDA", "http://127.0.0.1:8101")
# `?ham=1`: röle (Mac wda_relay.py) 2026-09-06'dan beri varsayılan olarak küçültülmüş kare verir
# (Kokpit için); telefonu sürerken koordinat piksel ister, o yüzden burada HAM yayın istenir.
MJPEG = os.environ.get("GHOST_WDA_MJPEG", "http://127.0.0.1:9100/?ham=1")
OTURUM = os.path.join(OPS, "phone_session.json")
OLCEK = os.path.join(OPS, "phone_olcek.json")
TUNEL_NOTU = ("Tünel ya da röle kapalı görünüyor. Mac açık mı? İki ajan var: "
              "ai.jarvis.tunnel (Mac→VPS -R 8101) ve ai.jarvis.wdarelay "
              "(Mac 8101 → telefonun Wi-Fi WDA'sı). Kontrol: ops/mac.py komut "
              "'launchctl print gui/$UID/ai.jarvis.wdarelay | head -20; "
              "tail -5 $HOME/ghost-ios/wda_relay.log'")


class Hata(Exception):
    pass


def _istek(yol, veri=None, yontem=None, zaman=30):
    url = WDA + yol
    gövde = json.dumps(veri).encode() if veri is not None else None
    req = urllib.request.Request(url, data=gövde, method=yontem or ("POST" if veri is not None else "GET"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=zaman) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            raise Hata("WDA %s → HTTP %s" % (yol, e.code))
    except urllib.error.URLError as e:
        raise Hata("WDA'ya ulaşılamadı (%s). %s" % (e.reason, TUNEL_NOTU))


def _hatali(cevap):
    """WDA hatası mı? (value.error dolu ya da status != 0)"""
    if not isinstance(cevap, dict):
        return None
    v = cevap.get("value")
    if isinstance(v, dict) and v.get("error"):
        return "%s: %s" % (v.get("error"), str(v.get("message", ""))[:200])
    return None


def durum():
    c = _istek("/status")
    v = c.get("value") or {}
    return {"ready": bool(v.get("ready")), "state": c.get("state"),
            "ios": (v.get("os") or {}).get("version"),
            "cihaz": (v.get("device") or "?")}


def _yeni_oturum(bundle=None):
    kap = {"alwaysMatch": {}}
    if bundle:
        kap["alwaysMatch"]["bundleId"] = bundle
    c = _istek("/session", {"capabilities": kap}, zaman=60)
    h = _hatali(c)
    if h:
        raise Hata("oturum açılamadı — " + h)
    sid = (c.get("value") or {}).get("sessionId") or c.get("sessionId")
    if not sid:
        raise Hata("oturum kimliği gelmedi")
    json.dump({"sid": sid, "ts": int(time.time())},
              open(OTURUM, "w", encoding="utf-8"))
    return sid


def oturum(yenile=False):
    """Oturumu tekrar kullan; ölmüşse bir kez yenile.

    WDA oturumu telefon kilitlenince/uygulama değişince ölebilir. Her komutta
    yeni oturum açmak yavaş (saniyeler) — bu yüzden diskte tutuluyor.
    """
    if not yenile and os.path.exists(OTURUM):
        try:
            sid = json.load(open(OTURUM, encoding="utf-8")).get("sid")
            if sid:
                c = _istek("/session/%s/window/size" % sid, zaman=15)
                if not _hatali(c):
                    return sid
        except (OSError, ValueError, Hata):
            pass
    return _yeni_oturum()


def _oturumlu(yol, veri=None, zaman=30):
    """Oturum gerektiren çağrı; oturum ölmüşse BİR KEZ yenileyip tekrarlar."""
    sid = oturum()
    c = _istek("/session/%s%s" % (sid, yol), veri, zaman=zaman)
    h = _hatali(c)
    if h and ("session" in h.lower() or "terminated" in h.lower()):
        sid = _yeni_oturum()
        c = _istek("/session/%s%s" % (sid, yol), veri, zaman=zaman)
        h = _hatali(c)
    if h:
        raise Hata(h)
    return c


def _png_boyut(ham):
    """PNG başlığından (genişlik, yükseklik) — Pillow yok, gerek de yok."""
    if ham[:8] == b"\x89PNG\r\n\x1a\n" and ham[12:16] == b"IHDR":
        return struct.unpack(">II", ham[16:24])
    return (0, 0)


def _jpeg_boyut(ham):
    """JPEG SOF başlığından (genişlik, yükseklik) — Pillow yok, gerek de yok."""
    if ham[:2] != b"\xff\xd8":
        return (0, 0)
    i = 2
    while i + 9 < len(ham):
        if ham[i] != 0xFF:
            i += 1
            continue
        isaret = ham[i + 1]
        if isaret in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            y, g = struct.unpack(">HH", ham[i + 5:i + 9])
            return (g, y)
        if isaret in (0xD8, 0x01) or 0xD0 <= isaret <= 0xD7:
            i += 2
            continue
        i += 2 + struct.unpack(">H", ham[i + 2:i + 4])[0]
    return (0, 0)


def _mjpeg_kare(zaman=6):
    """WDA'nın MJPEG yayınından TEK kare (JPEG bayt). Yayın yoksa None.

    Neden: /screenshot ucu tam boy PNG'yi tünelden taşıyor (~9 sn). Yayın zaten
    akıyor; ondan kare koparmak 1 sn'nin altında. Röle tarafı: wda_relay.py
    (Mac 9100 → telefon 9100), tünel -R 9100. Düşerse çağıran eski yola iner.
    """
    try:
        with urllib.request.urlopen(MJPEG, timeout=zaman) as r:
            buf = b""
            son = time.time() + zaman
            while time.time() < son:
                parca = r.read(65536)
                if not parca:
                    return None
                buf += parca
                m = re.search(rb"Content-Length:\s*(\d+)\r\n\r\n", buf)
                if not m:
                    continue
                n = int(m.group(1))
                while len(buf) < m.end() + n and time.time() < son:
                    parca = r.read(m.end() + n - len(buf))
                    if not parca:
                        return None
                    buf += parca
                kare = buf[m.end():m.end() + n]
                if len(kare) == n and kare[:2] == b"\xff\xd8":
                    return kare
                return None
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    return None


def ekran(dosya=None, gonder=None):
    dosya = dosya or os.path.join(OPS, "phone_ekran.png")
    ham = _mjpeg_kare()
    if ham:
        kaynak = "mjpeg"
        # Kare JPEG'dir; .png istendiyse uzantıyı düzelt (dönüştürücü yok).
        if dosya.lower().endswith(".png"):
            dosya = dosya[:-4] + ".jpg"
        g, y = _jpeg_boyut(ham)
    else:
        kaynak = "screenshot"          # MJPEG rayı kapalıysa eski, yavaş yol
        c = _istek("/screenshot", zaman=60)
        b64 = c.get("value")
        if not isinstance(b64, str) or not b64:
            raise Hata("ekran görüntüsü boş döndü")
        ham = base64.b64decode(b64)
        g, y = _png_boyut(ham)
    with open(dosya, "wb") as f:
        f.write(ham)
    sonuc = {"dosya": dosya, "bayt": len(ham),
             "piksel": "%dx%d" % (g, y), "kaynak": kaynak}
    if gonder:
        r = subprocess.run([sys.executable, os.path.join(OPS, "send_photo.py"),
                            dosya, gonder], capture_output=True, text=True, timeout=120)
        sonuc["telegram"] = "gönderildi" if r.returncode == 0 else (r.stderr or "hata")[:200]
    return sonuc


def boyut():
    """Nokta (WDA'nın dili) ve piksel (ekran görüntüsünün dili) birlikte."""
    c = _oturumlu("/window/size")
    v = c.get("value") or {}
    nokta = (v.get("width"), v.get("height"))
    piksel = (0, 0)
    kare = _mjpeg_kare(zaman=3)        # hızlı ray; kapalıysa yavaş /screenshot
    if kare:
        piksel = _jpeg_boyut(kare)
    if not piksel[0]:
        try:
            ham = base64.b64decode(_istek("/screenshot", zaman=60).get("value") or "")
            piksel = _png_boyut(ham)
        except (Hata, ValueError):
            piksel = (0, 0)
    olcek = round(piksel[0] / nokta[0], 2) if nokta[0] and piksel[0] else None
    if olcek:
        try:
            json.dump({"olcek": olcek}, open(OLCEK, "w", encoding="utf-8"))
        except OSError:
            pass
    return {"nokta": "%sx%s" % nokta, "piksel": "%dx%d" % piksel, "olcek": olcek}


def _olcek():
    """Piksel/nokta oranı — cihaz sabiti (13 mini: 3.0), her dokunuşta ağı yorma.

    Önbellek yoksa BİR KEZ ölçülür ve diske yazılır; `boyut` her koşuşta
    tazeler. Telefon değişirse ops/phone_olcek.json silinir, o kadar.
    """
    try:
        o = json.load(open(OLCEK, encoding="utf-8")).get("olcek")
        if o:
            return o
    except (OSError, ValueError):
        pass
    try:
        b = boyut()
        return b.get("olcek") or 1
    except Hata:
        return 1


def dokun(x, y, px=False):
    if px:
        o = _olcek()
        x, y = round(x / o), round(y / o)
    # W3C pointer eylemi — WDA'nın eski /wda/tap/0 ucundan daha güvenilir.
    eylem = {"actions": [{"type": "pointer", "id": "finger1",
                          "parameters": {"pointerType": "touch"},
                          "actions": [
                              {"type": "pointerMove", "duration": 0, "x": x, "y": y},
                              {"type": "pointerDown", "button": 0},
                              {"type": "pause", "duration": 60},
                              {"type": "pointerUp", "button": 0}]}]}
    _oturumlu("/actions", eylem)
    return {"dokunuldu": "%s,%s (nokta)" % (x, y)}


def basili(x, y, px=False, sure=0.9):
    """Uzun basma (bağlam menüsü). Koordinat kuralı dokun ile aynı."""
    if px:
        o = _olcek()
        x, y = round(x / o), round(y / o)
    eylem = {"actions": [{"type": "pointer", "id": "finger1",
                          "parameters": {"pointerType": "touch"},
                          "actions": [
                              {"type": "pointerMove", "duration": 0, "x": x, "y": y},
                              {"type": "pointerDown", "button": 0},
                              {"type": "pause", "duration": int(sure * 1000)},
                              {"type": "pointerUp", "button": 0}]}]}
    _oturumlu("/actions", eylem)
    return {"basildi": "%s,%s (nokta, %.1f sn)" % (x, y, sure)}


def kaydir(x1, y1, x2, y2, sure=0.4):
    eylem = {"actions": [{"type": "pointer", "id": "finger1",
                          "parameters": {"pointerType": "touch"},
                          "actions": [
                              {"type": "pointerMove", "duration": 0, "x": x1, "y": y1},
                              {"type": "pointerDown", "button": 0},
                              {"type": "pointerMove", "duration": int(sure * 1000),
                               "x": x2, "y": y2},
                              {"type": "pointerUp", "button": 0}]}]}
    _oturumlu("/actions", eylem)
    return {"kaydirildi": "%s,%s → %s,%s" % (x1, y1, x2, y2)}


def yaz(metin):
    _oturumlu("/wda/keys", {"value": [metin]}, zaman=120)
    return {"yazildi": metin[:60] + ("…" if len(metin) > 60 else "")}


def ana():
    _istek("/wda/homescreen", {}, zaman=30)
    return {"ana ekran": "tamam"}


def ac(bundle):
    _oturumlu("/wda/apps/launch", {"bundleId": bundle}, zaman=60)
    return {"açıldı": bundle}


def main(argv):
    if not argv:
        print(__doc__.strip())
        return 2
    k = argv[0]
    a = [x for x in argv[1:] if not x.startswith("--")]
    px = "--px" in argv
    try:
        if k == "durum":
            r = durum()
        elif k == "ekran":
            gonder = None
            if "--gonder" in argv:
                i = argv.index("--gonder")
                gonder = argv[i + 1] if i + 1 < len(argv) else "iPhone ekranı"
                a = [x for x in a if x != gonder]
            r = ekran(a[0] if a else None, gonder)
        elif k == "boyut":
            r = boyut()
        elif k == "ana":
            r = ana()
        elif k == "ac":
            r = ac(a[0])
        elif k == "dokun":
            r = dokun(int(a[0]), int(a[1]), px)
        elif k == "basili":
            r = basili(int(a[0]), int(a[1]), px=px, sure=float(a[2]) if len(a) > 2 else 0.9)
        elif k == "kaydir":
            r = kaydir(int(a[0]), int(a[1]), int(a[2]), int(a[3]),
                       float(a[4]) if len(a) > 4 else 0.4)
        elif k == "yaz":
            r = yaz(" ".join(a))
        else:
            print("bilinmeyen komut: " + k)
            return 2
    except Hata as e:
        print("⚠️ " + str(e))
        return 1
    print(json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
