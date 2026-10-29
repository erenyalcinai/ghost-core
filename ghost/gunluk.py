"""Günlük tek yer — journald (Faz 2.9 7b, 2026-10-30; bütünsel denetim 03 §7b).

NEDEN: 24 ayrı `ops/*.log` dosyası (5,5 MB) vardı; her nöbetçinin kendi `log()`'u, selfcheck'in kırpma döngüsü,
"hangi dosyaya bakacağım" sorusu. journald zaten var (kalıcı, /var/log/journal, ghost `systemd-journal` grubunda):
systemd birimlerinin stdout/stderr'i oraya düşer, cron işleri `logger` ile oraya yazabilir.

TASARIM: `gunluk.al("<ad>")` standart `logging.Logger` döndürür; VPS'te /dev/log (journald) üzerinden
SYSLOG_IDENTIFIER=`ghost-<ad>` ile yazar → `journalctl -t ghost-<ad>` (ve birimden koşuyorsa `-u ghost-<birim>`)
tek yer. Mac'te (journald yok) `~/.ghost/log/<ad>.log` dosyasına düşer. Elle koşumda (tty) satır ekrana da gider.
GHOST_TEST=1 → hiçbir yere yazmaz, `KAYITLAR` listesinde birikir (testler okur). Nöbetçiler `def log(msg):
gunluk.yaz("<ad>", msg)` ince sarmalayıcısıyla geçer — çağıran kod değişmez.
OKUMA: `gunluk.oku("<ad>", n=200, since="2 hours ago")` journalctl'den satır listesi; CLI `python3 -m ghost.gunluk
oku <ad> [n] [--since "..."] | adlar`. Kapı: journalctl salt-okunur, serbest.
KALAN dosyalar (bilinçli): ops/bridge.log (köprü, korunan — yama kartla), ops/kokpit_gunluk.log (cihaz teşhis
yüklemesi, veri), ops/guvenlik_kapisi.log (root kurulu kapı, kendi günlüğü), Mac tarafı betikler (launchd günlükleri).
"""
import logging
import logging.handlers
import os
import subprocess
import sys

ONEK = "ghost-"
KAYITLAR = []            # GHOST_TEST kipi: (ad, mesaj)
_LOGGERS = {}
_ADLAR = set()


def _test_kipi():
    return os.environ.get("GHOST_TEST") == "1"


def journald_var():
    return sys.platform != "darwin" and os.path.exists("/dev/log")


def dosya_yolu(ad):
    return os.path.join(os.path.expanduser("~"), ".ghost", "log", "%s.log" % ad)


class _ListeHandler(logging.Handler):
    def __init__(self, ad):
        super().__init__()
        self.ad = ad

    def emit(self, record):
        KAYITLAR.append((self.ad, record.getMessage()))


def _ad_temizle(ad):
    ad = "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in str(ad or "ghost").strip())
    return ad or "ghost"


def al(ad):
    """Adlı günlükçü (tek sefer kurulur). Yazım: al('uptime').info('…') ya da yaz('uptime', '…')."""
    ad = _ad_temizle(ad)
    lg = _LOGGERS.get(ad)
    if lg is not None:
        return lg
    lg = logging.getLogger("ghost.gunluk." + ad)
    lg.setLevel(logging.INFO)
    lg.propagate = False
    lg.handlers.clear()
    if _test_kipi():
        lg.addHandler(_ListeHandler(ad))
    elif journald_var():
        try:
            h = logging.handlers.SysLogHandler(address="/dev/log")
            h.ident = "%s%s: " % (ONEK, ad)
            h.setFormatter(logging.Formatter("%(message)s"))
            lg.addHandler(h)
        except OSError:
            lg.addHandler(_dosya_handler(ad))
    else:
        lg.addHandler(_dosya_handler(ad))
    if not _test_kipi():
        try:
            tty = sys.stderr.isatty()
        except (AttributeError, ValueError):
            tty = False
        if tty:
            e = logging.StreamHandler(sys.stderr)
            e.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%F %T"))
            lg.addHandler(e)
    _LOGGERS[ad] = lg
    _ADLAR.add(ad)
    return lg


def _dosya_handler(ad):
    yol = dosya_yolu(ad)
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    h = logging.FileHandler(yol, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%F %T"))
    return h


def yaz(ad, mesaj, seviye=logging.INFO):
    """İnce yol: nöbetçinin `log(msg)` sarmalayıcısı bunu çağırır. Asla istisna fırlatmaz."""
    try:
        al(ad).log(seviye, str(mesaj))
    except Exception:
        pass


def oku(ad, n=200, since=None, birim=False):
    """journalctl'den son n satır (yeni en altta). ad: 'uptime' → -t ghost-uptime; birim=True → -u ghost-<ad>.
    Mac/dosya kipinde dosyanın son n satırı. Hata → []."""
    ad = _ad_temizle(ad)
    if _test_kipi():
        return [m for a, m in KAYITLAR if a == ad][-int(n):]
    if not journald_var():
        try:
            with open(dosya_yolu(ad), encoding="utf-8", errors="replace") as f:
                return [s.rstrip("\n") for s in f.readlines()[-int(n):]]
        except OSError:
            return []
    cmd = ["journalctl", "-u" if birim else "-t", ONEK + ad, "-n", str(int(n)), "--no-pager", "-o", "short-iso"]
    if since:
        cmd += ["--since", str(since)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    return [s for s in r.stdout.splitlines() if s and not s.startswith("-- ")]


def adlar():
    """Bu süreçte açılmış günlük adları + journald'da görülen ghost-* kimlikleri."""
    out = set(_ADLAR)
    if journald_var() and not _test_kipi():
        try:
            r = subprocess.run(["journalctl", "-F", "SYSLOG_IDENTIFIER", "--no-pager"], capture_output=True, text=True, timeout=20)
            out |= {s[len(ONEK):] for s in r.stdout.split() if s.startswith(ONEK)}
        except (OSError, subprocess.SubprocessError):
            pass
    return sorted(out)


def _main(argv):
    argv = list(argv)
    if not argv or argv[0] in ("-h", "--help"):
        print('python3 -m ghost.gunluk oku <ad> [n] [--since "2 hours ago"] [--birim] | adlar | yaz <ad> "<mesaj>"')
        return 0
    cmd, args = argv[0], argv[1:]
    if cmd == "oku":
        since = None
        if "--since" in args:
            i = args.index("--since")
            since = args[i + 1] if i + 1 < len(args) else None
            del args[i:i + 2]
        birim = "--birim" in args
        args = [a for a in args if a != "--birim"]
        if not args:
            print("kullanım: oku <ad> [n]")
            return 2
        print("\n".join(oku(args[0], int(args[1]) if len(args) > 1 else 200, since=since, birim=birim)) or "(kayıt yok)")
    elif cmd == "adlar":
        print("\n".join(adlar()) or "(yok)")
    elif cmd == "yaz" and len(args) >= 2:
        yaz(args[0], " ".join(args[1:]))
        print("yazıldı: %s%s" % (ONEK, _ad_temizle(args[0])))
    else:
        print("bilinmeyen komut: %s" % cmd)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
