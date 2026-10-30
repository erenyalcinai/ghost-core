#!/usr/bin/env python3
"""Mac'te deterministik komut çalıştır — model devreye girmeden.

NEDEN: Mac işlerinin çoğu yargı gerektirmiyor ("git push", "tail log",
"launchctl print"). Her biri için tam bir Claude oturumu açmak dakikalar ve
token demekti; oysa komutun cevabı tek satır. Bu yol kuyruğu aynen kullanır
(Mac işçisi zaten 30 sn'de bir yokluyor) ama işi `bash -lc` ile koşturur.

Ajan oturumu YALNIZ gerçekten düşünmek gerektiğinde: "WDA neden kalkmıyor,
bul ve çöz" ajanın işi; "launchctl print ai.jarvis.wda" bunun işi.

Kullanım:
    python3 ops/mac_cmd.py "git -C ~/jarvis log --oneline -1"
    python3 ops/mac_cmd.py --bildir "git -C ~/jarvis push origin main"
    python3 ops/mac_cmd.py --bekleme 300 --zaman 240 "uzun-komut"

Varsayılan SESSİZ: sonuç bana döner, sahibin telefonu titremez. İz kaybolmaz —
komut audit'e yazılır, tam çıktı veriyolunda durur (`python3 -m ghost.bus oku <id>`).
Mac kapalıysa iş kuyrukta bekler; --bekleme dolunca "kuyrukta" deyip çıkar
(iş İPTAL OLMAZ, Mac uyanınca koşar).

Faz 3.11 (2026-10-30): kuyruk artık ops/mac-queue/<id>.json dosyası DEĞİL, ghost.db olay veriyolu
(ghost/bus.py type=job hedef=mac) — köprünün /mac işiyle aynı sıra; Mac işçisi `mac-claim` ile çeker,
sonucu `mac-bitir` ile `events.sonuc` sütununa yazar. Kimlik events.id.
"""
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPS = os.path.join(BASE, "ops")
AUDIT = os.path.join(OPS, "audit.jsonl")
sys.path.insert(0, BASE)
from ghost import bus  # noqa: E402


def _audit(detay):
    try:
        with open(AUDIT, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%F %T"), "actor": "mac_cmd",
                                "action": "shell", "class": "write",
                                "detail": detay[:300]}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def calistir(cmd, bekleme=180, zaman=120, cwd=None, sessiz=True):
    """Komutu veriyolu kuyruğuna koy (type=job hedef=mac kind=shell), sonucu bekle. (metin, bitti_mi) döner.
    Kimlik SQLite autoincrement — 2026-10-28'deki (Codex bulgu 4) aynı-dilim çakışması yapısal olarak yok."""
    meta = {"cmd": cmd, "timeout": zaman, "sessiz": sessiz, "chat_id": "", "reset": False, "rc": False}
    if cwd:
        meta["cwd"] = cwd
    jid = bus.is_ekle(cmd, kind="shell", hedef="mac", source="mac_cmd", meta=meta)
    _audit("kuyrukta #%d: %s" % (jid, cmd))

    son = time.time() + bekleme
    while time.time() < son:
        d = bus.is_oku(jid)
        if d and d.get("status") in ("done", "failed"):
            metin = d.get("sonuc") or ""
            # Rapor başlığını at, gövdeyi ver
            if "## Sonuç" in metin:
                metin = metin.split("## Sonuç", 1)[1].strip()
            if d.get("status") == "failed" and not metin:
                metin = "(iş failed — Mac işçisi yarıda bıraktı ya da iş geçersizdi)"
            _audit("bitti #%d" % jid)
            return metin, True
        time.sleep(2)
    return ("(Mac cevap vermedi — iş #%d kuyrukta bekliyor, Mac uyanınca "
            "koşacak. Sonuç: python3 -m ghost.bus oku %d)" % (jid, jid)), False


def main(argv):
    bekleme, zaman, cwd, sessiz = 180, 120, None, True
    args = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--bekleme":
            i += 1; bekleme = int(argv[i])
        elif a == "--zaman":
            i += 1; zaman = int(argv[i])
        elif a == "--cwd":
            i += 1; cwd = argv[i]
        elif a == "--bildir":
            sessiz = False
        else:
            args.append(a)
        i += 1
    if not args:
        print(__doc__.strip())
        return 2
    metin, bitti = calistir(" ".join(args), bekleme, zaman, cwd, sessiz)
    print(metin)
    return 0 if bitti else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
