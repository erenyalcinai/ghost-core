"""Onay servisi istemcisi — ghost tarafı yalnız SORAR, yazamaz (Faz 1 madde 10, 2026-10-28).

Servis (`ops/onay/onay_servisi.py`) `ghost-onay` kullanıcısıyla koşar; onay kaydı, Kokpit
cihaz anahtarları ve Kokpit karar onayları orada durur. Kurulu değilse (açık çekirdek,
geliştirme makinesi) ya da testte eski yerel yol (ghost/sahne.py, ghost/kokpit.py) kullanılır.

KAPALI DÜŞER: servis kuruluysa ve cevap vermiyorsa onay YOK sayılır, Kokpit isteği 503 alır.
Soket yolu ortamdan DEĞİŞTİRİLEMEZ (model kendi sahte servisini gösterebilirdi); testler
`SOKET` niteliğini yamalar.
"""
import base64
import hashlib
import json
import os
import pwd
import socket
from urllib.parse import urlparse

SOKET = "/run/ghost-onay/onay.sock"
KULLANICI = "ghost-onay"
ONAY_YOLLARI = ("/v1/eylem", "/v1/mesaj")


class ServisHatasi(Exception):
    pass


def servis_kurulu():
    try:
        pwd.getpwnam(KULLANICI)
        return True
    except KeyError:
        return False


def servis_modu():
    """Servis kurulu ve test değil → servis. Testte (GHOST_TEST) yerel yol: test ortamı
    gerçek mail göndermez (ops/gmail_gonder.py), kapı ise ortamdan bağımsız hep servise bakar."""
    return servis_kurulu() and not os.environ.get("GHOST_TEST")


def _cagir(op, zaman_asimi=5, **kw):
    istek = dict(kw, op=op)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(zaman_asimi)
            s.connect(SOKET)
            s.sendall((json.dumps(istek, ensure_ascii=False) + "\n").encode("utf-8"))
            parca = []
            while True:
                b = s.recv(65536)
                if not b:
                    break
                parca.append(b)
        cevap = json.loads(b"".join(parca).decode("utf-8"))
    except Exception as e:
        raise ServisHatasi("onay servisi cevap vermedi: %s" % type(e).__name__)
    if not isinstance(cevap, dict):
        raise ServisHatasi("onay servisi: bozuk cevap")
    return cevap


# ------------------------------------------------------------ onay kaydı
def kart_ac(eid, onizleme):
    return _cagir("kart_ac", eid=eid, onizleme=onizleme or "").get("ok", False)


def kartlar_esitle(acik_idler):
    return _cagir("kartlar_esitle", acik=list(acik_idler)).get("ok", False)


def sahip_onayi_telegram(metin, uid):
    """Köprü: sahibi doğrulanmış Telegram mesajı. Servis çağıranın köprü ana süreci olduğunu kendisi doğrular."""
    r = _cagir("sahip_onayi", metin=metin or "", uid=uid)
    return r.get("eid") if r.get("ok") else None


def gecerli(eid):
    try:
        return bool(_cagir("gecerli", eid=str(eid or "")).get("gecerli"))
    except ServisHatasi:
        return False


def tuket(eid, onizleme_sha=None):
    """Atomik geçerli-mi + tüket. onizleme_sha verilirse kartta sahibe gösterilenle eşleşmeli."""
    kw = {"eid": str(eid or "")}
    if onizleme_sha is not None:
        kw["onizleme_sha"] = onizleme_sha
    try:
        return bool(_cagir("tuket", **kw).get("tuketildi"))
    except ServisHatasi:
        return False


def az_once_tuketildi(eid, pencere_sn=120):
    try:
        return bool(_cagir("az_once_tuketildi", eid=str(eid or ""), pencere_sn=pencere_sn).get("evet"))
    except ServisHatasi:
        return False


def onizleme_sha(eid):
    try:
        r = _cagir("onizleme", eid=str(eid or ""))
    except ServisHatasi:
        return None
    return None if r.get("kirpildi") else r.get("sha")


# ------------------------------------------------------------ Kokpit
def kokpit_dogrula(headers, method, path, body=b""):
    """→ (cihaz | None, neden). Servis düşükse (None, 'servis') — API 503 basar, 401 DEĞİL
    (uygulama 401'de eşleme ekranına düşer)."""
    g = lambda k: headers.get(k) or headers.get(k.lower()) or ""  # noqa: E731
    kw = {"h": {"device": g("X-Ghost-Device"), "ts": g("X-Ghost-Ts"), "nonce": g("X-Ghost-Nonce"),
                "sig": g("X-Ghost-Sig")},
          "method": method.upper(), "path": path}
    yol = urlparse(path).path
    if method.upper() == "POST" and (yol in ONAY_YOLLARI or yol.startswith("/v1/kararlar/")):
        kw["body_b64"] = base64.b64encode(body or b"").decode("ascii")
    else:
        kw["body_sha"] = hashlib.sha256(body or b"").hexdigest()
    try:
        r = _cagir("kokpit", **kw)
    except ServisHatasi:
        return None, "servis"
    if not r.get("ok"):
        return None, "servis"
    return r.get("cihaz"), r.get("neden") or "?"


def karar_tuket(pid):
    try:
        return bool(_cagir("karar_tuket", pid=str(pid or "")).get("gecerli"))
    except ServisHatasi:
        return False


def kod_uret(ad):
    """Kod çağırana DÖNMEZ; servis Telegram'dan sahibe yollar. → gönderildi mi."""
    r = _cagir("kod_uret", ad=ad or "")
    if not r.get("ok"):
        raise ServisHatasi(r.get("hata") or "kod üretilemedi")
    return bool(r.get("gonderildi"))


def esle(kod, ad=""):
    return _cagir("esle", kod=kod or "", ad=ad or "").get("cihaz")


def iptal(device_id=None):
    return int(_cagir("iptal", id=device_id).get("n") or 0)


def cihazlar():
    return _cagir("cihazlar").get("cihazlar") or []


def sahip_id():
    try:
        v = _cagir("sahip").get("id")
        return int(v) if str(v or "").isdigit() else None
    except ServisHatasi:
        return None


def durum():
    return _cagir("durum")


if __name__ == "__main__":
    print(json.dumps(durum() if servis_kurulu() else {"kurulu": False}, ensure_ascii=False))
