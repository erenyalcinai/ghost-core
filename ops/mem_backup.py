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
]
DIRS = ["intel", "dossier", "ops/requests", "ops/jobs",
        "ops/masa_dosya"]   # Masa kalıcı: sahibin yüklediği foto/belge tek kopyaydı (2026-10-04)


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
    for rel in FILES:
        src = os.path.join(BASE, rel)
        if not os.path.exists(src):
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
    return man


KURULUM_NOTU = """# Tesisat — sunucuyu sıfırdan ayağa kaldırma

Kod GitHub'da, hafıza şifreli yedekte. Bu klasör ÜÇÜNCÜ parça: sunucuyu
Ghost'un çalıştığı hâle getiren kablolar. 2026-08-20'de sayıldı — 14 systemd
biriminin 12'si ve 14 cron satırı YALNIZCA sunucuda duruyordu, yani sunucu
gittiğinde kurtarma arkeolojiye dönüyordu.

## Sıra

1. Kod: `git clone` (ornek-org/jarvis) → `/home/ghost/jarvis`
2. Sırlar + hafıza: bu arşivin kökünden `secrets.env`, `ops/*.db`,
   `ops/*.jsonl`, `ops/*.json` yerlerine (ve `ops/masa_dosya/` — Masa ekleri)
3. Bağımlılıklar: `ortam.txt`'ye bak
4. systemd: `systemd/*` → `/etc/systemd/system/`, sonra
   `systemctl daemon-reload` + `systemd/DURUM.txt`'de enabled görünen her
   birim için `systemctl enable --now <birim>`
5. cron: `crontab crontab.txt`
6. nginx: `nginx/ghost-hooks` → `/etc/nginx/sites-available/`, symlink
   `sites-enabled`, `nginx -t`, `systemctl reload nginx`
7. TLS: sertifika YEDEKLENMEZ (özel anahtar root-only ve self-signed —
   yeniden üretilir). nginx `/etc/ssl/certs/ghost-hooks.crt` +
   `/etc/ssl/private/ghost-hooks.key` bekler; yeniden üret.
8. DNS: yeni sunucunun IP'si Cloudflare'de `hooks.*` kaydına yazılmalı;
   Mac tarafında `.env` içindeki `GHOST_VPS_IP` de güncellenmeli
   (deploy.sh'ta sabit yedek IP var, ona da bak).
9. SSH: Mac'in `~/.ssh/ghost_runtime.pub` anahtarı `authorized_keys`'e —
   yoksa Mac işçisi ve bayrak devri çalışmaz.
"""


def tesisat(stage):
    """Kablo tesisatı: kod ve hafıza yedeklense de sunucu giderse BUNLAR ölürdü.

    systemd birimleri, cron, nginx — üçü de repo dışında, yalnız makinede.
    Sertifika ÖZEL ANAHTARI bilerek alınmıyor: root-only okunamaz ve zaten
    self-signed, yeniden üretmek doğru yol (bkz. KURULUM.md).
    """
    d = os.path.join(stage, "tesisat")
    os.makedirs(d, exist_ok=True)
    man = []

    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        with open(os.path.join(d, "crontab.txt"), "w", encoding="utf-8") as f:
            f.write(r.stdout)
        man.append(("tesisat/crontab.txt", len(r.stdout.splitlines())))

    sd = os.path.join(d, "systemd")
    os.makedirs(sd, exist_ok=True)
    birimler = sorted(glob.glob("/etc/systemd/system/ghost-*.service")
                      + glob.glob("/etc/systemd/system/ghost-*.timer"))
    durum = []
    for src in birimler:
        try:
            shutil.copy2(src, os.path.join(sd, os.path.basename(src)))
        except OSError:
            continue
        ad = os.path.basename(src)
        e = subprocess.run(["systemctl", "is-enabled", ad],
                           capture_output=True, text=True)
        durum.append("%s\t%s" % (ad, (e.stdout or e.stderr).strip() or "?"))
    if durum:
        with open(os.path.join(sd, "DURUM.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(durum) + "\n")
    man.append(("tesisat/systemd/", len(birimler)))

    ng = os.path.join(d, "nginx")
    os.makedirs(ng, exist_ok=True)
    n = 0
    for src in glob.glob("/etc/nginx/sites-available/ghost*"):
        try:
            shutil.copy2(src, os.path.join(ng, os.path.basename(src)))
            n += 1
        except OSError:
            pass
    man.append(("tesisat/nginx/", n))

    # Ortam: yeniden kurarken neyin yüklü olması gerektiği
    satirlar = ["# Ortam — kurtarma sırasında bunlar gerekli"]
    try:
        satirlar.append(open("/etc/os-release", encoding="utf-8")
                        .read().split("\n")[0])
    except OSError:
        pass
    satirlar.append("python3: " + sys.version.split()[0])
    for komut in ("git", "gpg", "nginx", "ffmpeg", "node", "claude", "wrangler"):
        yol = shutil.which(komut)
        satirlar.append("%s: %s" % (komut, yol or "YOK"))
    with open(os.path.join(d, "ortam.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(satirlar) + "\n")

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

    with tempfile.TemporaryDirectory(prefix="ghostbak-") as tmp:
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
    log(ozet)
    print(ozet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
