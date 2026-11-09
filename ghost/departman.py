"""Departman kipi — koordinatörün derin işi departmana vermesi (Faz 4, 2026-11-08; plan
dossier/faz4/departmanlar-v1-plan-2026-11-08.md; iki kip Sahip kararı: `tek` bugünkü · `departman` gerçek ajan).

  python3 -m ghost.departman ver <ad> "<görev>" [--model fable|opus|sonnet|haiku]
      MODEL (Sahip 2026-11-08 "opus 5.5 iyi; ama işin önem ve zorluğuna göre karar verebilmelisin"): öncelik
        açık --model > önem yükseltmesi (iş sınıfı PERMANENT/SPEND → fable, kodda) > departman yaml varsayılanı
        (opus) > köprü mirası; seçim işarete biner ([DEPARTMAN:<ad>@fable]) → motor yaml'ı ezer. ZORLUK
        yükseltmesi koordinatörün kararı: çok adımlı/çelişkili araştırma, mimari/güvenlik, dışa giden metin,
        önceki opus turu "doğrulanamadı" → `--model fable`. Sohbet içi Agent aracında aynı şey `model=fable`.
      → olay veriyoluna bir İSTEK yazar (bus.istek, type=request, kind=dep:<ad>); köprü isteği sahiplenir
        (_istek_isle): PERMANENT/SPEND sınıfıysa önce sahibe onay kartı, değilse ÖZERK kalkanla iş olur
        (geri alınmaz adım yok → ÖNERİLEN EYLEM kartı; "yarısına kadar getir, kartı bırak"). Metnin başındaki
        [DEPARTMAN:<ad>] işareti → motor.run_claude `--agents/--agent <ad>`: iş departmanın kendi yönergesi,
        `izin` araç listesi ve modeliyle AYRI süreçte koşar; sonuç iş bitince sohbete düşer (bugünkü iş akışı).
        Kip `tek` ise iş yine kuyruğa girer ama bugünkü gibi (persona öneki) koşar. Bildirim yok (Sahip 2026-10-29).
        NOT: bus.is_ekle (type=job) kullanılmaz — köprü job'ları yalnız açılışta geri alır, request'leri her tick çeker.
  python3 -m ghost.departman liste | kip [tek|departman] | durum | karne (7 gün departman tablosu) | butce

KURAL: özerk oturum (GHOST_OZERK=1) `ver` ÇAĞIRAMAZ — açtığı iş özerk kalkanı taşımazdı (kalkan kaçağı);
hedef/vardiya kendi tur motorunu kullanır, geri alınmaz adım için ÖNERİLEN EYLEM yazar. Koordinatör = sahibin sohbeti.
Köprü kodu değişmedi (bus yolu).
"""
import os
import sys

from ghost import agents, governance

KIND_ONEK = "dep:"
YUKSELT_SINIF = (governance.PERMANENT, governance.SPEND)   # önem yükseltmesi: bu sınıflar en güçlü modele
YUKSELT_MODEL = "fable"


def model_sec(key, gorev, acik=None):
    """İşin modeli → (model|None, neden, isarete_yaz). Öncelik: açık seçim > önem yükseltmesi
    (governance.classify PERMANENT/SPEND → fable) > departman yaml varsayılanı > köprü mirası (None).
    Yalnız ilk ikisi işarete yazılır ([DEPARTMAN:x@model]); yaml varsayılanını motor kendisi uygular."""
    if acik:
        return acik, "açık seçim", True
    sinif = governance.classify(gorev)
    if sinif in YUKSELT_SINIF:
        return YUKSELT_MODEL, "iş sınıfı %s → önem yükseltmesi" % sinif, True
    yaml = agents.ajan_model(key)
    if yaml:
        return yaml, "departman varsayılanı", False
    return None, "köprü varsayılanı (miras)", False


def _metrik_is(gun=7, simdi=None):
    """metrik.jsonl'deki iş turları (rol=denetim hariç), son `gun` gün."""
    import json, time
    from ghost import metrik
    simdi = simdi or time.time()
    esik = simdi - gun * 86400
    out = []
    try:
        with open(metrik._yol("metrik.jsonl"), encoding="utf-8") as f:
            for satir in f:
                try:
                    r = json.loads(satir)
                    ts = time.mktime(time.strptime(r["ts"], "%Y-%m-%d %H:%M:%S"))
                except Exception:
                    continue
                if ts >= esik and r.get("tur") == "is" and r.get("rol") != "denetim":
                    out.append(r)
    except OSError:
        pass
    return out


def butce_durum(key, gun=7, simdi=None):
    """(kullanılan, tavan): departmanın son 7 günde koşturduğu iş sayısı ↔ yaml `butce_hafta` (0 = tavan yok).
    Fren değil GÖSTERGE (Sahip 2026-11-06 'limite gerek yok'): `ver` aşımda uyarır, iş yine kuyruğa girer."""
    tavan = int((agents.DEPARTMENTS.get(key) or {}).get("butce_hafta") or 0)
    kullanilan = sum(1 for r in _metrik_is(gun, simdi) if r.get("departman") == key)
    return kullanilan, tavan


def _denetimler(gun=7, simdi=None):
    """verify.jsonl: kind dep:<ad> kayıtları → {ad: {karar: n}}."""
    import json, time
    from ghost import config
    simdi = simdi or time.time()
    esik = simdi - gun * 86400
    out = {}
    try:
        with open(config.VERIFY_LOG, encoding="utf-8") as f:
            for satir in f:
                try:
                    r = json.loads(satir)
                    ts = time.mktime(time.strptime(r.get("ts", ""), "%Y-%m-%d %H:%M:%S"))
                except Exception:
                    continue
                k = r.get("kind") or ""
                if ts < esik or not k.startswith(KIND_ONEK):
                    continue
                d = out.setdefault(k[len(KIND_ONEK):], {})
                d[r.get("karar") or "?"] = d.get(r.get("karar") or "?", 0) + 1
    except OSError:
        pass
    return out


def karne_tablosu(gun=7, simdi=None):
    """Cuma karnesine 'Departmanlar' tablosu (4.1.4): iş · p50 · hata · denetim kararları · bütçe. Veri yoksa ''."""
    from ghost import metrik
    kayit = _metrik_is(gun, simdi)
    if not kayit:
        return ""
    den = _denetimler(gun, simdi)
    satirlar = []
    for key in list(agents.DEPARTMENTS) + ["genel"]:
        r = [x for x in kayit if (x.get("departman") or "genel") == key]
        if not r:
            continue
        ad = agents.DEPARTMENTS[key]["ad"] if key in agents.DEPARTMENTS else "Genel (departmansız)"
        sureler = [x["toplam_sn"] for x in r if x.get("toplam_sn") is not None]
        hata = sum(1 for x in r if x.get("hata") or x.get("zaman_asimi"))
        d = den.get(key, {})
        dmetin = ", ".join("%s %d" % (k, v) for k, v in sorted(d.items())) or "-"
        if key in agents.DEPARTMENTS:
            kul, tavan = butce_durum(key, gun, simdi)
            butce = "%d/%s" % (kul, tavan or "∞")
        else:
            butce = "-"
        satirlar.append("| %s | %d | %s | %d | %s | %s |" % (ad, len(r), metrik._yuzdelik(sureler, 50), hata, dmetin, butce))
    return ("## Departmanlar (%d gün)\n\n| Departman | İş | p50 sn | Hata | Denetim | Bütçe |\n|---|---|---|---|---|---|\n%s\n"
            % (gun, "\n".join(satirlar)))


def ver(key, gorev, model=None, source="departman"):
    """İşi kuyruğa yaz; (is_id, kip) döndürür. ValueError: bilinmeyen/ajan olmayan departman, boş görev, özerk oturum."""
    key = (key or "").strip().lower()
    if key not in agents.DEPARTMENTS:
        raise ValueError("departman yok: %s (var: %s)" % (key, ", ".join(agents.DEPARTMENTS)))
    if not agents.ajan_mi(key):
        raise ValueError("%s ajan değil (yalnız persona) — iş genel kuyruğa /cc ile verilir" % agents.DEPARTMENTS[key]["ad"])
    gorev = (gorev or "").strip()
    if not gorev:
        raise ValueError("görev boş")
    if os.environ.get("GHOST_OZERK") == "1":
        raise ValueError("özerk oturum departman işi açamaz (kalkan) — ÖNERİLEN EYLEM bloğu yaz, koordinatör açar")
    from ghost import bus
    kul, tavan = butce_durum(key)
    if tavan and kul >= tavan:
        print("uyarı: %s bu hafta %d iş koşturdu (bütçe %d) — iş yine kuyruğa giriyor, karnede görünür" % (
            agents.DEPARTMENTS[key]["ad"], kul, tavan), file=sys.stderr)
    secim, _neden, isarete = model_sec(key, gorev, model)
    isaret = (agents._ISARET_MODEL % (key, secim)) if isarete else (agents._ISARET % key)
    prompt = "%s\n%s" % (isaret, gorev)
    # classify = yalnız görev metni (işaret/kalkan sınıflandırmayı kirletmesin — uzun_is dersi 2026-10-30)
    is_id = bus.istek(prompt, KIND_ONEK + key, source, classify=gorev, model=(secim if isarete else None))
    return is_id, agents.mod_oku()


def _secenek(argv, ad):
    if ad in argv:
        i = argv.index(ad)
        if i + 1 < len(argv):
            v = argv[i + 1]
            del argv[i:i + 2]
            return v
    return None


def _main(argv):
    cmd = argv[0] if argv else "liste"
    if cmd == "ver":
        argv = list(argv[1:])
        model = _secenek(argv, "--model")
        if len(argv) < 2:
            print("kullanım: python3 -m ghost.departman ver <ad> \"<görev>\" [--model x]")
            return 2
        try:
            is_id, kip = ver(argv[0], " ".join(argv[1:]), model=model)
        except ValueError as e:
            print("hata: %s" % e)
            return 1
        key = argv[0].strip().lower()
        d = agents.DEPARTMENTS[key]
        secim, neden, _i = model_sec(key, " ".join(argv[1:]), model)
        print("istek #%s → %s (kip %s%s) — köprü işe çevirir, sonuç iş bitince sohbete düşer; `python3 -m ghost.bus oku %s`" % (
            is_id, d["ad"], kip, "" if kip == "departman" else ": bugünkü gibi koşar", is_id))
        print("model: %s (%s)" % (secim or "miras", neden))
        return 0
    if cmd == "kip":
        return agents._main(["mod"] + list(argv[1:]))
    if cmd == "karne":
        print(karne_tablosu() or "(7 günde departman işi yok)")
        return 0
    if cmd == "butce":
        for k in agents.ajanlar():
            kul, tavan = butce_durum(k)
            print("%s: %d/%s (7 gün)" % (agents.DEPARTMENTS[k]["ad"], kul, tavan or "∞"))
        return 0
    if cmd in ("liste", "durum"):
        return agents._main(["liste"])
    print(__doc__.strip().splitlines()[0])
    print("kullanım: python3 -m ghost.departman ver <ad> \"<görev>\" [--model x] | liste | kip [tek|departman] | karne | butce")
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
