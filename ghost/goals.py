"""Duran hedefler v2 — plan, tur hafızası, kill ölçütü, puan, bütçe (VISION.md: Ghost hiç durmaz).

Sahip bir hedef tanımlar ("AppA'e 100 kullanıcı", "X konusunu derinlemesine araştır"); Ghost boş
dururken PERİYODİK olarak hedefin üzerinde bir birim ilerleme yapar ve raporlar. Tik VPS köprü
döngüsünden gelir. FREEZE durdurur.

v2 (Faz 1.5, 2026-10-29 — bütünsel denetim 04 Ö3/Ö11): v1 her hedefe 6 saatte bir körlemesine
dokunuyor, önceki turun ne yaptığını bilmiyor, hedefin ne zaman biteceğini kod okumuyordu.
  * `plan`      — JSON alt görevler [{"ad","durum":"acik|tamam"}]; istem planı gösterir, iş adımı kapatır.
  * `son_rapor` — önceki turun raporu istem girdisine girer (iş `dossier/hedefler/<id>-son.md` yazar;
                  yoksa hafızadaki "iş #… (hedef)" kaydı); tekrar yok.
  * `kill`      — kod tarafından okunan bitiş ölçütü: "2026-11-16" (tarih geçince kapanır), "10 tur"
                  (tur sayısı dolunca kapanır), başka metin (model okur, kod dokunmaz).
  * `puan`      — Sahip 👍/👎 (+1/−1); toplam ≤ −2 ya da "ikinci plan" → duraklatılır.
  * `oncelik`   — yüksek önce; `butce_hafta` — haftada en çok N tur (6 saat sabitinin yerine); kota
                  penceresi doluysa (kota.ozerk_uygun) tur ertelenir.
REFLEKS (Ö11): Sahip sohbette "şunu halledelim / takip et / hedefimiz…" deyince Ghost `goals.add` ile
kaydeder ve ilk planı aynı turda gösterir — sahibin /hedef yazması gerekmez. "İkinci plan(da)" deyince
`ikinci_plan`. CLI: python3 -m ghost.goals liste|goster <id>|ekle "<başlık>" ["detay"] [--kill …] [--oncelik N]
[--butce N]|plan <id> "a|b|c"|adim <id> <n> tamam|acik|rapor <id> @dosya|puan <id> +|-|ikinci-plan <id>|
durdur|devam|tamam <id>
"""
import datetime
import json
import os
import re
import sqlite3
import sys
import time

from ghost import config, governance

_SCHEMA = """
CREATE TABLE IF NOT EXISTS goals(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  detail TEXT,
  status TEXT NOT NULL DEFAULT 'aktif',   -- aktif|duraklatıldı|tamam
  last_worked REAL NOT NULL DEFAULT 0,
  work_count INTEGER NOT NULL DEFAULT 0,
  created TEXT NOT NULL
);
"""
_V2_SUTUNLAR = (("plan", "TEXT NOT NULL DEFAULT '[]'"), ("son_rapor", "TEXT NOT NULL DEFAULT ''"),
                ("kill", "TEXT NOT NULL DEFAULT ''"), ("puan", "INTEGER NOT NULL DEFAULT 0"),
                ("oncelik", "INTEGER NOT NULL DEFAULT 1"), ("butce_hafta", "INTEGER NOT NULL DEFAULT 3"),
                ("hafta", "TEXT NOT NULL DEFAULT ''"), ("hafta_tur", "INTEGER NOT NULL DEFAULT 0"),
                ("notu", "TEXT NOT NULL DEFAULT ''"))
WORK_GAP_H = 6      # aynı hedefe en erken bu kadar saatte bir dokunulur
_TICK_GAP = 300     # tik en fazla 5 dk'da bir koşar
_last_tick = 0.0
DURAKLATILDI = "duraklatıldı"
PUAN_DURAKLAT = -2
_KILL_TARIH = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_KILL_TUR = re.compile(r"^(\d+)\s*tur$", re.I)


def _rapor_dizini():
    return os.path.join(config.BASE, "dossier", "hedefler")


def _conn():
    c = sqlite3.connect(config.DB_PATH, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_SCHEMA)
    for ad, tanim in _V2_SUTUNLAR:
        try:
            c.execute("ALTER TABLE goals ADD COLUMN %s %s" % (ad, tanim))
        except sqlite3.OperationalError:
            pass
    c.execute("UPDATE goals SET status=? WHERE status='duraklatildi'", (DURAKLATILDI,))  # eski yazım
    c.commit()
    return c


# ---------- kayıt ----------

def add(title, detail="", plan=None, kill="", oncelik=1, butce_hafta=3):
    c = _conn()
    try:
        cur = c.execute("INSERT INTO goals(title, detail, created, plan, kill, oncelik, butce_hafta) VALUES(?,?,?,?,?,?,?)",
                        (title, detail, time.strftime("%F %T"), _plan_json(plan), kill or "", int(oncelik), int(butce_hafta)))
        c.commit()
        governance.audit("goals", "add", governance.REVERSIBLE, title[:150])
        return cur.lastrowid
    finally:
        c.close()


def _plan_json(plan):
    if not plan:
        return "[]"
    out = []
    for p in plan:
        if isinstance(p, dict):
            out.append({"ad": str(p.get("ad", "")).strip(), "durum": p.get("durum", "acik")})
        elif str(p).strip():
            out.append({"ad": str(p).strip(), "durum": "acik"})
    return json.dumps(out, ensure_ascii=False)


def set_status(gid, status, notu=None):
    if status == "duraklatildi":
        status = DURAKLATILDI
    c = _conn()
    try:
        if notu is None:
            n = c.execute("UPDATE goals SET status=? WHERE id=?", (status, int(gid))).rowcount
        else:
            n = c.execute("UPDATE goals SET status=?, notu=? WHERE id=?", (status, notu, int(gid))).rowcount
        c.commit()
        return n > 0
    finally:
        c.close()


def plan_koy(gid, adimlar):
    c = _conn()
    try:
        n = c.execute("UPDATE goals SET plan=? WHERE id=?", (_plan_json(adimlar), int(gid))).rowcount
        c.commit()
        return n > 0
    finally:
        c.close()


def adim(gid, n, durum="tamam"):
    """Plan adımını (1'den başlar) kapat/aç."""
    g = goster(gid)
    if not g or not (1 <= int(n) <= len(g["plan"])):
        return False
    g["plan"][int(n) - 1]["durum"] = "tamam" if durum == "tamam" else "acik"
    return plan_koy(gid, g["plan"])


def rapor_kaydet(gid, metin):
    c = _conn()
    try:
        n = c.execute("UPDATE goals SET son_rapor=? WHERE id=?", ((metin or "")[:4000], int(gid))).rowcount
        c.commit()
        return n > 0
    finally:
        c.close()


def puan(gid, delta):
    """👍 +1 / 👎 −1; toplam PUAN_DURAKLAT'a inince hedef duraklatılır. (yeni puan, duraklatıldı mı)."""
    c = _conn()
    try:
        c.execute("UPDATE goals SET puan=puan+? WHERE id=?", (int(delta), int(gid)))
        r = c.execute("SELECT puan, status FROM goals WHERE id=?", (int(gid),)).fetchone()
        if not r:
            return None, False
        durduruldu = False
        if r[0] <= PUAN_DURAKLAT and r[1] == "aktif":
            c.execute("UPDATE goals SET status=?, notu=? WHERE id=?", (DURAKLATILDI, "puan %d → duraklatıldı" % r[0], int(gid)))
            durduruldu = True
        c.commit()
        return r[0], durduruldu
    finally:
        c.close()


def ikinci_plan(gid):
    """Sahip "ikinci plan(da)" dedi → duraklat; "devam" deyince set_status(aktif)."""
    return set_status(gid, DURAKLATILDI, "Sahip: ikinci plan")


def _satir(r):
    d = dict(zip(("id", "title", "detail", "status", "work_count", "last_worked", "plan", "son_rapor", "kill",
                  "puan", "oncelik", "butce_hafta", "hafta", "hafta_tur", "notu", "created"), r))
    try:
        d["plan"] = json.loads(d["plan"] or "[]")
    except ValueError:
        d["plan"] = []
    return d


_SEC = ("SELECT id,title,detail,status,work_count,last_worked,plan,son_rapor,kill,puan,oncelik,butce_hafta,"
        "hafta,hafta_tur,notu,created FROM goals")


def goster(gid):
    c = _conn()
    try:
        r = c.execute(_SEC + " WHERE id=?", (int(gid),)).fetchone()
        return _satir(r) if r else None
    finally:
        c.close()


def list_goals(all_states=False):
    c = _conn()
    try:
        q = _SEC
        if not all_states:
            q += " WHERE status='aktif'"
        rows = c.execute(q + " ORDER BY oncelik DESC, id").fetchall()
    finally:
        c.close()
    return [_satir(r) for r in rows]


# ---------- kill / bütçe / rapor ----------

def kill_durumu(goal, now=None):
    """(bitti_mi, neden). Yalnız kodun okuyabildiği biçimler; serbest metin → (False, '')."""
    k = (goal.get("kill") or "").strip()
    if not k:
        return False, ""
    if _KILL_TARIH.match(k):
        bugun = datetime.date.fromtimestamp(now or time.time())
        if bugun > datetime.date.fromisoformat(k):
            return True, "kill tarihi %s geçti" % k
        return False, ""
    m = _KILL_TUR.match(k)
    if m and int(goal.get("work_count") or 0) >= int(m.group(1)):
        return True, "kill: %s dolu" % k
    return False, ""


def _hafta(now):
    y, h, _ = datetime.date.fromtimestamp(now).isocalendar()
    return "%d-W%02d" % (y, h)


def son_rapor_oku(goal):
    """Önceki turun raporu: dossier/hedefler/<id>-son.md (iş yazar) > son_rapor sütunu > hafıza 'iş #… (hedef)'."""
    yol = os.path.join(_rapor_dizini(), "%d-son.md" % goal["id"])
    try:
        if os.path.getmtime(yol) >= float(goal.get("last_worked") or 0) - 1:
            with open(yol, encoding="utf-8") as f:
                return f.read()[:4000]
    except OSError:
        pass
    if goal.get("son_rapor"):
        return goal["son_rapor"]
    try:
        from ghost import memory
        rows = memory.recall("hedef #%d %s" % (goal["id"], goal["title"]), k=1, haric=())
        for _ts, kind, text in rows:
            if kind.startswith("iş") and "(hedef)" in text and "#%d" % goal["id"] in text:
                return text[:1500]
    except Exception:
        pass
    return ""


def _queue_work(goal):
    from ghost import bus
    plan = goal.get("plan") or []
    plan_metni = "\n".join("  %d. [%s] %s" % (i + 1, "x" if p.get("durum") == "tamam" else " ", p.get("ad", ""))
                           for i, p in enumerate(plan))
    if not plan_metni:
        plan_metni = ('  (plan yok — ilk turda 3-6 adımlık plan kur: python3 -m ghost.goals plan %d "adım1|adım2|…")'
                      % goal["id"])
    son = son_rapor_oku(goal)
    prompt = (
        "DURAN HEDEF ÇALIŞMASI — otomatik (sürekli biliş). Hedef #%d: %s\n%s"
        "Kill ölçütü: %s · Sahip puanı: %+d · bu %d. tur.\n"
        "PLAN:\n%s\n"
        "ÖNCEKİ TUR RAPORU:\n%s\n"
        "Bu turda plandaki AÇIK bir adımda BİR BİRİM somut ilerleme yap (araştır/üret/analiz et); önceki turu "
        "tekrarlama. Adım bittiyse `python3 -m ghost.goals adim %d <n> tamam`. Raporunu `dossier/hedefler/%d-son.md` "
        "dosyasına YAZ (sonraki tur girdisi) ve çıktıda ver: (1) ne yaptın (2) somut çıktı (3) sonraki adım. "
        "Kısa ve keskin; telefonda okunacak. Yapamadığın geri alınmaz adım varsa ÖNERİLEN EYLEM bloğu yaz.\n"
        "SINIR: geri alınamaz/harcamalı hiçbir şey yapma — yalnız öner."
        % (goal["id"], goal["title"], ("Detay: %s\n" % goal["detail"]) if goal.get("detail") else "",
           goal.get("kill") or "yok", int(goal.get("puan") or 0), int(goal.get("work_count") or 0) + 1,
           plan_metni, (son or "(ilk tur)")[:2500], goal["id"], goal["id"]))
    bus.istek(prompt, "hedef", "goal", announce="🎯 Hedef çalışılıyor: %s" % goal["title"][:80])


def tick(now=None, kota_uygun=None):
    """Sırası gelen TEK hedefi çalışmaya al (varsa). Ateşlenen id ya da None.
    Sıra: öncelik yüksek → en uzun süredir dokunulmamış; kill dolanlar kapanır; haftalık bütçe ve
    6 saat aralığı korunur; kota penceresi doluysa (kota.ozerk_uygun) tur ertelenir."""
    global _last_tick
    now = now or time.time()
    if now - _last_tick < _TICK_GAP:
        return None
    _last_tick = now
    if governance.is_frozen():
        return None
    if kota_uygun is None:
        try:
            from ghost import kota
            kota_uygun = kota.ozerk_uygun(now)[0]
        except Exception:
            kota_uygun = True
    if not kota_uygun:
        return None
    hafta = _hafta(now)
    c = _conn()
    try:
        c.execute("UPDATE goals SET hafta=?, hafta_tur=0 WHERE hafta!=?", (hafta, hafta))
        rows = c.execute(_SEC + " WHERE status='aktif' ORDER BY oncelik DESC, last_worked").fetchall()
        secilen = None
        for r in rows:
            g = _satir(r)
            bitti, neden = kill_durumu(g, now)
            if bitti:
                c.execute("UPDATE goals SET status='tamam', notu=? WHERE id=?", (neden, g["id"]))
                governance.audit("goals", "kill", governance.REVERSIBLE, "#%d %s" % (g["id"], neden))
                continue
            if g["last_worked"] >= now - WORK_GAP_H * 3600 or g["hafta_tur"] >= g["butce_hafta"]:
                continue
            secilen = g
            break
        c.commit()
        if not secilen:
            return None
        gid, prev_worked = secilen["id"], secilen["last_worked"]
        c.execute("UPDATE goals SET last_worked=?, work_count=work_count+1, hafta_tur=hafta_tur+1 WHERE id=?", (now, gid))
        c.commit()
    finally:
        c.close()
    try:
        _queue_work(secilen)
    except Exception as e:
        c = _conn()   # kuyruklanamadı → izi geri al; sonraki tick yeniden dener
        try:
            c.execute("UPDATE goals SET last_worked=?, work_count=work_count-1, hafta_tur=hafta_tur-1 WHERE id=?",
                      (prev_worked, gid))
            c.commit()
        finally:
            c.close()
        governance.audit("goals", "work.lost", governance.READ, "#%d kuyruklanamadı: %r" % (gid, e))
        return None
    governance.audit("goals", "work", governance.REVERSIBLE, "#%d %s" % (gid, secilen["title"][:100]))
    return gid


# ---------- CLI ----------

def _goster_metin(g):
    plan = "\n".join("  %d. [%s] %s" % (i + 1, "x" if p.get("durum") == "tamam" else " ", p.get("ad", ""))
                     for i, p in enumerate(g["plan"])) or "  (plan yok)"
    return ("#%d [%s] %s\n%s\nöncelik %d · bütçe %d/hafta (bu hafta %d) · %d tur · puan %+d · kill: %s%s\nPLAN:\n%s\nSON RAPOR:\n%s"
            % (g["id"], g["status"], g["title"], ("detay: " + g["detail"]) if g.get("detail") else "", g["oncelik"],
               g["butce_hafta"], g["hafta_tur"], g["work_count"], g["puan"], g["kill"] or "yok",
               ("\nnot: " + g["notu"]) if g.get("notu") else "", plan, (g.get("son_rapor") or "(yok)")[:600]))


def _secenek(argv, ad, varsayilan=None):
    if ad in argv:
        i = argv.index(ad)
        v = argv[i + 1]
        del argv[i:i + 2]
        return v
    return varsayilan


def _main(argv):
    argv = list(argv)
    if not argv or argv[0] == "liste":
        for g in list_goals(all_states=True):
            print("#%d [%s] %s · %d tur · puan %+d · öncelik %d · kill %s"
                  % (g["id"], g["status"], g["title"][:70], g["work_count"], g["puan"], g["oncelik"], g["kill"] or "-"))
        return 0
    if argv[0] == "goster":
        g = goster(argv[1])
        print(_goster_metin(g) if g else "yok")
        return 0
    if argv[0] == "ekle":
        kill = _secenek(argv, "--kill", "")
        oncelik = int(_secenek(argv, "--oncelik", 1))
        butce = int(_secenek(argv, "--butce", 3))
        if len(argv) < 2:
            print('kullanım: ekle "<başlık>" ["detay"] [--kill 2026-11-16|"10 tur"] [--oncelik N] [--butce N]')
            return 2
        print("hedef #%d" % add(argv[1], argv[2] if len(argv) > 2 else "", kill=kill, oncelik=oncelik, butce_hafta=butce))
        return 0
    if argv[0] == "plan":
        print("tamam" if plan_koy(argv[1], [a.strip() for a in argv[2].split("|")]) else "yok")
        return 0
    if argv[0] == "adim":
        print("tamam" if adim(argv[1], argv[2], argv[3] if len(argv) > 3 else "tamam") else "yok")
        return 0
    if argv[0] == "rapor":
        metin = argv[2]
        if metin.startswith("@"):
            with open(metin[1:], encoding="utf-8") as f:
                metin = f.read()
        print("tamam" if rapor_kaydet(argv[1], metin) else "yok")
        return 0
    if argv[0] == "puan":
        p, d = puan(argv[1], 1 if argv[2].startswith("+") else -1)
        print("puan %+d%s" % (p, " → duraklatıldı" if d else "") if p is not None else "yok")
        return 0
    if argv[0] == "ikinci-plan":
        print("duraklatıldı" if ikinci_plan(argv[1]) else "yok")
        return 0
    if argv[0] in ("durdur", "devam", "tamam"):
        durum = {"durdur": DURAKLATILDI, "devam": "aktif", "tamam": "tamam"}[argv[0]]
        print("tamam" if set_status(argv[1], durum) else "yok")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
