"""İzole yürütme — kaynak sınırlı subprocess.

v1 sınırları: süre (timeout, süreç GRUBU öldürülür), bellek (RLIMIT_AS),
çıktı boyutu, çalışma dizini, TEMİZ ortam (secrets miras alınmaz; çağıran
gerekirse env geçirir). Ağ izolasyonu v1'de YOK (gerçek ağ/dosya izolasyonu
için ileride container/nsjail katmanı — ARCHITECTURE.md). Her çalıştırma
çağıran tarafından denetim günlüğüne yazılmalı (governance.audit ile).

UYARI: cwd dışına çıkış engellenmez — `--dangerously-skip-permissions` ile
koşan claude host FS'ine erişebilir. Untrusted kod (bounty/hunt clone'ları)
için env sır-temizlenir (eski economy._tool_env deseni; paket 2026-10-29 arşivde) ama TAM izolasyon (FS/ağ
hapsi) firejail/nsjail gerektirir; otonom untrusted çalıştırmadan ÖNCE şart.
"""
import os
import resource
import signal
import subprocess
import time

MAX_OUT = 200_000  # karakter — taşan kısılır

# Çocuğa geçen ortam: yalnız zararsız temel değişkenler. CLOUDFLARE_API_TOKEN,
# POSTHOG_API_KEY gibi sırlar BİLEREK dışarıda — LLM-üretimi kod (synthesize)
# bu sandbox'ta koştuğu için miras kalan her sır sızma yüzeyidir.
_SAFE_VARS = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TERM", "TZ")


def _clean_env():
    return {k: os.environ[k] for k in _SAFE_VARS if k in os.environ}


def _limits(max_mem_mb, max_procs):
    def setlimits():
        # max_mem_mb None → RLIMIT_AS koyma. Sebep: RLIMIT_AS SANAL adres alanını
        # sınırlar (RSS değil); node-tabanlı claude başlangıçta büyük sanal alan
        # rezerve ettiğinden düşük sınır onu ÇÖKERTİR, yüksek sınır (>RAM) ise
        # korumaz. Gerçek RSS limiti cgroup/systemd-run gerektirir. Ağır işlerde
        # (claude) None geç + serialize et; hafif işlerde makul sınır kalsın.
        if max_mem_mb is not None:
            try:
                b = max_mem_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (b, b))
            except (ValueError, OSError):
                pass  # macOS RLIMIT_AS'i her zaman uygulamaz — timeout asıl fren
        # RLIMIT_NPROC KULLANICININ TÜM süreçlerini sayar (çocuğun değil). Sabit
        # düşük cap (eski 256), host'ta zaten o kadar süreç varsa çocukta HER
        # fork'u EAGAIN'e düşürür → git clone / claude motoru çalışmaz. Bu yüzden
        # opsiyonel: verilmezse KOYMA. Fork-bomb'u zaten timeout + süreç-grubu
        # kill (start_new_session) kapsıyor; NPROC yalnızca ek savunmaydı.
        if max_procs is not None:
            try:
                resource.setrlimit(resource.RLIMIT_NPROC, (max_procs, max_procs))
            except (ValueError, OSError):
                pass
    return setlimits


def run(cmd, timeout=120, cwd=None, env=None, max_mem_mb=1024, stdin_text=None,
        max_procs=None):
    """(rc, stdout, stderr, saniye) döndürür. Liste komut (shell=False).

    max_procs: RLIMIT_NPROC (None=koyma). Meşgul host'ta düşük değer git/claude
    gibi çok-süreçli işleri kırar; fork-bomb koruması timeout+pgkill'de."""
    t0 = time.time()
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            text=True, cwd=cwd, env=env if env is not None else _clean_env(),
            preexec_fn=_limits(max_mem_mb, max_procs), start_new_session=True)
    except Exception as e:
        return (-1, "", "sandbox hata: %r" % e, time.time() - t0)
    try:
        out, err = proc.communicate(input=stdin_text, timeout=timeout)
        return (proc.returncode, (out or "")[:MAX_OUT],
                (err or "")[:MAX_OUT], time.time() - t0)
    except subprocess.TimeoutExpired:
        # start_new_session=True → kendi süreç grubu; 'sh -c "evil &"' torunları
        # da dahil TÜM grup ölür (tek proc.kill torunları sağ bırakır).
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        try:
            out, _ = proc.communicate(timeout=5)
        except Exception:
            out = ""
        return (-9, (out or "")[:MAX_OUT], "TIMEOUT (%ds)" % timeout,
                time.time() - t0)
    except Exception as e:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        return (-1, "", "sandbox hata: %r" % e, time.time() - t0)
