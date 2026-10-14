#!/usr/bin/env python3
"""YubiKey kapısı (Mac) — Ghost'un Mac'e erişimi için FİZİKSEL düğme.

KARAR (Sahip, 2026-09-25): 2. YubiKey Mac'te takılı kalır. Takılıyken Ghost Mac'e
erişir (hızlı hat 8765, veri okuyucu 8766, iş kuyruğu işçisi); Sahip anahtarı
çıkardığı an üçü de "kilitli" der, iş kuyrukta bekler. Bu bir KİMLİK doğrulaması
DEĞİL, kapatma düğmesi: anahtar takılıyken olan her şey eskisi gibi.

yalnız USB'de var mı diye bakar (ioreg). Passkey'lerle çakışmaz.

Kapı üç halde olur:
  devre_disi  ~/.ghost/anahtar_seri yok → her şey eskisi gibi (kurulum öncesi güvenli)
  acik        dosyadaki seri (ya da "*" = herhangi Yubico) USB'de takılı
  kilitli     dosya var, anahtar yok

Mac'te CLI:  python3 ~/jarvis/ops/mac_anahtar.py durum | bagla [<seri>|*] | coz
VPS'ten:     python3 ops/mac.py anahtar [durum|bagla <seri>|coz]
`coz` yalnız kapı AÇIKKEN (anahtar takılıyken) çalışır — kilitliyken Ghost
kendini çözemez, tasarım gereği; Sahip Mac'in başında anahtarı takar.
"""
import json
import os
import re
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
SERI_DOSYA = os.path.join(HOME, ".ghost", "anahtar_seri")
YUBICO_VID = 4176            # 0x1050
_ONBELLEK = {"t": 0.0, "v": None}
ONBELLEK_SN = 2.0


def _ioreg():
    try:
        r = subprocess.run(["/usr/sbin/ioreg", "-p", "IOUSB", "-l", "-w0"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def ayristir(metin):
    """ioreg çıktısından Yubico cihazlarının seri numaralarını çıkar.
    Seri görünmüyorsa (USB seri gizli yapılandırılmış anahtar) '?' döner."""
    seriler = []
    for blok in re.split(r"^\s*[|\s]*\+-o ", metin, flags=re.M)[1:]:
        yubico = ('"Yubico"' in blok
                  or re.search(r'"idVendor"\s*=\s*%d\b' % YUBICO_VID, blok))
        if not yubico:
            continue
        m = re.search(r'"(?:USB Serial Number|kUSBSerialNumberString)"\s*=\s*"([^"]*)"', blok)
        seriler.append((m.group(1).strip() if m and m.group(1).strip() else "?"))
    return seriler


def takili(taze=False):
    """USB'deki Yubico serileri (2 sn önbellek; istek başına ioreg koşmasın)."""
    if not taze and _ONBELLEK["v"] is not None and time.time() - _ONBELLEK["t"] < ONBELLEK_SN:
        return list(_ONBELLEK["v"])
    v = ayristir(_ioreg())
    _ONBELLEK.update(t=time.time(), v=v)
    return list(v)


def gerekli():
    try:
        with open(SERI_DOSYA, encoding="utf-8") as f:
            s = f.read().strip()
    except OSError:
        return None
    return s or None


def durum(taze=False):
    g = gerekli()
    t = takili(taze)
    if g is None:
        kapi = "devre_disi"
    elif g == "*":
        kapi = "acik" if t else "kilitli"
    else:
        kapi = "acik" if g in t else "kilitli"
    return {"kapi": kapi, "gerekli": g, "takili": t}


def acik(taze=False):
    return durum(taze)["kapi"] != "kilitli"


KILIT = {"hata": "Mac kilitli: YubiKey takılı değil (sahibin fiziksel düğmesi). "
                 "İş kuyruğa: python3 ops/mac_cmd.py '<komut>' — anahtar takılınca koşar.",
         "kapi": "kilitli"}


def bagla(seri=None):
    t = takili(taze=True)
    if seri is None:
        if len(t) != 1 or t[0] == "?":
            return {"hata": "seri seçilemedi; takılı: %r — seriyi ya da '*' ver" % t}
        seri = t[0]
    if seri != "*" and seri not in t:
        return {"hata": "bu seri şu an takılı değil: %s (takılı: %r)" % (seri, t)}
    os.makedirs(os.path.dirname(SERI_DOSYA), exist_ok=True)
    with open(SERI_DOSYA, "w", encoding="utf-8") as f:
        f.write(seri + "\n")
    os.chmod(SERI_DOSYA, 0o600)
    return durum(taze=True)


def coz():
    try:
        os.remove(SERI_DOSYA)
    except FileNotFoundError:
        pass
    return durum(taze=True)


def main(argv):
    k = argv[0] if argv else "durum"
    if k == "durum":
        out = durum(taze=True)
    elif k == "bagla":
        out = bagla(argv[1] if len(argv) > 1 else None)
    elif k == "coz":
        out = coz()
    else:
        out = {"hata": "bilinmeyen: " + k}
    print(json.dumps(out, ensure_ascii=False))
    return 1 if out.get("hata") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
