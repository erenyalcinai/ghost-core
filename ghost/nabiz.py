"""Nabız birimi (Faz 3.1, 2026-10-30 — bütünsel denetim 03 h8-9): monitör / zamanlayıcı / hedef / kural
tick'leri köprüden AYRI bir süreçte koşar (`ghost-nabiz`, kullanıcı systemd birimi).

NEDEN: köprü (ops/telegram_bridge.py) hem sohbet hem nöbet döngüsüydü — her getUpdates turunda dört tick'i de
çağırıyordu. Köprü yeniden başlarken / takılırken monitör ve zamanlayıcı da duruyordu; sohbet birimi (3.2)
ayrılacaksa önce üreticiler ayrılmalı. Dört tick de zaten bus üreticisidir (`bus.istek` / `notify.telegram`),
köprü durumuna bağımlılıkları yok.

TASARIM
- `tur()`: dosya kilidi altında dört tick (her biri kendi try'ında; biri düşünce diğerleri koşar), sonra damga:
  durum `nabiz` {ts, pid, tur, hata{ad: metin}} + heartbeat damgası (`heartbeat.nabiz`, Nabız'da görünür).
- `kos()`: 20 sn'de bir `tur()`. Modüller kendi `_TICK_GAP`'ini uygular (55/55/300/60 sn) → döngü sık olsa da
  tick'ler eskisi gibi dakikada/5 dakikada bir.
- **Çift tick önleme (iki katman):** (1) damga tazeyse (`canli()`, <%d sn) köprü dört tick'e HİÇ dokunmaz;
  (2) damga bayatsa köprü `kopru_tick()` ile yedek yola düşer — aynı dosya kilidini BEKLEMEDEN alır, alamazsa o
  turu atlar. Kilit ardışık iki turu serileştirir; modüllerin DB tabanlı vadeleri (last_run/next_ts/last_worked)
  ardışık ikinci turu zaten boş geçirir. Birim ölürse köprü ≤%d sn içinde kendiliğinden devralır, birim dönünce
  bırakır — kimse bir şey yapmaz.
- Özerk DEĞİL: bu süreç Claude koşturmaz, yalnız kuyruklar/duyurur (köprünün yaptığı gibi); GHOST_OZERK yok →
  vekil yazma izni köprüyle aynı. FREEZE'i modüller kendileri yoklar (governance.is_frozen).
- `sahne.servis_esitle()` köprüde KALIR (4. tur K1: kart eşitleme yalnız köprü ana sürecinden).

CLI: python3 -m ghost.nabiz durum | kos [--aralik SN] [--bir]
"""
import contextlib
import fcntl
import json
import os
import signal
import sys
import time

from ghost import config, durum

DURUM_AD = "nabiz"
ARALIK_SN = 20
TAZE_SN = 120                 # köprü bu kadar süredir damga görmezse birimi ölü sayar
HEARTBEAT_ARALIK_SN = 60      # heartbeat.nabiz damgası (durum yazımı her turda, heartbeat dakikada bir)
__doc__ = __doc__ % (TAZE_SN, TAZE_SN)

_log = None                   # kos() bağlar; testte None → sessiz
_son_heartbeat = [0.0]
_tur_sayac = [0]
_kopru_onceki = [None]        # köprü yedek yolunun kenar günlüğü: True = birim canlıydı


def log(msg):
    if _log:
        try:
            _log(msg)
        except Exception:
            pass


def moduller():
    """Dört üretici (ad, modül). Fonksiyon: import sırası/yan etkisi çağrı anında; testler yamalar."""
    from ghost import goals, kurallar, monitors, schedule
    return (("monitors", monitors), ("schedule", schedule), ("goals", goals), ("kurallar", kurallar))


def kilit_yolu():
    """Testte (GHOST_OPS) geçici dizin; üretimde ~/.ghost (repo dizini kod + yapılandırma, 6c)."""
    if os.environ.get("GHOST_OPS"):
        return os.path.join(config.OPS, "nabiz.lock")
    d = os.path.join(os.path.expanduser("~"), ".ghost")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "nabiz.lock")


@contextlib.contextmanager
def kilit(bloke=True):
    """Dosya kilidi; `bende` True ise tick'ler koşar. bloke=False: alınamazsa beklemeden False verir."""
    f = open(kilit_yolu(), "a+")
    bende = False
    try:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | (0 if bloke else fcntl.LOCK_NB))
            bende = True
        except OSError:
            bende = False
        yield bende
    finally:
        if bende:
            try:
                fcntl.flock(f, fcntl.LOCK_UN)
            except OSError:
                pass
        f.close()


def _tickle(mods, log_fn=None):
    log_fn = log_fn or log
    sonuc = {}
    for ad, mod in mods:
        try:
            mod.tick()
            sonuc[ad] = None
        except Exception as e:  # noqa: BLE001 — biri düşünce diğerleri koşar
            sonuc[ad] = repr(e)[:200]
            try:
                log_fn("tick hata[%s]: %r" % (ad, e))
            except Exception:
                pass
    return sonuc


def damga(sonuc, now=None):
    now = now or time.time()
    _tur_sayac[0] += 1
    hata = {k: v for k, v in sonuc.items() if v}
    durum.yaz(DURUM_AD, {"ts": now, "at": time.strftime("%F %T", time.localtime(now)), "pid": os.getpid(),
                         "tur": _tur_sayac[0], "hata": hata})
    if now - _son_heartbeat[0] >= HEARTBEAT_ARALIK_SN:
        _son_heartbeat[0] = now
        try:
            durum.yaz("heartbeat." + DURUM_AD, {"ts": int(now), "at": time.strftime("%F %T", time.localtime(now))})
        except Exception:
            pass


def tur(now=None, mods=None):
    """Bir nabız turu: kilit altında dört tick, ardından damga. {ad: hata|None} döner."""
    with kilit(bloke=True):
        sonuc = _tickle(mods or moduller())
    damga(sonuc, now)
    return sonuc


def son(now=None):
    """Son damga (dict) ya da None; `yas` saniye eklenir."""
    d = durum.oku(DURUM_AD)
    if not d or not d.get("ts"):
        return None
    d = dict(d)
    d["yas"] = (now or time.time()) - float(d["ts"])
    return d


def canli(now=None, taze=TAZE_SN):
    """Birim son `taze` sn içinde damga bastı mı? (köprü buna bakar)"""
    d = son(now)
    return bool(d) and d["yas"] < taze


def kopru_tick(mods, log_fn=None):
    """Köprü tick'inden çağrılır. Birim canlıysa hiçbir şey yapmaz (False). Değilse yedek yol: kilidi beklemeden
    dener, alırsa dört tick'i koşar (True); alamazsa (birim tam o an tur atıyor) o turu atlar (False).
    Kenar geçişleri bir kez günlüğe düşer: birim sustu → köprü devraldı / birim döndü → köprü bıraktı."""
    c = canli()
    if _kopru_onceki[0] is None or c != _kopru_onceki[0]:
        if log_fn:
            log_fn("nabız birimi %s" % ("canlı — tick'ler onda" if c else "SUSTU — tick'ler köprüde (yedek yol)"))
        _kopru_onceki[0] = c
    if c:
        return False
    with kilit(bloke=False) as bende:
        if not bende:
            return False
        _tickle(mods, log_fn)
    return True


def durum_metni(now=None):
    d = son(now)
    if not d:
        return "nabız: hiç damga yok (birim hiç koşmadı)"
    satir = "nabız: %s (%d sn önce, pid %s, tur %s) — %s" % (
        d.get("at"), int(d["yas"]), d.get("pid"), d.get("tur"), "CANLI" if d["yas"] < TAZE_SN else "BAYAT")
    if d.get("hata"):
        satir += "\n  hata: " + json.dumps(d["hata"], ensure_ascii=False)
    return satir


def kos(aralik=ARALIK_SN, bir=False, log_fn=None, uyku=time.sleep):
    """Ana döngü (systemd). SIGTERM/SIGINT → tur bitince temiz çıkış."""
    global _log
    _log = log_fn
    dur = [False]

    def _dur(sig, frm):
        dur[0] = True
    for s in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(s, _dur)
        except (ValueError, OSError):
            pass
    log("nabız birimi başladı (pid %d, aralık %d sn)" % (os.getpid(), aralik))
    while True:
        try:
            tur()
        except Exception as e:  # noqa: BLE001 — döngü ölmez
            log("tur hata: %r" % e)
        if bir or dur[0]:
            break
        uyku(aralik)
    log("nabız birimi durdu")


def _main(argv):
    argv = list(argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip().splitlines()[-1].strip())
        return 0
    cmd, args = argv[0], argv[1:]
    if cmd == "durum":
        print(durum_metni())
        return 0
    if cmd == "kos":
        aralik = ARALIK_SN
        if "--aralik" in args:
            aralik = max(5, int(args[args.index("--aralik") + 1]))
        from ghost import gunluk
        kos(aralik=aralik, bir="--bir" in args, log_fn=lambda m: gunluk.yaz("nabiz", m))
        return 0
    print("bilinmeyen komut: %s" % cmd)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
