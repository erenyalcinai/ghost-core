"""Site nabzı — "Siteler nasıl?" (Nabız'ın 4. bölümü, 2026-09-06, Sahip isteği).

Kaynak `ops/site_trafik.py` (PostHog HogQL; iPhone Pro sınıfı (olasılık, kimlik değil) ve veri merkezi
botları AYIKLANIR — ham sayı %60 şişik). PostHog sorgusu saniyeler sürer, Nabız 15
sn'de bir yenilenir; o yüzden ölçüm burada YAPILMAZ, `ops/uptime_watch.py` nöbetçisi
saatte bir `tazele()` ile `ops/site_trafik_state.json` yazar, Nabız yalnız okur.

    python3 -m ghost.trafik olc     # şimdi ölç ve yaz
    python3 -m ghost.trafik oku     # Nabız'ın göreceği JSON
"""
import json
import os
import sys
import time

from ghost import config

STATE = "site_trafik_state.json"
GUN = 7
TAZELIK_SN = 55 * 60      # nöbetçi 10 dk'da bir uğrar; saatte bir ölçüm yeter
BAYAT_SN = 3 * 3600       # bundan eskiyse Nabız "ölçüm eski" der
POSTHOG_ANAHTARLAR = ("POSTHOG_HOST", "POSTHOG_API_KEY", "POSTHOG_PROJECT_ID")


def _site_trafik():
    """ops/site_trafik.py betiğini modül olarak yükle (kendi dizini sys.path'te değil)."""
    ops = os.path.join(config.BASE, "ops")
    if ops not in sys.path:
        sys.path.insert(0, ops)
    import site_trafik
    return site_trafik


def _yol():
    return os.path.join(config.OPS, STATE)


def _oku_ham():
    try:
        with open(_yol(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _bos():
    return {"g7": 0, "k7": 0, "g1": 0, "k1": 0, "sahip7": 0, "bot7": 0, "kaynaklar": {}}


def olc(gun=GUN):
    """PostHog'dan 7 gün + 24 saat kimlik dökümü çek, site site kutula, state yaz."""
    env = config.load_env()
    for k in POSTHOG_ANAHTARLAR:
        if env.get(k):
            os.environ.setdefault(k, env[k])
    st = _site_trafik()
    siteler = {h: _bos() for h in st.SITELER}
    hafta = st.kimlikler(gun)
    for k in hafta:
        s = siteler.setdefault(k["site"], _bos())
        sinif = st.sinifla(k)
        if sinif == "dis":
            s["g7"] += k["olay"]
            s["k7"] += 1
            for kaynak in (k.get("kaynak") or "").split(","):
                kaynak = kaynak.strip()
                if kaynak and kaynak not in ("$direct", "None", "null"):
                    s["kaynaklar"][kaynak] = s["kaynaklar"].get(kaynak, 0) + 1
        elif sinif == "sahip":
            s["sahip7"] += k["olay"]
        else:
            s["bot7"] += k["olay"]
    for k in st.kimlikler(1):
        if st.sinifla(k) == "dis":
            s = siteler.setdefault(k["site"], _bos())
            s["g1"] += k["olay"]
            s["k1"] += 1
    yabanci26 = [k for k in hafta if k["os"] == "iOS"
                 and str(k["osv"] or "").startswith(st.SAHIP_IOS)
                 and (k["gen"], k["yuk"]) != st.SAHIP_EKRAN]
    kayit = {"at": time.strftime("%F %T"), "ts": int(time.time()), "gun": gun,
             "siteler": siteler,
             "toplam_g7": sum(s["g7"] for s in siteler.values()),
             "toplam_g1": sum(s["g1"] for s in siteler.values()),
             "imza_uyari": len(yabanci26)}
    yol = _yol()
    with open(yol + ".tmp", "w", encoding="utf-8") as f:
        json.dump(kayit, f, ensure_ascii=False)
    os.replace(yol + ".tmp", yol)
    return kayit


def tazele(zorla=False):
    """Nöbetçi kancası: state taze ise dokunma, değilse ölç. Hata fırlatmaz."""
    ham = _oku_ham()
    if not zorla and ham and time.time() - ham.get("ts", 0) < TAZELIK_SN:
        return False
    try:
        olc()
        return True
    except Exception as e:  # ağ/anahtar hatası nöbetçiyi düşürmesin
        sys.stderr.write("trafik ölçülemedi: %r\n" % e)
        return False


def _uptime_ok():
    """uptime_state.json → {host: ok}."""
    try:
        with open(os.path.join(config.OPS, "uptime_state.json"), encoding="utf-8") as f:
            us = json.load(f)
    except Exception:
        return {}
    out = {}
    for url, ok in (us or {}).items():
        host = url.split("//")[-1].split("/")[0]
        out[host] = bool(ok)
    return out


def oku():
    """Nabız için: var/yok, ölçüm zamanı ve yaşı, toplamlar, site satırları (sıra sabit)."""
    ham = _oku_ham()
    ok = _uptime_ok()
    if not ham:
        return {"var": False, "siteler": [{"ad": h, "url": "https://" + h, "ok": ok.get(h)}
                                          for h in _site_trafik().SITELER]}
    yas = max(0, int(time.time()) - int(ham.get("ts", 0)))
    satirlar = []
    sira = list(_site_trafik().SITELER) + [h for h in ham.get("siteler", {}) if h not in _site_trafik().SITELER]
    for h in sira:
        s = ham.get("siteler", {}).get(h) or _bos()
        kaynak = sorted(s.get("kaynaklar", {}).items(), key=lambda x: -x[1])[:2]
        satirlar.append({"ad": h, "url": "https://" + h, "ok": ok.get(h),
                         "ziyaret_7g": s.get("g7", 0), "kimlik_7g": s.get("k7", 0),
                         "ziyaret_24s": s.get("g1", 0),
                         "kaynak": " · ".join(k for k, _ in kaynak)})
    return {"var": True, "olculdu": ham.get("at", ""), "yas_dk": yas // 60,
            "gun": ham.get("gun", GUN), "toplam_7g": ham.get("toplam_g7", 0),
            "toplam_24s": ham.get("toplam_g1", 0), "bayat": yas > BAYAT_SN,
            "imza_uyari": ham.get("imza_uyari", 0), "siteler": satirlar}


def main(argv):
    if not argv or argv[0] == "oku":
        print(json.dumps(oku(), ensure_ascii=False, indent=1))
        return 0
    if argv[0] == "olc":
        k = olc()
        print("ölçüldü %s · 7g dış %d · 24s dış %d" % (k["at"], k["toplam_g7"], k["toplam_g1"]))
        for h, s in k["siteler"].items():
            print("  %-18s 7g %3d (%2d kimlik)  24s %3d  iPhonePro? %2d  bot %2d"
                  % (h, s["g7"], s["k7"], s["g1"], s["sahip7"], s["bot7"]))
        return 0
    print(__doc__.strip())
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
