#!/usr/bin/env python3
"""Ghost hafıza yedeği — şifreli anlık görüntü, ornek-org/jarvis 'hafiza' dalına.

NEDEN: kod GitHub'da güvende ama hafıza değildi. ghost.db + audit + posta +
sırlar yalnızca VPS diskindeydi (ops/backups/ aynı diskte). Disk giderse Ghost
kod olarak dirilir, hatıra olarak ölür.

TASARIM KARARLARI:
- Ayrı repo AÇILAMADI: PAT'te 'Administration: write' yok. Bunun yerine aynı
  özel repoda YÖRÜNGE (orphan) dalı: kod geçmişiyle ortak atası yok, main'i
  hiç kirletmez.
- Her koşu dalı TEK COMMIT olarak yeniden yazar (force-push). Böylece repo
  saklama setinden (7+4+12) büyük olamaz; şifreli bloblar delta sıkışmadığı
  için normal geçmiş bırakılsaydı yılda ~2 GB olurdu.
- SQLite dosyaları .backup() ile kopyalanır — canlı DB'yi cp'lemek yırtık
  kopya üretir.
- Şifre GHOST_BACKUP_PASS'ten gelir. Tek gerçek kopya Sahip'de olmalı: şifre
  yalnız bu makinede dursaydı, makine ölünce yedek okunamaz hale gelirdi —
  yani tam ihtiyaç anında işe yaramazdı.
"""
import glob
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import date, datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPS = os.path.join(BASE, "ops")
REPO = os.environ.get("GHOST_BACKUP_REPO", "ornek-org/jarvis")
BRANCH = "hafiza"
WORK = os.path.join(OPS, "backups", ".repo")
# Geçici arşiv DİSKTE kurulur: /tmp tmpfs=RAM + usrquota — 2026-11-09 sabahı 140 MB arşiv gpg aşamasında "Disk quota
# exceeded" ile düştü (prova aynı sebeple 2026-10-05'te diske taşınmıştı). ops/backups /dev/sda1'de, 13 GB boş.
TMP_KOK = os.path.join(OPS, "backups", ".tmp")

# Saklama: günlük 7, haftalık 4 (pazartesi), aylık 12 (ayın 1'i)
KEEP = {"gunluk": 7, "haftalik": 4, "aylik": 12}

# SQLite — canlı, .backup() ile alınır
DBS = ["ops/ghost.db", "ops/economy.db"]

# Düz dosyalar. marka/ dışarıda: 61 MB'ın çoğu video karesi/ses, yeniden üretilir.
FILES = [
    "ops/audit.jsonl", "ops/email_spool.jsonl", "ops/email_seen.json",
    "ops/owner.json", "ops/payout.json", "ops/pending.json",
    "ops/baton.json",       # biz_state/port_state/heartbeats 2026-10-29'dan beri ghost.db `durum` tablosunda (DBS)
    "ops/uptime_state.json", "ops/offset.txt", "ops/chat_session.json",
    "secrets.env",          # anahtarların çoğu tek kopya; şifreli gider
    # 2026-11-04 (felaket kurtarma, Sahip "kalan gerekenleri yap"): sunucu giderse kaybolanlar — küçük, şifreli gider
    ".env",                 # WEBHOOK_SECRET vb. (Email Worker + Kısayollar aynı değeri bekler)
    "ops/sahne.json", "ops/sahne_gecmis.json", "ops/sahne_onaylar.json", "ops/onay_sonuc.jsonl",
    "ops/apns.jsonl", "ops/metrik.jsonl", "ops/verify.jsonl", "ops/kurallar_durum.json",
]
FILE_DESENLERI = ["mcp*.json"]   # MCP ayarları (sentry token'ı yalnız burada); yenisi eklenince kendiliğinden girer
DIRS = ["intel", "dossier", "ops/requests", "ops/jobs",
        "ops/masa_dosya",   # Masa kalıcı: sahibin yüklediği foto/belge tek kopyaydı (2026-10-04)
        "ops/kokpit_paylasim", "ops/gmail_taslak"]   # paylaşım uzantısıyla sahibin gönderdiği (9 MB) + mail taslakları
# Ev dizini (BASE dışı) — arşivde `ev/<yol>` altına girer, geri yüklemede ~/<yol>'a döner. ASLA stage dışına yazılmaz.
# kokpit_medya (178 MB sohbet medyası, çoğu Ghost'un ürettiği kopya; kalıcı olan Masa'da) bilerek dışarıda: yedeği 2,4 katına çıkarır.
EV_KOK = os.path.expanduser("~")
EV_DIRS = [".secrets",                                   # ASC .p8 Apple'dan yeniden İNDİRİLEMEZ, GSC servis hesabı vb.
           ".claude/projects/-home-ghost-jarvis/memory",  # harness hafızası: Sahip kararları (kanonik), 924 KB
           "site-tools"]                                # example.com deploy'unun purify.py'si (repoda yok)
import heartbeat  # noqa: E402  Nabız damgası (Faz 2.10; sys.path[0] = ops)



def log(msg):
    print("%s %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def _load_env():
    p = os.path.join(BASE, "secrets.env")
    if not os.path.exists(p):
        return
    for line in open(p, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _gh_token():
    """Önce Ghost'un kendi token'ı (süresiz), yoksa sahibinki. ops/gh_cred.sh ile aynı sıra: yedek rayı dolan bir sırra bağlı
    kalmasın — dolduğu gün yedek SESSİZCE durur, fark edilmesi aylar sürer."""
    _load_env()
    tok = os.environ.get("GHOST_GITHUB_TOKEN", "").strip()
    if tok and tok != "vekil":
        return tok
    try:  # sır vekili (Faz 2): git ~/.gitconfig insteadOf ile vekilden geçer, kimliği vekil ekler
        from ghost import vekil
        if vekil.aktif(None, "GHOST_GITHUB_TOKEN"):
            return None
    except Exception:
        pass
    with open(os.path.join(BASE, "mcp-github.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg["mcpServers"]["github"]["headers"]["Authorization"].split()[1]


def snapshot(stage):
    """Yedeklenecekleri stage/ altına topla. Döner: manifest listesi."""
    man = []
    for rel in DBS:
        src = os.path.join(BASE, rel)
        if not os.path.exists(src):
            continue
        dst = os.path.join(stage, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        # Canlı DB: .backup() tutarlı kopya verir, cp vermez.
        con = sqlite3.connect("file:%s?mode=ro" % src, uri=True)
        out = sqlite3.connect(dst)
        with out:
            con.backup(out)
        out.close()
        con.close()
        man.append((rel, os.path.getsize(dst)))
    desenli = sorted(os.path.relpath(p, BASE) for d in FILE_DESENLERI for p in glob.glob(os.path.join(BASE, d)))
    for rel in list(FILES) + [r for r in desenli if r not in FILES]:
        src = os.path.join(BASE, rel)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(stage, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        man.append((rel, os.path.getsize(dst)))
    for rel in DIRS:
        src = os.path.join(BASE, rel)
        if not os.path.isdir(src):
            continue
        dst = os.path.join(stage, rel)
        shutil.copytree(src, dst, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        n = sum(len(f) for _, _, f in os.walk(dst))
        man.append((rel + "/", n))
    ev_stage = os.path.join(stage, "ev")
    for rel in EV_DIRS:
        # Mutlak/../ yol stage dışına yazdırabilir (os.path.join mutlak ikinci parçada stage'i atar) → reddet.
        if os.path.isabs(rel) or ".." in rel.split("/"):
            continue
        src = os.path.join(EV_KOK, rel)
        if not os.path.isdir(src):
            continue
        dst = os.path.join(ev_stage, rel)
        if not os.path.realpath(dst).startswith(os.path.realpath(stage) + os.sep):
            continue
        shutil.copytree(src, dst, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        n = sum(len(f) for _, _, f in os.walk(dst))
        man.append(("ev/" + rel + "/", n))
    return man


KURULUM_NOTU = """# Tesisat — VPS giderse Ghost'u sıfırdan ayağa kaldırma (kısa reçete)

Kod GitHub'da (ornek-org/jarvis), hafıza bu şifreli arşivde, bu klasör
ÜÇÜNCÜ parça: sunucuyu Ghost'un çalıştığı hâle getiren kablolar.
TAM reçete (komutlar, süreler, doğrulamalar, Mac de yoksa Yol C):
`playbooks/felaket-kurtarma.md` (kaynak: dossier/felaket-kurtarma/).
Güncelleme: 2026-11-04 (ilk sürüm 2026-08-20; 10-28 sır vekili, 10-30
~/.ghost veri dizinleri, kullanıcı birimleri bu notu eskitmişti).

## Önce
- Hetzner snapshot/yedek VARSA ondan geri yükle (≤45 dk), sonra 9-10.
- Sıra: makine → kullanıcı → kod → hafıza → root servisleri (kapı/onay/vekil)
  → sırlar → birimler → dış bağlar → KÖPRÜ EN SON.
- Sır değeri ekrana/sohbete basılmaz; dosyadan dosyaya akar.

## Sıra (Yol B, Mac var: ≈3-3,5 sa; Mac root yedeği yoksa ≈4-5 sa)
1. Sunucu (Sahip·panel): fsn1, Ubuntu, ≥4 GB, Backups AÇIK; root anahtarı
   ghost_root_sk (YubiKey). Eski IP korunabiliyorsa onu ata.
2. Temel sistem (root): `paketler/apt-manual.txt` → `apt install`; Node 22;
   kullanıcı `ghost` uid 1000 + linger (`loginctl enable-linger ghost`);
   swap 2 GB; `conf/etc/...` altındaki sshd/needrestart/fail2ban/claude-code
   dosyalarını aynı yola koy; ufw: 22 + 80/443 yalnız Cloudflare.
3. Kod: `git clone` → `/home/ghost/jarvis` (geçici PAT; vekil gelince bırak).
4. Hafıza: arşiv kökünden `secrets.env`, `ops/*.db`, `ops/*.jsonl|json`,
   `dossier/`, `intel/`, `ops/jobs|requests`; `python3 ops/veri_tasi.py`
   (~/.ghost/<ad> + ops/<ad> symlink), `ops/masa_dosya/` → ~/.ghost/masa_dosya.
5. Root servisleri (Mac'ten YubiKey): `ops/root/faz0_kur.sh`, `ops/root/kur.sh`
   (kapı + managed-settings), `ops/root/kisitla.sh`, `ops/onay/kur.sh`,
   `ops/vekil/kur.sh`.
6. Sırlar: vekilin `sirlar.env`'i + onay durumu bu arşivde YOK (root).
   (a) Mac `~/.ghost/yedek-sir/root-*.tgz.gpg` → `/var/lib` (chown!)
   (b) yoksa panellerden yeni değer + `DON=1 sh ops/vekil/kur.sh`.
   Doğrulama: `python3 -m ghost.vekil durum` → `eksik: []`.
7. Kullanıcı ortamı: git insteadOf → vekil `github-git`; `core.hooksPath
   ops/githooks`; `.env` + `mcp*.json` arşiv kökünden, `ev/*` → ~ ; Claude CLI
   (~/.local/bin/claude); `paketler/pip-user.txt`, `paketler/npm-global.txt`,
   `paketler/araclar.txt` (~/.local/bin + ~/.ghost/araclar adları).
8. Birimler/cron/nginx/TLS:
   - sistem: `systemd/*` → /etc/systemd/system, `systemd/DURUM.txt`'de
     enabled olanlar (`ghost-huntbatch` hariç); drop-in'ler
     `conf/etc/systemd/system.control/`; tam liste `systemd/BIRIMLER.txt`.
     ghost-bridge'i yalnız ENABLE et.
   - kullanıcı: `systemd-kullanici/*` → ~/.config/systemd/user,
     `XDG_RUNTIME_DIR=/run/user/1000 systemctl --user daemon-reload`,
     `systemd-kullanici/DURUM.txt`'deki enabled birimler; linger satırı orada.
   - cron: `crontab.txt` (tek seferlik/bayat satırları ayıkla).
   - nginx: `nginx/*` → sites-available, `nginx/ENABLED.txt`'dekileri
     sites-enabled'a bağla; `nginx -t`. TLS özel anahtarı YEDEKLENMEZ:
     hooks için self-signed üret; ghost-api.example.com için CF Origin CA.
9. Dış bağlar: IP değiştiyse Cloudflare 3 A kaydı (hooks.example.com,
   hooks.owner.example, ghost-api.example.com) + Mac: tunnel/vekiltunel plist,
   ~/.ssh/config, ~/jarvis/.env `GHOST_VPS_IP`, `git remote vps`. IP aynı
   olsa da Mac'te `ssh-keygen -R <IP>` (yeni host anahtarı). Mac'in
   `ghost_runtime.pub`'ı `authorized_keys`'e — yoksa işçi/tünel çalışmaz.
10. Köprü: Mac köprüsü (ai.jarvis.macbridge) DURDURULMUŞ olmalı, sonra
    `systemctl start ghost-bridge` + `systemctl --user start ghost-sohbet`;
    selfcheck, Telegram ping, Kokpit ping, vekil `eksik: []`.

## Bu arşivde OLMAYANLAR (bilerek ya da henüz)
- `conf/OKUNAMADI.txt`: root-only dosyalar (ufw kuralları vb.) — kaynak
  repodaki ops/root/, ops/vekil/, ops/onay/ betikleri.
- Vekil `sirlar.env`, onay cihazları, TLS özel anahtarları (root) → Mac
  `~/.ghost/yedek-sir/root-*.tgz.gpg` (`ops/vekil/mac_root_yedek.sh`, YubiKey).
- kokpit_medya (178 MB sohbet medyası; kalıcı olan Masa'da) — bilerek.
- 2026-11-04'ten beri arşivde: `.env`, `mcp*.json`, `ops/kokpit_paylasim/`,
  `ops/gmail_taslak/`, sahne/onay_sonuc/metrik geçmişi ve `ev/` altında
  `~/.secrets/`, harness hafıza dizini, `~/site-tools/` → `ev/<yol>` →
  `~/<yol>` (mod 600/700 korunur, chown ghost).
"""

# --- Tesisat kaynakları (testler bu sabitleri geçici dizine çevirir) ---
SISTEM_BIRIM_DIZIN = "/etc/systemd/system"
KULLANICI_BIRIM_DIZIN = os.path.expanduser("~/.config/systemd/user")
NGINX_DIZIN = "/etc/nginx"
# Okunabilir ise AYNEN kopyalanan conf dosyaları — AÇIK LİSTE (sır taşımayan,
# küçük, kurulumda elle yeniden yazılması gereken). Joker yalnız ghost önekli
# ya da drop-in dizinlerinde; sır dosyası (secrets.env, sirlar.env, .env,
# anahtarlar) bu listeye GİRMEZ.
CONF_DESENLERI = [
    "/etc/systemd/system.control/ghost-*.d/*.conf",   # ghost-bridge MemoryHigh (faz0_kur.sh)
    "/etc/systemd/system/ghost-*.d/*.conf",
    "/etc/ssh/sshd_config.d/10-ghost-*.conf",          # hardening repoda YOK
    "/etc/needrestart/conf.d/ghost.conf",              # ghost-* restart istisnası (2026-09-26 kök neden)
    "/etc/claude-code/managed-settings.json",          # güvenlik kapısı kancası
    "/etc/fail2ban/jail.d/*.conf",
    "/etc/ufw/user.rules", "/etc/ufw/user6.rules",     # root-only → OKUNAMADI.txt'ye düşer
]
CONF_TAVAN = 256 * 1024           # conf dosyası bundan büyükse kopyalanmaz (yanlış eşleşme koruması)
ARAC_DIZINLERI = [os.path.expanduser("~/.local/bin"),
                  os.path.expanduser("~/.ghost/araclar")]


def _cmd(args, env=None, timeout=60):
    """Komutu koş, stdout'u döndür; yoksa/hata/zaman aşımı → None. Tesisat hiçbir
    komut yüzünden düşmemeli: eksik kalem yedeği durdurmaz, eksik diye yazılır."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 and not r.stdout.strip():
        return None
    return r.stdout


def _kullanici_env():
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
    return env


def _yaz(yol, metin):
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    with open(yol, "w", encoding="utf-8") as f:
        f.write(metin if metin.endswith("\n") or not metin else metin + "\n")


def _birim_kopyala(kaynak_dizin, hedef, is_enabled):
    """ghost-* birimlerini kopyala + DURUM.txt (ad<TAB>enabled durumu). Döner: sayı."""
    birimler = sorted(glob.glob(os.path.join(kaynak_dizin, "ghost-*.service"))
                      + glob.glob(os.path.join(kaynak_dizin, "ghost-*.timer")))
    os.makedirs(hedef, exist_ok=True)
    durum, n = [], 0
    for src in birimler:
        try:
            shutil.copy2(src, os.path.join(hedef, os.path.basename(src)))
        except OSError:
            continue
        n += 1
        ad = os.path.basename(src)
        durum.append("%s\t%s" % (ad, is_enabled(ad)))
    return n, durum


def _systemctl_enabled(ad, kullanici=False):
    args = ["systemctl"] + (["--user"] if kullanici else []) + ["is-enabled", ad]
    out = _cmd(args, env=_kullanici_env() if kullanici else None, timeout=15)
    return (out or "").strip() or "?"


def _linger():
    try:
        import pwd
        kul = os.environ.get("USER") or pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        kul = ""
    out = _cmd(["loginctl", "show-user", kul, "-p", "Linger"], timeout=15) if kul else None
    if out and "=" in out:
        return out.strip().split("=", 1)[1] or "?"
    # loginctl yoksa linger dosyası: /var/lib/systemd/linger/<kullanıcı>
    if kul and os.path.exists("/var/lib/systemd/linger/" + kul):
        return "yes"
    return "?"


def _conf_kopyala(hedef):
    """CONF_DESENLERI'ni mutlak yol aynasıyla `conf/` altına kopyala. Döner: (kopya, okunamadı)."""
    kopya, okunamadi = [], []
    for desen in CONF_DESENLERI:
        eslesen = sorted(glob.glob(desen)) if any(c in desen for c in "*?[") else [desen]
        for src in eslesen:
            if not os.path.isfile(src):
                continue
            try:
                if os.path.getsize(src) > CONF_TAVAN:
                    okunamadi.append("%s\tçok büyük" % src)
                    continue
                with open(src, "rb") as f:
                    veri = f.read()
            except PermissionError:
                okunamadi.append("%s\tokunamadı (root)" % src)
                continue
            except OSError as e:
                okunamadi.append("%s\t%s" % (src, e.__class__.__name__))
                continue
            dst = os.path.join(hedef, src.lstrip("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(veri)
            kopya.append(src)
    return kopya, okunamadi


def _paketler(hedef):
    """Paket/araç listeleri: yeniden kurarken NE kurulacağı (sürümler dahil). Döner: yazılan dosya sayısı."""
    n = 0
    listeler = {
        "apt-manual.txt": ["apt-mark", "showmanual"],
        "pip-user.txt": [sys.executable, "-m", "pip", "list", "--user", "--format=freeze"],
        "npm-global.txt": ["npm", "ls", "-g", "--depth=0"],
        "snap.txt": ["snap", "list"],
    }
    for ad, args in listeler.items():
        out = _cmd(args, timeout=90)
        if out and out.strip():
            _yaz(os.path.join(hedef, ad), out)
            n += 1
    satirlar = []
    for d in ARAC_DIZINLERI:
        if not os.path.isdir(d):
            continue
        satirlar.append("# %s" % d)
        for ad in sorted(os.listdir(d)):
            yol = os.path.join(d, ad)
            hedef_yol = os.path.realpath(yol) if os.path.islink(yol) else ""
            satirlar.append("%s%s" % (ad, ("\t→ " + hedef_yol) if hedef_yol else ""))
    if satirlar:
        _yaz(os.path.join(hedef, "araclar.txt"), "\n".join(satirlar))
        n += 1
    return n


def tesisat(stage):
    """Kablo tesisatı: kod ve hafıza yedeklense de sunucu giderse BUNLAR ölürdü.

    systemd (sistem + kullanıcı birimleri, enabled durumu, linger, tam birim
    listesi), cron, nginx (+ hangi siteler açık), okunabilir conf dosyaları,
    paket/araç listeleri — hepsi repo dışında, yalnız makinede. Sertifika
    ÖZEL ANAHTARI ve root sır dosyaları bilerek alınmıyor: root-only okunamaz,
    hooks sertifikası self-signed — yeniden üretmek doğru yol (bkz. KURULUM.md).
    Root-only olup okunamayan conf'lar `conf/OKUNAMADI.txt`'ye ADIYLA yazılır.
    """
    d = os.path.join(stage, "tesisat")
    os.makedirs(d, exist_ok=True)
    man = []

    out = _cmd(["crontab", "-l"], timeout=15)
    if out and out.strip():
        _yaz(os.path.join(d, "crontab.txt"), out)
        man.append(("tesisat/crontab.txt", len(out.splitlines())))

    # Sistem birimleri (root sahipli ama okunur) + tam birim listesi
    sd = os.path.join(d, "systemd")
    n, durum = _birim_kopyala(SISTEM_BIRIM_DIZIN, sd, _systemctl_enabled)
    if durum:
        _yaz(os.path.join(sd, "DURUM.txt"), "\n".join(durum))
    liste = _cmd(["systemctl", "list-unit-files", "--no-pager", "--no-legend"], timeout=30)
    if liste and liste.strip():
        _yaz(os.path.join(sd, "BIRIMLER.txt"), liste)
    man.append(("tesisat/systemd/", n))

    # Kullanıcı birimleri (ghost-api/nabiz/sohbet/tunnelwatch) + linger
    ku = os.path.join(d, "systemd-kullanici")
    n, durum = _birim_kopyala(KULLANICI_BIRIM_DIZIN, ku,
                              lambda ad: _systemctl_enabled(ad, kullanici=True))
    _yaz(os.path.join(ku, "DURUM.txt"),
         "\n".join(["# linger\t%s" % _linger()] + durum))
    man.append(("tesisat/systemd-kullanici/", n))

    ng = os.path.join(d, "nginx")
    os.makedirs(ng, exist_ok=True)
    n = 0
    for src in sorted(glob.glob(os.path.join(NGINX_DIZIN, "sites-available", "ghost*"))):
        try:
            shutil.copy2(src, os.path.join(ng, os.path.basename(src)))
            n += 1
        except OSError:
            pass
    acik = sorted(os.listdir(os.path.join(NGINX_DIZIN, "sites-enabled"))) \
        if os.path.isdir(os.path.join(NGINX_DIZIN, "sites-enabled")) else []
    if acik:
        _yaz(os.path.join(ng, "ENABLED.txt"), "\n".join(acik))
    man.append(("tesisat/nginx/", n))

    cf = os.path.join(d, "conf")
    kopya, okunamadi = _conf_kopyala(cf)
    if okunamadi:
        _yaz(os.path.join(cf, "OKUNAMADI.txt"), "\n".join(
            ["# root-only ya da alınamayan — kaynak repoda ops/root, ops/vekil, ops/onay"]
            + okunamadi))
    man.append(("tesisat/conf/", len(kopya)))

    pk = os.path.join(d, "paketler")
    man.append(("tesisat/paketler/", _paketler(pk)))

    # Ortam: yeniden kurarken neyin yüklü olması gerektiği
    satirlar = ["# Ortam — kurtarma sırasında bunlar gerekli"]
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            satirlar.append(f.readline().rstrip("\n"))
    except OSError:
        pass
    satirlar.append("python3: " + sys.version.split()[0])
    for komut in ("git", "gpg", "nginx", "ffmpeg", "node", "npm", "claude", "codex",
                  "wrangler", "uv", "asc", "rea", "jq"):
        yol = shutil.which(komut)
        satirlar.append("%s: %s" % (komut, yol or "YOK"))
    _yaz(os.path.join(d, "ortam.txt"), "\n".join(satirlar))

    with open(os.path.join(d, "KURULUM.md"), "w", encoding="utf-8") as f:
        f.write(KURULUM_NOTU)
    man.append(("tesisat/KURULUM.md", len(KURULUM_NOTU)))
    return man


def build_archive(tmp, stamp):
    stage = os.path.join(tmp, "ghost-%s" % stamp)
    os.makedirs(stage)
    man = snapshot(stage)
    man += tesisat(stage)
    meta = {
        "stamp": stamp,
        "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": os.uname().nodename,
        "icerik": [{"yol": r, "boyut": s} for r, s in man],
    }
    with open(os.path.join(stage, "MANIFEST.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    tar = os.path.join(tmp, "%s.tar.gz" % stamp)
    with tarfile.open(tar, "w:gz") as t:
        t.add(stage, arcname=os.path.basename(stage))

    enc = tar + ".gpg"
    pw = os.environ.get("GHOST_BACKUP_PASS", "")
    if not pw:
        raise SystemExit("GHOST_BACKUP_PASS yok — şifresiz yedek almam.")
    subprocess.run(
        ["gpg", "--batch", "--yes", "--quiet", "--symmetric",
         "--cipher-algo", "AES256", "--s2k-mode", "3", "--s2k-count", "65011712",
         "--passphrase-fd", "0", "-o", enc, tar],
        input=pw.encode(), check=True)
    os.remove(tar)
    return enc, meta


def git(args, cwd, token=None, check=True):
    env = dict(os.environ)
    cmd = ["git"]
    if token:
        env["GH_TOKEN"] = token
        helper = ("!f(){ echo username=x-access-token; "
                  "echo password=$GH_TOKEN; };f")
        cmd += ["-c", "credential.helper=" + helper]
    cmd += args
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit("git %s başarısız:\n%s%s" % (args[0], r.stdout, r.stderr))
    return r


PARCA_BOYUTU = 90 * 1024 * 1024   # GitHub tek dosya tavanı 100 MB (GH001); 2026-10-29'da arşiv 120 MB'a çıkıp push reddedildi


def parcala(enc_path, hedef_dizin, stamp):
    """Şifreli arşivi `<stamp>.tar.gz.gpg.NN` parçalarına yaz (≤90 MB); küçükse tek dosya. Yazılan adlar."""
    boyut = os.path.getsize(enc_path)
    if boyut <= PARCA_BOYUTU:
        ad = "%s.tar.gz.gpg" % stamp
        shutil.copy2(enc_path, os.path.join(hedef_dizin, ad))
        return [ad]
    adlar = []
    with open(enc_path, "rb") as f:
        n = 0
        while True:
            veri = f.read(PARCA_BOYUTU)
            if not veri:
                break
            ad = "%s.tar.gz.gpg.%02d" % (stamp, n)
            with open(os.path.join(hedef_dizin, ad), "wb") as p:
                p.write(veri)
            adlar.append(ad)
            n += 1
    return adlar


def damga(ad):
    """'2026-10-29.tar.gz.gpg' ya da '2026-10-29.tar.gz.gpg.01' → '2026-10-29'."""
    return ad.split(".tar.gz.gpg")[0]


def rotate(repo_dir, enc_path, stamp):
    """Yeni anlık görüntüyü yerleştir (gerekirse parçalı), saklama penceresi dışını damga bazında sil."""
    today = date.today()
    slots = ["gunluk"]
    if today.weekday() == 0:
        slots.append("haftalik")
    if today.day == 1:
        slots.append("aylik")
    for s in slots:
        d = os.path.join(repo_dir, s)
        os.makedirs(d, exist_ok=True)
        for eski in os.listdir(d):            # aynı damganın eski (tek/parçalı) kopyaları
            if damga(eski) == stamp:
                os.remove(os.path.join(d, eski))
        parcala(enc_path, d, stamp)
    silinen = []
    for s, keep in KEEP.items():
        d = os.path.join(repo_dir, s)
        if not os.path.isdir(d):
            continue
        damgalar = sorted({damga(x) for x in os.listdir(d) if ".tar.gz.gpg" in x})
        for eski_damga in damgalar[:-keep] if len(damgalar) > keep else []:
            for x in sorted(os.listdir(d)):
                if damga(x) == eski_damga:
                    os.remove(os.path.join(d, x))
                    silinen.append("%s/%s" % (s, x))
    return slots, silinen


def readme(meta, slots):
    return """# Ghost hafıza yedeği

Bu dal (`hafiza`) kod geçmişiyle ORTAK ATASI OLMAYAN bir yörünge dalıdır.
`main`'e karışmaz. Her koşuda tek commit olarak yeniden yazılır.

İçerik: gpg AES-256 ile şifrelenmiş tar.gz anlık görüntüler.
Saklama: günlük %d, haftalık %d, aylık %d.

## Geri yükleme

    git clone -b hafiza <repo> ghost-hafiza && cd ghost-hafiza
    # tek dosya: gunluk/EN-YENI.tar.gz.gpg · parçalı (>90 MB): gunluk/EN-YENI.tar.gz.gpg.00, .01, … → önce birleştir
    cat gunluk/EN-YENI.tar.gz.gpg.* > y.tar.gz.gpg   # parçalıysa; tek dosyaysa doğrudan kullan
    gpg --decrypt y.tar.gz.gpg > y.tar.gz
    tar xzf y.tar.gz

Parola sahibin şifre yöneticisinde. **Bu makinede duran kopya tek başına
yeterli değildir** — makine gittiğinde parola da giderdi, yedek okunamazdı.

Son koşu: %s (%s)
""" % (KEEP["gunluk"], KEEP["haftalik"], KEEP["aylik"],
       meta["utc"], ", ".join(slots))


def main():
    _load_env()
    token = _gh_token()
    stamp = date.today().isoformat()
    t0 = time.time()

    os.makedirs(TMP_KOK, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ghostbak-", dir=TMP_KOK) as tmp:
        enc, meta = build_archive(tmp, stamp)
        boyut = os.path.getsize(enc)
        sha = hashlib.sha256(open(enc, "rb").read()).hexdigest()[:12]
        log("arşiv hazır: %.1f MB (sha %s)" % (boyut / 1e6, sha))

        if os.path.isdir(WORK):
            shutil.rmtree(WORK)
        os.makedirs(WORK)
        git(["init", "-q", "-b", BRANCH], WORK)
        git(["remote", "add", "origin",
             "https://github.com/%s.git" % REPO], WORK)
        # Var olan dalı çek (yoksa sessizce boş başla)
        r = git(["fetch", "-q", "--depth", "1", "origin", BRANCH],
                WORK, token, check=False)
        if r.returncode == 0:
            git(["checkout", "-q", "FETCH_HEAD", "--", "."], WORK, check=False)
            git(["reset", "-q"], WORK, check=False)

        slots, silinen = rotate(WORK, enc, stamp)
        with open(os.path.join(WORK, "README.md"), "w", encoding="utf-8") as f:
            f.write(readme(meta, slots))
        with open(os.path.join(WORK, "SON.json"), "w", encoding="utf-8") as f:
            json.dump({"stamp": stamp, "sha256_12": sha, "boyut": boyut,
                       "slotlar": slots, "silinen": silinen,
                       "utc": meta["utc"]}, f, ensure_ascii=False, indent=1)

        git(["config", "user.email", "ghost@example.com"], WORK)
        git(["config", "user.name", "Ghost"], WORK)
        git(["add", "-A"], WORK)
        git(["commit", "-q", "-m",
             "Hafıza anlık görüntüsü %s (%s)" % (stamp, ", ".join(slots))], WORK)
        git(["push", "-q", "--force", "origin", "%s:%s" % (BRANCH, BRANCH)],
            WORK, token)
        shutil.rmtree(WORK, ignore_errors=True)

    ozet = "yedek ✓ %s %.1f MB → %s/%s (%s)" % (
        stamp, boyut / 1e6, REPO, BRANCH, ", ".join(slots))
    if silinen:
        ozet += " · budandı: %d" % len(silinen)
    ozet += " · %.0fs" % (time.time() - t0)
    heartbeat.beat("yedek")
    log(ozet)
    print(ozet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
