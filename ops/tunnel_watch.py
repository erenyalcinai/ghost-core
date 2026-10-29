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


EREN_ESIK = 3        # bu kadar ARDIŞIK "düşürüldü ama ray dönmedi" turu → sahibe (6 sa soğuma)


def telegram(msg, sinif="ghost", anahtar="zombi", soguma_sn=6 * 3600):
    """Alarm kapısından geçer (ghost/alarm.py, 2026-10-29). NEDEN: 30 günde 16 "🔌 ters tünel"
    mesajı sahibe gitti; zombi düşürme ve "ray döndü" Ghost'un kendi işidir, sahibin eli yok.
    Varsayılan sınıf GHOST (Nabız'da sarı); yalnız EREN_ESIK ardışık "düşürüldü ama ray dönmedi"
    turu sahibe gider (Mac'te `ai.jarvis.tunnel`e bakması gerekebilir). Eskisi: send_telegram.py."""
    try:
        from ghost import alarm
        alarm.bildir("tunnel", msg, sinif=sinif, anahtar=anahtar, soguma_sn=soguma_sn)
    except Exception as e:
        log("alarm hata: %r" % e)


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


def durum_yaz(durum, detay="", ardisik=0):
    """`ardisik`: kaç turdur üst üste "zombi-cozulemedi" (Sahip eşiği için, 2026-10-29)."""
    try:
        from ghost import durum as _durum          # Faz 1.9: durum tablosu `tunnel_watch`
        _durum.yaz("tunnel_watch", {"ts": int(time.time()), "at": time.strftime("%F %T"),
                                    "durum": durum, "detay": detay, "ardisik": ardisik})
    except Exception as e:
        log("durum yazma hata: %r" % e)


def _onceki():
    try:
        from ghost import durum as _durum
        d = _durum.tasi("tunnel_watch", STATE_FILE, {})   # eski json varsa bir kez içeri alınır
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def onceki_durum():
    return _onceki().get("durum", "")


def ardisik_say(onceki, yeni_durum):
    """Saf: önceki durum kaydı + bu turun durumu → ardışık çözülemedi sayısı.
    Zincir yalnız "zombi-cozulemedi" turlarıyla uzar; araya giren her başka durum sıfırlar."""
    if yeni_durum != "zombi-cozulemedi":
        return 0
    if onceki.get("durum") != "zombi-cozulemedi":
        return 1
    try:
        return int(onceki.get("ardisik", 1)) + 1
    except (TypeError, ValueError):
        return 1


def cozulemedi_sinifi(ardisik, esik=EREN_ESIK):
    """Saf: ardışık çözülemedi sayısı → ("ghost"|"sahip", anahtar). İlk turlar Ghost'ta kalır;
    esik'e ulaşınca sahibe (alarm.py soğuması 6 sa aynı anahtarı tekrar göndermez)."""
    return ("sahip", "zombi-kalici") if ardisik >= esik else ("ghost", "zombi-cozulemedi")


def main():
    onceki_kayit = _onceki()
    onceki = onceki_kayit.get("durum", "")

    if not dinliyor_mu(PORT):
        # Tünel hiç kurulmamış — Mac kapalı olabilir. Bu ARIZA DEĞİL, sessiz kal.
        durum_yaz("tunel-yok")
        return 0

    if cevap_veriyor_mu(PORT):
        if onceki not in ("saglikli", ""):
            log("ray kendiliğinden döndü (%s -> saglikli)" % onceki)
            _alarm_kapat()
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
        ardisik = ardisik_say(onceki_kayit, "zombi-cozulemedi")
        durum_yaz("zombi-cozulemedi", "aday yok", ardisik)
        sinif, anahtar = cozulemedi_sinifi(ardisik)
        telegram("🔌 Ters tünel ölü: %d dinliyor ama cevap vermiyor ve "
                 "düşürülecek notty oturum bulunamadı (%d. tur). Mac ve telefon rayı "
                 "şu an erişilemez." % (PORT, ardisik), sinif=sinif, anahtar=anahtar)
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
            _alarm_kapat()      # çözüldü: Nabız'daki sarı düşer; sahibe mesaj YOK (2026-10-29,
            return 0            # eskiden "🔧 zombi temizlendi" Telegram'a gidiyordu — Ghost'un işi)

    log("düşürüldü ama ray dönmedi")
    ardisik = ardisik_say(onceki_kayit, "zombi-cozulemedi")
    durum_yaz("zombi-cozulemedi", "%d oturum düşürüldü, düzelmedi" %
              min(MAX_KILL, len(liste)), ardisik)
    sinif, anahtar = cozulemedi_sinifi(ardisik)
    telegram("🔌 Ters tünel ölü ve kendiliğinden düzelmedi (%d. ardışık tur): %d oturum "
             "düşürüldü, %d hâlâ cevap vermiyor. Mac/telefon rayı kapalı — "
             "Mac'te `ai.jarvis.tunnel` durumuna bakmak gerekebilir."
             % (ardisik, min(MAX_KILL, len(liste)), PORT), sinif=sinif, anahtar=anahtar)
    return 1


def _alarm_kapat():
    """Ray döndü: açık tünel alarmları Nabız'dan düşer."""
    try:
        from ghost import alarm
        for a in ("zombi", "zombi-cozulemedi", "zombi-kalici"):
            alarm.kapat("tunnel", a)
    except Exception as e:
        log("alarm kapatma hata: %r" % e)


if __name__ == "__main__":
    sys.exit(main())
