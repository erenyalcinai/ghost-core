"""Nöbetçi alarmları için tek kapı — sahibe giden ile Ghost'ta kalan ayrılır.

NEDEN (2026-10-29, bütünsel denetim 06-arayuz §5 + 04-ozerklik Ö1): 30 günde sahibe
~429 otomatik mesaj gitti (günde ~14); 94'ü elinden hiçbir şey gelmeyen altyapı alarmıydı
(📵 iPhone-durum 32, 🚪 kendi rsync SSH'ımız 29 — bir günde 18 —, 🔌 ters tünel 16) ve
selfcheck "bayat kod → yenilendi" 115 kez Telegram'a düştü. Sahip ilkesi: "sürekli şarj
uyarısı verme, görüyorum zaten"; "ilerleme çekilir, itilmez". Nöbetçiler kendi
`send_telegram.py`/`tg_send`'ine gidiyordu; sınıf, tavan, soğuma yoktu.

İKİ SINIF:
- ``sinif="ghost"``: sahibe GİTMEZ. `ops/alarm_durum.json`'a kaynak/son metin/sayaç/ilk-son
  zaman yazılır; Nabız `ozet()` ile okur (sarı nokta). Ghost'un kendi çözeceği ya da
  kendiliğinden geçen şeyler (tünel zombisi temizlendi, 13 mini Wi-Fi'da değil, kod yenilendi).
- ``sinif="sahip"``: mevcut yoldan gider (`ghost.notify.telegram` → Telegram + Kokpit dökümü +
  APNs) ama GÜNLÜK TAVAN 5: aşan alarm Nabız'a düşer, Telegram'a günde BİR kez tek satır
  "ve N alarm daha Nabız'da". Aynı ``anahtar`` için SOĞUMA (varsayılan 6 sa): tekrar eden
  alarm ikinci kez gitmez, sayaç artar.

FREEZE'e dokunmaz (köprü/vekil ayrıca ele alır). Durum dosyası yazımı kilitli (fcntl) —
nöbetçiler ayrı timer'lardan aynı anda koşar. Testte (GHOST_TEST) ağ yok: `notify.telegram`
zaten kapılı, ayrıca ``gonder`` enjekte edilebilir; dosya yolu `GHOST_ALARM_DURUM` ile değişir.
"""
import fcntl
import json
import os
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPS = os.environ.get("GHOST_OPS") or os.path.join(BASE, "ops")     # testte geçici dizin
DURUM_DOSYASI = os.environ.get("GHOST_ALARM_DURUM") or os.path.join(OPS, "alarm_durum.json")

GUNLUK_TAVAN = 5                 # sahibe giden nöbetçi alarmı / gün
SOGUMA_SN = 6 * 3600             # aynı anahtar için varsayılan susma
SARI_PENCERE_SN = 24 * 3600      # ozet(): bu kadar süredir görülmeyen kayıt sarı sayılmaz
KAYIT_OMRU_SN = 7 * 24 * 3600    # bundan eski kayıt dosyadan düşer
SINIFLAR = ("ghost", "sahip")


def _dosya():
    return os.environ.get("GHOST_ALARM_DURUM") or DURUM_DOSYASI


def _gun(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _bos(gun):
    return {"surum": 1, "gun": gun, "gun_sayac": {"sahip": 0, "ghost": 0, "nabiz": 0, "gonderim": 0},
            "tasan_bildirildi": None, "kayitlar": {}}


def _oku(yol):
    try:
        with open(yol, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("kayitlar"), dict):
            return d
    except (OSError, ValueError):
        pass
    return None


def _yaz(yol, d):
    os.makedirs(os.path.dirname(yol) or ".", exist_ok=True)
    tmp = yol + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, yol)


class _Kilit:
    """Dosya kilidi — iki nöbetçi aynı anda yazarsa sayaç kaybolmasın."""

    def __init__(self, yol):
        self.yol = yol + ".lock"
        self.f = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.yol) or ".", exist_ok=True)
        self.f = open(self.yol, "a+")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        try:
            fcntl.flock(self.f, fcntl.LOCK_UN)
            self.f.close()
        except Exception:
            pass


def _gun_devri(d, simdi):
    """Gün değiştiyse gün sayaçlarını sıfırla, eski kayıtları buda."""
    gun = _gun(simdi)
    if d.get("gun") != gun:
        d["gun"] = gun
        d["gun_sayac"] = {"sahip": 0, "ghost": 0, "nabiz": 0, "gonderim": 0}
        d["tasan_bildirildi"] = None
    for k in list(d["kayitlar"]):
        try:
            if simdi - float(d["kayitlar"][k].get("son_ts", 0)) > KAYIT_OMRU_SN:
                del d["kayitlar"][k]
        except (TypeError, ValueError):
            del d["kayitlar"][k]
    return d


def _varsayilan_gonder(metin):
    from ghost import notify
    return bool(notify.telegram(metin))


def bildir(kaynak, metin, sinif="ghost", anahtar=None, soguma_sn=SOGUMA_SN,
           simdi=None, gonder=None):
    """Alarmı sınıfına göre işle. Döner: {"gitti": bool, "neden": str, "sayac": int}.

    neden: "gitti" (sahibe ulaştı) · "ghost" (Ghost sınıfı, Nabız'da) ·
    "soguma" (aynı anahtar soğumada) · "tavan" (günlük tavan dolu, Nabız'a düştü).
    ``anahtar`` verilmezse kaynak adı anahtardır (kaynağın tüm alarmları tek kalem).
    """
    if sinif not in SINIFLAR:
        raise ValueError("sinif: %r" % (sinif,))
    simdi = time.time() if simdi is None else simdi
    anahtar = anahtar or kaynak
    kimlik = "%s:%s" % (kaynak, anahtar)
    metin = (metin or "").strip()
    gonder = gonder or _varsayilan_gonder
    yol = _dosya()
    with _Kilit(yol):
        d = _gun_devri(_oku(yol) or _bos(_gun(simdi)), simdi)
        k = d["kayitlar"].get(kimlik) or {"kaynak": kaynak, "anahtar": anahtar, "sayac": 0,
                                           "ilk_ts": simdi, "son_gonderim_ts": None,
                                           "nabiz": 0, "kapandi_ts": None}
        if k.get("kapandi_ts"):          # kapatılmış kayıt yeniden alarm verdi → yeni olay
            k.update({"sayac": 0, "ilk_ts": simdi, "son_gonderim_ts": None, "nabiz": 0,
                      "kapandi_ts": None})
        k.update({"sinif": sinif, "son_metin": metin[:600], "son_ts": simdi})
        k["sayac"] = int(k.get("sayac", 0)) + 1
        d["gun_sayac"][sinif] = d["gun_sayac"].get(sinif, 0) + 1
        gitti, neden = False, "ghost"
        if sinif == "sahip":
            son = k.get("son_gonderim_ts")
            if son is not None and soguma_sn and simdi - float(son) < soguma_sn:
                neden = "soguma"
            elif d["gun_sayac"].get("gonderim", 0) >= GUNLUK_TAVAN:
                neden = "tavan"
                k["nabiz"] = int(k.get("nabiz", 0)) + 1
                d["gun_sayac"]["nabiz"] = d["gun_sayac"].get("nabiz", 0) + 1
                if d.get("tasan_bildirildi") != d["gun"]:
                    d["tasan_bildirildi"] = d["gun"]
                    try:
                        gonder("🔕 Günlük alarm tavanı (%d) doldu — ve %d alarm daha Nabız'da (%s)."
                               % (GUNLUK_TAVAN, d["gun_sayac"]["nabiz"], kaynak))
                    except Exception:
                        pass
            else:
                try:
                    gitti = bool(gonder(metin))
                except Exception:
                    gitti = False
                if gitti:
                    neden = "gitti"
                    k["son_gonderim_ts"] = simdi
                    d["gun_sayac"]["gonderim"] = d["gun_sayac"].get("gonderim", 0) + 1
                else:
                    neden = "gonderilemedi"
        d["kayitlar"][kimlik] = k
        _yaz(yol, d)
    return {"gitti": gitti, "neden": neden, "sayac": k["sayac"], "kimlik": kimlik}


def kapat(kaynak, anahtar=None, simdi=None):
    """Alarm konusu çözüldü (ray döndü, iPhone normale döndü): kayıt sarıdan düşer, sayaç
    saklanır. Bir sonraki aynı alarm yeni olay sayılır."""
    simdi = time.time() if simdi is None else simdi
    yol = _dosya()
    kimlik = "%s:%s" % (kaynak, anahtar or kaynak)
    with _Kilit(yol):
        d = _oku(yol)
        if not d:
            return False
        k = d["kayitlar"].get(kimlik)
        if not k:
            return False
        k["kapandi_ts"] = simdi
        _yaz(yol, d)
    return True


def ozet(simdi=None):
    """Nabız için okunabilir özet (health.py bunu çağırır; dosya yoksa boş döner).

    {"gun": "2026-10-29",
     "sayac": {"sahip": 3, "ghost": 12, "nabiz": 1, "gonderim": 3, "tavan": 5},
     "sari": [{"kaynak": "tunnel", "anahtar": "zombi", "sinif": "ghost", "metin": "…",
               "sayac": 4, "ilk": "02:10", "son": "05:40", "son_ts": 1761700000.0,
               "nabizda": False}, ...]}   # son 24 sa içinde görülen, kapanmamış kayıtlar;
    "nabizda": Sahip sınıfı olup tavan/soğuma yüzünden Telegram'a gitmemiş (Sahip görmemiş)."""
    simdi = time.time() if simdi is None else simdi
    d = _oku(_dosya())
    gun = _gun(simdi)
    if not d:
        return {"gun": gun, "sayac": {"sahip": 0, "ghost": 0, "nabiz": 0, "gonderim": 0,
                                      "tavan": GUNLUK_TAVAN}, "sari": []}
    gs = d.get("gun_sayac", {}) if d.get("gun") == gun else {}
    sayac = {"sahip": gs.get("sahip", 0), "ghost": gs.get("ghost", 0), "nabiz": gs.get("nabiz", 0),
             "gonderim": gs.get("gonderim", 0), "tavan": GUNLUK_TAVAN}
    sari = []
    for k in d.get("kayitlar", {}).values():
        try:
            son_ts = float(k.get("son_ts", 0))
        except (TypeError, ValueError):
            continue
        if k.get("kapandi_ts") or simdi - son_ts > SARI_PENCERE_SN:
            continue
        saat = lambda ts: time.strftime("%H:%M", time.localtime(float(ts)))
        sari.append({"kaynak": k.get("kaynak"), "anahtar": k.get("anahtar"),
                     "sinif": k.get("sinif"), "metin": k.get("son_metin", ""),
                     "sayac": k.get("sayac", 0), "ilk": saat(k.get("ilk_ts", son_ts)),
                     "son": saat(son_ts), "son_ts": son_ts,
                     "nabizda": k.get("sinif") == "sahip" and (
                         k.get("nabiz", 0) > 0 or k.get("son_gonderim_ts") is None)})
    sari.sort(key=lambda s: -s["son_ts"])
    return {"gun": gun, "sayac": sayac, "sari": sari}


def _main(argv):
    o = ozet()
    print("gün %s · sahibe %d/%d · Ghost'ta %d · Nabız'a düşen %d"
          % (o["gun"], o["sayac"]["gonderim"], o["sayac"]["tavan"], o["sayac"]["ghost"],
             o["sayac"]["nabiz"]))
    for s in o["sari"]:
        print("  %s %s/%s ×%d %s–%s%s  %s" % (
            "🟡" if s["sinif"] == "ghost" else "🔴", s["kaynak"], s["anahtar"], s["sayac"],
            s["ilk"], s["son"], " (Nabız'da)" if s["nabizda"] else "", s["metin"][:90]))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv[1:]))
