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

if BASE not in sys.path:
    sys.path.insert(0, BASE)
from ghost import gunluk, notify, vekil  # noqa: E402  sır vekili (Faz 2); Telegram gönderimi tek yerde (Faz 2.9)
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
    gunluk.yaz("selfcheck", msg)   # journald: journalctl -t ghost-selfcheck (Faz 2.9)


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
        return
    try:
        if not notify.telegram(text, ENV, dokum=False):
            log("telegram gönderilemedi")
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
    # Faz 3.11 ön adım (denetim 03 4b, 2026-10-30): ops/mac-queue/done 473 bitmiş iş dosyası, GC yoktu — 30 gün.
    done_cutoff = time.time() - 30 * 86400
    for p in glob.glob(os.path.join(OPS, "mac-queue", "done", "*")):
        try:
            if os.path.isfile(p) and os.path.getmtime(p) < done_cutoff:
                os.remove(p)
                removed += 1
        except OSError:
            pass
    try:  # Faz 3.11 (2026-10-30): biten job/request olayları 30 gün sonra silinir (chat olaylarına dokunmaz)
        from ghost import bus as _bus
        n = _bus.gc()
        if n:
            log("temizlik: veriyolundan %d biten iş/istek olayı silindi" % n)
    except Exception as e:
        log("bus gc hata: %r" % e)
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
WATCHED = ["uptime", "biz_watch", "marka_deploy", "defter", "port_watch",
           # Faz 2.10 (2026-10-30): tüm nöbetçiler damga bırakır; hiç atmamış isim atlanır
           "tunnel_watch", "giris", "knowsync", "acik_nabiz", "chatgpt_export", "gmail_is_nabzi",
           "sms_watch", "iphone_watch", "listen", "brief", "yedek", "weekly"]


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


def vekil_yerinde():
    """Sır vekili (Faz 2, 2026-10-28) kuruluysa ayakta mı ve canlı kopya repo kaynağıyla aynı mı? Vekil düşükse git
    push, Telegram (anahtar ortamda kaldıkça doğrudan yola düşer), Gmail ve App Store çağrıları etkilenir."""
    if IS_MAC:
        return None
    try:
        from ghost import vekil
        if not vekil.kurulu():
            return None
        import urllib.request
        try:
            d = json.loads(urllib.request.urlopen(vekil.TABAN + "/_durum", timeout=5).read())
        except Exception as e:
            return ("🔐 Sır vekili CEVAP VERMİYOR (%s) — git push/Gmail/ASC etkilenir. "
                    "Mac'ten: ssh ghost_vps 'systemctl restart ghost-vekil'" % type(e).__name__)
        if d.get("eksik"):
            return "🔐 Sır vekilinde eksik anahtar: %s" % ", ".join(d["eksik"])
        import hashlib
        canli, repo = "/usr/local/lib/ghost-vekil/vekil_servisi.py", os.path.join(BASE, "ops", "vekil", "vekil_servisi.py")
        h = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()  # noqa: E731
        if os.path.exists(canli) and h(canli) != h(repo):
            isaret = os.path.join(BASE, "ops", ".vekil_fark_uyari")
            if not (os.path.exists(isaret) and time.time() - os.path.getmtime(isaret) < 86400):
                open(isaret, "w").close()
                return "🔐 Sır vekili: repo kaynağı ile canlı kopya FARKLI — kurulum için YubiKey ile ops/vekil/kur.sh"
    except Exception as e:
        log("vekil denetimi hata: %r" % e)
    return None


NABIZ_VPS = "ghost-nabiz"      # kullanıcı systemd birimi: nabız (monitör/zamanlayıcı/hedef/kural tick'leri, Faz 3.1)


def api_bayat_yenile(kod_ts):
    """Kokpit API (ghost.api) bayat kodla koşuyorsa hemen yenile.

    NEDEN (2026-09-07): Nabız'a "Kota" alanı eklendi, köprü yenilendi ama telefona
    Nabız'ı servis eden ghost-api 02:22'deki kodla koşmaya devam etti — alan hiç
    gitmedi, Sahip "gerçek kota gözükmüyor" dedi. API durumsuz ve istek başına
    kısa; köprüden farklı olarak boşta olmasını beklemeye gerek yok (3 sn kesinti,
    uygulama yeniden dener). Yalnız VPS."""
    return _kullanici_birimi_bayat_yenile(kod_ts, API_VPS, "Kokpit API")


def nabiz_bayat_yenile(kod_ts):
    """Nabız birimi (ghost.nabiz) bayat kodla koşuyorsa yenile (Faz 3.1). Durumsuz: tur arası yeniden
    başlatma kayıp vermez (vadeler DB'de); 5 sn boşlukta köprü yedek yola bile geçmez (eşik 120 sn)."""
    return _kullanici_birimi_bayat_yenile(kod_ts, NABIZ_VPS, "Nabız birimi")


SOHBET_VPS = "ghost-sohbet"    # kullanıcı systemd birimi: kalıcı Claude sohbet süreci (Faz 3.2)


def sohbet_bayat_yenile(kod_ts):
    """Sohbet sunucusu (ghost.sohbet_sunucu) bayat kodla koşuyorsa ve BOŞTAYSA yenile. Yenileme claude sürecini
    kapatır (sonraki tur --resume ile açar, transcript diskte); tur ortasında ya da teslim edilmemiş sonuç varken
    ASLA — Faz 3.2'nin amacı o cevabı yaşatmak."""
    try:
        from ghost import sohbet_istemci
        d = sohbet_istemci.sunucu_durumu()
        if d and (d.get("mesgul") or d.get("bekleyen")):
            return None
    except Exception:
        pass
    return _kullanici_birimi_bayat_yenile(kod_ts, SOHBET_VPS, "Sohbet sunucusu")


def _kullanici_birimi_bayat_yenile(kod_ts, birim, etiket):
    if IS_MAC:
        return None
    try:
        env = _user_env()
        r = subprocess.run(["systemctl", "--user", "show", birim, "-p", "MainPID", "--value"],
                           capture_output=True, text=True, timeout=10, env=env)
        pid = int(r.stdout.strip() or 0)
        if not pid:
            return None
        surec_ts = _surec_ts(pid)
        if not bayat_kod(kod_ts, surec_ts):
            return None
        r = subprocess.run(["systemctl", "--user", "restart", birim],
                           capture_output=True, text=True, timeout=30, env=env)
        ok = r.returncode == 0
        log("%s bayat kod (süreç %s) → yenilendi: %s" % (
            etiket, time.strftime("%d %b %H:%M", time.localtime(surec_ts)), "tamam" if ok else r.stderr[-200:]))
        return ("♻️ %s bayat koddaydı → yenilendi: %s" % (etiket, "tamam" if ok else "BAŞARISIZ, elle bak"))
    except Exception as e:
        log("%s bayat denetimi hata: %r" % (etiket, e))
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
    """Faz 1.9: bayat_state.json → durum tablosu ("selfcheck.bayat"); ilk okumada dosya taşınır."""
    try:
        from ghost import durum
        d = durum.tasi("selfcheck.bayat", BAYAT_STATE, {})
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _bayat_durum_yaz(imza, simdi, ilk=None):
    try:
        from ghost import durum
        durum.yaz("selfcheck.bayat", {"imza": imza, "ts": simdi, "ilk": ilk if ilk is not None else simdi})
    except Exception as e:
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
        from ghost import durum             # Faz 1.9: nöbetçi damgası durum tablosunda (`tunnel_watch`)
        ts = durum.ts("tunnel_watch")
        if ts is None:
            ts = os.path.getmtime(TUNNEL_STATE)   # henüz taşınmamış eski dosya
        taze = time.time() - ts < TUNNEL_STALE
    except Exception:
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


# --- Alarm sınıflaması (2026-10-29, ghost/alarm.py) -------------------------------------
# NEDEN: 30 günde selfcheck Telegram'a 115 mesaj attı; çoğu "bayat kod → yenilendi: tamam"
# (işi zaten yapmış) ve "BAYAT KOD koşuyor, boşalınca yenilenecek" (10-28'de 13 yenileme/24 sa).
# sahibin eli yok → GHOST sınıfı (Nabız'da sarı). sahibe gidenler: yenileme BAŞARISIZ (1 sa),
# Beyin yanıt vermiyor (2 sa), vekil/onay/kapı/köprü düşük (1 sa), root bekleyen kopya (günde 1),
# nöbetçi sessiz / denetim günlüğü / bilinmeyen (6 sa). Günlük tavan 5 alarm.py'de.
SOGUMA_1S, SOGUMA_2S, SOGUMA_6S, SOGUMA_GUN = 3600, 2 * 3600, 6 * 3600, 24 * 3600
SINIF_KURALLARI = (
    # (satırda aranan parçalar — hepsi geçmeli, sinif, anahtar, soğuma sn)
    (("BAŞARISIZ",), "sahip", "yenileme-basarisiz", SOGUMA_1S),
    (("→ yenilendi",), "ghost", "bayat-yenilendi", 0),
    (("BAYAT KOD koşuyor",), "ghost", "bayat-kosuyor", 0),
    (("Beyin", "YANIT VERMİYOR"), "sahip", "beyin", SOGUMA_2S),
    (("Köprü çalışmıyordu",), "sahip", "kopru-dusuk", SOGUMA_1S),
    (("Sır vekili", "CEVAP VERMİYOR"), "sahip", "vekil-dusuk", SOGUMA_1S),
    (("Sır vekilinde eksik anahtar",), "sahip", "vekil-eksik", SOGUMA_GUN),
    (("Onay servisi", "CEVAP VERMİYOR"), "sahip", "onay-dusuk", SOGUMA_1S),
    (("Güvenlik kapısı",), "sahip", "kapi-kopyasi", SOGUMA_GUN),
    (("ghost-hook", "bayat"), "sahip", "hook-bayat", SOGUMA_GUN),
    (("canlı kopya FARKLI",), "sahip", "kopya-farkli", SOGUMA_GUN),
    (("Claude CLI",), "ghost", "cli-guncellendi", 0),
    (("Model eşlemesi bayat",), "sahip", "model-eslemesi", SOGUMA_GUN),
    (("dakikadır takılı",), "ghost", "is-takili", 0),
    (("NÖBETÇİ SESSİZ",), "sahip", "nobetci-sessiz", SOGUMA_6S),
)


def sinifla(satir):
    """Saf: öz-denetim satırı → (sinif, anahtar, soguma_sn). Bilinmeyen satır sahibe, 6 sa."""
    for parcalar, sinif, anahtar, soguma in SINIF_KURALLARI:
        if all(p in satir for p in parcalar):
            return sinif, anahtar, soguma
    import hashlib
    return "sahip", "diger-" + hashlib.sha1(satir[:40].encode("utf-8")).hexdigest()[:8], SOGUMA_6S


def alarmlari_dagit(problems, bildir=None, simdi=None):
    """Satırları sınıflandırıp alarm kapısından geçirir; sahibe gidecekleri TEK mesajda toplar.
    Döner: (eren_satirlari, ghost_satirlari). `bildir` test için enjekte edilir."""
    if bildir is None:
        from ghost import alarm
        bildir = alarm.bildir
    giden, kalan = [], []
    toplayici = giden.append      # alarm.py tavan/soğuma kararını verir, gönderimi biz toplarız
    for satir in problems:
        sinif, anahtar, soguma = sinifla(satir)
        r = bildir("selfcheck", satir, sinif=sinif, anahtar=anahtar, soguma_sn=soguma,
                   simdi=simdi, gonder=lambda m: (toplayici(m) or True))
        if r.get("neden") != "gitti":
            kalan.append(satir)
    return giden, kalan


def main():
    if not vekil.hazir("telegram", ENV):
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
        problems.append("🧠 Beyin (Claude) YANIT VERMİYOR — auth/API/limit? "
                        "Yedek motor YOK (tek beyin; codex yalnız üçüncü göz, Faz 2.9).")
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
    sorun = nabiz_bayat_yenile(kod_ts)
    if sorun:
        problems.append(sorun)
    sorun = sohbet_bayat_yenile(kod_ts)
    if sorun:
        problems.append(sorun)
    sorun = hook_bayat_uyar(kod_ts)
    if sorun:
        problems.append(sorun)
    sorun = kapi_kopyasi_uyar()
    if sorun:
        problems.append(sorun)
    for kontrol in (test_kapisi_yerinde, github_kimligi_yerinde,
                    tunel_nobetcisi_yerinde, sahip_profili_yerinde, onay_servisi_yerinde, vekil_yerinde):
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
    if not IS_MAC:
        try:  # Boşta Ghost v1 (Faz 1.7, 2026-10-29): günde bir, köprü boşta, pencere <%50 → tek satır ya da sessiz
            import bosta_ghost
            kostu, ihtiyac, satir = bosta_ghost.tick(bosta=_kopru_bosta(pid))
            if kostu:
                log("boşta ghost koştu: ihtiyaç %s → %s" % (ihtiyac, satir or "sessiz"))
        except Exception as e:
            log("boşta ghost hata: %r" % e)
        try:  # Epizod özeti (Faz 1.3, 2026-10-29): 30 dk sessiz, ≥3 turlu, özetlenmemiş döküm → araçsız alt model, tur başına ≤2
            from ghost import epizod
            ids, _kucuk = epizod.tick()
            if ids:
                log("epizod özetlendi: %s" % ids)
        except Exception as e:
            log("epizod hata: %r" % e)
        try:  # Deploy → etki kartı (Faz 1.8): 24 sa / 7 gün dolan sürüm kayıtlarını ölçüp Masa'ya tablo
            from ghost import etki
            dok = etki.tick()
            if dok:
                log("etki kartı güncellendi: %s" % dok)
        except Exception as e:
            log("etki hata: %r" % e)
        try:  # Mağaza yorumlarına cevap organı (Faz 2.3, 2026-10-29): saatte bir cevapsız yorum → taslak + onay-komut kartı (günde ≤5)
            import yorum_cevap
            _acilan = yorum_cevap.tick()
            if _acilan:
                log("yorum cevap kartı açıldı: %s" % ", ".join("%s:%s→%s" % a for a in _acilan))
        except Exception as e:
            log("yorum cevap hata: %r" % e)
        try:  # Haftalık GÜNDEM (Faz 2.2, 2026-10-29): 24 sa sessizlik → aktif; Pazar ≥20:30 gündem kartı; Cuma ≥18:00 karne
            from ghost import gundem
            _g = gundem.tick()
            if _g:
                log("gündem: %s" % ", ".join(_g))
        except Exception as e:
            log("gündem hata: %r" % e)
        try:  # Tek Gün kartı (Faz 2.8, 2026-10-29): sabah parçaları tek mesaj — telefon ilk hareket edince ya da 09:00
            from ghost import gun_karti
            _m = gun_karti.tick()
            if _m:
                log("tek gün kartı gönderildi (%d karakter)" % len(_m))
        except Exception as e:
            log("tek gün kartı hata: %r" % e)
        try:  # Uzun ufuklu iş bekçisi (Faz 2.6, 2026-10-30): rapor vermeyen adım işi → soru/tamam; FREEZE → iptal
            from ghost import uzun_is
            _u = uzun_is.tick()
            if _u:
                log("uzun iş: %s" % ", ".join(_u))
        except Exception as e:
            log("uzun iş hata: %r" % e)
        try:  # Gece vardiyası (Faz 2.1, 2026-10-30): pencere içinde hedef kuyrukla, pencere bitince sabah kartına rapor
            from ghost import vardiya
            _v = vardiya.tick()
            if _v:
                log("vardiya: %s" % ", ".join(_v))
        except Exception as e:
            log("vardiya hata: %r" % e)
        try:  # Haftalık sentez dosyası (dossier/sentez/*.md, Faz 1.11) → Masa "Raporlar" kartı; özerk iş Masa'ya yazamaz, burası yazar
            import glob as _glob
            from ghost import masa as _masa, durum as _durum
            _yayinlanan = set(_durum.oku("sentez.yayinlanan", []) or [])
            for _f in sorted(_glob.glob(os.path.join(BASE, "dossier", "sentez", "*.md"))):
                _ad = os.path.basename(_f)
                if _ad in _yayinlanan:
                    continue
                _masa.yayinla(_f, "Raporlar", "Haftalık sentez %s" % _ad[:-3])
                _yayinlanan.add(_ad)
                log("sentez Masa'ya yayınlandı: %s" % _ad)
                _durum.yaz("sentez.yayinlanan", sorted(_yayinlanan))
        except Exception as e:
            log("sentez yayın hata: %r" % e)
        try:  # Root işleri tek kart (Faz 1.14): YubiKey/ghost_vps isteyen uyarılar günde bir "Root işleri" kartında toplanır
            _root = [p for p in problems if ("YubiKey" in p or "ghost_vps" in p or "ghost_root_sk" in p)]
            from ghost import durum as _durum
            _rdurum = _durum.oku("selfcheck.root_kart", {}) or {}
            _imza = "|".join(sorted(x[:60] for x in _root))
            if _root and (time.time() - float(_rdurum.get("ts", 0)) > 86400 or _rdurum.get("imza") != _imza):
                from ghost import sahne as _sahne
                _sahne.onay("Root işleri (YubiKey) — %d madde" % len(_root),
                            "\n".join("• " + x for x in _root) + "\n\nReçete: playbooks/onay-komut.md §Root")
                _durum.yaz("selfcheck.root_kart", {"ts": time.time(), "imza": _imza})
                log("root işleri kartı açıldı (%d)" % len(_root))
        except Exception as e:
            log("root işleri kartı hata: %r" % e)

    if problems:
        log(("öz-denetim: " + " | ".join(problems)))
        giden, kalan = alarmlari_dagit(problems)
        if kalan:
            log("Nabız'da kaldı (Ghost sınıfı/soğuma/tavan): " + " | ".join(kalan))
        if giden:
            msg = "🩺 Ghost öz-denetim (%s):\n" % ("Mac" if IS_MAC else "VPS") + "\n".join(giden)
            tg_send(msg)
    else:
        log("sağlıklı")


if __name__ == "__main__":
    main()
