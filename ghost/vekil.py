"""Sır vekili istemcisi (Faz 2 madde 16, 2026-10-28) — API çağrısının adresi ve kimlik başlığı tek yerde.

Vekil (`ops/vekil/vekil_servisi.py`, `ghost-vekil` kullanıcısı, 127.0.0.1:8797) kuruluysa çağrı vekile
gider ve kimlik başlığı EKLENMEZ (vekil ekler; anahtar bu süreçte yok). Kurulu değilse (açık çekirdek,
Mac, test) bugünkü gibi doğrudan servise gider, anahtar verilen ortamdan okunur.

    from ghost import vekil
    url = vekil.url("cloudflare", "/client/v4/zones", env)
    h = vekil.basliklar("cloudflare", env)

Kabukta: `$(python3 -m ghost.vekil url cloudflare /client/v4/zones)` ya da doğrudan
`curl http://127.0.0.1:8797/cloudflare/client/v4/zones` (vekil kuruluyken başlık gerekmez).
"""
import os
import pwd
import sys

TABAN = "http://127.0.0.1:8797"
KULLANICI = "ghost-vekil"

_DOGRUDAN = {
    "cloudflare": ("https://api.cloudflare.com", "CLOUDFLARE_API_TOKEN", "Authorization", "Bearer %s"),
    "posthog": (None, "POSTHOG_API_KEY", "Authorization", "Bearer %s"),
    "github": ("https://api.github.com", "GHOST_GITHUB_TOKEN", "Authorization", "token %s"),
    "telegram": ("https://api.telegram.org", "TELEGRAM_BOT_TOKEN", None, None),
    "telegram-dosya": ("https://api.telegram.org", "TELEGRAM_BOT_TOKEN", None, None),
    # 2. aşama
    "elevenlabs": ("https://api.elevenlabs.io", "ELEVENLABS_API_KEY", "xi-api-key", "%s"),
    "sentry": ("https://sentry.io", "SENTRY_ACCESS_TOKEN", "Authorization", "Bearer %s"),
    "asc": ("https://api.appstoreconnect.apple.com", "ASC_KEY_PATH", "Authorization", None),   # JWT
    "github-mcp": ("https://api.githubcopilot.com", "GITHUB_MCP_TOKEN", "Authorization", "Bearer %s"),
    "github-mcp-yaz": ("https://api.githubcopilot.com", "GITHUB_MCP_YAZ_TOKEN", "Authorization", "Bearer %s"),
    # 3. aşama
    "higgsfield": ("https://api.higgsfield.ai", "HF_API_KEY_SECRET", None, None),     # başlık: higgsfield.py kendisi
    "wikidata": ("https://www.wikidata.org", "WIKIDATA_BOT_PASS", None, None),         # parola gövdede yer tutucu
    "github-git": ("https://github.com", "GHOST_GITHUB_TOKEN", None, None),            # git: url.insteadOf
    "github-git-sahip": ("https://github.com", "GITHUB_MCP_TOKEN", None, None),
}
# Gövdedeki yer tutucular (vekil yalnız o servisin sabit host'una giderken gerçeğini koyar)
GOVDE_YER_TUTUCU = {"WIKIDATA_BOT_PASS": "__WIKIDATA_BOT_PASS__"}


def sir_ya_da_yer_tutucu(ad, env=None):
    """Vekil kuruluysa gövde yer tutucusu, değilse ortamdaki değer (Wikidata parolası gibi gövdede giden sırlar)."""
    if aktif(env, ad) and ad in GOVDE_YER_TUTUCU:
        return GOVDE_YER_TUTUCU[ad]
    return _env(env).get(ad, "")


def kurulu():
    try:
        pwd.getpwnam(KULLANICI)
        return True
    except KeyError:
        return False


_ERISIM = {}


def _erisilir():
    """Vekil portu açık mı? Süreç başına 30 sn önbellek. Düşükse ve anahtar hâlâ ortamdaysa doğrudan yola
    düşülür (geçiş dönemi, 1-3. aşama: köprünün Telegram'ı vekile bağlı kalmasın). 4. aşamada anahtar
    ghost'tan silinince düşülecek yol kalmaz — vekil systemd Restart=always ile ayakta tutulur."""
    import socket
    import time
    simdi = time.time()
    if simdi - _ERISIM.get("ts", 0) < 30:
        return _ERISIM["ok"]
    try:
        with socket.create_connection(("127.0.0.1", 8797), timeout=0.5):
            ok = True
    except OSError:
        ok = False
    _ERISIM.update(ts=simdi, ok=ok)
    return ok


def aktif(env=None, anahtar=None):
    if not kurulu() or os.environ.get("GHOST_TEST"):
        return False
    if _erisilir():
        return True
    return not (anahtar and _env(env).get(anahtar))   # vekil düşük + anahtar elde → doğrudan


def _env(env):
    return env if env is not None else os.environ


def url(servis, yol="", env=None):
    """Servisin tam URL'si. yol '/' ile başlar (telegram: '/sendMessage' gibi yöntem yolu)."""
    if servis not in _DOGRUDAN:
        raise KeyError(servis)
    if yol and not yol.startswith("/"):
        yol = "/" + yol
    if aktif(env, _DOGRUDAN[servis][1]):
        return "%s/%s%s" % (TABAN, servis, yol)
    taban, anahtar, _, _ = _DOGRUDAN[servis]
    e = _env(env)
    if servis == "posthog":
        taban = (e.get("POSTHOG_HOST") or "https://eu.posthog.com").rstrip("/")
        if not taban.startswith("http"):
            taban = "https://" + taban
    if servis == "telegram":
        return "%s/bot%s%s" % (taban, e.get(anahtar, ""), yol)
    if servis == "telegram-dosya":
        return "%s/file/bot%s%s" % (taban, e.get(anahtar, ""), yol)
    return taban + yol


def basliklar(servis, env=None):
    """Kimlik başlığı: vekil kuruluysa BOŞ (vekil ekler), değilse ortamdaki anahtarla."""
    if aktif(env, _DOGRUDAN[servis][1]):
        return {}
    _, anahtar, ad, bicim = _DOGRUDAN[servis]
    if servis == "asc":
        t = _asc_jwt_yerel(_env(env))
        return {"Authorization": "Bearer " + t} if t else {}
    deger = _env(env).get(anahtar, "")
    return {ad: bicim % deger} if (ad and deger) else {}


def _asc_jwt_yerel(e):
    """Vekil yokken (Mac, açık çekirdek) App Store Connect JWT'si yerelde, ortamdaki .p8 ile."""
    try:
        import time
        import jwt
        with open(e["ASC_KEY_PATH"], encoding="utf-8") as f:
            k = f.read()
        n = int(time.time())
        return jwt.encode({"iss": e["ASC_ISSUER_ID"], "iat": n, "exp": n + 600, "aud": "appstoreconnect-v1"},
                          k, algorithm="ES256", headers={"kid": e["ASC_KEY_ID"]})
    except Exception:
        return None


def apns_jwt(env=None):
    """APNs (HTTP/2, curl) için kısa ömürlü JWT: vekil kuruluysa ondan (.p8 vekilde), değilse None
    (çağıran yerelde imzalar)."""
    if not aktif(env, "APNS_KEY_PATH"):
        return None
    import urllib.request
    try:
        return urllib.request.urlopen(TABAN + "/_jwt/apns", timeout=5).read().decode().strip() or None
    except Exception:
        return None


def hazir(servis, env=None):
    """Bu servis çağrılabilir mi? (vekil kurulu ya da anahtar ortamda)"""
    return aktif(env, _DOGRUDAN[servis][1]) or bool(_env(env).get(_DOGRUDAN[servis][1]))


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["url"] and len(a) >= 2:
        print(url(a[1], a[2] if len(a) > 2 else "")) if aktif() else sys.exit(
            "vekil kurulu değil — doğrudan çağrı için anahtar ortamda: $%s" % _DOGRUDAN[a[1]][1])
    elif a[:1] == ["durum"]:
        import json
        import urllib.request
        if not aktif():
            print(json.dumps({"kurulu": kurulu(), "aktif": False}))
        else:
            print(urllib.request.urlopen(TABAN + "/_durum", timeout=5).read().decode())
    else:
        print("kullanım: python3 -m ghost.vekil url <servis> [yol] | durum")
        sys.exit(2)
