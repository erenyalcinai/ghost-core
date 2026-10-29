#!/usr/bin/env python3
"""Ghost nöbetçisi: siteleri yoklar, durum DEĞİŞİMİNDE Telegram'a haber verir.

Sessizlik = her şey yolunda. launchd 10 dakikada bir çalıştırır.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import heartbeat   # aynı dizin (ops) — sys.path[0] betiğin dizini

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)
from ghost import durum, gunluk, sahip  # noqa: E402  (profil: ops/sahip.json)

# İzlenen siteler profilden (ops/sahip.json "siteler": izle/strict200/get_only).
# STRICT_200: 404 = içerik kayboldu (Pages dağıtımı bozuldu, NFC kart boş sayfa).
# GET_ONLY: HEAD'e 501 dönen uçlar — varsayılan kiple 10 dk'da bir SAHTE alarm verirdi.
NFC_KART = sahip.nfc_kart()          # kartın URL'si: STRICT_200 içinde (boş sayfa = ölüm)
SITES = sahip.izlenen_urller()
STRICT_200 = sahip.strict200()
GET_ONLY = sahip.get_only()
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPS = os.path.join(BASE, "ops")
STATE_FILE = os.path.join(OPS, "uptime_state.json")
# Olay müdahalesi zinciri AYRI dosyada: uptime_state.json'ı health/api/trafik/köprü {url: bool}
# diye okur, oraya başka anahtar koymak onları kırar (2026-10-29).
MUDAHALE_FILE = os.path.join(OPS, "uptime_mudahale_state.json")

ENV = {}
# .env gitignore'da — CI runner'da YOKTUR. Import anında koşulsuz açmak,
# bu modülü import eden testleri makineye bağımlı kılar: yerelde yeşil,
# CI'da kırmızı (24 Ağustos 2026'da tam olarak bu oldu). Yoksa boş geç;
# token gerçekten gerektiğinde main() yüksek sesle durur.
_ENV_PATH = os.path.join(BASE, ".env")
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                ENV[k] = v.strip()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ghost import notify, vekil  # noqa: E402  sır vekili (Faz 2); Telegram gönderimi tek yerde (Faz 2.9)


def log(msg):
    gunluk.yaz("uptime", msg)   # journald: journalctl -t ghost-uptime (Faz 2.9)


def owner_id():
    if ENV.get("TELEGRAM_OWNER_ID", "").strip().isdigit():
        return int(ENV["TELEGRAM_OWNER_ID"])
    try:
        with open(os.path.join(OPS, "owner.json"), encoding="utf-8") as f:
            return json.load(f)["id"]
    except Exception:
        return None


def tg_send(text):
    """Faz 2.9: gönderim tek yerde (ghost/notify.py); dokum=False = eski ham yol (kokpit/apns kopyası yok)."""
    if not owner_id():
        log("sahip yok, mesaj atlanıyor")
        return
    if not notify.telegram(text, ENV, dokum=False):
        log("telegram gönderilemedi")


def check(url):
    limit = 400 if url in STRICT_200 else 500
    method = "GET" if url in GET_ONLY else "HEAD"
    req = urllib.request.Request(url, method=method,
                                 headers={"User-Agent": "GhostWatch/1.0"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            ok = r.status < limit
            if not ok:
                log("neden %s: HTTP %s (%.1fs)" % (url, r.status, time.time() - t0))
            return ok
    except urllib.error.HTTPError as e:
        # 4xx = site CEVAP VERİYOR (405 HEAD reddi, 403 bot koruması...) →
        # ayakta say; yalnız 5xx düşük. (urlopen 4xx'i exception yapar —
        # eski kod bu yüzden 4xx'e sahte 'DÜŞTÜ' alarmı + olay işi üretiyordu.)
        # STRICT_200 hedeflerinde bu gevşeklik ölümcül: 404 = içerik kayboldu.
        ok = e.code < limit
        if not ok:
            log("neden %s: HTTP %s cf-ray=%s (%.1fs)" % (
                url, e.code, e.headers.get("cf-ray", "-"), time.time() - t0))
        return ok
    except Exception as e:
        # NEDENİ YAZ (2026-09-29 dersi): owner.example 17:14'te "DÜŞTÜ" dedi ama
        # zaman aşımı mı, TLS mi, DNS mi bilinmiyordu — Cloudflare kaydında
        # istek hiç görünmedi, teşhis dolaylı yoldan yapıldı.
        log("neden %s: %s: %s (%.1fs)" % (url, type(e).__name__, str(e)[:120], time.time() - t0))
        return False


# Olay müdahalesi eşiği (2026-10-29, denetim 02-durum d4): 4/4 otomatik "OLAY MÜDAHALESİ" kartı
# Sahip tarafından reddedildi — hepsi geçici erişim dalgalanmasıydı (30 günde gerçek DOWN 0).
# Artık uç 2 ARDIŞIK turda düşük VE ilk düşüşten bu yana ≥5 dk geçmişse, kesinti başına BİR kez.
MUDAHALE_ESIK_SN = 5 * 60


def mudahale_gerekir_mi(url, simdi, dusuk_onceki, mudahale_onceki, esik_sn=MUDAHALE_ESIK_SN):
    """Saf: bu turda DOWN olan url için olay işi açılsın mı?
    dusuk_onceki: {url: ilk_dusus_ts} (önceki turdan) · mudahale_onceki: {url: ts} (bu kesintide açıldı)."""
    ilk = dusuk_onceki.get(url)
    if ilk is None:
        return False                      # ilk tur: tek seferlik dalgalanma olabilir
    if url in mudahale_onceki:
        return False                      # bu kesinti için zaten açıldı
    try:
        return simdi - float(ilk) >= esik_sn
    except (TypeError, ValueError):
        return False


def main():
    if not vekil.hazir("telegram", ENV):
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN yok — .env okunamadı ya da eksik. "
            "Sessizce devam etmek yerine duruyorum (mesajlar hiçbir yere gitmezdi).")
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            prev = json.load(f)
    except Exception:
        prev = {}

    heartbeat.beat("uptime")
    # Karşılıklı nöbet: selfcheck'i BU yoklar, selfcheck de bunu yoklar. İkisi
    # ayrı systemd timer'ı — birlikte susmaları için sistemin tamamı durmalı.
    changes = heartbeat.report(["selfcheck"])

    simdi = time.time()
    try:
        mz = durum.tasi("uptime_mudahale", MUDAHALE_FILE, {}) or {}   # Faz 1.9: durum tablosu
    except Exception:
        mz = {}
    dusuk_onceki = mz.get("dusuk") or {}          # {url: ilk düşüş ts}
    mudahale_onceki = mz.get("mudahale") or {}    # {url: açıldığı ts}
    cur, down_now, dusuk, mudahale = {}, [], {}, {}
    for url in SITES:
        ok = check(url)
        if not ok:
            time.sleep(10)  # tek seferlik hıçkırıkta alarm çalmasın
            ok = check(url)
        cur[url] = ok
        before = prev.get(url)
        if before is None and not ok:
            changes.append("🔴 %s ERİŞİLEMİYOR" % url)
        elif before is True and not ok:
            changes.append("🔴 %s DÜŞTÜ" % url)
        elif before is False and ok:
            changes.append("🟢 %s ayağa kalktı" % url)
        if not ok:
            dusuk[url] = dusuk_onceki.get(url) or simdi
            if url in mudahale_onceki:
                mudahale[url] = mudahale_onceki[url]
            if mudahale_gerekir_mi(url, simdi, dusuk_onceki, mudahale_onceki):
                down_now.append(url)
                mudahale[url] = simdi
    try:  # ayağa kalkan uç zincirden düşer; yazılamazsa nöbetçi durmaz
        durum.yaz("uptime_mudahale", {"dusuk": dusuk, "mudahale": mudahale})
    except Exception as e:
        log("müdahale durumu yazılamadı: %r" % e)

    # Kalıcı düşen uç için otomatik araştırma isteği bırak (kendi kendini iyileştirme) —
    # eşik: 2 ardışık DOWN + 5 dk (mudahale_gerekir_mi)
    if down_now:
        for url in down_now:
            prompt = (
                "OLAY MÜDAHALESİ — otomatik tetiklendi (nöbetçi).\n"
                "Belirti: %s erişilemiyor / 5xx.\n"
                "Tüm entegrasyonların var (GitHub, Sentry, Cloudflare env token, "
                "PostHog). Araştır: (1) Cloudflare'de bu zone'da trafik/hata/kaynak "
                "anomalisi; (2) Sentry'de ilgili yeni hata; (3) ilgili repoda son "
                "commit ve deploy (şüpheli değişiklik?). Kök nedeni belirle.\n"
                "SONUÇ: kısa kök-neden + ÖNERİLEN düzeltme. DEĞİŞİKLİK YAPMA — "
                "sadece teşhis ve öneri; uygulama kararını Sahip verecek." % url
            )
            try:  # olay veriyolu (Faz 3): köprü type=request olayını işe çevirir
                sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                from ghost import bus
                bus.istek(prompt, "olay", "uptime",
                          announce="🚨 %s düştü — otomatik olay müdahalesi başlatıldı" % url)
            except Exception as e:
                log("istek yazılamadı: %r" % e)

    # Önce İLET, sonra kaydet: mesaj gitmezse durum eskisinde kalır →
    # sonraki turda değişim yeniden algılanır (DÜŞTÜ bildirimi kaybolmaz).
    if changes:
        msg = "👻 Ghost nöbetçi raporu:\n" + "\n".join(changes)
        log(msg.replace("\n", " | "))
        try:
            tg_send(msg)
        except Exception as e:
            log("telegram hata: %r — durum kaydedilmedi, sonraki tur yeniden dener" % e)
            return
    else:
        log("sessiz: " + ", ".join("%s=%s" % (u.split("//")[1], "ok" if v else "DOWN")
                                   for u, v in cur.items()))

    with open(STATE_FILE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(cur, f)
    os.replace(STATE_FILE + ".tmp", STATE_FILE)

    # Nabız "Siteler" (2026-09-06): PostHog ziyaret sayısı saatte bir buradan tazelenir —
    # Nabız 15 sn'de bir yenilenir, sorgu oraya sığmaz. Hata nöbetçiyi düşürmez.
    try:
        sys.path.insert(0, BASE)
        from ghost import trafik
        if trafik.tazele():
            log("site trafiği ölçüldü")
    except Exception as e:
        log("trafik: %r" % e)


if __name__ == "__main__":
    main()
