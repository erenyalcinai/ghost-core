"""Canlı görüntü kaynakları — kokpit Sahne'nin `canli` bileşeni için.

  telefon: Mac'teki röle (ops/wda_relay.py) WDA'nın MJPEG yayınını VPS'te
           127.0.0.1:9100'e taşır (ters tünel). Tek kare ya da akış geçişi.
  mac:     hızlı hat 127.0.0.1:8765 `/ekran` (~0.6 sn küçültülmüş JPEG).

Hiçbiri fırlatmaz: kaynak yoksa None — API "uzuv uykuda" der, çökmez.
Testte (GHOST_TEST) ağa hiç çıkılmaz.
"""
import base64
import json
import os
import re
import time
import urllib.request

MJPEG = os.environ.get("GHOST_WDA_MJPEG", "http://127.0.0.1:9100")
MACLINK = os.environ.get("GHOST_MACLINK", "http://127.0.0.1:8765")


def _test():
    return bool(os.environ.get("GHOST_TEST"))


WDA = os.environ.get("GHOST_WDA", "http://127.0.0.1:8101")
# WDA MJPEG sunucusu varsayılan: ölçek 100, kalite yüksek, 10 kare/sn → 1124×2436'da 1–3 MB/sn.
# Mac'in Wi-Fi uplink'i bunu taşıyamıyor (2026-09-06, tünel her büyütmede öldü). Hafif kip:
HAFIF = {"mjpegScalingFactor": 40, "mjpegServerScreenshotQuality": 35, "mjpegServerFramerate": 4}
_hafif = {"sid": None, "ts": 0.0}
HAFIF_TAZELE_SN = 600
_kare_onbellek = {"ts": 0.0, "kare": None}
KARE_ONBELLEK_SN = 1.0


def _wda_json(method, yol, govde=None, zaman=4):
    req = urllib.request.Request(WDA + yol, method=method,
                                 data=(json.dumps(govde).encode() if govde is not None else None),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=zaman) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def telefon_hafiflet(zorla=False):
    """WDA'nın MJPEG ayarlarını hafif kipe çek. NOT (2026-09-06): bu WDA sürümü ayarları
    kabul edip UYGULAMIYOR (kare boyu değişmedi); küçültme Mac rölesinde yapılıyor. Fonksiyon
    ileride WDA düzelirse diye duruyor, otomatik çağrılmıyor."""
    if _test():
        return False
    try:
        st = _wda_json("GET", "/status")
        sid = st.get("sessionId")
        if (not zorla and sid and sid == _hafif["sid"]
                and time.time() - _hafif["ts"] < HAFIF_TAZELE_SN):
            return True
        if not sid:
            r = _wda_json("POST", "/session", {"capabilities": {"alwaysMatch": {}}}, zaman=15)
            sid = r.get("sessionId") or (r.get("value") or {}).get("sessionId")
        if not sid:
            return False
        _wda_json("POST", "/session/%s/appium/settings" % sid, {"settings": HAFIF})
        _hafif.update(sid=sid, ts=time.time())
        return True
    except Exception:
        return False


def telefon_kare(zaman=4):
    """MJPEG yayınından TEK kare (JPEG bayt) ya da None. ops/phone.py ile aynı mantık.
    1 sn önbellek: şerit kartı + tam ekran aynı kareyi tünelden iki kez çekmesin."""
    if _test():
        return None
    if time.time() - _kare_onbellek["ts"] < KARE_ONBELLEK_SN and _kare_onbellek["kare"]:
        return _kare_onbellek["kare"]
    # telefon_hafiflet() ÇAĞRILMIYOR: WDA bu sürümde MJPEG ayarlarını yok sayıyor (kalite 10 ile
    # 100 aynı boy, 2026-09-06 ölçüldü); küçültme Mac rölesinde (wda_relay hafif kip). Her karede
    # /status sormak kırılgan tünele fazladan yük olurdu.
    kare = _telefon_kare_ham(zaman)
    if kare:
        _kare_onbellek.update(ts=time.time(), kare=kare)
    return kare


def _telefon_kare_ham(zaman=4):
    try:
        with urllib.request.urlopen(MJPEG, timeout=zaman) as r:
            buf = b""
            for _ in range(200):
                parca = r.read(65536)
                if not parca:
                    return None
                buf += parca
                m = re.search(rb"Content-Length:\s*(\d+)\r\n\r\n", buf)
                if not m:
                    continue
                n = int(m.group(1))
                while len(buf) < m.end() + n:
                    parca = r.read(m.end() + n - len(buf))
                    if not parca:
                        return None
                    buf += parca
                kare = buf[m.end():m.end() + n]
                return kare if kare[:2] == b"\xff\xd8" else None
    except Exception:
        return None
    return None


def telefon_akis(zaman=4):
    """MJPEG yayınını olduğu gibi geçir: (content_type, yineleyici) ya da None."""
    if _test():
        return None
    try:
        r = urllib.request.urlopen(MJPEG, timeout=zaman)
    except Exception:
        return None
    ct = r.headers.get("Content-Type") or "multipart/x-mixed-replace; boundary=--BoundaryString"

    def _gen():
        try:
            while True:
                parca = r.read(32768)
                if not parca:
                    break
                yield parca
        finally:
            try:
                r.close()
            except Exception:
                pass
    return ct, _gen()


def _mac_token():
    from ghost import health
    return health._mac_token()


BOY_ARALIK = (240, 3024)     # ?boy= için izin verilen uzun kenar (px)


def boy_sinirla(v):
    """`?boy=` değerini güvenli int'e çevir: boş/geçersiz → None (Mac varsayılanı 1512),
    aksi hâlde 240..3024'e kırpılır. Mac tarafı (`mac_agent.py /ekran`) sips -Z ile uzun
    kenarı bu boya indirir — Kokpit'teki küçük şerit kartı 640 ister, tam ekran ister vermez."""
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return max(BOY_ARALIK[0], min(BOY_ARALIK[1], n))


def mac_kare(tam=False, boy=None):
    """Mac ekranından bir kare → (bayt, mime) ya da None. `boy`: uzun kenar px (None → 1512)."""
    if _test():
        return None
    govde = {"tam": bool(tam)}
    if boy:
        govde["boy"] = int(boy)
    try:
        req = urllib.request.Request(
            MACLINK + "/ekran", data=json.dumps(govde).encode(), method="POST",
            headers={"Content-Type": "application/json", "X-Ghost-Token": _mac_token()})
        with urllib.request.urlopen(req, timeout=15) as r:
            c = json.loads(r.read().decode("utf-8", "replace"))
        if "png_b64" not in c:
            return None
        ham = base64.b64decode(c["png_b64"])
        bicim = (c.get("bicim") or "png").lower()
        return ham, ("image/jpeg" if bicim in ("jpg", "jpeg") else "image/png")
    except Exception:
        return None
