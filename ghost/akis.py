"""Akış — Ghost'un zaman çizelgesi ve sohbet olay akışı (docs/kokpit.md Faz 1).

İki iş:
  1. `claude -p --output-format stream-json` satırlarını (köprünün run_claude'u
     `on_event` ile verir) NORMALİZE edip `events` tablosuna yazar
     (type=chat, status=done — kuyruk değil, kayıt). Bus'un ilk gerçek üretici
     + tüketici çifti: üretici köprü, tüketici kokpit API'si (SSE / yoklama).
  2. Zaman çizelgesi: audit.jsonl + ops/jobs + sohbet olaylarını tek listede,
     her satırda kaynağa bağlantı (commit → GitHub, Sentry, PostHog).

Olay sözlüğü (tur): baslangic · metin · arac · arac_sonuc · sonuc · sahne.
Hiçbir fonksiyon fırlatmaz — akış, köprünün cevabını asla düşürmesin.
"""
import glob
import json
import os
import re
import sqlite3
import time

from ghost import bus, config

GITHUB_REPO = "https://github.com/ornek-org/jarvis"
SENTRY_URL = "https://example-org.sentry.io/issues/"
POSTHOG_URL = "https://eu.posthog.com/project/000000"
OZET = 240            # olay özet uzunluğu
_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")


def _kisalt(s, n=OZET):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _arac_ozet(ad, girdi):
    """Araç girdisinden tek satırlık özet: komut / dosya / sorgu / url."""
    if not isinstance(girdi, dict):
        return _kisalt(girdi, 160)
    for k in ("command", "file_path", "path", "pattern", "query", "url", "prompt",
              "description", "skill", "message"):
        if girdi.get(k):
            return _kisalt(girdi[k], 160)
    return _kisalt(json.dumps(girdi, ensure_ascii=False), 160)


def stream_olaylar(satir):
    """Bir stream-json satırı → normalize olay listesi (0..n). Bilinmeyen tip → []."""
    try:
        o = json.loads(satir)
    except Exception:
        return []
    if not isinstance(o, dict):
        return []
    t = o.get("type")
    if t == "system" and o.get("subtype") == "init":
        return [{"tur": "baslangic", "model": o.get("model", ""),
                 "sid": o.get("session_id", "")}]
    if t == "assistant":
        out = []
        for b in (o.get("message") or {}).get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and (b.get("text") or "").strip():
                out.append({"tur": "metin", "metin": _kisalt(b["text"], 600)})
            elif b.get("type") == "tool_use":
                out.append({"tur": "arac", "ad": b.get("name", ""),
                            "ozet": _arac_ozet(b.get("name"), b.get("input"))})
        return out
    if t == "user":
        out = []
        for b in (o.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                icerik = b.get("content")
                if isinstance(icerik, list):
                    icerik = " ".join(x.get("text", "") for x in icerik
                                      if isinstance(x, dict))
                out.append({"tur": "arac_sonuc", "hata": bool(b.get("is_error")),
                            "ozet": _kisalt(icerik, 200)})
        return out
    if t == "result":
        return [{"tur": "sonuc", "hata": bool(o.get("is_error")),
                 "sure_ms": o.get("duration_ms"), "tur_sayisi": o.get("num_turns"),
                 "maliyet": o.get("total_cost_usd")}]
    return []


def olay_yaz(kaynak, olay):
    try:
        return bus.emit("chat", kaynak, olay, status="done")
    except Exception:
        return None


def olay_kaydedici(kaynak):
    """run_claude'a verilecek geri-çağrı: satır → olay(lar) → events. Asla fırlatmaz."""
    def _cb(satir):
        try:
            for ev in stream_olaylar(satir):
                olay_yaz(kaynak, ev)
        except Exception:
            pass
        try:  # Faz 1.12: onaylı komutun sonucu (süre/özet/hash) karta ve push'a
            from ghost import onay_sonuc
            onay_sonuc.izle(satir)
        except Exception:
            pass
    return _cb


def son_id():
    """events tablosundaki son sohbet olayının id'si (tur başlangıcı işareti; yarim_ozet için)."""
    try:
        c = sqlite3.connect(config.DB_PATH, timeout=10)
        r = c.execute("SELECT COALESCE(MAX(id),0) FROM events WHERE type='chat'").fetchone()
        c.close()
        return int(r[0] or 0)
    except Exception:
        return 0


def yarim_ozet(since_id, kaynak="sohbet", tavan=1500):
    """Faz 1.10 (2026-10-29, denetim 09 #4): zaman aşımında o ana kadarki görünür metin kaybolmasın.
    since_id'den sonraki metin olaylarını (araç özetleri değil) birleştirir; sondan tavan kadar.
    (metin, arac_sayisi) — hiç yoksa ('', n)."""
    metinler, arac = [], 0
    for ev in olaylar(since=since_id, limit=500):
        if ev.get("kaynak") != kaynak:
            continue
        if ev.get("tur") == "metin" and ev.get("metin"):
            metinler.append(ev["metin"])
        elif ev.get("tur") == "arac":
            arac += 1
    metin = "\n".join(metinler).strip()
    if len(metin) > tavan:
        metin = "…" + metin[-tavan:]
    return metin, arac


def olaylar(since=0, limit=100):
    """Sohbet olayları (type=chat), id > since."""
    try:
        c = sqlite3.connect(config.DB_PATH, timeout=10)
        rows = c.execute("SELECT id, ts, source, payload FROM events WHERE type='chat' "
                         "AND id > ? ORDER BY id LIMIT ?", (int(since), int(limit))).fetchall()
        c.close()
    except Exception:
        return []
    out = []
    for i, ts, src, payload in rows:
        try:
            p = json.loads(payload)
        except Exception:
            p = {"tur": "?", "metin": payload}
        out.append(dict(p, id=i, ts=ts, kaynak=src))
    return out


# Bu kadar süre YENİ olay gelmezse akış ölü sayılır. Canlı bir tur her araç
# çağrısında olay basar, dolayısıyla uzun sessizlik = ölüm. 40 dk'ydı; kusuru
# 40 dk taşımak yerine 12 dk'ya çekildi (bitmemis_kapat asıl kapı, bu yedek).
MESGUL_TAVAN_SN = 12 * 60


def bitmemis_kapat():
    """Açılışta çağrılır: `sonuc` olayı gelmeden kesilmiş her akışı kapat.

    KÖK SEBEP (2026-09-03, Sahip: "çalışıyor alanı bazen bugda kalıyor"): akışa
    sentetik sonuç yazan yer `run_claude`'un içiydi — yani AYNI SÜREÇ. Köprü
    turun ortasında ölürse (systemctl restart, çökme, OOM) o kod hiç koşmaz;
    son olay `arac` olarak kalır ve uygulama MESGUL_TAVAN_SN (40 dk) boyunca
    "Ghost çalışıyor" gösterir. Bugün köprü iki kez yeniden başlatıldı, kusur
    tam oradan göründü.

    Buradaki süpürme yapısal olarak güvenli: bu fonksiyon köprü AÇILIRKEN, ilk
    tur başlamadan koşar. O anda yaşayan hiçbir akış yoktur — `sonuc`suz her
    akışın sahibi süreç tanım gereği ölmüştür. Dönen: kapatılan kaynak sayısı."""
    try:
        c = sqlite3.connect(config.DB_PATH, timeout=10)
        # YALNIZ tur akışları: 'sohbet' ve 'is#<no>'. source='sahne' bir TUR DEĞİL,
        # sahne basımlarının kayıt defteridir — hiç 'sonuc' almaz, almamalı da.
        # Süpürme oraya da uzansaydı her açılışta bir sahte "kesildi" satırı
        # birikirdi (canlı veride yakalandı, kod yazıldıktan sonra).
        kaynaklar = [r[0] for r in c.execute(
            "SELECT DISTINCT source FROM events WHERE type='chat'").fetchall()
            if r[0] == "sohbet" or str(r[0]).startswith("is#")]
        acik = []
        for k in kaynaklar:
            r = c.execute("SELECT payload FROM events WHERE type='chat' AND source=? "
                          "ORDER BY id DESC LIMIT 1", (k,)).fetchone()
            if not r:
                continue
            try:
                if json.loads(r[0]).get("tur") != "sonuc":
                    acik.append(k)
            except Exception:
                continue
        c.close()
    except Exception:
        return 0
    for k in acik:
        olay_yaz(k, {"tur": "sonuc", "hata": True, "subtype": "kesildi-restart",
                     "metin": "(tur köprü yeniden başlarken kesildi)"})
    return len(acik)


def sohbet_durumu(kaynak="sohbet"):
    """Sohbet şeridi şu an çalışıyor mu? Telegram'daki "typing" + "⏳ hâlâ
    çalışıyorum" karşılığı — uygulama bunu canlı satır olarak gösterir.
    Son olaya bakar: baslangic/metin/arac/arac_sonuc → meşgul (özet = son araç);
    sonuc → boşta. {"aktif": bool, "ozet": str, "ts": str, "sure_sn": int, "basladi": str}.
    `sure_sn` turun BAŞINDAN beri geçen saniye (sunucu saatiyle): uygulama sayacı
    buradan kurar — istemci kendi saatini tutunca sekme/uygulama açılışında sıfırlanıyordu
    (Sahip, 2026-09-26)."""
    try:
        c = sqlite3.connect(config.DB_PATH, timeout=10)
        rows = c.execute("SELECT id, ts, payload FROM events WHERE type='chat' AND source=? "
                         "ORDER BY id DESC LIMIT 12", (kaynak,)).fetchall()
        basladi = _tur_baslangici(c, kaynak, rows[0][0]) if rows else ""
        c.close()
    except Exception:
        return {"aktif": False, "ozet": "", "ts": "", "sure_sn": 0, "basladi": ""}
    if not rows:
        return {"aktif": False, "ozet": "", "ts": "", "sure_sn": 0, "basladi": ""}
    try:
        son = json.loads(rows[0][2])
    except Exception:
        son = {}
    if son.get("tur") == "sonuc":
        return {"aktif": False, "ozet": "", "ts": rows[0][1], "sure_sn": 0, "basladi": ""}
    try:
        t0 = time.mktime(time.strptime(rows[0][1][:19], "%Y-%m-%d %H:%M:%S"))
        if time.time() - t0 > MESGUL_TAVAN_SN:
            return {"aktif": False, "ozet": "", "ts": rows[0][1], "sure_sn": 0, "basladi": ""}
    except Exception:
        pass
    ozet = ""
    for _, _, payload in rows:           # en son araç çağrısı = "şu an ne yapıyor"
        try:
            p = json.loads(payload)
        except Exception:
            continue
        if p.get("tur") == "arac":
            ozet = "%s · %s" % (p.get("ad", ""), p.get("ozet", ""))
            break
        if p.get("tur") == "metin" and not ozet:
            ozet = p.get("metin", "")[:120]
    return {"aktif": True, "ozet": ozet[:160], "ts": rows[0][1],
            "sure_sn": _gecen_sn(basladi or rows[0][1]), "basladi": basladi or rows[0][1]}


def _tur_baslangici(c, kaynak, son_id):
    """Sürmekte olan turun ilk olayının ts'i: son 'sonuc' satırından SONRAKİ ilk satır
    (baslangic olayı yoksa da çalışır — kesik turda ilk araç çağrısı sayılır).
    id'yi tersten yürür; son sonuç bulununca durur, tablo boyutuna bağlı değil."""
    try:
        r = c.execute("SELECT id FROM events WHERE type='chat' AND source=? AND id<=? "
                      "AND payload LIKE '%\"tur\": \"sonuc\"%' ORDER BY id DESC LIMIT 1",
                      (kaynak, son_id)).fetchone()
        esik = r[0] if r else 0
        r = c.execute("SELECT ts FROM events WHERE type='chat' AND source=? AND id>? "
                      "ORDER BY id LIMIT 1", (kaynak, esik)).fetchone()
        return r[0] if r else ""
    except Exception:
        return ""


def _gecen_sn(ts):
    """'YYYY-MM-DD HH:MM:SS' (sunucu yerel saati) → şimdiye kadar geçen saniye, ≥0."""
    try:
        t0 = time.mktime(time.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S"))
        return max(0, int(time.time() - t0))
    except Exception:
        return 0


def olay_son():
    try:
        c = sqlite3.connect(config.DB_PATH, timeout=10)
        r = c.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]
        c.close()
        return r
    except Exception:
        return 0


def olaylar_bekle(since=0, limit=100, bekle=0):
    son = time.time() + max(0, min(float(bekle or 0), 60))
    while True:
        rows = olaylar(since, limit)
        if rows or time.time() >= son:
            return rows
        time.sleep(0.5)


# ---------------------------------------------------------------- ZAMAN ÇİZELGESİ
def _baglanti(metin):
    m = _SHA.search(metin or "")
    if m and len(m.group(0)) >= 7 and any(ch.isdigit() for ch in m.group(0)) \
            and any(ch.isalpha() for ch in m.group(0)):
        return GITHUB_REPO + "/commit/" + m.group(0)
    low = (metin or "").lower()
    if "sentry" in low:
        return SENTRY_URL
    if "posthog" in low:
        return POSTHOG_URL
    return None


def _audit(n=400):
    out = []
    try:
        with open(config.AUDIT_LOG, encoding="utf-8") as f:
            lines = f.readlines()[-n:]
    except OSError:
        return out
    for i, l in enumerate(lines):
        try:
            r = json.loads(l)
        except Exception:
            continue
        detay = r.get("detail", "")
        out.append({"id": "audit:%s:%d" % (r.get("ts", ""), i), "ts": r.get("ts", ""),
                    "kaynak": "audit", "tur": r.get("action", ""),
                    "baslik": "%s · %s" % (r.get("actor", ""), r.get("action", "")),
                    "detay": _kisalt(detay, 300), "sinif": r.get("class", ""),
                    "baglanti": _baglanti("%s %s" % (r.get("action", ""), detay))})
    return out


def _isler():
    out = []
    for p in glob.glob(os.path.join(config.OPS, "jobs", "*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                j = json.load(f)
            jid = os.path.basename(p)[:-5]
        except Exception:
            continue
        ts = j.get("ended") or j.get("started") or ""
        out.append({"id": "is:%s" % jid, "ts": ts, "kaynak": "is",
                    "tur": j.get("status", ""),
                    "baslik": "iş #%s · %s · %s" % (jid, j.get("kind", "iş"), j.get("status", "")),
                    "detay": _kisalt(j.get("prompt", ""), 200),
                    "sinif": j.get("department", ""), "baglanti": None})
    return out


def _sohbet(limit=300):
    out = []
    for e in olaylar(0, 100000)[-limit:]:
        tur = e.get("tur")
        if tur == "arac":
            baslik, detay = "araç · %s" % e.get("ad", ""), e.get("ozet", "")
        elif tur == "sonuc":
            baslik = "tur bitti" + (" (hata)" if e.get("hata") else "")
            detay = "%s ms · %s tur" % (e.get("sure_ms"), e.get("tur_sayisi"))
        elif tur == "sahne":
            baslik, detay = "sahne · %s" % e.get("baslik", ""), ""
        elif tur == "metin":
            baslik, detay = "Ghost", e.get("metin", "")
        else:
            continue
        out.append({"id": "olay:%d" % e["id"], "ts": e.get("ts", ""), "kaynak": e.get("kaynak", ""),
                    "tur": tur, "baslik": baslik, "detay": _kisalt(detay, 300),
                    "sinif": "", "baglanti": _baglanti(detay)})
    return out


def zaman_cizelgesi(limit=60, once=None, kaynaklar=None):
    """Birleşik çizelge, yeniden eskiye. `once`: yalnız bu ts'den ESKİ satırlar (sayfalama)."""
    rows = _audit() + _isler() + _sohbet()
    if kaynaklar:
        rows = [r for r in rows if r["kaynak"] in kaynaklar]
    if once:
        rows = [r for r in rows if r["ts"] and r["ts"] < once]
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows[:max(1, min(int(limit or 60), 300))]
