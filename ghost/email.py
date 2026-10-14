"""E-posta organı — giden (Resend API) + gelen (webhook üzerinden, worker CF'de).

Gelen: ops/email_worker.js → hooks.example.com/hook/email → webhook.handle_hook
'job' alanıyla claude'a triage ettirir (kod hazır, aktivasyon setup_email.sh).

Giden: send() Resend HTTP API kullanır (RESEND_API_KEY .env'e eklenince aktif).
Onay: e-posta göndermek 'permanent' sınıf — köprü guard'ı onaylatır.
"""
import json
import urllib.request

from ghost import config, governance

FROM_DEFAULT = "ghost@owner.example"


def can_send(env=None):
    env = config.load_env() if env is None else env
    return bool(env.get("RESEND_API_KEY"))


def send(to, subject, body, from_addr=None, env=None):
    """(ok, mesaj). RESEND_API_KEY yoksa nazikçe reddeder."""
    env = config.load_env() if env is None else env
    key = env.get("RESEND_API_KEY")
    if not key:
        return False, ("giden e-posta için RESEND_API_KEY gerekli "
                       "(resend.com — ücretsiz kota, .env'e ekle)")
    governance.audit("email", "send", governance.PERMANENT,
                     "%s | %s" % (to, subject[:80]))
    payload = json.dumps({
        "from": from_addr or env.get("GHOST_EMAIL_FROM", FROM_DEFAULT),
        "to": [to], "subject": subject, "text": body}).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=payload,
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
        return bool(d.get("id")), "gönderildi (%s)" % d.get("id", "?")
    except Exception as e:
        return False, "gönderim hatası: %r" % e
