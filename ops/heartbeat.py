#!/usr/bin/env python3
"""Nöbetçi kalp atışı — "sessizlik = sağlık" sözleşmesinin kör noktasını kapatır.

Ghost'un bütün nöbetçileri (uptime_watch, biz_watch, selfcheck, marka cron'ları)
aynı sözleşmeyle çalışır: sorun yoksa KONUŞMA. Bu doğru bir tasarım, ama tek
başına iki farklı durumu ayırt edilemez kılar:

    (a) her şey yolunda                → sessizlik
    (b) nöbetçi ölmüş / cron kalkmamış → aynı sessizlik

(b) tam da alarma en çok ihtiyaç duyulan an. 2026-08-15'te selfcheck'in kendisi
bir import hatasıyla nöbet anında düştü ve alarm hiç gitmedi (bkz. selfcheck.py
baştaki not) — o olay bu dosyanın gerekçesi.

Çözüm: her nöbetçi koştuğunda bir damga bırakır; damgayı BAŞKA bir nöbetçi
yoklar. selfcheck ↔ uptime_watch karşılıklı bakar (ikisi ayrı systemd timer'ı —
birlikte ölmeleri için sistemin tamamının durması gerekir), cron tabanlı
işleri de selfcheck yoklar.

KENDİ KENDİNİ AYARLAR: bir isim yalnızca o makinede EN AZ BİR KEZ atmışsa
izlenir. Böylece aynı dosya Mac'te ve VPS'te ayrı nöbetçi kümeleriyle çalışır,
karşı makinenin işleri için sahte alarm üretmez.

Kullanım:
    python3 ops/heartbeat.py beat <isim>     # kabuk cron'ları için
    import heartbeat; heartbeat.beat("x")    # python nöbetçileri için
"""
import json
import os
import sys
import time

# GHOST_OPS: testler gerçek ops/ dizinine dokunmasın (tests/common.py kuralı).
OPS = os.environ.get("GHOST_OPS") or os.path.dirname(os.path.abspath(__file__))
BEAT_DIR = os.path.join(OPS, "heartbeats")      # eski damga dizini: ilk okumada durum tablosuna taşınır
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _durum():
    """Faz 1.9 (2026-10-29): damgalar ghost.db `durum` tablosunda `heartbeat.<ad>`; susturma `heartbeat_alerted`."""
    from ghost import durum
    return durum

# isim -> (etiket, bayat sayılan yaş [sn]). Eşik iki kurala göre seçilir —
# tek bir "×3" kuralı yanlış olurdu:
#   sık koşanlar (≤1 saat)  → periyodun ~3 katı. Tek tur kaçırmak (ağ, yük,
#                              timer kayması) alarm üretmesin; ×3 ucuz çünkü
#                              gecikme yine dakikalar mertebesinde.
#   seyrek koşanlar (>1 saat) → periyot + birkaç saat pay. Burada ×3 demek
#                              GÜNLERCE körlük demek: günde bir koşan defter
#                              işi için ×3 = 3 gün. Kaçan turdan birkaç saat
#                              sonra haber vermek doğrusu.
# Bu ayrım tests/test_heartbeat.py'de sabitlendi (ilk yazımda ×3 yazmıştım,
# test günlük işte yakaladı).
WATCHERS = {
    "uptime":       ("nöbetçi (site yoklama, 10 dk)",      40 * 60),
    "selfcheck":    ("öz-denetim (köprü/iş/beyin, 15 dk)", 50 * 60),
    "biz_watch":    ("iş nöbetçisi (saatlik)",              3 * 3600),
    "marka_deploy": ("ghostinprod.com dağıtımı (6 saat)",  14 * 3600),
    # Faz 2.10 (2026-10-30): Nabız nöbetçi listesi 5 → 17. Eşik kuralı yukarıdaki gibi (tests/test_heartbeat.py).
    "port_watch":   ("port nöbeti (10 dk)",                  40 * 60),
    "tunnel_watch": ("Mac ters tünel nöbeti (1 dk)",          10 * 60),
    "giris":        ("SSH giriş nöbeti (5 dk)",               20 * 60),
    "knowsync":     ("bilgi senkronu/push (15 dk)",           50 * 60),
    "acik_nabiz":   ("ghost-core nabzı (30 dk)",              2 * 3600),
    "chatgpt_export": ("ChatGPT export bekçisi (30 dk)",      2 * 3600),
    "gmail_is_nabzi": ("Gmail iş nabzı (4 saat)",             10 * 3600),
    "sms_watch":    ("SMS rayı nöbeti (6 saat)",              14 * 3600),
    "iphone_watch": ("iPhone rayı nöbeti (6 saat)",           14 * 3600),
    "listen":       ("dinleme organı (6 saat)",               14 * 3600),
    "brief":        ("sabah brifingi (günlük 08:04)",         30 * 3600),
    "yedek":        ("hafıza dış yedeği (günlük)",            30 * 3600),
    "weekly":       ("haftalık sentez (Pazar 20:07)",         7 * 86400 + 8 * 3600),
    # "defter" (günlük defter taslağı, 21:40) 2026-09-09'da KALDIRILDI — Sahip:
    # "artık günlük defter taslağına gerek yok". Cron da silindi.
}


def _path(name):
    return os.path.join(BEAT_DIR, name + ".json")


def beat(name):
    """Damgayı bas. Nöbetçinin ASIL işini asla bozmamalı → hata yutulur."""
    try:
        _durum().yaz("heartbeat." + name, {"ts": int(time.time()), "at": time.strftime("%F %T")})
    except Exception:                       # noqa: BLE001 — damga kritik değil
        pass


def age(name):
    """Son atıştan bu yana geçen saniye; hiç atmamışsa None."""
    try:
        d = _durum().tasi("heartbeat." + name, _path(name))
        return None if d is None else time.time() - d["ts"]
    except Exception:
        return None


def stale(names):
    """Bu makinede atmış AMA bayatlamış nöbetçiler → [(isim, etiket, yaş_dk)].

    Hiç atmamış isim atlanır: o nöbetçi bu makinede kurulu değil (Mac/VPS
    ayrımı) ya da yeni kuruldu ve ilk turunu beklemekte."""
    out = []
    for name in names:
        if name not in WATCHERS:
            continue
        a = age(name)
        if a is None:
            continue
        label, limit = WATCHERS[name]
        if a > limit:
            out.append((name, label, int(a / 60)))
    return out


ALERT_STATE = os.path.join(BEAT_DIR, "_alerted.json")
ALERT_GAP = 6 * 3600        # aynı ölü nöbetçi için iki alarm arası en az süre


def should_alert(name, gap=ALERT_GAP):
    """Ölü nöbetçi 10 dakikada bir bağırmasın: ilk tespitte konuş, sonra 6
    saatte bir hatırlat. Nöbetçi dirildiğinde damga silinir → bir sonraki
    ölümde yine ANINDA haber verilir (susturma kalıcı değil)."""
    try:
        st = _durum().tasi("heartbeat_alerted", ALERT_STATE, {}) or {}
    except Exception:
        st = {}
    now = time.time()
    if now - st.get(name, 0) < gap:
        return False
    st[name] = now
    try:
        _durum().yaz("heartbeat_alerted", st)
    except Exception:
        pass
    return True


def clear_alert(names):
    """Sağlıklı dönen isimlerin susturmasını kaldır."""
    try:
        st = _durum().tasi("heartbeat_alerted", ALERT_STATE, {}) or {}
    except Exception:
        return
    if not any(n in st for n in names):
        return
    for n in names:
        st.pop(n, None)
    try:
        _durum().yaz("heartbeat_alerted", st)
    except Exception:
        pass


def report(names):
    """Nöbetçi-nöbeti tek çağrıda: bayat olanlar için alarm satırları döner
    (susturma kuralı uygulanmış), sağlıklı olanların susturması temizlenir."""
    bad = stale(names)
    bad_names = {n for n, _l, _m in bad}
    clear_alert([n for n in names if n not in bad_names])
    out = []
    for name, label, mins in bad:
        if should_alert(name):
            out.append("💀 NÖBETÇİ SESSİZ: %s — %d dakikadır atmıyor "
                       "(sessizlik artık 'sağlıklı' demek değil)." % (label, mins))
    return out


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "beat":
        beat(sys.argv[2])
    elif len(sys.argv) >= 2 and sys.argv[1] == "durum":
        for n, (label, limit) in WATCHERS.items():
            a = age(n)
            print("%-14s %-40s %s" % (
                n, label,
                "hiç atmadı" if a is None else
                "%d dk önce%s" % (a / 60, "  ⚠ BAYAT" if a > limit else "")))
    else:
        print(__doc__)
        sys.exit(2)
