"""Ghost sürüm damgası — /v1/ping ve Nabız için (Faz 1.13, 2026-10-29).

NEDEN (denetim 02 e1): Kokpit "hangi sunucu koduyla konuşuyorum" sorusunu cevaplayamıyordu; sürüm kolonu yok.
Kaynak: git kısa sha + commit zamanı (bir kez okunur, önbellek); git yoksa dosya mtime."""
import os
import subprocess
import time

from ghost import config

_ONBELLEK = {}


def bilgi():
    """{"surum": "<kısa sha>", "zaman": "YYYY-MM-DD HH:MM", "build": <epoch int>} — asla fırlatmaz."""
    if _ONBELLEK:
        return dict(_ONBELLEK)
    surum, zaman = "", ""
    try:
        r = subprocess.run(["git", "log", "-1", "--format=%h %ct"], cwd=config.BASE, capture_output=True,
                           text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            surum, ct = r.stdout.split()
            zaman = time.strftime("%F %H:%M", time.localtime(int(ct)))
            build = int(ct)
        else:
            raise RuntimeError
    except Exception:
        try:
            build = int(os.path.getmtime(os.path.join(config.BASE, "ghost", "api.py")))
        except OSError:
            build = 0
        surum, zaman = "?", time.strftime("%F %H:%M", time.localtime(build)) if build else ""
    _ONBELLEK.update({"surum": surum, "zaman": zaman, "build": build})
    return dict(_ONBELLEK)
