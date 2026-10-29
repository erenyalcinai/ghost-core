#!/usr/bin/env python3
"""Ghost nöbetçisi: DIŞARIYA açılmış portları yoklar.

Neden var (tarihçe): 2026-08-19'da VPS'te güvenlik duvarı YOKTU ve
`python3 -m http.server` varsayılan
olarak 0.0.0.0'a bağlanır. Yani "geçici yerel sunucu" diye açılan her şey
anında internete açılır. 2026-08-19'da bir önizleme sunucusu (port 8898)
`marka/maskot/` dizinini ~11 saat halka açık sundu; kimse fark etmedi, çünkü
bunu izleyen bir şey yoktu. Bu dosya o boşluğu kapatır.

GÜNCEL (2026-09-02, root'tan `ufw status verbose` ile doğrulandı): ufw ARTIK
AKTİF — default deny, 22 açık, 80/443 yalnız Cloudflare aralıkları. Bu nöbetçi
ikinci katman olarak kalır (ufw kapanırsa/kural bozulursa yine yakalar).

KAPSAM: yalnız TESPİT + DAR bir otomatik kapatma. Güvenlik duvarı kurmak root
ister ve Ghost'ta sudo yok — ama açık portu KAPATMAK için root gerekmiyor,
çünkü sızan sunucular zaten `ghost` kullanıcısının kendi süreçleri.

OTOMATİK KAPATMA yalnızca ispatlanabilir güvenli sınıfta yapılır:
sahibi `ghost` VE komut satırı `http.server` kalıbına uyuyor. Bunun dışındaki
her şey (root süreçleri, tanımadığım servisler) SADECE bildirilir — yanlış
süreç öldürmek, açık porttan daha pahalıya patlar.

Sessizlik = her şey yolunda. Yalnız DEĞİŞİMDE konuşur.
"""
import json
import os
import re
import signal
import subprocess
import time

import heartbeat   # aynı dizin (ops) — sys.path[0] betiğin dizini

# Dışarıya açık olması MEŞRU olan portlar. Buraya port eklemek = "bu port
# internete açık kalsın" demek; kanıtsız ekleme.
IZINLI = {
    22,    # SSH — fail2ban aktif
    80,    # web (Cloudflare üzerinden)
    443,   # web/TLS
}

# Otomatik kapatılabilir süreç kalıbı. DAR tutuldu: bugüne kadar sızan tek
# şey buydu ve zararsızca öldürülebilir (durum tutmaz, veri yazmaz).
OTO_KAPAT = re.compile(r"python3?\s+-m\s+http\.server|SimpleHTTPServer")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPS = os.path.join(BASE, "ops")
STATE_FILE = os.path.join(OPS, "port_state.json")
LOG_FILE = os.path.join(OPS, "port_watch.log")

ENV = {}
with (open(os.path.join(BASE, ".env"), encoding="utf-8") if os.path.exists(os.path.join(BASE, ".env")) else open(os.devnull)) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            ENV[k] = v.strip()

from ghost import durum, notify  # noqa: E402  durum tablosu (Faz 1.9); Telegram tek yerde (Faz 2.9)


def log(msg):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(time.strftime("%F %T ") + msg + "\n")


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


def disa_acik(adres):
    """'0.0.0.0:8898' / '[::]:22' / '127.0.0.1:8787' → dışarıya açık mı?

    Loopback (127.x, [::1]) ve link-local dışındaki her bağlanma dışarıdan
    erişilebilir demektir — güvenlik duvarı olmadığı için istisna yok.
    """
    ip, _, port = adres.rpartition(":")
    ip = ip.strip("[]")
    if ip.startswith("127.") or ip in ("::1", "localhost"):
        return None
    if ip.startswith("fe80:") or "%lo" in adres:
        return None
    try:
        return int(port)
    except ValueError:
        return None


def dinleyenler():
    """[(port, pid, kullanıcı, komut)] — dışarıya açık TCP dinleyicileri."""
    try:
        out = subprocess.run(["ss", "-tlnpH"], capture_output=True,
                             text=True, timeout=15).stdout
    except Exception as e:
        log("ss çalıştırılamadı: %r" % e)
        return None                      # None = ÖLÇEMEDİM (boş liste değil)

    bulunan = []
    for satir in out.splitlines():
        alan = satir.split()
        if len(alan) < 4:
            continue
        port = disa_acik(alan[3])
        if port is None or port in IZINLI:
            continue
        m = re.search(r"pid=(\d+)", satir)
        pid = int(m.group(1)) if m else None
        kullanici, komut = "?", "?"
        if pid:
            try:
                r = subprocess.run(["ps", "-o", "user=,args=", "-p", str(pid)],
                                   capture_output=True, text=True, timeout=10)
                parca = r.stdout.strip().split(None, 1)
                if len(parca) == 2:
                    kullanici, komut = parca[0], parca[1]
            except Exception:
                pass
        bulunan.append((port, pid, kullanici, komut))
    return bulunan


def kapat(pid):
    """Süreci nazikçe durdur; inatçıysa zorla. Yalnız kendi süreçlerimizde."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    for _ in range(10):
        time.sleep(0.3)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
    try:
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.5)
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except Exception:
        pass
    return False


def main():
    try:
        onceki = set(durum.tasi("port_watch", STATE_FILE, []) or [])   # Faz 1.9: durum tablosu
    except Exception:
        onceki = set()

    heartbeat.beat("port_watch")
    # Karşılıklı nöbet: bu nöbetçi de sessiz kalırsa selfcheck haber verir.
    haber = heartbeat.report(["selfcheck"])

    acik = dinleyenler()
    if acik is None:
        # Ölçemedim: durumu EZME. Boş liste yazsaydık gerçek bir sızıntı
        # "kapandı" sanılır ve sonraki turda yeniden alarm üretmezdi.
        log("ss okunamadı — durum korundu")
        if haber:
            tg_send("👻 Ghost port nöbetçisi:\n" + "\n".join(haber))
        return

    simdiki = set()
    for port, pid, kullanici, komut in acik:
        simdiki.add(port)
        if port in onceki:
            continue                     # zaten bildirildi, tekrar bağırma

        kisa = (komut[:90] + "…") if len(komut) > 90 else komut
        if kullanici == "ghost" and OTO_KAPAT.search(komut or ""):
            if kapat(pid):
                haber.append("🔒 port %d İNTERNETE AÇIKTI — kapatıldı\n   %s"
                             % (port, kisa))
                simdiki.discard(port)    # kapandı; durum dosyasına yazma
                log("kapatıldı: port=%d pid=%s cmd=%s" % (port, pid, kisa))
            else:
                haber.append("🔴 port %d AÇIK — kapatamadım (pid %s)\n   %s"
                             % (port, pid, kisa))
        else:
            haber.append("🔴 port %d İNTERNETE AÇIK (sahip: %s)\n   %s\n"
                         "   otomatik kapatmadım — tanımadığım süreç, kararı sen ver."
                         % (port, kullanici, kisa))

    for port in sorted(onceki - simdiki):
        haber.append("🟢 port %d artık kapalı" % port)

    # Önce İLET, sonra kaydet: mesaj gitmezse durum eskisinde kalır ve
    # sonraki turda değişim yeniden algılanır (uyarı kaybolmaz).
    if haber:
        msg = "👻 Ghost port nöbetçisi:\n" + "\n".join(haber)
        log(msg.replace("\n", " | "))
        try:
            tg_send(msg)
        except Exception as e:
            log("telegram hata: %r — durum kaydedilmedi, sonraki tur yeniden dener" % e)
            return
    else:
        log("sessiz: dışarıya açık beklenmedik port yok")

    durum.yaz("port_watch", sorted(simdiki))


if __name__ == "__main__":
    main()
