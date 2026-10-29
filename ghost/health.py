"""Ghost sağlık toplayıcısı — MAKİNE-OKUNUR nabız.

NEDEN VAR (2026-09-02): sağlık bilgisi 12+ ayrı state dosyasına dağılmıştı ve
köprünün `saglik_text`/`durum_text` fonksiyonları yalnız insan metni üretiyor,
üstelik VPS'te `launchctl` (Mac komutu) çağırıp sessizce boş dönüyordu.
Kokpit uygulamasının "Nabız" ekranı ve köprünün /saglik komutu artık aynı
toplayıcıdan beslenir. Kural: hiçbir alt toplayıcı fırlatmaz; okunamayan
alan "bilinmiyor" olur — sağlık ekranı kendisi hasta olmasın.
"""
import glob
import json
import os
import platform
import subprocess
import time
import urllib.request

from ghost import config

# nöbetçi damgaları için "bayat" eşiği (sn) — periyodunun ~2-3 katı
NOBETCI_ESIK = {"selfcheck": 45 * 60, "uptime": 40 * 60, "port_watch": 40 * 60,
                "biz_watch": 3 * 3600, "marka_deploy": 14 * 3600}
try:  # Faz 2.10: tek kaynak ops/heartbeat.WATCHERS (varsa); yoksa yukarıdaki çekirdek eşikleri
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("_heartbeat", os.path.join(config.BASE, "ops", "heartbeat.py"))
    if _spec and _spec.loader:
        _hb = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_hb)
        NOBETCI_ESIK.update({k: v[1] for k, v in getattr(_hb, "WATCHERS", {}).items()})
except Exception:
    pass
AKTIF_IS = ("kuyrukta", "çalışıyor", "denetimde")


def _json(ad, default=None):
    try:
        with open(os.path.join(config.OPS, ad), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _durum(ad, eski_dosya, default=None):
    """Faz 1.9: durum tablosu; eski ops/<dosya>.json varsa bir kez içeri alınır."""
    try:
        from ghost import durum
        return durum.tasi(ad, os.path.join(config.OPS, eski_dosya), default)
    except Exception:
        return default


def servisler():
    """[(ad, durum)] — Linux'ta systemd, Mac'te launchd. Hata → boş liste."""
    out = []
    try:
        if platform.system() == "Darwin":
            r = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10)
            for l in r.stdout.splitlines():
                if "jarvis" in l:
                    cols = l.split("\t")
                    pid, label = cols[0], cols[-1]
                    out.append((label.split(".")[-1],
                                "çalışıyor" if pid.strip("-").isdigit() else "beklemede"))
        else:
            for u in ("ghost-bridge", "ghost-hook"):
                r = subprocess.run(["systemctl", "is-active", u], capture_output=True,
                                   text=True, timeout=5)
                out.append((u, r.stdout.strip() or "bilinmiyor"))
            env = dict(os.environ)
            env.setdefault("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
            r = subprocess.run(["systemctl", "--user", "is-active", "ghost-api"],
                               capture_output=True, text=True, timeout=5, env=env)
            out.append(("ghost-api", r.stdout.strip() or "bilinmiyor"))
    except Exception:
        pass
    return out


def _vps():
    d = {"ad": "VPS", "durum": "iyi", "detay": ""}
    try:
        load = os.getloadavg()[0]
        st = os.statvfs(config.BASE)
        disk = st.f_bavail * st.f_frsize / 1e9
        mem = 0
        for l in open("/proc/meminfo"):
            if l.startswith("MemAvailable"):
                mem = int(l.split()[1]) / 1024
        d["detay"] = "yük %.2f · disk %.0f GB boş · bellek %.0f MB boş" % (load, disk, mem)
        d["disk_bos_gb"] = round(disk, 1)
        d["bellek_bos_mb"] = int(mem)
        if disk < 3 or mem < 300:
            d["durum"] = "kritik"
        elif disk < 6 or mem < 600 or load > 3:
            d["durum"] = "uyari"
    except Exception:
        d["durum"] = "bilinmiyor"
    return d


def _mac_token():
    try:
        for l in open(os.path.join(config.BASE, "secrets.env"), encoding="utf-8"):
            if l.startswith("GHOST_MACLINK_TOKEN="):
                return l.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def _mac():
    d = {"ad": "Mac", "durum": "bilinmiyor", "detay": ""}
    if os.environ.get("GHOST_TEST"):
        return d
    try:
        req = urllib.request.Request("http://127.0.0.1:8765/durum",
                                     headers={"X-Ghost-Token": _mac_token()})
        with urllib.request.urlopen(req, timeout=2) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        d["durum"] = "iyi" if j.get("durum") == "ayakta" else "uyari"
        d["detay"] = j.get("makine", "") or j.get("durum", "")
    except Exception as e:
        d["durum"] = "uyari"
        d["detay"] = "hızlı hat cevap vermiyor (uykuda/tünel kopuk)"
    tw = _durum("tunnel_watch", "tunnel_watch_state.json", {}) or {}
    if tw.get("durum") and tw.get("durum") != "saglikli":
        d["detay"] += " · tünel: %s" % tw.get("durum")
    return d


def _wda_canli(zaman=2.0):
    """8101 (ters tünel → Mac rölesi → telefon WDA) ŞU AN cevap veriyor mu? Dosya değil, yoklama."""
    if os.environ.get("GHOST_TEST"):
        return None
    try:
        with urllib.request.urlopen("http://127.0.0.1:8101/status", timeout=zaman) as r:
            return bool(json.loads(r.read().decode("utf-8", "replace")).get("value"))
    except Exception:
        return False


def _iphone():
    """Sahip 2026-09-06: 'Nabız bağlantı durumunda geride kalıyor' — iphone_state.json Mac'in
    30 dk'da bir yazdığı rapor; canlılık artık 8101 yoklamasıyla ölçülür, dosya yalnız ayrıntı."""
    d = {"ad": "iPhone (Ghost)", "durum": "bilinmiyor", "detay": ""}
    canli = _wda_canli()
    st = _json("iphone_state.json", {})
    if canli is True:
        d["durum"], d["detay"] = "iyi", "WDA hazır (canlı)"
    elif canli is False:
        d["durum"] = "uyari"
        d["detay"] = "WDA cevap vermiyor (tünel/röle/telefon)"
        if st and not st.get("wda_ready") and st.get("neden"):
            d["detay"] += " · son rapor: %s" % st.get("neden", "")[:60]
    elif st:
        d["durum"] = "iyi" if st.get("wda_ready") else "uyari"
        d["detay"] = ("WDA hazır" if st.get("wda_ready") else "WDA yok: %s" % st.get("neden", ""))
        d["detay"] += " · %s" % st.get("at", "")
    return d


def _baglanti(mak):
    """Uzuv bağlantısı tek bakışta (Nabız üst şeridi): tünel + Mac hızlı hat + telefon WDA.
    Hepsi ŞU ANKİ yoklamadan; tunnel_watch_state yalnız nöbetçinin son notu."""
    mac = next((m for m in mak if m["ad"] == "Mac"), {})
    tel = next((m for m in mak if m["ad"].startswith("iPhone")), {})
    tw = _durum("tunnel_watch", "tunnel_watch_state.json", {}) or {}
    return {"mac": mac.get("durum") == "iyi", "telefon": tel.get("durum") == "iyi",
            "tunel": tw.get("durum", ""), "tunel_at": tw.get("at", ""),
            "ts": int(time.time())}


def _siteler():
    us = _json("uptime_state.json", {}) or {}
    out = []
    for url, ok in us.items():
        ad = url.split("//")[-1].rstrip("/")
        if "/" in ad:
            ad = ad.split("/")[0] + "/…"
        out.append({"ad": ad, "url": url, "ok": bool(ok)})
    return out


def _nobetciler():
    """Faz 1.9: damgalar durum tablosunda `heartbeat.<ad>` (ops/heartbeat.py); eski ops/heartbeats/*.json
    dosyaları ilk bakışta bir kez içeri alınır."""
    out = []
    now = time.time()
    try:
        from ghost import durum
    except Exception:
        return out
    for p in sorted(glob.glob(os.path.join(config.OPS, "heartbeats", "*.json"))):
        ad = os.path.basename(p)[:-5]
        if not ad.startswith("_"):
            try:
                durum.tasi("heartbeat." + ad, p)
            except Exception:
                pass
    try:
        kayitlar = durum.liste("heartbeat.")
    except Exception:
        return out
    for r in kayitlar:
        ad = r["ad"][len("heartbeat."):]
        try:
            j = durum.oku(r["ad"]) or {}
            yas = int(now - float(j.get("ts", 0)))
            out.append({"ad": ad, "son": j.get("at", ""), "yas_sn": yas,
                        "ok": yas < NOBETCI_ESIK.get(ad, 2 * 3600)})
        except Exception:
            out.append({"ad": ad, "son": "", "yas_sn": -1, "ok": False})
    return out


def _isler():
    d = {"aktif": 0, "bekleyen_onay": 0, "ozet": []}
    jobs = []
    for p in glob.glob(os.path.join(config.OPS, "jobs", "*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                j = json.load(f)
            jid = int(os.path.basename(p)[:-5])
            jobs.append((jid, j))
        except Exception:
            continue
    jobs.sort(reverse=True)
    for jid, j in jobs[:6]:
        d["ozet"].append({"id": jid, "durum": j.get("status", "?"), "tur": j.get("kind", ""),
                          "prompt": (j.get("prompt") or "")[:120],
                          "baslangic": j.get("started", "")})
    d["aktif"] = sum(1 for _, j in jobs if j.get("status") in AKTIF_IS)
    d["bekleyen_onay"] = len(_json("pending.json", {}) or {})
    return d


def _baglam():
    cs = _json("chat_session.json", {}) or {}
    ctx, win = cs.get("ctx") or 0, cs.get("window") or 0
    return {"yuzde": int(100 * ctx / win) if win else 0, "tur": cs.get("turns", 0),
            "maliyet": round(cs.get("cost_session", 0) or 0, 2)}


def _duyular():
    """sahibin telefonundan gelen izinli bağlam (konum/takvim/cihaz) — Faz 5."""
    try:
        from ghost import duyular
        return duyular.ozet()
    except Exception:
        return {"var": False, "acik": True}


def nabiz():
    from ghost import governance
    st = _json("state.json", {}) or {}
    mak = [_vps(), _mac(), _iphone()]
    svc = servisler()
    for ad, durum in svc:
        mak.append({"ad": ad, "durum": "iyi" if durum in ("çalışıyor", "active") else
                    ("bilinmiyor" if durum == "bilinmiyor" else "kritik"), "detay": durum})
    siteler, nob, isler = _siteler(), _nobetciler(), _isler()
    freeze = governance.is_frozen()
    ozet = "iyi"
    if any(m["durum"] == "kritik" for m in mak) or any(not s["ok"] for s in siteler):
        ozet = "kritik"
    elif freeze or any(m["durum"] == "uyari" for m in mak) or any(not n["ok"] for n in nob):
        ozet = "uyari"
    vps = mak[0]
    try:
        from ghost import channels
        kanal = channels.birincil()
    except Exception:
        kanal = "telegram"
    return {"at": time.strftime("%F %T"), "ozet": ozet, "freeze": freeze,
            "guard": bool(st.get("guard", True)), "model": st.get("model", ""),
            "kanal": kanal, "sesli": bool(st.get("voice")),
            "makineler": mak, "siteler": siteler, "nobetciler": nob, "isler": isler,
            "baglam": _baglam(), "duyular": _duyular(), "disk_bos_gb": vps.get("disk_bos_gb"),
            "bellek_bos_mb": vps.get("bellek_bos_mb"),
            # Üç soru (2026-09-04): benden ne bekliyor · bugün ne yaptı · iş nasıl.
            # Altyapı tek satıra iner; ayrıntı yukarıdaki alanlarda duruyor.
            "bekleyen": _bekleyen(), "bugun": _bugun(), "is": _is(),
            # Siteler (2026-09-06, Sahip isteği): erişim + gerçek dış ziyaret (7g/24s).
            "site_nabzi": _site_nabzi(),
            # Uzuv bağlantısı canlı (2026-09-06): Mac hızlı hat + telefon WDA + nöbetçi notu.
            "baglanti": _baglanti(mak),
            # Abonelik kotası (2026-09-07, Sahip: dolar değil kota): oturum/haftalık/Fable.
            "kota": _kota(),
            # Ölçüler (2026-10-29): ilk çıktı/tur p50-p95, kayıp cevap, sayaçlar; Ghost'a giden alarmlar.
            "metrik": _metrik(), "alarmlar": _alarmlar(),
            "altyapi": _altyapi(ozet, mak, siteler, nob)}


def _metrik():
    try:
        from ghost import metrik
        o = metrik.ozet(7)
        o["sayac"] = metrik.sayac_toplam(7)
        return o
    except Exception:
        return {"var": False}


def _alarmlar():
    """Nöbetçi alarmlarının sahibe değil Ghost'a giden sınıfı (ghost/alarm.py) — Nabız sarı nokta."""
    try:
        from ghost import alarm
        return alarm.ozet()
    except Exception:
        return {"sari": [], "sayac": {}}


def _bekleyen():
    try:
        from ghost import gun, kokpit
        return {"kararlar": kokpit.bekleyen_kararlar(), "sorular": gun.sorular()}
    except Exception:
        return {"kararlar": [], "sorular": []}


def _bugun():
    try:
        from ghost import gun
        return gun.bugun()
    except Exception:
        return None


def _is():
    try:
        from ghost import isnabzi
        return isnabzi.oku()
    except Exception:
        return {"var": False}


def _kota():
    try:
        from ghost import kota
        return kota.oku()
    except Exception:
        return {"var": False, "limitler": []}


def _site_nabzi():
    try:
        from ghost import trafik
        return trafik.oku()
    except Exception:
        return {"var": False, "siteler": []}


def _altyapi(ozet, mak, siteler, nob):
    """Tek satır + sorun listesi: 'her şey yolunda' ya da neyin bozuk olduğu."""
    sorun = ([m["ad"] + ": " + (m.get("detay") or m["durum"]) for m in mak if m["durum"] in ("kritik", "uyari")]
             + [s["ad"] + " erişilemiyor" for s in siteler if not s.get("ok")]
             + [n["ad"] + " nöbetçisi sustu" for n in nob if not n.get("ok")])
    sayim = "%d makine · %d site · %d nöbetçi" % (len(mak), len(siteler), len(nob))
    return {"durum": ozet, "satir": ("Altyapı yolunda — " + sayim) if not sorun else
            ("%d sorun — %s" % (len(sorun), sorun[0])), "sorunlar": sorun[:6]}


def servis_satirlari():
    """Köprünün /saglik metni için: '• ad: durum' satırları."""
    return ["• %s: %s" % (ad, durum) for ad, durum in servisler()]


if __name__ == "__main__":
    print(json.dumps(nabiz(), ensure_ascii=False, indent=1))
