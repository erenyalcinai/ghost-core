"""Gece vardiyası / çalışma penceresi (Faz 2.1, 2026-10-30 — bütünsel denetim 01 B2, 04 §9).

NEDEN: Ghost'un üretim saati sahibin uyanık saatinden kopsun — boş kota penceresi gece işe dönüşsün. Hedef listesi
sahibin (K13): hedefsiz gece = uyku. Sahip hedefi İSTEDİĞİ ZAMAN, istediği biçimde verir (sohbette "şunu araştır /
şunu çöz" → goals'a kayıt); pencere saati, gecelik tavan ve kota eşiği de ayardır — iş de zaman da değişir, kod değişmez.

TASARIM: ayar durum tablosunda `vardiya` {acik, baslangic "23:00", bitis "07:00", tavan 3, kota_tavan 60, aralik_dk 25}.
`tick` (selfcheck, 15 dk): pencere içindeyse, FREEZE yoksa, kota penceresi eşiğin altındaysa, bu pencerede tavan
dolmadıysa ve bir önceki vardiya işi hâlâ koşmuyorsa → sıradaki AKTİF hedefi (öncelik ↓, en uzun süredir dokunulmamış ↑)
`goals._queue_work(vardiya=True)` ile kuyruklar (kind `vardiya:<gid>`; 6 saat aralığı ve haftalık bütçe gece için geçerli
değil, kill ölçütü/puan/durum geçerli). Pencere bitince (ilk tick) SABAH RAPORU: koşulan her hedef için iş kaydı
(ops/jobs kind eşlemesi) + `dossier/hedefler/<id>-son.md`'nin ilk satırları → "✅ bitti / ⚠️ yarım-hata / 🔍 denetimde
/ ⏳ sürüyor", açık onay kartı sayısı; `gun_karti.parca_kaydet("vardiya", …)` ile Tek Gün kartına girer (ayrı mesaj
YOK) + dossier/vardiya/<pencere>.md → Masa Ghost. Hedef koşulmadıysa rapor satırı yok (sessizlik).
CLI: python3 -m ghost.vardiya durum | ayar [--baslangic HH:MM] [--bitis HH:MM] [--tavan N] [--kota N] [--aralik DK] |
ac | kapat | tick | rapor [--kuru]
"""
import datetime
import glob
import json
import os
import re
import sys
import time

from ghost import config, durum, goals, governance

DURUM_AD = "vardiya"
KIND = "vardiya:%d"
VARSAYILAN = {"acik": True, "baslangic": "23:00", "bitis": "07:00", "tavan": 3, "kota_tavan": 60, "aralik_dk": 25}
AKTIF_IS = ("kuyrukta", "çalışıyor", "denetimde")
_SAAT = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

# kancalar (test)
_kuyruk = None       # (goal) -> None
_rapor_dizini = lambda: os.path.join(config.BASE, "dossier", "vardiya")   # test: geçici dizin
_kota_yuzde = None   # () -> int|None
_yayinla = None      # (yol) -> masa id
_parca = None        # (metin) -> None


class VardiyaHata(Exception):
    pass


# ---------- ayar ----------
def ayar_oku():
    st = durum.oku(DURUM_AD) or {}
    a = dict(VARSAYILAN)
    a.update(st.get("ayar") or {})
    return a


def ayar_yaz(**kw):
    """Geçerli alanları doğrulayıp yazar; döner: yeni ayar."""
    a = ayar_oku()
    for k, v in kw.items():
        if v is None:
            continue
        if k in ("baslangic", "bitis"):
            if not _SAAT.match(str(v)):
                raise VardiyaHata("%s HH:MM olmalı (verilen %r)" % (k, v))
            h, m = str(v).split(":")
            a[k] = "%02d:%s" % (int(h), m)
        elif k == "tavan":
            a[k] = max(1, min(8, int(v)))
        elif k == "kota_tavan":
            a[k] = max(10, min(95, int(v)))
        elif k == "aralik_dk":
            a[k] = max(5, min(240, int(v)))
        elif k == "acik":
            a[k] = bool(v)
        else:
            raise VardiyaHata("bilinmeyen ayar: %s" % k)
    if a["baslangic"] == a["bitis"]:
        raise VardiyaHata("başlangıç ve bitiş aynı olamaz")
    durum.guncelle(DURUM_AD, lambda d: dict(d or {}, ayar=a), {})
    governance.audit("vardiya", "ayar", governance.REVERSIBLE, json.dumps(a, ensure_ascii=False))
    return a


# ---------- pencere ----------
def _dk(hm):
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def pencere(now=None, ayar=None):
    """(içinde_mi, pencere_kimliği). Kimlik = pencerenin BAŞLADIĞI gün (gece yarısını aşan pencerede dün)."""
    now = now or time.time()
    a = ayar or ayar_oku()
    t = datetime.datetime.fromtimestamp(now)
    dk = t.hour * 60 + t.minute
    bas, bit = _dk(a["baslangic"]), _dk(a["bitis"])
    gun = t.date()
    if bas < bit:                                  # gündüz penceresi
        return (bas <= dk < bit), gun.isoformat()
    if dk >= bas:                                  # gece yarısından önce
        return True, gun.isoformat()
    if dk < bit:                                   # gece yarısından sonra
        return True, (gun - datetime.timedelta(days=1)).isoformat()
    return False, gun.isoformat()


# ---------- yardımcılar ----------
def _isler():
    """ops/jobs/*.json → {kind: kayıt} (vardiya:* olanlar, en yeni jid)."""
    out = {}
    for yol in glob.glob(os.path.join(config.OPS, "jobs", "*.json")):
        try:
            jid = int(os.path.splitext(os.path.basename(yol))[0])
            with open(yol, encoding="utf-8") as f:
                j = json.load(f)
        except (ValueError, OSError, json.JSONDecodeError):
            continue
        k = j.get("kind") or ""
        if k.startswith("vardiya:") and (k not in out or out[k][0] < jid):
            out[k] = (jid, j)
    return out


def _kota():
    if _kota_yuzde is not None:
        return _kota_yuzde()
    try:
        from ghost import kota
        d = kota.pencere_durumu()
        return d.get("yuzde") if d.get("bilinir") else None
    except Exception:
        return None


def adaylar():
    """Aktif, kill'i dolmamış hedefler: öncelik yüksek → en uzun süredir dokunulmamış."""
    out = []
    for g in goals.list_goals(all_states=True):
        if g.get("status") != "aktif":
            continue
        bitti, _ = goals.kill_durumu(g)
        if bitti:
            continue
        out.append(g)
    out.sort(key=lambda g: (-int(g.get("oncelik") or 0), float(g.get("last_worked") or 0)))
    return out


def _kuyrukla(goal, now):
    c = goals._conn()
    try:
        c.execute("UPDATE goals SET last_worked=?, work_count=work_count+1 WHERE id=?", (now, goal["id"]))
        c.commit()
    finally:
        c.close()
    if _kuyruk is not None:
        _kuyruk(goal)
    else:
        goals._queue_work(goal, kind=KIND % goal["id"], announce=None, vardiya=True)


# ---------- tick ----------
def tick(now=None):
    """Selfcheck bekçisi. Yapılanlar listesi (boş = sessiz)."""
    now = now or time.time()
    a = ayar_oku()
    st = durum.oku(DURUM_AD) or {}
    icinde, pid = pencere(now, a)
    yapilan = []
    if not icinde:
        if st.get("pencere") and st.get("pencere") != st.get("rapor_gonderildi"):
            m = rapor(st, now)
            durum.guncelle(DURUM_AD, lambda d: dict(d or {}, rapor_gonderildi=st["pencere"]), {})
            yapilan.append("rapor %s (%d satır)" % (st["pencere"], len(m.splitlines()) if m else 0))
        return yapilan
    if not a.get("acik", True) or governance.is_frozen():
        return yapilan
    if st.get("pencere") != pid:
        st = dict(st, pencere=pid, kosulan=[])
        durum.guncelle(DURUM_AD, lambda d: dict(d or {}, pencere=pid, kosulan=[]), {})
    kosulan = list(st.get("kosulan") or [])
    if len(kosulan) >= int(a["tavan"]):
        return yapilan
    if kosulan and now - float(kosulan[-1]["ts"]) < int(a["aralik_dk"]) * 60:
        return yapilan
    isler = _isler()
    for k in kosulan:
        kayit = isler.get(KIND % k["gid"])
        if kayit and (kayit[1].get("status") in AKTIF_IS):
            return yapilan                         # önceki vardiya işi sürüyor → sıra bekler
    yuzde = _kota()
    if yuzde is not None and yuzde >= int(a["kota_tavan"]):
        return yapilan
    koşulmuş = {k["gid"] for k in kosulan}
    aday = next((g for g in adaylar() if g["id"] not in koşulmuş), None)
    if aday is None:
        return yapilan                             # hedefsiz gece = uyku
    _kuyrukla(aday, now)
    kosulan.append({"gid": aday["id"], "baslik": aday["title"][:80], "ts": now})
    durum.guncelle(DURUM_AD, lambda d: dict(d or {}, pencere=pid, kosulan=kosulan), {})
    governance.audit("vardiya", "kuyruk", governance.REVERSIBLE, "#%d %s (pencere %s, %d/%d)" % (
        aday["id"], aday["title"][:60], pid, len(kosulan), int(a["tavan"])))
    yapilan.append("#%d kuyruklandı (%d/%d)" % (aday["id"], len(kosulan), int(a["tavan"])))
    return yapilan


# ---------- sabah raporu ----------
def _son_rapor(gid, sonra_ts):
    yol = os.path.join(goals._rapor_dizini(), "%d-son.md" % gid)
    try:
        if os.path.getmtime(yol) < sonra_ts:
            return ""
        with open(yol, encoding="utf-8", errors="replace") as f:
            satirlar = [x.strip() for x in f.read().splitlines() if x.strip() and not x.startswith("#")]
        return " ".join(satirlar[:3])[:220]
    except OSError:
        return ""


def _acik_onay_sayisi():
    try:
        from ghost import sahne
        ids = []
        for b in sahne.oku().get("bilesenler") or []:
            sahne._eylem_idleri(b, ids)
        return sum(1 for i in dict.fromkeys(ids) if str(i).startswith("onay-"))
    except Exception:
        return 0


def rapor(st=None, now=None, kuru=False):
    """Pencere kapanınca: koşulan hedeflerin hükmü → Tek Gün kartı parçası + dossier/vardiya/<pencere>.md (Masa Ghost).
    Koşulan yoksa boş döner (sessiz)."""
    st = st if st is not None else (durum.oku(DURUM_AD) or {})
    now = now or time.time()
    kosulan = st.get("kosulan") or []
    if not kosulan:
        return ""
    isler = _isler()
    sat = []
    for k in kosulan:
        kayit = isler.get(KIND % k["gid"])
        stt = kayit[1].get("status") if kayit else None
        jid = kayit[0] if kayit else None
        if stt == "bitti":
            simge, hukum = "✅", "bitti"
        elif stt == "denetimde takıldı":
            simge, hukum = "🔍", "denetimde takıldı"
        elif stt in ("hata", "iptal"):
            simge, hukum = "⚠️", "yarım (%s)" % stt
        elif stt in AKTIF_IS:
            simge, hukum = "⏳", "sürüyor"
        else:
            simge, hukum = "💤", "işe dönüşmedi (kota/kuyruk)"
        ozet = _son_rapor(k["gid"], float(k["ts"]))
        sat.append("%s #%d %s — %s%s%s" % (simge, k["gid"], k["baslik"][:60], hukum,
                                           (" · iş #%d" % jid) if jid else "", (": " + ozet) if ozet else ""))
    n = _acik_onay_sayisi()
    if n:
        sat.append("❓ onay bekleyen kart: %d" % n)
    metin = "\n".join(sat)
    if kuru:
        return metin
    try:
        if _parca is not None:
            _parca(metin)
        else:
            from ghost import gun_karti
            gun_karti.parca_kaydet("vardiya", metin, now)
    except Exception:
        pass
    try:
        d = _rapor_dizini()
        os.makedirs(d, exist_ok=True)
        yol = os.path.join(d, "%s.md" % st.get("pencere", "pencere"))
        with open(yol, "w", encoding="utf-8") as f:
            f.write("# 🌙 Gece vardiyası · %s\n\n%s\n" % (st.get("pencere", ""), "\n".join("- " + x for x in sat)))
        if _yayinla is not None:
            _yayinla(yol)
        else:
            from ghost import masa
            masa.yayinla(yol, "Ghost")
    except Exception:
        pass
    governance.audit("vardiya", "rapor", governance.READ, "%s: %d hedef" % (st.get("pencere"), len(kosulan)))
    return metin


# ---------- CLI ----------
def durum_metni(now=None):
    now = now or time.time()
    a = ayar_oku()
    st = durum.oku(DURUM_AD) or {}
    icinde, pid = pencere(now, a)
    ad = adaylar()
    return ("vardiya %s · pencere %s–%s · tavan %d/gece · kota eşiği %%%d · aralık %d dk\n"
            "şu an %s (pencere %s) · bu pencerede koşulan %d · aday hedef %d%s\nson rapor: %s" % (
                "AÇIK" if a.get("acik", True) else "KAPALI", a["baslangic"], a["bitis"], a["tavan"], a["kota_tavan"],
                a["aralik_dk"], "içeride" if icinde else "dışarıda", pid,
                len(st.get("kosulan") or []) if st.get("pencere") == pid else 0, len(ad),
                (": " + ", ".join("#%d %s" % (g["id"], g["title"][:30]) for g in ad[:4])) if ad else " (hedefsiz gece = uyku)",
                st.get("rapor_gonderildi") or "-"))


def _secenek(argv, ad):
    if ad in argv:
        i = argv.index(ad)
        if i + 1 < len(argv):
            v = argv[i + 1]
            del argv[i:i + 2]
            return v
    return None


def _main(argv):
    argv = list(argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip().splitlines()[-2].strip() + " " + __doc__.strip().splitlines()[-1].strip())
        return 0
    cmd, args = argv[0], argv[1:]
    try:
        if cmd == "durum":
            print(durum_metni())
        elif cmd == "ayar":
            a = ayar_yaz(baslangic=_secenek(args, "--baslangic"), bitis=_secenek(args, "--bitis"),
                         tavan=_secenek(args, "--tavan"), kota_tavan=_secenek(args, "--kota"),
                         aralik_dk=_secenek(args, "--aralik"))
            print("ayar: " + json.dumps(a, ensure_ascii=False))
        elif cmd == "ac":
            ayar_yaz(acik=True)
            print("vardiya AÇIK")
        elif cmd == "kapat":
            ayar_yaz(acik=False)
            print("vardiya KAPALI")
        elif cmd == "tick":
            print("\n".join(tick()) or "(değişiklik yok)")
        elif cmd == "rapor":
            print(rapor(kuru="--kuru" in args) or "(koşulan hedef yok)")
        else:
            raise VardiyaHata("bilinmeyen komut: %s" % cmd)
    except (VardiyaHata, ValueError) as e:
        print("hata: %s" % e)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
