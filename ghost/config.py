"""Tek kaynak config: yollar + .env/secrets.env yükleme.

Testler GHOST_DB / GHOST_OPS ortam değişkenleriyle yolları ezebilir.
"""
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Yollar env'den ÇAĞRI ANINDA çözülür (PEP 562), import anında donmaz. Sebep:
# `unittest discover` bazen config'i tests/common env'i ayarlamadan önce
# yükler; sabit olsalardı testler gerçek ops/ghost.db'ye düşer + kirletirdi.
# Tüketiciler (bus/memory/registry/goals) zaten config.DB_PATH'i fonksiyon
# içinde okuyor → dinamik çözüm production davranışını değiştirmez (prod'da
# env sabit), testlerde ise doğru temp yolu verir.
def _ops():
    return os.environ.get("GHOST_OPS") or os.path.join(BASE, "ops")


def __getattr__(name):
    if name == "OPS":
        return _ops()
    if name == "DB_PATH":
        return os.environ.get("GHOST_DB") or os.path.join(_ops(), "ghost.db")
    if name == "AUDIT_LOG":
        return os.path.join(_ops(), "audit.jsonl")
    if name == "FREEZE_FILE":
        return os.path.join(_ops(), "FREEZE")
    if name == "VERIFY_LOG":
        return os.path.join(_ops(), "verify.jsonl")
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def _parse_env_file(path):
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def load_env():
    """.env + secrets.env birleşik (secrets üstün gelir)."""
    env = _parse_env_file(os.path.join(BASE, ".env"))
    env.update(_parse_env_file(os.path.join(BASE, "secrets.env")))
    return env
