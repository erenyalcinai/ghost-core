#!/usr/bin/env python3
"""Model sınavı (Faz 4.1.5, 2026-11-08) — aynı görev × birden çok model, nesnel + ikinci göz + körleme puan.

Amaç: departman modelini ÖLÇÜMLE seçmek ("şüphede yukarı"). Görevler dossier/faz4/sinav/gorevler.json (salt-okunur,
yan etkisiz, cevabı doğrulanabilir; her görevde `beklenen` regex listesi). Her koşu departmanın GERÇEK ajan tanımıyla
(--agents/--agent → aynı yönerge + izin listesi) ve ÖZERK kalkanla (GHOST_OZERK=1) koşar; MCP yüklenmez (eşit koşul).

  python3 ops/model_sinavi.py kos <kosum> [--gorevler 2,8,20] [--modeller sonnet,opus,fable] [--zaman 600]
  python3 ops/model_sinavi.py ozet <kosum> [--masa]        # tablo: görev×model puan/denetim/sn/$ + model toplamı
  python3 ops/model_sinavi.py kor <kosum>                  # körleme sayfaları: A/B/C karışık + anahtar.json
  python3 ops/model_sinavi.py puan <kosum> <gorev> <A|B|C> # sahibin seçimi (kör)
  python3 ops/model_sinavi.py liste

Çıktı: dossier/faz4/sinav/<kosum>/<gorev>-<model>.json|md, ozet.md, kor-<gorev>.md, anahtar.json, puanlar.json.
Günlük: journald `ghost-model-sinavi`. Koşular SIRALI (4 GB VPS, paralel claude OOM riski — vps-render-oom dersi).
"""
import json
import os
import random
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ghost import agents, config, modeller, motor  # noqa: E402

KOK = os.path.join(config.BASE, "dossier", "faz4", "sinav")
GOREVLER = os.path.join(KOK, "gorevler.json")
MODELLER = ("sonnet", "opus", "fable")


def _log(msg):
    try:
        from ghost import gunluk
        gunluk.yaz("model-sinavi", msg)
    except Exception:
        pass
    print(msg, flush=True)


def gorevler(secim=None):
    with open(GOREVLER, encoding="utf-8") as f:
        hepsi = json.load(f)
    if secim:
        ids = {int(x) for x in str(secim).split(",") if x.strip()}
        hepsi = [g for g in hepsi if g["id"] in ids]
    return hepsi


def puanla(metin, beklenen):
    """Nesnel puan: beklenen regex'lerin kaçı çıktıda var (0..1) + eksikler."""
    metin = metin or ""
    eksik = [b for b in beklenen if not re.search(b, metin)]
    return (len(beklenen) - len(eksik)) / len(beklenen) if beklenen else None, eksik


def kos_bir(gorev, model, zaman=600, kosucu=None):
    """Tek koşu → kayıt dict. kosucu(cmd, env, zaman) test için enjekte edilir."""
    tam = modeller.tam_model(model)
    cmd = [motor.CLAUDE, "-p", gorev["gorev"], "--output-format", "json", "--model", tam,
           "--strict-mcp-config"] + list(motor.CLAUDE_ARGS) + agents.ajan_args(gorev["departman"], model=tam)
    # model=tam: sınavın modeli yaml varsayılanını (opus) ezer — ajan tanımındaki model CLI --model'den üstün.
    env = dict(motor.ORTAM, GHOST_OZERK="1")
    t0 = time.time()
    if kosucu is None:
        def kosucu(cmd, env, zaman):
            p = subprocess.run(cmd, cwd=config.BASE, env=env, capture_output=True, text=True, timeout=zaman)
            return p.returncode, p.stdout, p.stderr
    try:
        rc, out, err = kosucu(cmd, env, zaman)
        zaman_asimi = False
    except subprocess.TimeoutExpired:
        rc, out, err, zaman_asimi = -1, "", "zaman aşımı", True
    sure = round(time.time() - t0, 1)
    try:
        obj = json.loads((out or "").strip())
    except Exception:
        obj = {}
    sonuc = (obj.get("result") or "").strip()
    puan, eksik = puanla(sonuc, gorev.get("beklenen") or [])
    return {"gorev": gorev["id"], "departman": gorev["departman"], "model": model, "tam_model": tam,
            "sure_sn": sure, "maliyet_usd": obj.get("total_cost_usd"), "tur": obj.get("num_turns"),
            "hata": bool(obj.get("is_error")) or rc != 0 or zaman_asimi, "zaman_asimi": zaman_asimi,
            "puan": puan, "eksik": eksik, "uzunluk": len(sonuc), "sonuc": sonuc,
            "stderr": (err or "")[-400:], "ts": time.strftime("%F %T")}


def denetle(gorev, kayit, runner=None):
    """İkinci göz (verify.review, sonnet, salt-okunur) — karar kayda yazılır."""
    from ghost import verify
    try:
        karar, _not, rec = verify.review(gorev["gorev"], kayit["sonuc"], runner or motor.run_claude,
                                         kind="sinav:%s" % gorev["id"], jid=None, ozerk=True)
        kayit["denetim"] = karar
        kayit["denetim_gerekce"] = (rec or {}).get("gerekce", "")[:200]
    except Exception as e:  # noqa: BLE001
        kayit["denetim"] = "hata"
        kayit["denetim_gerekce"] = repr(e)[:120]
    return kayit


def kos(kosum, secim=None, modeller_=MODELLER, zaman=600, denetim=True, kosucu=None, runner=None):
    d = os.path.join(KOK, kosum)
    os.makedirs(d, exist_ok=True)
    gl = gorevler(secim)
    _log("sınav %s: %d görev × %s" % (kosum, len(gl), ",".join(modeller_)))
    for g in gl:
        for m in modeller_:
            yol = os.path.join(d, "%s-%s.json" % (g["id"], m))
            if os.path.exists(yol):
                _log("  atla (var): görev %s %s" % (g["id"], m))
                continue
            k = kos_bir(g, m, zaman, kosucu)
            if denetim and not k["hata"]:
                k = denetle(g, k, runner)
            with open(yol, "w", encoding="utf-8") as f:
                json.dump(k, f, ensure_ascii=False, indent=1)
            with open(yol[:-5] + ".md", "w", encoding="utf-8") as f:
                f.write("# Görev %s · %s · %s\n\n%s\n\n---\n%s\n" % (g["id"], g["departman"], m, g["gorev"], k["sonuc"]))
            _log("  görev %s %s: puan %s · denetim %s · %ss · $%s%s" % (
                g["id"], m, k["puan"], k.get("denetim", "-"), k["sure_sn"], k["maliyet_usd"], " · HATA" if k["hata"] else ""))
    return ozet(kosum)


def _kayitlar(kosum):
    d = os.path.join(KOK, kosum)
    out = []
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if fn.endswith(".json") and fn[0].isdigit():
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                out.append(json.load(f))
    return out


def _puanlar(kosum):
    yol = os.path.join(KOK, kosum, "puanlar.json")
    try:
        return json.load(open(yol, encoding="utf-8"))
    except Exception:
        return {}


def ozet(kosum, masa=False):
    """ozet.md: görev×model tablo + model toplamı; sahibin kör puanları varsa model başına sayılır."""
    kay = _kayitlar(kosum)
    if not kay:
        return ""
    modeller_ = sorted({k["model"] for k in kay}, key=lambda m: MODELLER.index(m) if m in MODELLER else 9)
    anahtar = _anahtar(kosum, olustur=False)
    puanlar = _puanlar(kosum)
    satir = []
    for gid in sorted({k["gorev"] for k in kay}):
        hucre = []
        for m in modeller_:
            k = next((x for x in kay if x["gorev"] == gid and x["model"] == m), None)
            if not k:
                hucre.append("-"); continue
            p = "%.0f%%" % (100 * k["puan"]) if k.get("puan") is not None else "-"
            hucre.append("%s · %s · %ss · $%.2f%s" % (p, k.get("denetim", "-"), int(k["sure_sn"]), k.get("maliyet_usd") or 0,
                                                     " ⚠" if k["hata"] else ""))
        secim = puanlar.get(str(gid))
        secim_model = (anahtar.get(str(gid)) or {}).get(secim) if secim else None
        dep = next(x["departman"] for x in kay if x["gorev"] == gid)
        satir.append("| %s | %s | %s | %s |" % (gid, dep, " | ".join(hucre), secim_model or "-"))
    toplam = []
    for m in modeller_:
        ks = [k for k in kay if k["model"] == m]
        pl = [k["puan"] for k in ks if k.get("puan") is not None]
        gecti = sum(1 for k in ks if k.get("denetim") == "geçti")
        sure = sorted(k["sure_sn"] for k in ks)
        mal = sum(k.get("maliyet_usd") or 0 for k in ks)
        sahip = sum(1 for gid, h in puanlar.items() if (anahtar.get(gid) or {}).get(h) == m)
        toplam.append("| %s | %d | %.0f%% | %d/%d | %ss | $%.2f | %d | %d |" % (
            m, len(ks), 100 * sum(pl) / len(pl) if pl else 0, gecti, len(ks), int(sure[len(sure) // 2]) if sure else 0, mal,
            sum(1 for k in ks if k["hata"]), sahip))
    m = ("# Model sınavı — %s\n\nHer hücre: nesnel puan (beklenen bulgular) · ikinci göz · süre · maliyet. Sahip sütunu: kör seçim "
         "(`python3 ops/model_sinavi.py puan %s <görev> <A|B|C>`).\n\n| Görev | Dep | %s | Sahip |\n|---|---|%s---|\n%s\n\n"
         "## Model toplamı\n\n| Model | Koşu | Ort. puan | Denetim geçti | p50 sn | Maliyet | Hata | Sahip seçimi |\n|---|---|---|---|---|---|---|---|\n%s\n"
         % (kosum, kosum, " | ".join(modeller_), "---|" * len(modeller_), "\n".join(satir), "\n".join(toplam)))
    yol = os.path.join(KOK, kosum, "ozet.md")
    with open(yol, "w", encoding="utf-8") as f:
        f.write(m)
    if masa:
        try:
            from ghost import masa as _masa
            _masa.yayinla(yol, "Ghost", "Model sınavı %s" % kosum)
        except Exception as e:  # noqa: BLE001
            _log("masa yayın hatası: %r" % e)
    return m


def _anahtar(kosum, olustur=True):
    """Körleme anahtarı: görev → {A: model, B: model, C: model} (karışık, kalıcı)."""
    yol = os.path.join(KOK, kosum, "anahtar.json")
    try:
        return json.load(open(yol, encoding="utf-8"))
    except Exception:
        if not olustur:
            return {}
    kay = _kayitlar(kosum)
    anahtar = {}
    for gid in sorted({k["gorev"] for k in kay}):
        ms = [k["model"] for k in kay if k["gorev"] == gid]
        random.shuffle(ms)
        anahtar[str(gid)] = {harf: m for harf, m in zip("ABC", ms)}
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(anahtar, f, ensure_ascii=False, indent=1)
    return anahtar


def kor(kosum):
    """Görev başına kör sayfa: A/B/C çıktıları, model adı yok."""
    kay = _kayitlar(kosum)
    anahtar = _anahtar(kosum)
    yollar = []
    for gid, harfler in anahtar.items():
        g = next(k for k in kay if str(k["gorev"]) == gid)
        parca = ["# Kör sınav — görev %s (%s)\n\n> %s\n" % (gid, g["departman"], next(
            x["gorev"] for x in gorevler() if str(x["id"]) == gid))]
        for harf, m in sorted(harfler.items()):
            k = next(x for x in kay if str(x["gorev"]) == gid and x["model"] == m)
            parca.append("\n## %s\n\n%s\n" % (harf, k["sonuc"] or "(boş)"))
        parca.append("\nSeçim: `python3 ops/model_sinavi.py puan %s %s <A|B|C>`\n" % (kosum, gid))
        yol = os.path.join(KOK, kosum, "kor-%s.md" % gid)
        with open(yol, "w", encoding="utf-8") as f:
            f.write("".join(parca))
        yollar.append(yol)
    return yollar


def puan(kosum, gid, harf):
    harf = harf.strip().upper()
    if harf not in "ABC" or len(harf) != 1:
        raise ValueError("harf A|B|C")
    yol = os.path.join(KOK, kosum, "puanlar.json")
    p = _puanlar(kosum)
    p[str(int(gid))] = harf
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=1)
    model = (_anahtar(kosum, olustur=False).get(str(int(gid))) or {}).get(harf)
    ozet(kosum)
    return model


def _secenek(argv, ad, varsayilan=None):
    if ad in argv:
        i = argv.index(ad)
        v = argv[i + 1]
        del argv[i:i + 2]
        return v
    return varsayilan


def main(argv):
    argv = list(argv)
    if not argv or argv[0] == "liste":
        for g in gorevler():
            print("%2d %-11s %s" % (g["id"], g["departman"], g["gorev"][:100]))
        return 0
    cmd = argv.pop(0)
    if cmd == "kos":
        secim = _secenek(argv, "--gorevler")
        mods = tuple(_secenek(argv, "--modeller", ",".join(MODELLER)).split(","))
        zaman = int(_secenek(argv, "--zaman", "600"))
        masa = "--masa" in argv
        if masa:
            argv.remove("--masa")
        kosum = argv[0] if argv else time.strftime("%Y-%m-%d")
        kos(kosum, secim, mods, zaman)
        ozet(kosum, masa=masa)
        print("özet: %s" % os.path.join(KOK, kosum, "ozet.md"))
        return 0
    if cmd == "ozet":
        masa = "--masa" in argv
        print(ozet(argv[0], masa=masa) or "(kayıt yok)")
        return 0
    if cmd == "kor":
        for y in kor(argv[0]):
            print(y)
        return 0
    if cmd == "puan":
        print("seçim kaydedildi → model: %s" % puan(argv[0], argv[1], argv[2]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
