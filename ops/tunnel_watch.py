#!/usr/bin/env python3
"""Ters tünel nöbetçisi — yarı-açık (zombi) SSH oturumunu bulup düşürür.

ARIZA (2026-09-01'de yaşandı, bkz. hafıza [[ters-tunel-zombi-port]]):
Mac uyuduğunda/ağ değiştirdiğinde ters tünelin VPS ucu yarı-açık kalır. Portlar
`ss -tln`de LISTEN görünmeye devam eder ama veri akmaz; Mac'in launchd'ı yeni
tüneli kurar, `-R` portlarını BIND EDEMEZ (tunnel.err'e "remote port forwarding
failed" yazar). Sonuç: Mac ve telefon rayı ölü.

NEDEN KENDİLİĞİNDEN DÜZELMİYOR: VPS'in sshd'sinde `ClientAliveInterval` yok
(varsayılan 0 = kapalı), yani ölü eşi hiç sormuyor. Toplanması yalnız çekirdeğin
TCP keepalive'ına kalıyor: 7200 + 75×9 = 131 dakika. Kökten çözüm root ister
(`/etc/ssh/sshd_config.d/` + ClientAliveInterval 30) — bu betik o gelene kadar
yerini doldurur, geldikten sonra da artık riski toplar.

AYIRT ETME (bu betiğin kalbi): "port dinlemiyor" ile "dinliyor ama susuyor"
AYNI ŞEY DEĞİL. Birincisi Mac kapalı demektir, normaldir, dokunulmaz. Yalnız
ikincisi zombidir. LISTEN görünmesi canlılık kanıtı DEĞİLDİR — soketi VPS'in
kendi sshd'si tutar.

GÜVENLİK: yalnız tty'siz oturumlar aday. sahibin kendi interaktif SSH'ı
tty'li olduğu için (`ghost@pts/N`) bu süzgece TAKILMAZ. Kendi atalarımızı da
dışlarız. Mac'te iş varken hiç dokunmaz.

ADAY DÜZELTMESİ (2026-09-06): `ssh -N` ile kurulan tünelin sshd süreci
`sshd-session: ghost` diye görünür — "@notty" EKİ YOK (notty, kanal açmış ama
tty almamış oturumdur: Mac işçisinin ControlMaster'ı). Betik 2026-09-05 gecesi
17 zombi turunda hep işçinin oturumunu düşürdü, tüneli hiç bulamadı
("düşürüldü ama ray dönmedi"). Şimdi önce çıplak oturumlar (tünel), sonra
notty'ler aday; sıralama yaşlıdan gence.
"""
import glob
import json
import os
import socket
import subprocess
import sys
import time

OPS = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(OPS)
LOG_FILE = os.path.join(OPS, "tunnel_watch.log")
STATE_FILE = os.path.join(OPS, "tunnel_watch_state.json")
QUEUE = os.path.join(OPS, "mac-queue")

PORT = 8765          # maclink — birincil sağlık göstergesi
PORT_WDA = 8101      # telefon rayı; aynı tünelden geçer
PROBE_TIMEOUT = 5.0  # bu sürede tek bayt gelmezse "susuyor" (timer 1 dk, 2026-09-06)
MIN_AGE = 60         # bu yaştan genç oturumu öldürme (taze tünel olabilir)
MAX_KILL = 3         # bir turda en fazla bu kadar oturum düşür
SETTLE = 4.0         # öldürdükten sonra tünelin yeniden kurulmasını bekle


def log(msg):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write("%s %s\n" % (time.strftime("%F %T"), msg))


def telegram(msg):
    try:
        subprocess.run([sys.executable, os.path.join(OPS, "send_telegram.py")],
                       input=msg, text=True, timeout=30,
                       capture_output=True)
    except Exception as e:
        log("telegram hata: %r" % e)


def dinliyor_mu(port):
    """Soket LISTEN durumunda mı? (/proc/net/tcp, 127.0.0.1, state 0A)"""
    hedef = "0100007F:%04X" % port
    try:
        with open("/proc/net/tcp", encoding="utf-8") as f:
            for satir in f.read().splitlines()[1:]:
                p = satir.split()
                if len(p) > 3 and p[1].upper() == hedef and p[3] == "0A":
                    return True
    except Exception as e:
        log("dinleme kontrolü hata: %r" % e)
    return False


def cevap_veriyor_mu(port):
    """Bağlanıp tek bayt bekle. Herhangi bir HTTP cevabı sağlıktır —
    403/404 de olur (token yok / yol yok); önemli olan AKIŞ."""
    s = socket.socket()
    s.settimeout(PROBE_TIMEOUT)
    try:
        s.connect(("127.0.0.1", port))
        s.sendall(b"GET /durum HTTP/1.0\r\n\r\n")
        return bool(s.recv(16))
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def atalar():
    """Kendi süreç zincirimiz — sakın onları öldürme."""
    zincir, pid = set(), os.getpid()
    while pid and pid > 1:
        zincir.add(pid)
        try:
            with open("/proc/%d/status" % pid, encoding="utf-8") as f:
                for satir in f:
                    if satir.startswith("PPid:"):
                        pid = int(satir.split()[1])
                        break
                else:
                    break
        except Exception:
            break
    return zincir


def _tunel_mi(cmd):
    """`sshd-session: ghost` (ek yok) → -N tüneli. `@notty` → işçi. `@pts` → insan."""
    kuyruk = cmd.split("sshd-session:", 1)[1].strip()
    return "@" not in kuyruk


def adaylar():
    """Yaşlıdan gence sıralı tty'siz sshd oturumları — önce tünel (-N), sonra notty."""
    korunan = atalar()
    out = subprocess.run(["ps", "-eo", "pid,etimes,cmd"],
                         capture_output=True, text=True, timeout=20).stdout
    liste = []
    for satir in out.splitlines()[1:]:
        p = satir.split(None, 2)
        if len(p) < 3:
            continue
        pid, yas, cmd = int(p[0]), int(p[1]), p[2]
        # tty'siz oturumlar: çıplak (tünel) ya da @notty (işçi). @pts (insan) asla.
        if "sshd-session:" in cmd and "@pts" not in cmd and "[priv]" not in cmd:
            if pid not in korunan and yas >= MIN_AGE:
                liste.append((1 if _tunel_mi(cmd) else 0, yas, pid, cmd.strip()))
    liste.sort(reverse=True)  # önce tünel, sonra en yaşlı
    return [(yas, pid, cmd) for _, yas, pid, cmd in liste]


def mac_isi_var_mi():
    """Kuyrukta bekleyen/koşan iş varsa oturumlara dokunma."""
    return bool(glob.glob(os.path.join(QUEUE, "*.json")))


def durum_yaz(durum, detay=""):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"ts": int(time.time()), "at": time.strftime("%F %T"),
                       "durum": durum, "detay": detay}, f, ensure_ascii=False)
    except Exception as e:
        log("durum yazma hata: %r" % e)


def onceki_durum():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f).get("durum", "")
    except Exception:
        return ""


def main():
    onceki = onceki_durum()

    if not dinliyor_mu(PORT):
        # Tünel hiç kurulmamış — Mac kapalı olabilir. Bu ARIZA DEĞİL, sessiz kal.
        durum_yaz("tunel-yok")
        return 0

    if cevap_veriyor_mu(PORT):
        if onceki not in ("saglikli", ""):
            log("ray kendiliğinden döndü (%s -> saglikli)" % onceki)
        durum_yaz("saglikli")
        return 0

    # Dinliyor ama susuyor => ZOMBİ.
    log("ZOMBİ: %d dinliyor ama %.0f sn'de cevap yok" % (PORT, PROBE_TIMEOUT))

    if mac_isi_var_mi():
        log("kuyrukta iş var — bu tur dokunulmadı")
        durum_yaz("zombi-beklemede", "kuyrukta iş var")
        return 0

    liste = adaylar()
    if not liste:
        log("aday notty oturum yok — düşürecek bir şey bulunamadı")
        durum_yaz("zombi-cozulemedi", "aday yok")
        if onceki != "zombi-cozulemedi":
            telegram("🔌 Ters tünel ölü: %d dinliyor ama cevap vermiyor ve "
                     "düşürülecek notty oturum bulunamadı. Mac ve telefon rayı "
                     "şu an erişilemez." % PORT)
        return 1

    for sira, (yas, pid, cmd) in enumerate(liste[:MAX_KILL], 1):
        log("oturum düşürülüyor (%d/%d): pid=%d yaş=%ds" %
            (sira, min(MAX_KILL, len(liste)), pid, yas))
        try:
            os.kill(pid, 15)
        except ProcessLookupError:
            continue
        except PermissionError:
            log("pid %d öldürülemedi (yetki)" % pid)
            continue
        time.sleep(SETTLE)
        if _tunel_mi(cmd) and not cevap_veriyor_mu(PORT):
            # Tünel oturumu düştü; Mac'in launchd'ı yeni tüneli en erken 30 sn'de
            # (ThrottleInterval) kurar — 4 sn'de dönmemesi normal. İşçinin notty
            # oturumunu da üstüne kesme (2026-09-06 01:51'de kesti, kuyruk hattı
            # boşuna düştü). Sonucu sonraki tur (2 dk) doğrular.
            log("tünel oturumu düşürüldü (pid %d); yeniden kurulmasını sonraki tura bıraktım" % pid)
            durum_yaz("zombi-tunel-dusuruldu", "pid %d, launchd 30 sn" % pid)
            return 0
        if cevap_veriyor_mu(PORT):
            wda = "açık" if cevap_veriyor_mu(PORT_WDA) else "KAPALI"
            log("RAY DÖNDÜ (pid %d düşürüldükten sonra), 8101 %s" % (pid, wda))
            durum_yaz("saglikli", "zombi pid %d temizlendi" % pid)
            telegram("🔧 Ters tünel zombi oturumu temizlendi (pid %d, yaş %d "
                     "sn). Mac rayı geri döndü, telefon (8101) %s." %
                     (pid, yas, wda))
            return 0

    log("düşürüldü ama ray dönmedi")
    durum_yaz("zombi-cozulemedi", "%d oturum düşürüldü, düzelmedi" %
              min(MAX_KILL, len(liste)))
    if onceki != "zombi-cozulemedi":
        telegram("🔌 Ters tünel ölü ve kendiliğinden düzelmedi: %d oturum "
                 "düşürüldü, %d hâlâ cevap vermiyor. Mac/telefon rayı kapalı — "
                 "Mac'te `ai.jarvis.tunnel` durumuna bakmak gerekebilir."
                 % (min(MAX_KILL, len(liste)), PORT))
    return 1


if __name__ == "__main__":
    sys.exit(main())
