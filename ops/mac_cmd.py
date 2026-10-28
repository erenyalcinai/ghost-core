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
komut audit'e yazılır, tam çıktı ops/mac-queue/done/<id>.sonuc.md'de durur.
Mac kapalıysa iş kuyrukta bekler; --bekleme dolunca "kuyrukta" deyip çıkar
(iş İPTAL OLMAZ, Mac uyanınca koşar).
"""
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPS = os.path.join(BASE, "ops")
QUEUE = os.path.join(OPS, "mac-queue")
AUDIT = os.path.join(OPS, "audit.jsonl")


def _audit(detay):
    try:
        with open(AUDIT, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%F %T"), "actor": "mac_cmd",
                                "action": "shell", "class": "write",
                                "detail": detay[:300]}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def calistir(cmd, bekleme=180, zaman=120, cwd=None, sessiz=True):
    """Komutu kuyruğa koy, sonucu bekle. (metin, bitti_mi) döner."""
    # 2026-10-28 (Codex bulgu 4): kimlik 100 ms'lik zamandan geliyordu, aynı dilimdeki iki çağrı aynı dosyayı
    # 'w' ile açıp birbirini eziyordu (veri kaybı). Şimdi: ms çözünürlük, dosya önce geçici ada yazılır,
    # hedefe `link` ile atomik ve TEK SAHİPLİ taşınır (hedef varsa kimlik bir artar). İşçi yalnız
    # [0-9]*.json bekler, yarım yazılmış dosya hiç görmez.
    os.makedirs(QUEUE, exist_ok=True)
    jid = int(time.time() * 1000) % 2147483647
    job = {"id": jid, "kind": "shell", "cmd": cmd, "timeout": zaman,
           "sessiz": sessiz, "prompt": cmd, "chat_id": "",
           "ts": time.strftime("%F %T"), "reset": False, "rc": False}
    if cwd:
        job["cwd"] = cwd
    gecici = os.path.join(QUEUE, ".%d-%d.json.tmp" % (jid, os.getpid()))
    while True:
        job["id"] = jid
        with open(gecici, "w", encoding="utf-8") as f:
            json.dump(job, f, ensure_ascii=False)
        try:
            os.link(gecici, os.path.join(QUEUE, "%d.json" % jid))
            break
        except FileExistsError:
            jid += 1
        finally:
            try:
                os.unlink(gecici)
            except OSError:
                pass
    _audit("kuyrukta #%d: %s" % (jid, cmd))

    sonuc = os.path.join(QUEUE, "done", "%d.sonuc.md" % jid)
    son = time.time() + bekleme
    while time.time() < son:
        if os.path.exists(sonuc):
            metin = open(sonuc, encoding="utf-8", errors="replace").read()
            # Rapor başlığını at, gövdeyi ver
            if "## Sonuç" in metin:
                metin = metin.split("## Sonuç", 1)[1].strip()
            _audit("bitti #%d" % jid)
            return metin, True
        time.sleep(2)
    return ("(Mac cevap vermedi — iş #%d kuyrukta bekliyor, Mac uyanınca "
            "koşacak. Sonuç: ops/mac-queue/done/%d.sonuc.md)" % (jid, jid)), False


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
