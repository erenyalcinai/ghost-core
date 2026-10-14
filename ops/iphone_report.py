#!/usr/bin/env python3
"""iPhone durum raportörü — MAC'TE çalışır, VPS'e yazar.

Nöbetçinin (ops/iphone_watch.py) canlılık yarısı bu betiğe dayanır. Sebep
mimari: VPS telefonu göremez ve Mac'e inbound erişim YOK — yalnız Mac'in
başlattığı SSH var. Yani "telefon ayakta mı" sorusunu ancak Mac cevaplayıp
VPS'e itebilir. mac_worker'ın kuyruk yoklaması da aynı yönde çalışır.

Neyi ölçer:
- WDA gerçekten cevap veriyor mu (127.0.0.1:8101/status, aşağıdaki port
  sözleşmesine bak). Süreç listesine
  BAKMAZ: xcodebuild süreci ayakta olup WDA'nın çoktan ölmüş olması sık
  görülen bir yalancı yeşildir. Tek dürüst kanıt HTTP cevabıdır.
- Cihaz gerçekten bağlı mı (go-ios). WDA'sız da olsa bu ayrımı bilmek gerekir:
  "telefon çıkarılmış" ile "WDA çökmüş" farklı işler.

Sessizdir — Telegram'a kendisi mesaj atmaz. Karar ve uyarı VPS'teki nöbetçinin
işi; raportör yalnız gerçeği yazar. Böylece Mac uykudan kalkınca eski durumu
tekrar tekrar bildirmez.

launchd yarım saatte bir çalıştırır (ops/launchd/ai.jarvis.iphone.plist).
"""
import json
import os
import subprocess
import time
import urllib.request

HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, "jarvis")
OPS = os.path.join(BASE, "ops")
LOG_FILE = os.path.join(OPS, "iphone_report.log")
IOS_BIN = os.path.join(HOME, "ghost-ios", "bin", "ios")
UDID = "00008110-000000000000000A"
# 8101: WDA rölesi (ai.jarvis.wdarelay, ops/wda_relay.py) — telefonun Wi-Fi'daki
# WDA ucuna taşır; eski USB yönlendirmesinin (ai.jarvis.wdaforward) yerini aldı.
# Yerel 8100 BOŞ bırakılır — mobilecli/mobile-mcp orayı kendi yönetir; işgal
# edilirse ekran görüntüsü/dokunma "parse error" ile ölür (2026-08-20 dersi).
WDA_URL = "http://127.0.0.1:8101/status"
UZAK = "/home/ghost/jarvis/ops/iphone_state.json"

ENV = {}
try:
    with open(os.path.join(BASE, ".env"), encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                ENV[_k] = _v.strip()
except OSError:
    pass

VPS = os.environ.get("GHOST_VPS") or "ghost@" + ENV.get("GHOST_VPS_IP", "203.0.113.10")
SSH_KEY = os.path.join(HOME, ".ssh", "ghost_runtime")
SSH = ["ssh", "-i", SSH_KEY, "-o", "ConnectTimeout=10",
       "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes",
       "-o", "ControlMaster=auto",
       "-o", "ControlPath=" + os.path.expanduser("~/.ssh/ghost_cm-%r@%h:%p"),
       "-o", "ControlPersist=120", VPS]


def log(msg):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(time.strftime("%F %T ") + msg + "\n")


def cihaz_bagli():
    """(bağlı_mı, açıklama). go-ios yoksa None → bilinmiyor, yalan söyleme."""
    if not os.path.exists(IOS_BIN):
        return None, "go-ios binary yok"
    try:
        r = subprocess.run([IOS_BIN, "list"], capture_output=True, text=True,
                           timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        return None, "go-ios çalışmadı (%s)" % type(e).__name__
    return (UDID in r.stdout), ("listede" if UDID in r.stdout else "listede yok")


def wda_durum():
    """(ready_mi, sebep). HTTP cevabı tek geçerli kanıt."""
    try:
        with urllib.request.urlopen(WDA_URL, timeout=10) as resp:
            d = json.load(resp)
    except Exception as e:
        return False, "8101 cevap vermiyor (%s)" % type(e).__name__
    val = d.get("value") or {}
    if d.get("state") == "success" or val.get("ready") is True:
        return True, "ready"
    return False, "cevap verdi ama hazır değil (%s)" % str(d.get("state"))[:40]


def main():
    ready, sebep = wda_durum()
    bagli, bagli_not = cihaz_bagli()
    if not ready and bagli is False:
        # Kablo artık ŞART DEĞİL (röle Wi-Fi'dan gider); bu yüzden "bağlı değil"
        # tek başına arıza değil, yalnız bağlamdır.
        sebep = "%s (ayrıca telefon USB'de görünmüyor)" % sebep

    durum = {
        "ts": int(time.time()),
        "at": time.strftime("%F %T"),
        "wda_ready": ready,
        "neden": sebep,
        "cihaz_bagli": bagli,
        "cihaz_not": bagli_not,
        "udid": UDID,
    }
    govde = json.dumps(durum, ensure_ascii=False)
    # Yerel kopya: SSH koparsa Mac'te en azından son durum kalsın.
    with open(os.path.join(OPS, "iphone_state.json"), "w", encoding="utf-8") as f:
        f.write(govde)
    try:
        r = subprocess.run(
            SSH + ["cat > %s.tmp && mv %s.tmp %s" % (UZAK, UZAK, UZAK)],
            input=govde, text=True, capture_output=True, timeout=60)
        if r.returncode:
            log("VPS'e yazılamadı rc=%s %s" % (r.returncode, r.stderr.strip()[:200]))
        else:
            log("yazıldı: ready=%s cihaz=%s (%s)" % (ready, bagli, sebep))
    except subprocess.SubprocessError as e:
        log("SSH hata: %r" % e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
