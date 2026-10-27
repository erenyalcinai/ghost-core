"""Mac'i VPS'ten ANINDA sür — kuyruk beklemeden.

Kuyruk yolu (ops/mac_cmd.py) duruyor ve YEDEK HAT olarak kalmalı: Mac uykuda
ya da tünel kopukken iş orada birikir, uyanınca akar. Bu dosya hızlı hattır;
karşı taraf yoksa hemen hata verir, işi saklamaz.

    python3 ops/mac.py durum
    python3 ops/mac.py komut "git -C ~/jarvis log --oneline -1"
    python3 ops/mac.py ekran [dosya] [--gonder "açıklama"] [--tam]

GUI (masaüstünü sür — cua-driver üstünden, telefon.py ile aynı refleks):
    python3 ops/mac.py tikla 756 491 [--sag] [--cift] [--on] [--px]
    python3 ops/mac.py yaz "metin"
    python3 ops/mac.py tus cmd+n            (tek tuş da olur: enter, esc)
    python3 ops/mac.py kaydir 756 491 asagi 8 [--sayfa]
    python3 ops/mac.py ac Notes             (uygulamayı öne getir)
    python3 ops/mac.py pencereler
    python3 ops/mac.py cua <arac> '<json>'  (ham geçiş — 60+ araç)

YubiKey kapısı (2026-09-25, Sahip kararı — Mac'te takılı 2. anahtar = fiziksel düğme):
    python3 ops/mac.py anahtar               (durum — kapı kilitliyken de okunur)
    python3 ops/mac.py anahtar bagla [<seri>|*]   (yalnız anahtar takılıyken)
    python3 ops/mac.py anahtar coz
Kilitliyken komut/ekran/GUI 423 döner; iş kuyruğa (mac_cmd) girer, anahtar takılınca akar.

KOORDİNAT (2026-08-23'te ölçüldü, tahmin değil): `ekran` çıktısı sips -Z 1512
ile küçültülür → görüntü 1512x982 = NOKTA uzayı. cua'nın tıklama uzayı ise
GERÇEK PİKSEL (3024x1964). İkisi 2 kat farklı; ilk denemede tıklama ekranın
sol üst çeyreğine düştü. Bu yüzden `tikla`/`kaydir` görüntüden okunan noktayı
kendisi çarpar. Ham piksel veriyorsan (get_appbop_state çıktısı) --px de.

Ölçüm (2026-08-20): kuyruk 6-14 sn, bu uç ~0.2 sn. Fark işin süresinden değil,
30 sn'lik yoklama aralığından geliyordu.
"""
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPS = os.path.join(BASE, "ops")
UC = os.environ.get("GHOST_MACLINK", "http://127.0.0.1:8765")
KOPUK = ("Mac ucu cevap vermiyor (%s). Mac uyuyor ya da tünel kapalı olabilir.\n"
         "Yedek hat çalışıyor: python3 ops/mac_cmd.py \"komut\" — iş kuyrukta "
         "bekler, Mac uyanınca koşar.")


def _token():
    p = os.path.join(BASE, "secrets.env")
    for satir in open(p, encoding="utf-8", errors="replace"):
        satir = satir.strip()
        if satir.startswith("GHOST_MACLINK_TOKEN="):
            return satir.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _cagir(yol, veri=None, zaman=180):
    # 2026-10-26: bir test sahte iste'yi ıskalayıp GERÇEK Mac'e çıktı (2026-10-26, kişisel veri testi).
    # Kapı ağ katmanında: GHOST_TEST altında hızlı hat hiç aranmaz (tests/common.py tek işaret).
    if os.environ.get("GHOST_TEST") == "1":
        return {"hata": "test ortamı: Mac hızlı hattına çıkılmaz"}
    req = urllib.request.Request(
        UC + yol, data=json.dumps(veri).encode() if veri is not None else None,
        method="POST" if veri is not None else "GET",
        headers={"Content-Type": "application/json", "X-Ghost-Token": _token()})
    try:
        with urllib.request.urlopen(req, timeout=zaman) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return {"hata": "HTTP %s" % e.code}
    except urllib.error.URLError as e:
        return {"hata": KOPUK % e.reason}


def komut(cmd, zaman=120, cwd=None):
    d = {"cmd": cmd, "timeout": zaman}
    if cwd:
        d["cwd"] = cwd
    return _cagir("/calistir", d, zaman + 30)


CUA = "$HOME/bin/cua-driver"


def cua(arac, arg=None, zaman=90):
    """cua-driver aracını Mac'te çağır. Argüman shell'e değil, base64 üzerinden
    dosyaya gider: metinde tırnak/yeni satır/Türkçe olsa da bozulmaz."""
    ham = base64.b64encode(json.dumps(arg or {}, ensure_ascii=False).encode()).decode()
    cmd = ("printf %s '" + ham + "' | base64 -d > /tmp/ghost_cua.json && "
           + CUA + " call " + arac + " \"$(cat /tmp/ghost_cua.json)\"")
    c = komut(cmd, zaman=zaman)
    if c.get("hata"):
        return c
    metin = (c.get("cikti") or "").strip()
    try:
        return json.loads(metin)
    except ValueError:
        return {"cikti": metin, "rc": c.get("rc")}


OLCEK = os.path.join(OPS, "mac_olcek.json")


def olcek():
    """Görüntü (nokta) ile tıklama (piksel) uzayı arasındaki çarpan.
    Bir kez ölçülür, dosyada saklanır; her tıklamada ölçmek pahalı."""
    try:
        return float(json.load(open(OLCEK))["carpan"])
    except Exception:
        pass
    # screenshot_out_file: metadata'yı al, dev b64'ü tünelden geçirme.
    d = cua("get_appbop_state", {"screenshot_out_file": "/Users/sahip/jarvis/ops/mac_olcek.png"})
    try:
        c = float(d["screenshot_height"]) / float(d["screen_height"])
    except Exception:
        c = 2.0
    try:
        json.dump({"carpan": c, "ekran": d.get("screenshot_height")}, open(OLCEK, "w"))
    except OSError:
        pass
    return c


def tikla(x, y, sag=False, cift=False, on=False, px=False):
    k = 1.0 if px else olcek()
    d = {"x": int(round(float(x) * k)), "y": int(round(float(y) * k)),
         "scope": "appbop"}
    if sag:
        d["button"] = "right"
    if cift:
        d["count"] = 2
    if on:
        d["delivery_mode"] = "foreground"
    return cua("click", d)


def yaz(metin, on=False):
    d = {"text": metin, "scope": "appbop"}
    if on:
        d["delivery_mode"] = "foreground"
    return cua("type_text", d)


def tus(kombo, on=False):
    """cmd+n / cmd+shift+4 / enter / esc"""
    k = [p.strip() for p in kombo.replace("-", "+").split("+") if p.strip()]
    d = {"scope": "appbop"}
    if on:
        d["delivery_mode"] = "foreground"
    if len(k) == 1:
        d["key"] = k[0]
        return cua("press_key", d)
    d["keys"] = k
    return cua("hotkey", d)


YON = {"asagi": "down", "yukari": "up", "sol": "left", "sag": "right",
       "down": "down", "up": "up", "left": "left", "right": "right"}


def kaydir(x, y, yon="asagi", miktar=5, sayfa=False, px=False):
    """Fare tekerleği. cua delta DEĞİL `direction` ister — delta_y ile çağırınca
    sessizce invalid_arguments döner (2026-08-23'te bu tuzağa düştüm).
    x,y verilir çünkü tekerlek o noktanın ALTINDAKİ öğeye iner: iç içe
    overflow:auto bölgeleri ancak böyle kayar, klavye yolu onlara işlemez."""
    k = 1.0 if px else olcek()
    d = {"x": int(round(float(x) * k)), "y": int(round(float(y) * k)),
         "direction": YON.get(str(yon).lower(), "down"),
         "amount": max(1, min(50, int(miktar))), "scope": "appbop"}
    if sayfa:
        d["by"] = "page"
    return cua("scroll", d)


def _hedef(uygulama):
    """Ad -> (pid, window_id). cua araçları pid ister, ad değil; üstelik uygulamanın
    birden çok penceresi varsa window_id vermezsen 'candidates' döner, iş yapmaz.
    NOT: pencere adları macOS diline göre gelir — Notes değil 'Notlar'."""
    ad = uygulama.lower()
    aday = None
    for p in (pencereler().get("windows") or []):
        if ad not in (p.get("app_name") or "").lower():
            continue
        # ekranda olan ve başlığı olan pencere gerçek pencere; diğerleri panel/gölge
        puan = (2 if p.get("is_on_screen") else 0) + (1 if p.get("title") else 0)
        if aday is None or puan > aday[0]:
            aday = (puan, p.get("pid"), p.get("window_id"))
    return (aday[1], aday[2]) if aday else (None, None)


def ac(uygulama):
    pid, wid = _hedef(uygulama)
    if pid is None:
        komut("open -a %s" % json.dumps(uygulama), zaman=30)
        import time as _t
        _t.sleep(2)
        pid, wid = _hedef(uygulama)
    if pid is None:
        return {"hata": "uygulama bulunamadı: " + uygulama}
    d = {"pid": int(pid)}
    if wid is not None:
        d["window_id"] = int(wid)
    return cua("bring_to_front", d)


def pencereler():
    return cua("list_windows", {})


def ekran(dosya=None, gonder=None, tam=False):
    """Varsayılan küçültülmüş JPEG (~0.6 sn); tam=True retina PNG (~5 sn)."""
    c = _cagir("/ekran", {"tam": bool(tam)}, 120)
    if "png_b64" not in c:
        return c
    ham = base64.b64decode(c["png_b64"])
    dosya = dosya or os.path.join(OPS, "mac_ekran." + (c.get("bicim") or "png"))
    open(dosya, "wb").write(ham)
    sonuc = {"dosya": dosya, "bayt": len(ham)}
    if gonder:
        r = subprocess.run([sys.executable, os.path.join(OPS, "send_photo.py"),
                            dosya, gonder], capture_output=True, text=True, timeout=120)
        sonuc["telegram"] = "gönderildi" if r.returncode == 0 else (r.stderr or "hata")[:200]
    return sonuc


def anahtar(alt="durum", seri=None):
    """YubiKey kapısı (Mac'te fiziksel düğme, ops/mac_anahtar.py).
    durum: /durum üzerinden okunur — kapı KİLİTLİYKEN de cevap verir.
    bagla/coz: Mac'te komut koşar, yani yalnız anahtar takılıyken (tasarım)."""
    if alt == "durum":
        c = _cagir("/durum")
        if c.get("hata"):
            return c
        return c.get("anahtar") or {"hata": "Mac ucu eski kod: /durum 'anahtar' alanı yok "
                                            "(launchctl kickstart -k gui/$UID/ai.jarvis.maclink)"}
    if alt in ("bagla", "coz"):
        arg = (" " + seri) if (alt == "bagla" and seri) else ""
        r = komut("python3 $HOME/jarvis/ops/mac_anahtar.py %s%s" % (alt, arg), zaman=30)
        if r.get("hata"):
            return r
        try:
            return json.loads(r.get("cikti") or "")
        except ValueError:
            return {"hata": "Mac cevabı JSON değil: " + (r.get("cikti") or "")[:200]}
    return {"hata": "anahtar durum|bagla [<seri>|*]|coz"}


def main(argv):
    if not argv:
        print(__doc__.strip())
        return 2
    k = argv[0]
    if k == "durum":
        c = _cagir("/durum")
    elif k == "komut":
        c = komut(" ".join(x for x in argv[1:] if not x.startswith("--")))
    elif k == "ekran":
        gonder, arg = None, argv[1:]
        if "--gonder" in arg:
            i = arg.index("--gonder")
            gonder = arg[i + 1] if i + 1 < len(arg) else "Mac ekranı"
            arg = arg[:i] + arg[i + 2:]
        # Bayrakları dosya adı sanma: "ekran --tam x.png" çağrısı "--tam" adlı
        # dosya yazıyordu. Dosya adı = bayrak olmayan ilk argüman.
        arg = [x for x in arg if not x.startswith("--")]
        dosya = arg[0] if arg else None
        c = ekran(dosya, gonder, "--tam" in argv)
    elif k == "tikla":
        c = tikla(argv[1], argv[2], "--sag" in argv, "--cift" in argv,
                  "--on" in argv, "--px" in argv)
    elif k == "yaz":
        c = yaz(argv[1], "--on" in argv)
    elif k == "tus":
        c = tus(argv[1], "--on" in argv)
    elif k == "kaydir":
        a = [x for x in argv[1:] if not x.startswith("--")]
        c = kaydir(a[0], a[1], a[2] if len(a) > 2 else "asagi",
                   a[3] if len(a) > 3 else 5, "--sayfa" in argv, "--px" in argv)
    elif k == "ac":
        c = ac(" ".join(argv[1:]))
    elif k == "pencereler":
        c = pencereler()
    elif k == "cua":
        c = cua(argv[1], json.loads(argv[2]) if len(argv) > 2 else {})
    elif k == "anahtar":
        c = anahtar(argv[1] if len(argv) > 1 else "durum", argv[2] if len(argv) > 2 else None)
    else:
        print("bilinmeyen komut: " + k)
        return 2
    if "cikti" in c:
        print("(rc=%s, %s sn)\n%s" % (c.get("rc"), c.get("sn"), c.get("cikti")))
    else:
        print(json.dumps(c, ensure_ascii=False))
    return 1 if c.get("hata") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
