"""Olay veriyolu — her girdi (mesaj, cron, monitör, webhook, istek) tek tip
olaya iner; kalıcı SQLite kuyruğu (süreç yeniden başlasa da olay kaybolmaz).

Faz 3 (2026-09-03): otomatik iş istekleri ARTIK BURADAN akar — üreticiler
(monitör, zamanlayıcı, hedef, webhook, uptime, haftalık sentez) `istek()` ile
type=request olayı bırakır; köprü `next_pending("request")` ile tüketir.
ops/requests dizini yalnız geriye dönük okunur (yeni yazan yok).

Faz 3.11 (2026-10-30) kuyruk birleşimi: İŞ KUYRUĞU da burada — type=job, `hedef` sütunu vps|mac.
Köprünün bellek-içi job_q'su ve ops/mac-queue/*.json dosya kuyruğu bu tablonun önüne/arkasına indi:
  - köprü `enqueue_job` → `is_ekle(hedef="vps")`; açılışta `bekleyen_isler("vps")` yeniden kuyruğa alınır
    (restart'ta kuyruktaki iş KAYBOLMAZ; koşan iş 'claimed' → failed, yan etkili iş iki kez koşmaz);
  - `/mac`, `/cc`, mac_cmd → `is_ekle(hedef="mac")`; Mac işçisi SSH ile `python3 -m ghost.bus mac-claim`
    (atomik sahiplenme, JSON) → koşar → `mac-bitir <id> [done|failed]` (sonuç stdin → `sonuc` sütunu).
  Şema göçü ghost/semalar.py (SURUM 2). GC: biten job/request olayları 30 gün sonra silinir (`gc`); chat
  olayları zaman çizelgesidir (ghost/akis.py), GC'ye GİRMEZ.
"""
import json
import sys
import time

from ghost import semalar

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  type TEXT NOT NULL,           -- chat|job|request|monitor|incident|webhook|schedule
  source TEXT NOT NULL,         -- telegram|cron|uptime|email|...
  payload TEXT NOT NULL,        -- JSON
  status TEXT NOT NULL DEFAULT 'pending',  -- pending|claimed|done|failed
  done_ts TEXT,
  hedef TEXT,                   -- job: vps|mac
  kind TEXT,                    -- job: iş türü (iş, mac, shell, otomatik…)
  sonuc TEXT,                   -- job(mac): işçinin yazdığı çıktı
  ust_id INTEGER                -- job: türetildiği request olayı
);
CREATE INDEX IF NOT EXISTS ev_status ON events(status, id);
"""

HEDEFLER = ("vps", "mac")
GC_GUN = 30


def _conn():
    # semalar.baglan: WAL + sürümlü göç (2: iş sütunları). _SCHEMA yalnız belgeler/taze db için durur.
    c = semalar.baglan()
    c.executescript(_SCHEMA)
    return c


def emit(ev_type, source, payload, status="pending"):
    """status='done' → bilgi amaçlı olay (sohbet akışı gibi): kuyruğa girmez,
    next_pending onu hiç görmez; yalnız zaman çizelgesi (ghost/akis.py) okur."""
    c = _conn()
    try:
        cur = c.execute("INSERT INTO events(ts, type, source, payload, status) VALUES(?,?,?,?,?)",
                        (time.strftime("%F %T"), ev_type, source,
                         json.dumps(payload, ensure_ascii=False), status))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def next_pending(ev_type=None):
    """Sıradaki bekleyen olay (dict) ya da None. ev_type verilirse yalnız o tür."""
    c = _conn()
    try:
        if ev_type:
            r = c.execute("SELECT id, ts, type, source, payload FROM events "
                          "WHERE status='pending' AND type=? ORDER BY id LIMIT 1",
                          (ev_type,)).fetchone()
        else:
            r = c.execute("SELECT id, ts, type, source, payload FROM events "
                          "WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
    finally:
        c.close()
    if not r:
        return None
    return {"id": r[0], "ts": r[1], "type": r[2], "source": r[3],
            "payload": json.loads(r[4])}


def claim_next(ev_type):
    """Sıradaki bekleyen olayı ATOMİK sahiplen (pending → claimed) ve döndür; yoksa None.
    next_pending yalnız okur: iki tüketici aynı olayı alabilir, işleme sırasında çökülürse olay
    'pending' kalıp yeniden koşar (dış inceleme GPT, 2026-10-27). Sahiplenilen olay mark() ile
    done/failed olur; çökmede 'claimed' kalır ve bayat_sahiplenmeler() onu bulur — kendiliğinden
    YENİDEN KOŞMAZ (yan etkili bir iş iki kez yapılmasın; en çok bir kez + görünür)."""
    c = _conn()
    try:
        r = c.execute("UPDATE events SET status='claimed', done_ts=? WHERE id=("
                      "SELECT id FROM events WHERE status='pending' AND type=? ORDER BY id LIMIT 1) "
                      "AND status='pending' RETURNING id, ts, type, source, payload",
                      (time.strftime("%F %T"), ev_type)).fetchone()
        c.commit()
    finally:
        c.close()
    if not r:
        return None
    return {"id": r[0], "ts": r[1], "type": r[2], "source": r[3],
            "payload": json.loads(r[4])}


def bayat_sahiplenmeler(ev_type, yas_sn=600):
    """Sahiplenilip yas_sn'den uzun süredir bitmemiş olaylar → 'failed' yapılır ve döndürülür
    (köprü çökmüş/yeniden başlamış, iş yarıda kalmış). Çağıran sahibine haber verir."""
    sinir = time.strftime("%F %T", time.localtime(time.time() - yas_sn))
    c = _conn()
    try:
        rows = c.execute("UPDATE events SET status='failed', done_ts=? WHERE status='claimed' "
                         "AND type=? AND done_ts < ? RETURNING id, ts, source, payload",
                         (time.strftime("%F %T"), ev_type, sinir)).fetchall()
        c.commit()
    finally:
        c.close()
    return [{"id": r[0], "ts": r[1], "source": r[2], "payload": json.loads(r[3])} for r in rows]


def mark(event_id, status="done"):
    c = _conn()
    try:
        c.execute("UPDATE events SET status=?, done_ts=? WHERE id=?",
                  (status, time.strftime("%F %T"), event_id))
        c.commit()
    finally:
        c.close()


def stats():
    c = _conn()
    try:
        rows = c.execute("SELECT status, COUNT(*) FROM events GROUP BY status").fetchall()
    finally:
        c.close()
    return dict(rows)


def istek(prompt, kind, source, announce=None, gate=False, classify=None, model=None,
          her_zaman_onay=False):
    """Otomatik iş isteği (köprü işe çevirir). Eski ops/requests/*.json alanlarıyla
    bire bir: prompt, kind, announce, gate (dış kaynaklı → onay kapısı), classify
    (sınıflandırma için güvenilir metin), model."""
    payload = {"prompt": prompt, "kind": kind}
    if announce:
        payload["announce"] = announce
    if gate:
        payload["gate"] = True
    if classify:
        payload["classify"] = classify
    if model:
        payload["model"] = model
    if her_zaman_onay:          # serbest dış metin: sınıfı ne olursa olsun önce sahibe sorulur
        payload["her_zaman_onay"] = True
    return emit("request", source, payload)


def bekleyen_istekler(limit=50):
    c = _conn()
    try:
        rows = c.execute("SELECT id, ts, source, payload FROM events WHERE status='pending' "
                         "AND type='request' ORDER BY id LIMIT ?", (int(limit),)).fetchall()
    finally:
        c.close()
    return [{"id": r[0], "ts": r[1], "source": r[2], "payload": json.loads(r[3])} for r in rows]


# ---------------- iş kuyruğu (Faz 3.11) ----------------
def _is_satir(r):
    return {"id": r[0], "ts": r[1], "source": r[2], "payload": json.loads(r[3]), "status": r[4],
            "hedef": r[5], "kind": r[6], "ust_id": r[7]}


_IS_SUTUN = "id, ts, source, payload, status, hedef, kind, ust_id"


def is_ekle(prompt, kind="iş", hedef="vps", source="bridge", model=None, meta=None, ust_id=None):
    """Tek iş kaydı (type=job). payload: prompt + kind + model + meta (Mac işi için reset/rc/cmd/timeout/
    cwd/sessiz/chat_id). Kimlik events.id'dir — Mac işçisi ve mac_cmd bu numarayı kullanır."""
    if hedef not in HEDEFLER:
        raise ValueError("hedef vps|mac olmalı: %r" % hedef)
    payload = {"prompt": prompt, "kind": kind}
    if model:
        payload["model"] = model
    if meta:
        payload.update({k: v for k, v in meta.items() if v is not None})
    c = _conn()
    try:
        cur = c.execute("INSERT INTO events(ts, type, source, payload, status, hedef, kind, ust_id) "
                        "VALUES(?,?,?,?,'pending',?,?,?)",
                        (time.strftime("%F %T"), "job", source, json.dumps(payload, ensure_ascii=False),
                         hedef, kind, ust_id))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def bekleyen_isler(hedef="vps", limit=200):
    """status=pending AND hedef=? işler, eski→yeni. Köprü açılışta vps olanları bellek kuyruğuna geri alır."""
    c = _conn()
    try:
        rows = c.execute("SELECT %s FROM events WHERE type='job' AND status='pending' AND hedef=? "
                         "ORDER BY id LIMIT ?" % _IS_SUTUN, (hedef, int(limit))).fetchall()
    finally:
        c.close()
    return [_is_satir(r) for r in rows]


def is_sahiplen(hedef):
    """Sıradaki bekleyen işi ATOMİK sahiplen (pending → claimed); yoksa None. Mac işçisi `mac-claim` ile
    çağırır; köprü vps işlerini kendi bellek kuyruğundan alır ve koşmaya başlarken mark(claimed) der."""
    c = _conn()
    try:
        r = c.execute("UPDATE events SET status='claimed', done_ts=? WHERE id=("
                      "SELECT id FROM events WHERE type='job' AND status='pending' AND hedef=? "
                      "ORDER BY id LIMIT 1) AND status='pending' RETURNING %s" % _IS_SUTUN,
                      (time.strftime("%F %T"), hedef)).fetchone()
        c.commit()
    finally:
        c.close()
    return _is_satir(r) if r else None


def is_bitir(is_id, sonuc=None, durum="done"):
    """İşi kapat: status done|failed, sonuc sütunu (≤200k). Var olan satır sayısı (0 → yok) döner."""
    if durum not in ("done", "failed"):
        raise ValueError("durum done|failed olmalı")
    c = _conn()
    try:
        cur = c.execute("UPDATE events SET status=?, done_ts=?, sonuc=COALESCE(?, sonuc) WHERE id=? AND type='job'",
                        (durum, time.strftime("%F %T"), (sonuc or "")[:200000] if sonuc is not None else None,
                         int(is_id)))
        c.commit()
        return cur.rowcount
    finally:
        c.close()


def is_oku(is_id):
    """Tek iş: alanlar + status + sonuc (mac_cmd sonucu buradan bekler). Yoksa None."""
    c = _conn()
    try:
        r = c.execute("SELECT %s, sonuc, done_ts FROM events WHERE id=? AND type='job'" % _IS_SUTUN,
                      (int(is_id),)).fetchone()
    finally:
        c.close()
    if not r:
        return None
    d = _is_satir(r)
    d["sonuc"], d["done_ts"] = r[8], r[9]
    return d


def is_geri_birak(is_id):
    """claimed → pending (işçi işi henüz KOŞMADAN hata aldı; güvenli yeniden deneme). Koşan iş için ÇAĞRILMAZ."""
    c = _conn()
    try:
        cur = c.execute("UPDATE events SET status='pending', done_ts=NULL WHERE id=? AND type='job' "
                        "AND status='claimed'", (int(is_id),))
        c.commit()
        return cur.rowcount
    finally:
        c.close()


def bayat_isler(hedef, yas_sn):
    """hedef'teki sahiplenilmiş ama yas_sn'den uzun süredir bitmemiş işler → failed + liste (yeniden KOŞMAZ)."""
    sinir = time.strftime("%F %T", time.localtime(time.time() - yas_sn))
    c = _conn()
    try:
        rows = c.execute("UPDATE events SET status='failed', done_ts=? WHERE type='job' AND status='claimed' "
                         "AND hedef=? AND done_ts < ? RETURNING %s" % _IS_SUTUN,
                         (time.strftime("%F %T"), hedef, sinir)).fetchall()
        c.commit()
    finally:
        c.close()
    return [_is_satir(r) for r in rows]


def is_say(hedef=None):
    """{status: n} — yalnız job olayları (hedef verilirse o hedef)."""
    c = _conn()
    try:
        if hedef:
            rows = c.execute("SELECT status, COUNT(*) FROM events WHERE type='job' AND hedef=? GROUP BY status",
                             (hedef,)).fetchall()
        else:
            rows = c.execute("SELECT status, COUNT(*) FROM events WHERE type='job' GROUP BY status").fetchall()
    finally:
        c.close()
    return dict(rows)


def gc(gun=GC_GUN, uygula=True, simdi=None):
    """Biten (done|failed) job/request olaylarını gun'den eskiyse sil. chat/webhook olayları DOKUNULMAZ
    (zaman çizelgesi + kural motoru). Silinen (ya da kuru koşuda silinecek) sayı."""
    simdi = time.time() if simdi is None else simdi
    sinir = time.strftime("%F %T", time.localtime(simdi - gun * 86400))
    c = _conn()
    try:
        if uygula:
            cur = c.execute("DELETE FROM events WHERE type IN ('job','request') AND status IN ('done','failed') "
                            "AND COALESCE(done_ts, ts) < ?", (sinir,))
            n = cur.rowcount
            c.commit()
        else:
            n = c.execute("SELECT COUNT(*) FROM events WHERE type IN ('job','request') AND status IN "
                          "('done','failed') AND COALESCE(done_ts, ts) < ?", (sinir,)).fetchone()[0]
    finally:
        c.close()
    return n


def _cli(argv):
    """python3 -m ghost.bus mac-claim | mac-bitir <id> [done|failed] (sonuç stdin) | mac-bayat [dk]
    | bekleyen [vps|mac] | oku <id> | durum | gc [--uygula]"""
    cmd = argv[0] if argv else "durum"
    if cmd == "mac-claim":                     # Mac işçisi (SSH): JSON tek satır ya da boş çıktı
        ev = is_sahiplen("mac")
        if ev:
            print(json.dumps(ev, ensure_ascii=False))
        return 0
    if cmd == "mac-bitir":
        if len(argv) < 2:
            print("kullanım: mac-bitir <id> [done|failed]  (sonuç stdin)", file=sys.stderr)
            return 2
        durum = argv[2] if len(argv) > 2 else "done"
        sonuc = sys.stdin.read() if not sys.stdin.isatty() else None
        n = is_bitir(argv[1], sonuc, durum)
        print("ok" if n else "yok")
        return 0 if n else 1
    if cmd == "mac-geri":
        print("ok" if is_geri_birak(argv[1]) else "yok")
        return 0
    if cmd == "mac-bayat":                     # 90 dk varsayılan (eski reclaim_stale ile aynı)
        dk = int(argv[1]) if len(argv) > 1 else 90
        for ev in bayat_isler("mac", dk * 60):
            print(json.dumps({"id": ev["id"], "prompt": str(ev["payload"].get("prompt", ""))[:120]},
                             ensure_ascii=False))
        return 0
    if cmd == "bekleyen":
        hedef = argv[1] if len(argv) > 1 else "vps"
        for ev in bekleyen_isler(hedef):
            print("#%d %s [%s] %s" % (ev["id"], ev["ts"], ev["kind"], str(ev["payload"].get("prompt", ""))[:100]))
        return 0
    if cmd == "oku":
        d = is_oku(argv[1])
        print(json.dumps(d, ensure_ascii=False, indent=1) if d else "yok")
        return 0 if d else 1
    if cmd == "gc":
        uygula = "--uygula" in argv
        n = gc(uygula=uygula)
        print("%s %d olay (job/request, %d günden eski)" % ("silindi" if uygula else "silinecek", n, GC_GUN))
        return 0
    if cmd == "durum":
        print(json.dumps({"olaylar": stats(), "is": {"vps": is_say("vps"), "mac": is_say("mac")}},
                         ensure_ascii=False))
        return 0
    print(_cli.__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
