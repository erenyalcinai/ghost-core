#!/usr/bin/env python3
"""Ghost Mac işçisi.

VPS'teki Mac-kuyruğunu dışarı doğru yoklar (SSH), Mac-yerel işleri BURADA
(Mac'te, tam yetkiyle) çalıştırır, sonucu Telegram'a yollar. Mac'e inbound
erişim yok — sadece Mac'in başlattığı SSH. launchd 30 sn'de bir çalıştırır;
Mac uykudayken çalışmaz → işler VPS'te bekler, uyanınca akar.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import uuid

HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, "jarvis")
OPS = os.path.join(BASE, "ops")
LOG_FILE = os.path.join(OPS, "mac_worker.log")
sys.path.insert(0, OPS)
import mac_anahtar  # noqa: E402  — YubiKey kapısı: anahtar yoksa iş kuyrukta bekler
KILIT_IZ = os.path.join(OPS, ".mac_anahtar_kilitli")   # yalnız hal değişince log
SESSION_FILE = os.path.join(OPS, "mac_session.txt")  # süreklilik için oturum id
INTEL_SYNC_MARK = os.path.join(OPS, ".intel_sync")   # VPS→Mac bilgi senkronu damgası

ENV = {}
# .env + secrets.env (secrets üstün) — ghost/config.load_env ile AYNI semantik.
# 2026-08-21: burası eskiden yalnız .env okuyordu. Sır secrets.env'e konunca
# (doğru yer) Mac tarafı onu hiç görmüyordu ve özellik sessizce ölüydü —
# NTFY_TOPIC'te tam olarak bu oldu, ancak canlı test yakaladı. İki dosya da
# okunmalı ki "sırrı doğru yere koydum ama okunmuyor" tuzağı tekrarlamasın.
for _dosya in (".env", "secrets.env"):
    try:
        with open(os.path.join(BASE, _dosya), encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    ENV[_k] = _v.strip()
    except Exception:
        pass

VPS = os.environ.get("GHOST_VPS") or "ghost@" + ENV.get("GHOST_VPS_IP", "203.0.113.10")
SSH_KEY = os.path.join(HOME, ".ssh", "ghost_runtime")  # ghost-only anahtar
RQUEUE = "/home/ghost/jarvis/ops/mac-queue"
CLAUDE = ENV.get("CLAUDE_BIN", "/usr/local/bin/claude")

SSH = ["ssh", "-i", SSH_KEY, "-o", "ConnectTimeout=10",
       "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes",
       "-o", "ControlMaster=auto",
       "-o", "ControlPath=" + os.path.expanduser("~/.ssh/ghost_cm-%r@%h:%p"),
       "-o", "ControlPersist=120", VPS]


def log(msg):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(time.strftime("%F %T ") + msg + "\n")


def ssh(cmd, timeout=30):
    return subprocess.run(SSH + [cmd], capture_output=True, text=True, timeout=timeout)


def ssh_write(remote, data, timeout=60):
    """Mac→VPS dosya yazımı, atomik (tmp+mv). Yarım dosya okunmasın."""
    return subprocess.run(
        SSH + ["cat > %s.tmp && mv %s.tmp %s" % (remote, remote, remote)],
        input=data, text=True, capture_output=True, timeout=timeout)


def _sonuc_yaz(jid, name, prompt, result):
    """İş sonucunu VPS'e geri yaz.

    KÖK SEBEP (2026-08-20): sonuç YALNIZCA Telegram'a gidiyordu. Yani Ghost
    Mac'e iş verebiliyor ama KENDİ İŞİNİN SONUCUNU OKUYAMIYORDU — done/
    dosyasında sadece istek duruyordu. Tanı işi çalıştırıp raporu sahibin
    yapıştırmasını beklemek zorunda kalınca ortaya çıktı. Sonuç artık diskte:
    Ghost okuyabilir, geçmiş Telegram akışında kaybolmaz.
    """
    govde = ("# Mac işi #%s\n# %s\n\n## İstek\n%s\n\n## Sonuç\n%s\n"
             % (jid, time.strftime("%F %T"), prompt[:2000], (result or "")[:200000]))
    hedef = "%s/done/%s.sonuc.md" % (RQUEUE, name[:-5] if name.endswith(".json") else name)
    try:
        r = ssh_write(hedef, govde)
        if r.returncode != 0:
            log("sonuç VPS'e yazılamadı (#%s): %s" % (jid, (r.stderr or "")[:200]))
    except Exception as e:
        log("sonuç yazımı hata (#%s): %r" % (jid, e))


def send_ntfy(text):
    """Yedek push kanalı (ntfy.sh/<gizli konu>) — Telegram ulaşılamazsa.
    VPS tarafındaki ghost/notify.py'nin Mac karşılığı; buraya ghost paketini
    import etmemek için küçük tutuldu."""
    topic = ENV.get("NTFY_TOPIC")
    if not topic:
        return False
    try:
        req = urllib.request.Request("https://ntfy.sh/" + topic,
                                     data=text[:4000].encode("utf-8"),
                                     headers={"Title": "Ghost (Mac)"})
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except Exception as e:
        log("ntfy hata: %r" % e)
        return False


def send_telegram(text):
    """True=iletildi. Telegram ulaşılamazsa ntfy yedeğine düşer.

    NEDEN (2026-08-21): eskiden başarısızlık yalnız LOGLANIYORDU — yani
    Telegram kesildiğinde Mac işinin sonucu sahibe hiç ulaşmıyor, sadece
    kimsenin bakmadığı bir dosyaya yazılıyordu. Tek kanal riski tam olarak
    burada acıtıyor: iş bitmiş, sonuç kaybolmuş."""
    try:
        r = subprocess.run(["python3", os.path.join(OPS, "send_telegram.py")],
                           input=text, text=True, capture_output=True, timeout=60)
        if r.returncode != 0:
            log("send_telegram başarısız (rc=%d): %s" % (r.returncode, (r.stderr or "")[:200]))
            if send_ntfy(text):
                log("sonuç yedek kanaldan (ntfy) iletildi")
                return True
            return False
        return True
    except Exception as e:
        log("send_telegram hata: %r" % e)
        if send_ntfy(text):
            log("sonuç yedek kanaldan (ntfy) iletildi")
            return True
        return False


# Telefon işleri. Mac işleri BUGÜNE KADAR hiç MCP yüklemiyordu; iPhone yalnız
# Mac'e bağlı olduğu için telefonu ancak buradan sürebiliriz. Her işe yüklemek
# yanlış olurdu: mobile-mcp npx ile kalkıyor, her Mac işine saniyeler eklerdi.
# Bu yüzden köprüdeki _mcp_servers_for deseninin aynısı — anahtar kelimeyle daralt.
# NOT: sondaki \b BİLEREK yok. Türkçe eklemeli bir dil: "telefon\b" kalıbı
# "telefonu", "telefondan", "iPhone'da" gibi gerçek kullanımların HİÇBİRİNİ
# yakalamaz. Test edilince görüldü: "telefonu aç ve TikTok'a bak" eşleşmiyordu.
IPHONE_KW = re.compile(
    r"(\biphone|\btelefon|\bmobil cihaz|\bwda\b|\bwebdriveragent|"
    r"\bmobile-mcp|\bcihaz(?:ın|in)? ekran)", re.I)
MCP_MOBILE = os.path.join(BASE, "mcp-mobile.json")


def _claude_once(prompt, sid, resume):
    env = dict(os.environ)
    env["PATH"] = env.get("PATH", "") + ":/usr/local/bin:/opt/homebrew/bin"
    base = [CLAUDE, "-p", prompt, "--output-format", "json",
            "--dangerously-skip-permissions"]
    if IPHONE_KW.search(prompt or "") and os.path.exists(MCP_MOBILE):
        base += ["--mcp-config", MCP_MOBILE]
        log("iPhone işi: mobile-mcp yükleniyor")
    cmd = base + (["--resume", sid] if resume else ["--session-id", sid])
    return subprocess.run(cmd, cwd=BASE, capture_output=True, text=True,
                          timeout=1800, env=env)


def _parse_result(r, sid):
    """(sonuç|None, hata_mı). Başarıda oturumu kaydeder."""
    out = (r.stdout or "").strip()
    if out:
        try:
            obj = json.loads(out)
        except Exception:
            return out, False  # json değilse ham çıktı
        if obj.get("is_error"):
            return "⚠️ " + (obj.get("result") or "hata"), True
        try:
            open(SESSION_FILE, "w", encoding="utf-8").write(obj.get("session_id", sid))
        except Exception:
            pass
        return (obj.get("result") or obj.get("text") or "(boş)").strip(), False
    return "⚠️ Mac hata:\n" + (r.stderr or "bilinmeyen")[-1500:], True


def run_local(prompt):
    # Oturum sürekliliği: varsa devam et (--resume), yoksa yeni (--session-id)
    sid = None
    if os.path.exists(SESSION_FILE):
        try:
            sid = open(SESSION_FILE, encoding="utf-8").read().strip() or None
        except Exception:
            sid = None
    resume = sid is not None
    if not sid:
        sid = str(uuid.uuid4())
    try:
        r = _claude_once(prompt, sid, resume)
    except subprocess.TimeoutExpired:
        return "⏱ Mac işi zaman aşımı (30 dk)."
    result, err = _parse_result(r, sid)
    if not err or not resume:
        return result
    # Resume başarısız (oturum kaybolmuş/bozuk) → oturumu temizle,
    # BİR KEZ taze oturumla yeniden dene; yoksa bozuk id kalıcılaşır
    # ve her Mac işi sonsuza dek hata döner.
    try:
        os.remove(SESSION_FILE)
    except OSError:
        pass
    log("resume başarısız, taze oturumla yeniden deneniyor")
    sid = str(uuid.uuid4())
    try:
        r = _claude_once(prompt, sid, False)
    except subprocess.TimeoutExpired:
        return "⏱ Mac işi zaman aşımı (30 dk)."
    result, _ = _parse_result(r, sid)
    return result


def jkey(p):
    # iş id'sine göre sayısal sırala — sohbet bağlamında mesaj sırası önemli
    try:
        return int(os.path.basename(p).split(".")[0])
    except ValueError:
        return 0


def _kapi_kilitli():
    """YubiKey kapısı kilitliyse True; hal DEĞİŞİNCE tek satır log (30 sn'de bir
    koşan işçi her turda yazmasın). İş dosyalarına dokunulmaz — VPS kuyruğunda
    bekler, anahtar takılınca ilk turda akar."""
    kilitli = not mac_anahtar.acik()
    vardi = os.path.exists(KILIT_IZ)
    if kilitli and not vardi:
        log("KİLİTLİ: YubiKey takılı değil — kuyruk bekliyor")
        open(KILIT_IZ, "w").close()
    elif not kilitli and vardi:
        log("AÇILDI: YubiKey takıldı — kuyruk akıyor")
        os.remove(KILIT_IZ)
    return kilitli


def poll_once(quiet=False):
    """Bekleyen Mac işlerini işle. En az bir iş işlendiyse True döner.
    quiet=True: 'başladı' bildirimini atla (mac-modu akıcı olsun)."""
    if _kapi_kilitli():
        return False
    try:
        # Yalnız sayı-adlı iş dosyaları: claimed_*.json bu desene UYMAZ —
        # yarıda kalan iş bir sonraki turda yeniden çalıştırılmaz (deploy
        # gibi yıkıcı işlerin çift koşması burada engelleniyor).
        r = ssh("ls %s/[0-9]*.json 2>/dev/null" % RQUEUE)
    except Exception:
        return False  # VPS'e ulaşılamıyor (ağ yok) — sessiz geç
    files = [l.strip() for l in r.stdout.splitlines() if l.strip().endswith(".json")]
    if not files:
        return False
    files.sort(key=jkey)
    did = False
    for path in files:
        name = os.path.basename(path)
        claimed = "%s/claimed_%s" % (RQUEUE, name)
        try:
            # atomik sahiplen: mv başarısızsa başka tur almış, geç
            mv = ssh("mkdir -p %s/done && mv %s %s 2>/dev/null && cat %s" %
                     (RQUEUE, path, claimed, claimed))
        except Exception:
            break  # SSH koptu — kalanlar sonraki tura
        if mv.returncode != 0 or not mv.stdout.strip():
            continue
        try:
            did = _run_job(mv.stdout, path, claimed, name, quiet) or did
        except Exception as e:
            # İş henüz koşmadıysa geri bırak (güvenli yeniden deneme);
            # _run_job koşan işi kendi içinde arşivler, buraya düşmez.
            log("iş hatası (%s): %r — kuyruğa geri bırakılıyor" % (name, e))
            try:
                ssh("mv %s %s 2>/dev/null" % (claimed, path))
            except Exception:
                pass
    return did


def _finish(claimed, name):
    """Biten işi sil değil, arşivle — tamamlanmışların kaydı kalsın."""
    ssh("mv %s %s/done/%s 2>/dev/null || rm -f %s" % (claimed, RQUEUE, name, claimed))


def _run_job(raw, path, claimed, name, quiet):
    """Tek işi çalıştır. İş fiilen koştuysa istisnayı YUTAR (çift koşma olmasın);
    koşmadan hata olursa yukarı fırlatır (çağıran geri bırakır)."""
    try:
        job = json.loads(raw)
    except Exception:
        ssh("rm -f %s" % claimed)
        return False
    jid, prompt = job.get("id", "?"), job.get("prompt", "")
    if job.get("reset"):
        try:
            os.remove(SESSION_FILE)
        except OSError:
            pass
        send_telegram("🖥 Mac oturumu sıfırlandı (temiz bağlam).")
        _finish(claimed, name)
        log("Mac oturumu sıfırlandı")
        return True
    if job.get("kind") == "shell":
        # Deterministik Mac komutu — MODEL YOK. Bugüne kadar "git push",
        # "tail log", "launchctl print" gibi yargı gerektirmeyen işler için
        # tam bir Claude oturumu açılıyordu: dakikalar ve token, sıfır karar.
        # Bu yol komutu doğrudan koşturur; ajan oturumu yalnız gerçekten
        # düşünmek gereken işlere kalır.
        cmd = job.get("cmd") or prompt
        to = int(job.get("timeout") or 120)
        t0 = time.time()
        try:
            r = subprocess.run(["/bin/bash", "-lc", cmd], cwd=job.get("cwd") or BASE,
                               capture_output=True, text=True, timeout=to)
            cikti = ((r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr.strip() else "")).strip()
            rc = r.returncode
        except subprocess.TimeoutExpired:
            cikti, rc = "(zaman aşımı: %d sn)" % to, -1
        except Exception as e:
            cikti, rc = "(çalıştırılamadı: %r)" % e, -1
        sure = time.time() - t0
        # Diske TAM çıktı, Telegram'a kırpılmış: telefonda okunuyor.
        tam = "$ %s\n(rc=%d, %.1f sn)\n\n%s" % (cmd, rc, sure, cikti or "(boş)")
        _sonuc_yaz(jid, name, cmd, tam[:20000])
        kisa = cikti if len(cikti) <= 2500 else cikti[:2500] + "\n… (tamamı diskte)"
        # sessiz: rutin yoklamalar sahibin telefonunu titretmesin. İz kaybolmaz —
        # sonuç diske yazılır, VPS tarafında audit'e düşer.
        if not quiet and not job.get("sessiz"):
            send_telegram("🖥 Mac komutu (rc=%d, %.1f sn):\n$ %s\n\n%s"
                          % (rc, sure, cmd[:200], kisa or "(boş)"))
        _finish(claimed, name)
        log("shell #%s rc=%d (%.1fs): %s" % (jid, rc, sure, cmd[:80]))
        return True
    if job.get("rc"):
        # Remote-control oturumu (telefondaki Claude uygulamasında görünür)
        env = dict(os.environ)
        env["PATH"] = env.get("PATH", "") + ":/usr/local/bin:/opt/homebrew/bin"
        env.pop("CLAUDE_CODE_CHILD_SESSION", None)
        sname = "ghost-" + uuid.uuid4().hex[:6]
        rc_dir = os.path.join(OPS, "rc")
        os.makedirs(rc_dir, exist_ok=True)
        logf = os.path.join(rc_dir, sname + ".log")
        cmd = ["script", "-q", "/dev/null", CLAUDE, "--remote-control", sname]
        if prompt:
            cmd.append(prompt)
        try:
            with open(logf, "wb") as f:
                p = subprocess.Popen(cmd, cwd=BASE, stdout=f, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, env=env,
                                     start_new_session=True)
            # pid dosyası: cleanup_rc atıl oturumu bulup kapatabilsin
            with open(os.path.join(rc_dir, sname + ".pid"), "w") as pf:
                pf.write(str(p.pid))
            url = None
            for _ in range(12):
                time.sleep(2)
                try:
                    with open(logf, "rb") as lf:
                        txt = lf.read().decode("utf-8", "ignore")
                    m = re.search(r"https://claude\.ai/code/session_\w+", txt)
                    if m:
                        url = m.group(0)
                        break
                except Exception:
                    pass
            send_telegram("🖥 Remote-control oturumu başladı: %s%s" %
                          (sname, ("\n📱 " + url) if url else " — uygulamada görünecek"))
        except Exception as e:
            send_telegram("⚠️ Oturum başlatılamadı: %r" % e)
        _finish(claimed, name)
        log("rc oturum #%s" % jid)
        return True
    log("Mac işi #%s: %s" % (jid, prompt[:80]))
    if not quiet:
        send_telegram("🖥 Mac işi başladı...")
    result = run_local(prompt)
    # Sonucu ÖNCE diske yaz: Telegram düşerse bile kayıt kalsın.
    _sonuc_yaz(jid, name, prompt, result)
    # İş KOŞTU — bundan sonra ne olursa olsun kuyruğa geri dönmemeli
    # (çift çalıştırma yasağı). Sonuç iletilemezse logla ve arşivle.
    try:
        if not send_telegram("🖥 Mac işi tamamlandı:\n\n%s" % result):
            log("iş #%s sonucu Telegram'a iletilemedi; sonuç: %s"
                % (jid, result[:500]))
    finally:
        try:
            _finish(claimed, name)
        except Exception as e:
            log("arşivleme hatası (%s): %r" % (name, e))
        log("Mac işi #%s tamam" % jid)
        # İş bitti → hafıza senkronunu tetikle (damgayı sil; ≤30sn'de senkron olur).
        # Olay-bazlı senkron: öğrenilen şey periyot beklemeden karşıya geçer.
        try:
            os.remove(MEMSYNC_MARK)
        except OSError:
            pass
    return True


MEMSYNC_MARK = os.path.join(OPS, ".memsync")


def sync_memory():
    """İki yönlü hafıza + varlık grafiği birleştirme (Mac↔VPS). 30 dk'da bir;
    sessiz geçer. Yön: (1) Mac kayıtlarını VPS'e it → VPS birleştirir;
    (2) VPS kayıtlarını çek → Mac birleştirir. Dedup: memories (ts,text),
    entities ad, relations (src,rel,dst)."""
    try:
        if (os.path.exists(MEMSYNC_MARK)
                and time.time() - os.path.getmtime(MEMSYNC_MARK) < 1800):
            return
        sys.path.insert(0, BASE)
        from ghost import memsync
        local = memsync.dump_json()
        # (1) Mac → VPS: yerel JSON'u VPS'te birleştir. merge_json_v2 yetenek
        # işareti: VPS henüz eski koddaysa (deploy penceresi) dict yükü çöp
        # kayda çevirirdi — işaret yoksa push bu turda atlanır.
        push = subprocess.run(
            SSH + ["cd ~/jarvis && python3 -c 'import sys; "
                   "from ghost import memsync; "
                   "f = getattr(memsync, \"merge_json_v2\", None); "
                   "print(f(sys.stdin.read()) if f else \"eski\")'"],
            input=local, capture_output=True, text=True, timeout=60)
        if (push.stdout or "").strip() == "eski":
            log("memsync: VPS eski formatta, push atlandı (deploy gerekli)")
        # (2) VPS → Mac: VPS'in tüm kayıtlarını çek, yerelde birleştir
        pull = subprocess.run(
            SSH + ["cd ~/jarvis && python3 -c 'from ghost import memsync; "
                   "print(memsync.dump_json())'"],
            capture_output=True, text=True, timeout=60)
        added = memsync.merge_json(pull.stdout) if pull.returncode == 0 else 0
        with open(MEMSYNC_MARK, "w", encoding="utf-8") as f:
            f.write(str(int(time.time())))
        if added or (push.stdout or "").strip() not in ("", "0", "eski"):
            log("hafıza senkron: VPS'e +%s, Mac'e +%d"
                % ((push.stdout or "?").strip(), added))
    except Exception as e:
        log("memsync hata: %r" % e)


def sync_intel():
    """VPS'te üretilen bilgi 30 DK'da bir Mac'e çekilir — kanonik kaynak
    driftini kapatır: intel/ (brifing arşivi) + dossier/arastirmalar/
    (VPS'te üretilen araştırma dosyaları). Hata olursa sessiz geçer."""
    try:
        if (os.path.exists(INTEL_SYNC_MARK)
                and time.time() - os.path.getmtime(INTEL_SYNC_MARK) < 1800):
            return
        rsh = "ssh -i %s -o BatchMode=yes -o ConnectTimeout=10" % SSH_KEY
        for sub in ("intel", "dossier/arastirmalar"):
            subprocess.run(
                ["rsync", "-az", "-e", rsh,
                 "%s:jarvis/%s/" % (VPS, sub),
                 os.path.join(BASE, *sub.split("/")) + "/"],
                capture_output=True, timeout=120)
        with open(INTEL_SYNC_MARK, "w", encoding="utf-8") as f:
            f.write(str(int(time.time())))
    except Exception:
        pass


HARNESS_SYNC_MARK = os.path.join(OPS, ".harness_sync")
MAC_HMEM = os.path.expanduser("~/.claude/projects/-Users-sahip-jarvis/memory")
VPS_HMEM = ".claude/projects/-home-ghost-jarvis/memory"  # ghost home'a göre


def _idx_hedef(satir):
    """MEMORY.md satırından hedef hafıza dosyasını çıkar: '- [X](y.md) — ...' → 'y.md'.
    Başlık/boş satırlarda None döner (onlar taban tarafından olduğu gibi korunur)."""
    m = re.search(r"\]\(([^)]+\.md)\)", satir)
    return m.group(1) if m else None


def sync_harness():
    """Harness hafızası (Claude'un proje memory/ dizini) iki yönlü senkron —
    Ghost'un Mac'te ve VPS'te öğrendikleri tek beyin olur. 30 dk'da bir.
    Düz .md dosyaları: rsync -au (yeni olan kazanır, dosya-birleşim).
    MEMORY.md indeksi özel: HEDEF BAZLI birleşim (aşağıdaki nota bak)."""
    try:
        if (os.path.exists(HARNESS_SYNC_MARK)
                and time.time() - os.path.getmtime(HARNESS_SYNC_MARK) < 1800):
            return
        os.makedirs(MAC_HMEM, exist_ok=True)
        # MEMORY.md birleşimi HEDEF (dosya adı) bazlı — satır bazlı DEĞİL.
        # Eski davranış uzaktaki her yeni SATIRI ekliyordu: bir hafızayı
        # güncelleyince eski satır karşı makinede sağ kalıp geri geliyor,
        # indekste aynı dosyanın eski ve yeni hâli yan yana birikiyordu
        # (2026-08-21: 38 hafıza için 62 satır, owner-card.example tek başına 7 kez).
        # Kural: bir hedefin zaten satırı varsa uzaktaki sürüm EKLENMEZ;
        # yalnızca yerelde hiç bulunmayan hafızalar eklenir. Çakışmada
        # indeksi daha yeni olan taraf taban alınır (az önce güncellenen o).
        r = ssh("cat %s/MEMORY.md 2>/dev/null" % VPS_HMEM, timeout=20)
        rts = ssh("stat -c %%Y %s/MEMORY.md 2>/dev/null" % VPS_HMEM, timeout=20)
        idx = os.path.join(MAC_HMEM, "MEMORY.md")
        if r.returncode == 0 and r.stdout.strip():
            uzak = r.stdout.splitlines()
            yerel = []
            if os.path.exists(idx):
                yerel = open(idx, encoding="utf-8").read().splitlines()
            try:
                uzak_ts = int(rts.stdout.strip())
            except Exception:
                uzak_ts = 0
            yerel_ts = int(os.path.getmtime(idx)) if os.path.exists(idx) else 0
            taban, oteki = ((uzak, yerel) if uzak_ts > yerel_ts
                            else (yerel, uzak))
            gorulen, birlesik = set(), []
            for l in taban:  # tabanın kendi içindeki tekrarı da toparla
                h = _idx_hedef(l)
                if h and h in gorulen:
                    continue
                if h:
                    gorulen.add(h)
                birlesik.append(l)
            for l in oteki:
                h = _idx_hedef(l)
                if h and h not in gorulen and l.strip().startswith("- ["):
                    gorulen.add(h)
                    birlesik.append(l)
            out = "\n".join(birlesik) + "\n"
            if not os.path.exists(idx) or open(idx, encoding="utf-8").read() != out:
                with open(idx, "w", encoding="utf-8") as f:
                    f.write(out)
        # dosya birleşimi (iki yön, yeni kazanır) — birleşik MEMORY.md de yayılır
        rsh = "ssh -i %s -o BatchMode=yes -o ConnectTimeout=10" % SSH_KEY
        subprocess.run(["rsync", "-au", "-e", rsh, MAC_HMEM + "/",
                        "%s:%s/" % (VPS, VPS_HMEM)],
                       capture_output=True, timeout=60)
        subprocess.run(["rsync", "-au", "-e", rsh,
                        "%s:%s/" % (VPS, VPS_HMEM), MAC_HMEM + "/"],
                       capture_output=True, timeout=60)
        with open(HARNESS_SYNC_MARK, "w", encoding="utf-8") as f:
            f.write(str(int(time.time())))
    except Exception as e:
        log("harness senkron hata: %r" % e)


# Otomatik kapatma VARSAYILAN KAPALI (0). Elle yönetim var: /cc list, /cc stop.
# >0 yaparsan yalnızca o kadar dakika AKTİVİTESİZ (log mtime) kalan oturumlar
# kapanır — AKTİF/kullanılan oturum yaşı ne olursa olsun ASLA kapanmaz.
RC_IDLE_MIN = int(ENV.get("GHOST_RC_IDLE_MIN", "0"))


def cleanup_rc():
    """Ölü RC oturumlarını her zaman kayıttan sil (süreç zaten gitmiş).
    Canlı oturumlar: RC_IDLE_MIN=0 ise DOKUNULMAZ; >0 ise sadece o kadar dakika
    aktivitesiz kalanlar kapatılır. Aktif oturum asla kapanmaz (atıllık ölçütü)."""
    rc_dir = os.path.join(OPS, "rc")
    if not os.path.isdir(rc_dir):
        return
    now = time.time()
    for fn in os.listdir(rc_dir):
        if not fn.endswith(".pid"):
            continue
        pidf = os.path.join(rc_dir, fn)
        logf = os.path.join(rc_dir, fn[:-4] + ".log")
        try:
            pid = int(open(pidf).read().strip())
        except (ValueError, OSError):
            try:
                os.remove(pidf)
            except OSError:
                pass
            continue
        try:
            os.kill(pid, 0)  # yaşıyor mu?
        except OSError:
            try:
                os.remove(pidf)  # ölmüş — kaydı temizle
            except OSError:
                pass
            continue
        if RC_IDLE_MIN <= 0:
            continue  # otomatik kapatma kapalı → canlı oturuma dokunma
        try:
            idle = now - os.path.getmtime(logf)
        except OSError:
            continue  # log yoksa aktivite bilinmiyor → güvenli tarafta kal, dokunma
        if idle > RC_IDLE_MIN * 60:
            try:
                os.killpg(pid, 15)  # script+claude birlikte (kendi grubu)
            except OSError:
                try:
                    os.kill(pid, 15)
                except OSError:
                    pass
            try:
                os.remove(pidf)
            except OSError:
                pass
            log("rc oturumu kapatıldı: %s (%d dk aktivitesiz)" % (fn[:-4], idle // 60))


def reclaim_stale():
    """Claim'lenip yarıda kalmış işleri arşivle + haber ver. Bilerek yeniden
    ÇALIŞTIRMIYORUZ: iş komutu yıkıcı olabilir (deploy) ve ne kadarının
    koştuğu bilinmez — karar sahibe bırakılır."""
    try:
        r = ssh("find %s -maxdepth 1 -name 'claimed_*.json' -mmin +90" % RQUEUE)
        for p in [l.strip() for l in r.stdout.splitlines() if l.strip()]:
            n = os.path.basename(p)
            ssh("mkdir -p %s/done && mv %s %s/done/%s" % (RQUEUE, p, RQUEUE, n))
            log("yetim claimed iş arşivlendi: %s" % n)
            send_telegram("⚠️ Bir Mac işi yarıda kalmış (%s). Güvenlik için "
                          "yeniden çalıştırmadım, arşive taşıdım — gerekirse "
                          "/mac ile tekrar ver." % n)
    except Exception:
        pass  # ağ yoksa sessiz geç


KNOWSYNC_MARK = os.path.join(OPS, ".knowsync")


def sync_knowledge():
    """Bilgi dosyalarını (dossier/playbooks/CLAUDE.md/README/intel) git ile
    senkronla — drift'i kökten çözer. 30 dk'da bir: bilgi değiştiyse otomatik
    commit → VPS'teki çıplak repoya (jarvis.git) pull --rebase + push.
    Kod dosyalarına DOKUNMAZ (onlar elle commit + deploy.sh ile gider).
    Çakışmada rebase iptal edilir, log'a düşer — veri kaybı olmaz."""
    try:
        if (os.path.exists(KNOWSYNC_MARK)
                and time.time() - os.path.getmtime(KNOWSYNC_MARK) < 1800):
            return
        env = dict(os.environ)
        env["GIT_SSH_COMMAND"] = ("ssh -i %s -o BatchMode=yes -o ConnectTimeout=10"
                                  % SSH_KEY)

        def git(*args, timeout=90):
            return subprocess.run(["git", "-C", BASE, *args],
                                  capture_output=True, text=True,
                                  timeout=timeout, env=env)

        if git("remote", "get-url", "vps").returncode != 0:
            return  # uzak henüz kurulmamış — sessiz geç
        git("add", "dossier", "playbooks", "intel", "CLAUDE.md", "README.md")
        if git("diff", "--cached", "--quiet").returncode != 0:
            git("commit", "-m", "oto: bilgi senkron (mac)")
        pull = git("pull", "--rebase", "--autostash", "vps", "main", timeout=120)
        if pull.returncode != 0:
            git("rebase", "--abort")
            log("bilgi senkron: rebase çakışması — iptal edildi, elle bak")
        else:
            git("push", "vps", "main", timeout=120)
        with open(KNOWSYNC_MARK, "w", encoding="utf-8") as f:
            f.write(str(int(time.time())))
    except Exception as e:
        log("bilgi senkron hata: %r" % e)


def main():
    cleanup_rc()
    reclaim_stale()
    sync_intel()
    sync_memory()
    sync_harness()
    sync_knowledge()
    # Tek-seferlik /mac <iş> kuyruğu (Mac uykudayken bekleyen durumlar için).
    # İş çıkarsa kısa süre takip et (~6sn); boşsa çık (launchd 30sn'de bir çağırır).
    if not poll_once():
        return
    for _ in range(12):
        time.sleep(6)
        if not poll_once():
            break


if __name__ == "__main__":
    main()
