#!/usr/bin/env python3
"""Ghost öz-denetim nöbetçisi — rol farkındalıklı (Mac + VPS aynı dosya).

Kontrol: (1) köprü çalışıyor mu — Mac'te launchd `ai.jarvis.macbridge`,
VPS'te systemd `ghost-bridge`; (2) takılı iş var mı (>45 dk);
(3) çöp temizliği — eski iş/rc/inbox dosyaları, şişen loglar.
Sorun varsa Telegram'a haber verir ve köprüyü diriltmeyi dener.
Sağlıklıysa sessiz. Mac: launchd 15 dk; VPS: systemd timer 15 dk.
"""
import glob
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

# Depo kökünü yola ekle: launchd/systemd bu betiği KENDİ çalışma dizininden
# koşuyor (WorkingDirectory yok) → `import ghost` çöküyordu. Kanıt (2026-08-15,
# ops/selfcheck.err): beyin erişilemezken yedek motoru bildirmek için yapılan
# `ghost.engines` importu ModuleNotFoundError verdi → NÖBETÇİ TAM NÖBET ANINDA
# DÜŞTÜ, Claude çökmüşken alarm hiç gitmedi. O import yalnız kriz anında
# çalıştığı için hata da yalnız o an ortaya çıkıyor; bu yüzden yol en başta
# sabitleniyor.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import heartbeat  # noqa: E402 — aynı dizin (ops), yol yukarıda sabitlendi

IS_MAC = sys.platform == "darwin"
BRIDGE_MAC = "ai.jarvis.macbridge"
BRIDGE_VPS = "ghost-bridge"

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPS = os.path.join(BASE, "ops")
JOBS_DIR = os.path.join(OPS, "jobs")
LOG_FILE = os.path.join(OPS, "selfcheck.log")
STUCK_SEC = 45 * 60
KEEP_DAYS = 7           # iş/rc/inbox dosyaları bu yaştan sonra silinir
LOG_MAX = 5 * 1024 * 1024  # log bu boyutu aşarsa son 2000 satıra kırpılır

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

API = "https://api.telegram.org/bot" + ENV.get("TELEGRAM_BOT_TOKEN", "")
_env = dict(os.environ)
_env["PATH"] = _env.get("PATH", "") + ":/usr/local/bin:/opt/homebrew/bin:/usr/bin"
# secrets.env de env'e (beyin probu CLAUDE_CODE_OAUTH_TOKEN'a muhtaç — yoksa
# claude auth'suz çağrılır ve HEP "beyin düştü" yanlış alarmı verir).
_secrets = os.path.join(BASE, "secrets.env")
if os.path.exists(_secrets):
    with open(_secrets, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                _env.setdefault(k.strip(), v.strip())


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
    oid = owner_id()
    if not oid:
        return
    data = urllib.parse.urlencode({"chat_id": oid, "text": text}).encode()
    req = urllib.request.Request(API + "/sendMessage", data=data)
    try:
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:
        log("telegram hata: %r" % e)


def bridge_running():
    try:
        if IS_MAC:
            r = subprocess.run(["launchctl", "list"], capture_output=True,
                               text=True, timeout=10, env=_env)
            for l in r.stdout.splitlines():
                if BRIDGE_MAC in l:
                    pid = l.split("\t")[0]
                    return pid != "-" and pid.lstrip("-").isdigit()
            return False
        r = subprocess.run(["systemctl", "is-active", BRIDGE_VPS],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() == "active"
    except Exception as e:
        log("servis kontrol hata: %r" % e)
    return False


def revive():
    try:
        if IS_MAC:
            subprocess.run(["launchctl", "kickstart", "-k",
                            "gui/%d/%s" % (os.getuid(), BRIDGE_MAC)],
                           capture_output=True, timeout=15, env=_env)
            return True
        # ghost kullanıcısı için sudoers'ta sadece bu komuta izin var
        r = subprocess.run(["sudo", "-n", "systemctl", "restart", BRIDGE_VPS],
                           capture_output=True, text=True, timeout=20)
        return r.returncode == 0
    except Exception as e:
        log("diriltme hata: %r" % e)
        return False


def stuck_jobs():
    out = []
    now = time.time()
    if not os.path.isdir(JOBS_DIR):
        return out
    for fn in os.listdir(JOBS_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            j = json.load(open(os.path.join(JOBS_DIR, fn), encoding="utf-8"))
        except Exception:
            continue
        # "denetimde" de takılabilir (ikinci göz çöktüyse) — alarm onu da görsün.
        if j.get("status") in ("çalışıyor", "denetimde") and j.get("started"):
            try:
                t = time.mktime(time.strptime(j["started"], "%Y-%m-%d %H:%M:%S"))
                if now - t > STUCK_SEC:
                    out.append((fn.replace(".json", ""), int((now - t) / 60)))
            except Exception:
                pass
    return out


def cleanup():
    """Süresiz birikmeyi durdur: eski iş kayıtları, rc logları, inbox medyası
    silinir; şişen loglar kırpılır."""
    cutoff = time.time() - KEEP_DAYS * 86400
    removed = 0
    for pat in (os.path.join(JOBS_DIR, "*.json"),
                os.path.join(OPS, "rc", "*.log"),
                os.path.join(OPS, "inbox", "*")):
        for p in glob.glob(pat):
            try:
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
                    removed += 1
            except OSError:
                pass
    for p in glob.glob(os.path.join(OPS, "*.log")):
        try:
            if os.path.getsize(p) > LOG_MAX:
                with open(p, encoding="utf-8", errors="ignore") as f:
                    tail = f.readlines()[-2000:]
                with open(p, "w", encoding="utf-8") as f:
                    f.writelines(tail)
                log("kırpıldı: %s" % os.path.basename(p))
        except OSError:
            pass
    if removed:
        log("temizlik: %d eski dosya silindi" % removed)


BRAIN_PROBE_GAP = 2 * 3600      # beyin probu en fazla 2 saatte bir (maliyet/limit)
BRAIN_STAMP = os.path.join(OPS, ".brain_probe")


CLI_STAMP = os.path.join(OPS, "cli_update.stamp")
CLI_GAP = 24 * 3600
MAC_CLI_STAMP = os.path.join(OPS, "mac_cli_update.stamp")
# Mac'teki Claude CLI kullanıcı npm önekinde (~/.local root'a ait → resmi yükleyici kuramıyor;
# /usr/local npm'i sudo ister). 2026-10-27: Mac 2.1.220'de kalmıştı, VPS 2.1.311.
MAC_CLI_GUNCELLE = ('export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH; P=$HOME/.ghost/araclar/npm; '
                    'E=$($P/bin/claude --version 2>/dev/null | cut -d" " -f1); '
                    'npm install -g --prefix $P @anthropic-ai/claude-code@latest >/dev/null 2>&1; '
                    'echo "$E $($P/bin/claude --version 2>/dev/null | cut -d" " -f1)"')


def _tam_model(m):
    try:
        from ghost import modeller
        return modeller.tam_model(m)
    except Exception:
        return m


def cli_guncel_tut():
    """Claude CLI'yi günde bir `claude update` ile tazele (yalnız yerel kurulum,
    ~/.local/share/claude). Gerekçe (Sahip 2026-10-01): "opus" takma adını CLI
    kendi içinde çözer; bayat CLI yeni modeli (Opus 5.5) ne takma adla bulur
    ne tam kimlikle kabul eder. Koşan süreçler etkilenmez (yeni ikili ayrı dosya).
    Döner: bildirilecek satırlar (boş = sessiz)."""
    try:
        if time.time() - os.path.getmtime(CLI_STAMP) < CLI_GAP:
            return []
    except OSError:
        pass
    out = []
    try:
        from ghost import modeller
        yol = os.path.realpath(modeller.claude_bin())
        if "/.local/share/claude/" in yol:
            eski, yeni, hata = modeller.cli_guncelle(timeout=300)
            if hata:
                log("claude update hata: %s" % hata.strip()[-200:])
            if yeni and yeni != eski:
                log("claude CLI %s → %s" % (eski, yeni))
                out.append("⬆️ Claude CLI %s → %s (model eşlemesi tazelendi)" % (eski, yeni))
        for u in modeller.uyari():
            out.append("🧓 Model eşlemesi bayat — " + u)
    except Exception as e:
        log("cli_guncel_tut hata: %r" % e)
    try:
        open(CLI_STAMP, "w").close()
    except OSError:
        pass
    return out


def mac_cli_guncel_tut():
    """Mac'teki Claude CLI'yi günde bir npm @latest'e çek (hızlı hat). Mac kapalıysa damga
    basılmaz → Mac açılınca ilk turda dener. Mac işçisi CLAUDE_BIN ile bu ikiliyi kullanır;
    koşan oturum etkilenmez. Döner: bildirilecek satırlar (boş = sessiz)."""
    if os.environ.get("GHOST_TEST"):
        return []
    try:
        if time.time() - os.path.getmtime(MAC_CLI_STAMP) < CLI_GAP:
            return []
    except OSError:
        pass
    try:
        from ops import mac
        r = mac.komut(MAC_CLI_GUNCELLE, zaman=240)
    except Exception as e:  # noqa: BLE001
        log("mac cli güncelleme: Mac'e ulaşılamadı (%r)" % e)
        return []
    if not isinstance(r, dict) or r.get("hata") or r.get("rc"):
        log("mac cli güncelleme hata: %s" % str(r)[:200])
        return []
    parca = (r.get("cikti") or "").split()
    try:
        open(MAC_CLI_STAMP, "w").close()
    except OSError:
        pass
    if len(parca) == 2 and parca[0] != parca[1]:
        log("Mac claude CLI %s → %s" % tuple(parca))
    elif len(parca) < 2:
        log("mac cli güncelleme: sürüm okunamadı (%r)" % parca)
    return []


def brain_reachable():
    """Claude gerçekten yanıt veriyor mu (binary VAR yetmez — auth/API canlı mı).
    Kısıtlı: son probdan 2 saat geçmediyse atla (None=atlandı, True/False=sonuç).
    Tek beyinli VPS'te Claude sessizce düşerse Ghost cevap veremez — görünür kıl."""
    try:
        if time.time() - os.path.getmtime(BRAIN_STAMP) < BRAIN_PROBE_GAP:
            return None
    except OSError:
        pass
    claude = ENV.get("CLAUDE_BIN") or "claude"
    try:
        # Token ekonomisi: prob en ucuz modelle ve /tmp'den koşar — jarvis
        # dizininden koşarsa CLAUDE.md+hafıza yüklenip her probda boşa
        # binlerce token yazılıyordu. Amaç yalnız "auth/API canlı mı".
        p = subprocess.run([claude, "-p", "ping", "--output-format", "text",
                            "--model", _tam_model("haiku")],
                           capture_output=True, text=True, timeout=90, env=_env,
                           cwd="/tmp")
        ok = p.returncode == 0 and bool(p.stdout.strip())
    except Exception:
        ok = False
    try:
        open(BRAIN_STAMP, "w").close()   # damga: probu koştuk
    except OSError:
        pass
    return ok


# Bu makinede damga bırakan nöbetçiler yoklanır; hiç atmamış isim atlanır
# (heartbeat.stale kuralı) → aynı dosya Mac'te ve VPS'te sahte alarm vermez.
WATCHED = ["uptime", "biz_watch", "marka_deploy", "defter", "port_watch"]


def test_kapisi_yerinde():
    """pre-push test kapısı bağlı mı — bağlı değilse sessizce yeniden bağla.

    Kapı `ops/githooks/pre-push` dosyasında versiyonlanıyor ama onu devreye
    sokan `core.hooksPath` ayarı .git/config'te durur, yani VERSİYONLANMAZ.
    Taze bir klonda (kurtarma, yeni makine) kapı sessizce yok olur ve kod
    testsiz akmaya başlar — üstelik hiçbir hata vermeden. Bu yüzden 15
    dakikada bir yoklanıp gerekiyorsa geri bağlanıyor.
    """
    hook = os.path.join(OPS, "githooks", "pre-push")
    if not os.path.exists(hook):
        return None  # repo bu sürümden eski; uyarma
    try:
        r = subprocess.run(["git", "-C", BASE, "config", "--get",
                            "core.hooksPath"], capture_output=True, text=True,
                           timeout=10)
        if r.stdout.strip() == "ops/githooks":
            return None
        subprocess.run(["git", "-C", BASE, "config", "core.hooksPath",
                        "ops/githooks"], capture_output=True, timeout=10)
        log("test kapısı yeniden bağlandı (core.hooksPath boştu)")
        return "🔧 Test kapısı bağlı değildi (core.hooksPath), yeniden bağladım."
    except Exception as e:
        return "🔧 Test kapısı doğrulanamadı: %r" % e


GH_HELPER = "!ops/gh_cred.sh"


def github_kimligi_yerinde():
    """GitHub kimlik yardımcısı bağlı mı — değilse sessizce geri bağla.

    `test_kapisi_yerinde` ile aynı dersin ikinci uygulaması: yardımcı betik
    (ops/gh_cred.sh) versiyonlanıyor ama onu devreye sokan credential ayarı
    .git/config'te durur. Bağlı değilse dış yedek ve CI sessizce durur —
    push "could not read Username" ile düşer, kimse bakmaz.
    """
    if not os.path.exists(os.path.join(OPS, "gh_cred.sh")):
        return None  # repo bu sürümden eski; uyarma
    anahtar = "credential.https://github.com.helper"
    try:
        r = subprocess.run(["git", "-C", BASE, "config", "--get", anahtar],
                           capture_output=True, text=True, timeout=10)
        if r.stdout.strip() == GH_HELPER:
            return None
        subprocess.run(["git", "-C", BASE, "config", anahtar, GH_HELPER],
                       capture_output=True, timeout=10)
        log("github kimliği yeniden bağlandı (credential helper boştu)")
        return "🔧 GitHub kimliği bağlı değildi, yeniden bağladım."
    except Exception as e:
        return "🔧 GitHub kimliği doğrulanamadı: %r" % e


KOD_TOLERANS = 180  # sn: deploy'un rsync→restart arası bu kadar sürebilir


# Sistem kütüphaneleri (2026-09-26): needrestart artık ghost-* servislerini otomatik
# yeniden başlatmıyor (turun ortasında cevabı öldürüyordu). Karşılığı: paket veritabanı
# köprü süreçinden yeniyse (apt bir şey güncelledi) köprü "bayat" sayılır ve BOŞTAYKEN
# yenilenir — güncel kütüphane yine yüklenir, ama cevap kesilmeden. Mac'te dosya yok.
EK_YOLLAR = ["/var/lib/dpkg/status"]


def _kod_ts(ek_yollar=None):
    """Çalışan koda ait EN YENİ dosya zamanı (köprü + çekirdek paket + sistem paketleri)."""
    yollar = [os.path.join(OPS, "telegram_bridge.py")]
    yollar += list(EK_YOLLAR if ek_yollar is None else ek_yollar)
    for kok, _dirs, dosyalar in os.walk(os.path.join(BASE, "ghost")):
        if "__pycache__" in kok:
            continue
        yollar += [os.path.join(kok, d) for d in dosyalar if d.endswith(".py")]
    zamanlar = []
    for y in yollar:
        try:
            zamanlar.append(os.path.getmtime(y))
        except OSError:
            pass
    return max(zamanlar) if zamanlar else 0


def _bridge_pid():
    try:
        if IS_MAC:
            r = subprocess.run(["launchctl", "list"], capture_output=True,
                               text=True, timeout=10, env=_env)
            for l in r.stdout.splitlines():
                if BRIDGE_MAC in l:
                    pid = l.split("\t")[0]
                    return int(pid) if pid.lstrip("-").isdigit() else 0
            return 0
        r = subprocess.run(["systemctl", "show", BRIDGE_VPS, "-p", "MainPID",
                            "--value"], capture_output=True, text=True, timeout=10)
        return int(r.stdout.strip() or 0)
    except Exception:
        return 0


def _surec_ts(pid):
    """Sürecin başlangıç zamanı (epoch). Bulunamazsa 0."""
    if not pid:
        return 0
    try:
        if IS_MAC:
            r = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                               capture_output=True, text=True, timeout=10)
            s = r.stdout.strip()
            return time.mktime(time.strptime(s, "%a %b %d %H:%M:%S %Y")) if s else 0
        # Linux: /proc/<pid>/stat alan 22 = açılıştan beri geçen tik
        with open("/proc/%d/stat" % pid, encoding="utf-8") as f:
            alanlar = f.read().rsplit(") ", 1)[1].split()
        tik = float(alanlar[19])                      # 22. alan (0-tabanlı: 19)
        with open("/proc/uptime", encoding="utf-8") as f:
            uptime = float(f.read().split()[0])
        return (time.time() - uptime) + tik / os.sysconf("SC_CLK_TCK")
    except Exception:
        return 0


API_VPS = "ghost-api"          # kullanıcı systemd birimi: Kokpit API (ghost.api, 127.0.0.1:8790)


def _user_env():
    """`systemctl --user` için oturum veriyolu ortamı (timer'dan koşarken tanımsız)."""
    e = dict(_env)
    uid = os.getuid()
    e.setdefault("XDG_RUNTIME_DIR", "/run/user/%d" % uid)
    e.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/%d/bus" % uid)
    return e


def hook_bayat_uyar(kod_ts):
    """Webhook sunucusu (ghost-hook, SİSTEM birimi) bayat kodla koşuyorsa uyar — yenileyemeyiz
    (VPS'te sudo yok). NEDEN (2026-10-27): webhook 'her_zaman_onay' düzeltmesi commit'lendi ama
    ghost-hook 26 Ekim'den beri koşuyordu; düzeltme canlı sanıldı, değildi. Günde bir kez uyarır."""
    if IS_MAC:
        return None
    try:
        r = subprocess.run(["systemctl", "show", "ghost-hook", "-p", "MainPID", "--value"],
                           capture_output=True, text=True, timeout=10)
        pid = int(r.stdout.strip() or 0)
        if not pid or not bayat_kod(kod_ts, _surec_ts(pid)):
            return None
        isaret = os.path.join(BASE, "ops", ".hook_bayat_uyari")
        if os.path.exists(isaret) and time.time() - os.path.getmtime(isaret) < 86400:
            return None
        open(isaret, "w").close()
        return ("🪝 Webhook sunucusu (ghost-hook) bayat kodla koşuyor — yenile: Mac'ten "
                "ssh -i ~/.ssh/ghost_vps root@VPS 'systemctl restart ghost-hook'")
    except Exception:
        return None


KAPI_CANLI = "/usr/local/lib/ghost-kapi/guvenlik_kapisi.py"


def kapi_kopyasi_uyar():
    """Repodaki güvenlik kapısı ile root'a kurulu CANLI kapı farklıysa uyar (2026-10-28).
    NEDEN: kapı B turunda root sahipli /usr/local/lib/ghost-kapi/'ye taşındı; repo kaynak,
    canlı kopya ayrı. Bir düzeltme repoya commit'lenip root'a install edilmezse kapı eski
    kodla korur ve kimse fark etmez (ghost-hook'un bayatlaması gibi). Yenileme YubiKey ister,
    bu yüzden kendiliğinden yapamayız — günde bir kez uyarırız. Yalnız VPS, kapı kuruluysa."""
    if IS_MAC or not os.path.exists(KAPI_CANLI):
        return None
    try:
        import hashlib
        repo = os.path.join(BASE, "ops", "guvenlik_" + "kapisi.py")
        h = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
        if h(repo) == h(KAPI_CANLI):
            return None
        isaret = os.path.join(BASE, "ops", ".kapi_fark_uyari")
        if os.path.exists(isaret) and time.time() - os.path.getmtime(isaret) < 86400:
            return None
        open(isaret, "w").close()
        return ("🔒 Güvenlik kapısı: repo kaynağı ile canlı kopya (%s) FARKLI — düzeltme kurulmamış. "
                "Mac'te YubiKey ile: /opt/homebrew/bin/ssh -i ~/.ssh/ghost_root_sk root@VPS "
                "'install -m644 /home/ghost/jarvis/ops/guvenlik_kapisi.py /usr/local/lib/ghost-kapi/'" % KAPI_CANLI)
    except Exception:
        return None


def onay_servisi_yerinde():
    """Onay servisi (Faz 1, 2026-10-28) kuruluysa cevap veriyor mu ve canlı kopya repo kaynağıyla aynı mı?
    Servis düşükse Kokpit 503 alır, onaylar kapalı düşer (mail gitmez, kapı onay istisnası açılmaz) —
    sessiz kalmamalı. Kurulu değilse (Mac, açık çekirdek) None."""
    if IS_MAC:
        return None
    try:
        from ghost import onay
        if not onay.servis_kurulu():
            return None
        try:
            d = onay.durum()
        except onay.ServisHatasi as e:
            return ("🔐 Onay servisi CEVAP VERMİYOR (%s) — Kokpit 503, onaylar kapalı. "
                    "Mac'ten: ssh ghost_vps 'systemctl restart ghost-onay'" % e)
        if not d.get("sahip") or not d.get("telegram"):
            return "🔐 Onay servisinin ayarı eksik (sahip/telegram) — eşleme kodu gönderilemez."
        import hashlib
        canli = "/usr/local/lib/ghost-onay/onay_servisi.py"
        repo = os.path.join(BASE, "ops", "onay", "onay_servisi.py")
        h = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()  # noqa: E731
        if os.path.exists(canli) and h(canli) != h(repo):
            isaret = os.path.join(BASE, "ops", ".onay_fark_uyari")
            if not (os.path.exists(isaret) and time.time() - os.path.getmtime(isaret) < 86400):
                open(isaret, "w").close()
                return ("🔐 Onay servisi: repo kaynağı ile canlı kopya FARKLI — kurulum için YubiKey ile "
                        "ops/onay/kur.sh")
    except Exception as e:
        log("onay servisi denetimi hata: %r" % e)
    return None


def api_bayat_yenile(kod_ts):
    """Kokpit API (ghost.api) bayat kodla koşuyorsa hemen yenile.

    NEDEN (2026-09-07): Nabız'a "Kota" alanı eklendi, köprü yenilendi ama telefona
    Nabız'ı servis eden ghost-api 02:22'deki kodla koşmaya devam etti — alan hiç
    gitmedi, Sahip "gerçek kota gözükmüyor" dedi. API durumsuz ve istek başına
    kısa; köprüden farklı olarak boşta olmasını beklemeye gerek yok (3 sn kesinti,
    uygulama yeniden dener). Yalnız VPS."""
    if IS_MAC:
        return None
    try:
        env = _user_env()
        r = subprocess.run(["systemctl", "--user", "show", API_VPS, "-p", "MainPID", "--value"],
                           capture_output=True, text=True, timeout=10, env=env)
        pid = int(r.stdout.strip() or 0)
        if not pid:
            return None
        surec_ts = _surec_ts(pid)
        if not bayat_kod(kod_ts, surec_ts):
            return None
        r = subprocess.run(["systemctl", "--user", "restart", API_VPS],
                           capture_output=True, text=True, timeout=30, env=env)
        ok = r.returncode == 0
        log("Kokpit API bayat kod (süreç %s) → yenilendi: %s" % (
            time.strftime("%d %b %H:%M", time.localtime(surec_ts)), "tamam" if ok else r.stderr[-200:]))
        return ("♻️ Kokpit API bayat koddaydı → yenilendi: %s" % ("tamam" if ok else "BAŞARISIZ, elle bak"))
    except Exception as e:
        log("Kokpit API bayat denetimi hata: %r" % e)
        return None


def bayat_kod(kod_ts, surec_ts, tolerans=KOD_TOLERANS):
    """Diskteki kod, çalışan süreçten YENİ mi? (saf fonksiyon — test edilebilir)

    NEDEN VAR (2026-08-24): deploy dosyaları taşıdı ama `systemctl restart`
    adımına HİÇ ulaşmadı (rsync'ten sonra sessizce düştü). Kod diskte yeni,
    bellekte eski — üstelik her nöbetçi "sağlıklı" diyordu, çünkü kimse
    "çalışan süreç bu kodu mu koşuyor?" diye sormuyordu. Düzeltme canlıya
    çıktı sanıldı; oysa 20 dakika boyunca eski köprü koşuyordu.
    """
    if not kod_ts or not surec_ts:
        return False        # ölçemiyorsak susarız (yanlış alarm üretme)
    return kod_ts > surec_ts + tolerans


BAYAT_COOLDOWN = 60 * 60        # sn: aynı bayat süreç için en fazla saatte bir
BAYAT_STATE = os.path.join(OPS, "bayat_state.json")


def bayat_uyarilsin(imza, simdi, onceki, cooldown=BAYAT_COOLDOWN):
    """Bayat-kod uyarısı BU turda basılsın mı? (saf fonksiyon — test edilebilir)

    NEDEN VAR: nöbetçi 15 dk'da bir koşuyor ve tekrar bastırması yoktu. Ghost
    kendi kodunu VPS'te düzenleyip commit'liyor, restart ise BİLEREK manuel
    (CLAUDE.md kural 6) — yani her kod değişikliğiyle restart arasında geçen
    süre boyunca aynı uyarı 15 dk'da bir düşerdi. Alarm yorgunluğu, nöbetçiyi
    işe yaramaz hâle getirir: her seferinde bağıran nöbetçi okunmaz olur.

    imza = bayat SÜRECİN kimliği (başlangıç zamanı). Süreç değişip yeniden
    bayatlarsa yeni bir olaydır → hemen haber ver. Aynı süreç bayat kaldıkça
    saatte bir hatırlat.
    """
    if onceki.get("imza") != imza:
        return True                       # yeni olay: beklet me
    if "ts" not in onceki:
        return True                       # eksik durum: güvenli taraf uyarmak
    try:
        gecen = simdi - float(onceki["ts"])
    except (TypeError, ValueError):
        return True                       # durum bozuksa susma, uyar
    return gecen >= cooldown


BAYAT_IYILESTIR_SN = 10 * 60    # sn: bu kadar süredir bayat VE köprü boşta ise kendiliğinden yenile


def bayat_iyilestir_mi(imza, simdi, onceki, bosta, min_sure=BAYAT_IYILESTIR_SN):
    """Bayat köprü BU turda kendiliğinden yenilensin mi? (saf fonksiyon — test edilebilir)

    NEDEN VAR (2026-09-07): Ghost kodunu VPS'te düzenleyip commit'liyor; Mac'ten gelen
    deploy.sh restart atar ama VPS'teki commit'ten sonra kimse atmıyordu. Köprü 3 gün eski
    kodla koştu, nöbetçi saatte bir "BAYAT KOD" diye sahibi dürttü; Sahip "bu bildirim
    neden geliyor, çöz" dedi. Restart çalışan işi keser — o yüzden yalnız köprü BOŞTAYKEN
    (çocuk süreç yok, koşan iş yok) ve bayatlık en az min_sure sürmüşken (aynı süreç için
    ilk görüldüğü andan itibaren) yenilenir. Oturum kimliği diskte, --resume ile sürer.
    """
    if not bosta:
        return False
    if onceki.get("imza") != imza:
        return False                      # ilk görüş: önce kayda geç, bir sonraki turda karar
    try:
        ilk = float(onceki.get("ilk", onceki.get("ts")))
    except (TypeError, ValueError):
        return False
    return simdi - ilk >= min_sure


def _kopru_bosta(pid):
    """Köprünün çocuk süreci (claude oturumu) yok VE koşan iş yok → boşta. Ölçemezsek False.
    Mac'te de geçerli (2026-09-07): Mac köprüsü yedek (ROLE=mac) ve Mac'teki autoreload yalnız
    telegram_bridge.py'yi izliyor; ghost/*.py knowsync ile gelince Mac köprüsü bayat kalıyor,
    nöbetçi saatte bir 'BAYAT KOD' diyordu. kickstart çocuk oturum yokken güvenli."""
    if not pid:
        return False
    try:
        r = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True, timeout=10, env=_env)
        cocuklar = set(r.stdout.split())
        # Kalıcı sohbet süreci (ghost/kalici_oturum.py) boştayken de çocuk olarak
        # görünür; durum dosyası "meşgul değil" diyorsa çocuk sayılmaz (2026-09-07).
        try:
            from ghost import kalici_oturum
            kd = kalici_oturum.durum_oku()
            if kd.get("pid") and not kd.get("mesgul"):
                cocuklar.discard(str(kd["pid"]))
        except Exception:
            pass
        if cocuklar:
            return False
    except Exception:
        return False
    if os.path.isdir(JOBS_DIR):
        for fn in os.listdir(JOBS_DIR):
            if not fn.endswith(".json"):
                continue
            try:
                j = json.load(open(os.path.join(JOBS_DIR, fn), encoding="utf-8"))
            except Exception:
                continue
            if j.get("status") in ("çalışıyor", "denetimde"):
                return False
    return True


def _bayat_durum_oku():
    try:
        with open(BAYAT_STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _bayat_durum_yaz(imza, simdi, ilk=None):
    try:
        with open(BAYAT_STATE, "w", encoding="utf-8") as f:
            json.dump({"imza": imza, "ts": simdi, "ilk": ilk if ilk is not None else simdi}, f)
    except OSError as e:
        log("bayat durum yazılamadı: %r" % e)


TUNNEL_STATE = os.path.join(OPS, "tunnel_watch_state.json")
TUNNEL_STALE = 10 * 60   # user timer 2 dk'da bir atar; bu kadar sessizlik = ölü


def tunel_nobetcisi_yerinde():
    """Ters tünel nöbetçisi (ops/tunnel_watch.py) hâlâ atıyor mu — atmıyorsa
    yerine bir tur biz koş.

    Nöbetçi KULLANICI seviyesinde systemd timer'ıyla koşar (root yok), o da
    `loginctl enable-linger`e bağlı. Linger düşerse timer sessizce ölür ve
    zombi tünel arızası [[ters-tunel-zombi-port]] yine 131 dakika sürer.
    Bu fonksiyon o sessiz ölümün üstünü örtmesin diye var: selfcheck sistem
    timer'ıyla koşar, root kurulumludur, linger'dan bağımsızdır.
    Yalnız VPS'te anlamlı — tünel orada sonlanır.
    """
    if IS_MAC:
        return None
    try:
        taze = time.time() - os.path.getmtime(TUNNEL_STATE) < TUNNEL_STALE
    except OSError:
        taze = False
    if taze:
        return None
    try:
        p = subprocess.run([sys.executable, os.path.join(OPS, "tunnel_watch.py")],
                           capture_output=True, text=True, timeout=120)
    except Exception as e:
        return "🔌 Tünel nöbetçisi koşturulamadı: %r" % e
    log("tünel nöbetçisi bayattı — selfcheck yedeği koştu (rc=%d)" % p.returncode)
    if p.returncode != 0:
        return ("🔌 Ters tünel ölü ve nöbetçi düzeltemedi — Mac/telefon rayı "
                "kapalı olabilir (ops/tunnel_watch.log'a bak).")
    return None


def sahip_profili_yerinde():
    """ops/sahip.json (kişisel sabitlerin tek adresi, 2026-10-14) yoksa ya da bozuksa uyar:
    kod kırılmaz ama Ghost örnek/boş profille koşar — sessiz kimlik kaybı olmasın."""
    try:
        from ghost import sahip
        return sahip.durum()
    except Exception as e:
        return "👤 Sahip profili denetimi hata: %r" % e


def main():
    if not ENV.get("TELEGRAM_BOT_TOKEN"):
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN yok — .env okunamadı ya da eksik. "
            "Sessizce devam etmek yerine duruyorum (mesajlar hiçbir yere gitmezdi).")
    heartbeat.beat("selfcheck")
    # Nöbetçi-nöbeti: bu betik sağlıklıyken SESSİZ kalıyor; sessizlik hem
    # "yolunda" hem "öldüm" demek olduğu için diğerlerinin damgası yoklanır.
    problems = heartbeat.report(WATCHED)
    if not bridge_running():
        ok = revive()
        problems.append("🔴 Köprü çalışmıyordu — diriltme %s."
                        % ("başladı" if ok else "BAŞARISIZ, elle bak"))
    for jid, mins in stuck_jobs():
        problems.append("⏳ İş #%s %d dakikadır takılı." % (jid, mins))
    brain = brain_reachable()
    if brain is False:
        alt = [n for n, _c, _n in __import__("ghost.engines", fromlist=["x"]).available()
               if n != "claude"]
        problems.append("🧠 Beyin (Claude) YANIT VERMİYOR — auth/API/limit? "
                        + ("Yedek motor: %s" % ", ".join(alt) if alt
                           else "Yedek motor YOK (tek beyin)."))
    # Bayat kod: köprü ayakta ama diskteki koddan ESKİ bir süreç koşuyor.
    # Köprü BOŞTAYSA (çocuk oturum yok, koşan iş yok) ve bayatlık ≥10 dk sürdüyse
    # kendiliğinden yenilenir (bkz. bayat_iyilestir_mi); meşgulse yalnız uyarır.
    # Bu betik köprünün çocuğu DEĞİL (systemd timer) — senkron restart burada güvenli.
    pid = _bridge_pid()
    kod_ts, surec_ts = _kod_ts(), _surec_ts(pid)
    if bayat_kod(kod_ts, surec_ts):
        simdi = time.time()
        imza = "%d" % int(surec_ts)
        onceki = _bayat_durum_oku()
        ilk = onceki.get("ilk", onceki.get("ts")) if onceki.get("imza") == imza else simdi
        disk_s = time.strftime("%H:%M", time.localtime(kod_ts))
        surec_s = time.strftime("%d %b %H:%M", time.localtime(surec_ts))
        if bayat_iyilestir_mi(imza, simdi, onceki, bosta=_kopru_bosta(pid)):
            ok = revive()
            log("bayat kod + köprü boşta → yenilendi (%s)" % ("tamam" if ok else "BAŞARISIZ"))
            problems.append("♻️ Köprü boştaydı, bayat kod (disk %s, süreç %s) → yenilendi: %s"
                            % (disk_s, surec_s, "tamam" if ok else "BAŞARISIZ, elle bak"))
        elif bayat_uyarilsin(imza, simdi, onceki):
            # Aynı bayat süreç için saatte bir uyar (bkz. bayat_uyarilsin).
            _bayat_durum_yaz(imza, simdi, ilk)
            problems.append(
                "🧟 Köprü BAYAT KOD koşuyor — disk %s, süreç %s'den beri ayakta; "
                "köprü boşalınca kendiliğinden yenilenecek (elle: %s)"
                % (disk_s, surec_s,
                   "launchctl kickstart -k gui/501/%s" % BRIDGE_MAC if IS_MAC
                   else "systemctl restart %s" % BRIDGE_VPS))
        else:
            log("bayat kod sürüyor (süreç %s) — uyarı susturuldu, saatte bir"
                % time.strftime("%d %b %H:%M", time.localtime(surec_ts)))
    sorun = api_bayat_yenile(kod_ts)
    if sorun:
        problems.append(sorun)
    sorun = hook_bayat_uyar(kod_ts)
    if sorun:
        problems.append(sorun)
    sorun = kapi_kopyasi_uyar()
    if sorun:
        problems.append(sorun)
    for kontrol in (test_kapisi_yerinde, github_kimligi_yerinde,
                    tunel_nobetcisi_yerinde, sahip_profili_yerinde, onay_servisi_yerinde):
        sorun = kontrol()
        if sorun:
            problems.append(sorun)
    try:  # denetim günlüğü yazılamamış kayıt (governance.audit yedeği) — sessiz kayıp yok
        from ghost import governance as _gov
        if os.path.exists(_gov.AUDIT_YEDEK) and os.path.getsize(_gov.AUDIT_YEDEK) > 0:
            n = sum(1 for _ in open(_gov.AUDIT_YEDEK, encoding="utf-8", errors="replace"))
            problems.append("🧾 Denetim günlüğü %d kaydı yazamadı (yedek %s) — disk/izin kontrol et."
                            % (n, _gov.AUDIT_YEDEK))
    except Exception:
        pass
    problems.extend(cli_guncel_tut())
    problems.extend(mac_cli_guncel_tut())
    cleanup()
    try:  # abonelik kotası (Nabız "Kota" bölümü) — 15 dk'da bir tazele, sessiz
        from ghost import kota
        kota.yenile(min_yas=600)
    except Exception as e:
        log("kota yenileme hata: %r" % e)

    if problems:
        msg = "🩺 Ghost öz-denetim (%s):\n" % ("Mac" if IS_MAC else "VPS") + "\n".join(problems)
        log(msg.replace("\n", " | "))
        tg_send(msg)
    else:
        log("sağlıklı")


if __name__ == "__main__":
    main()
