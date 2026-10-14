"""Ses organı — Ghost artık konuşur.

İki motor (2026-09-26, Sahip kararı "ElevenLabs ile başlayalım"):
  • elevenlabs — secrets.env'de ELEVENLABS_API_KEY varsa VARSAYILAN. Model
    eleven_flash_v2_5 (ucuz, ~75 ms; ELEVENLABS_MODEL ile değişir), ses
    ELEVENLABS_VOICE_ID (yoksa /v1/voices'tan ilk ses; önbelleğe alınır).
    Fiyat: 1000 karakter ≈ 5¢ (Flash) / 10¢ (v3, multilingual v2).
  • edge — edge-tts (ücretsiz, tr-TR-EmelNeural). Anahtar yoksa ya da ElevenLabs
    hata verirse buraya düşer; cevap hiç sessiz kalmaz.
GHOST_TTS=edge|elevenlabs ortam değişkeni seçimi zorlar. Son kullanılan motor
`son_motor` değişkeninde (teşhis). CLI: python3 -m ghost.voice "metin" [--motor x] [--gonder]
tts() metni mp3'e çevirir; send_voice() Telegram'a sesli mesaj olarak yollar
ve Kokpit dökümüne ses balonu düşürür.
Köprü: /sesli on|off — açıkken sohbet cevapları ses olarak da gider;
sesli mesajla sorulunca cevap otomatik seslidir (sesle sor, sesle al).
"""
import json
import os
import subprocess
import time
import urllib.request
import uuid

from ghost import config

VOICE = "tr-TR-EmelNeural"
MAX_CHARS = 900  # uzun metnin ilk kısmı seslendirilir (dinlenebilirlik)
ELEVEN_API = "https://api.elevenlabs.io/v1"
ELEVEN_MODEL = "eleven_flash_v2_5"
son_motor = None          # "elevenlabs" | "edge" | None — son tts() hangi motorla üretti
_eleven_voice_cache = {}


def _eleven_key(env):
    return (env.get("ELEVENLABS_API_KEY") or os.environ.get("ELEVENLABS_API_KEY") or "").strip()


def _eleven_voice_id(env, key):
    """ELEVENLABS_VOICE_ID yoksa hesabın ses listesinden ilkini al (bir kez)."""
    vid = (env.get("ELEVENLABS_VOICE_ID") or os.environ.get("ELEVENLABS_VOICE_ID") or "").strip()
    if vid:
        return vid
    if "id" in _eleven_voice_cache:
        return _eleven_voice_cache["id"]
    req = urllib.request.Request(ELEVEN_API + "/voices", headers={"xi-api-key": key})
    try:
        js = json.loads(urllib.request.urlopen(req, timeout=20).read())
        sesler = js.get("voices") or []
        vid = sesler[0]["voice_id"] if sesler else ""
    except Exception:
        vid = ""
    _eleven_voice_cache["id"] = vid
    return vid


def _tts_elevenlabs(speech, out, env, timeout=60):
    """ElevenLabs REST → mp3. Başarısızsa False (çağıran edge'e düşer)."""
    key = _eleven_key(env)
    if not key:
        return False
    vid = _eleven_voice_id(env, key)
    if not vid:
        return False
    model = (env.get("ELEVENLABS_MODEL") or os.environ.get("ELEVENLABS_MODEL") or ELEVEN_MODEL).strip()
    body = json.dumps({"text": speech, "model_id": model,
                       "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}).encode()
    req = urllib.request.Request(
        "%s/text-to-speech/%s?output_format=mp3_44100_128" % (ELEVEN_API, vid), data=body,
        headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        if len(data) < 1000:
            return False
        with open(out, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


def _strip_for_speech(text):
    """Konuşmaya uygunlaştır: emoji/işaret gürültüsünü azalt."""
    keep = []
    for ch in text:
        if ch.isalnum() or ch.isspace() or ch in ".,!?;:()%-–'\"/":
            keep.append(ch)
    out = "".join(keep)
    return " ".join(out.split())[:MAX_CHARS]


def tts(text, voice=VOICE, timeout=60, motor=None):
    """Metin → mp3 dosya yolu (None = üretilemedi). motor: None=otomatik, "elevenlabs", "edge"."""
    global son_motor
    speech = _strip_for_speech(text)
    if not speech:
        return None
    out = os.path.join(config.OPS, "inbox", "tts_%s.mp3" % uuid.uuid4().hex[:8])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    secenek = (motor or os.environ.get("GHOST_TTS") or "").strip().lower()
    if secenek != "edge":
        try:
            sirlar = config.load_env()
        except Exception:
            sirlar = {}
        if _eleven_key(sirlar) and _tts_elevenlabs(speech, out, sirlar, timeout=timeout):
            son_motor = "elevenlabs"
            return out
        if secenek == "elevenlabs":
            son_motor = None
            return None            # açıkça istendi, düşme yok — hata görünür kalsın
    son_motor = "edge"
    env = dict(os.environ)
    env["PATH"] = env.get("PATH", "") + ":" + os.path.expanduser("~/.local/bin")
    try:
        r = subprocess.run(
            ["python3", "-m", "edge_tts", "--voice", voice,
             "--text", speech, "--write-media", out],
            capture_output=True, timeout=timeout, env=env)
        if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
            return out
    except Exception:
        pass
    try:
        os.remove(out)
    except OSError:
        pass
    return None


def send_voice(chat_id, mp3_path, caption="", env=None):
    """Telegram sendAudio (mp3). (ok döner; dosyayı temizler.)"""
    env = config.load_env() if env is None else env
    token = env.get("TELEGRAM_BOT_TOKEN")
    if not token or not mp3_path:
        return False
    try:  # kokpit dökümü: sesli yanıt uygulamada oynatılabilsin (dosya silinmeden önce kopyalanır)
        from ghost import kokpit
        kokpit.outbox_yaz("", "ghost", medya=kokpit.medya_sakla(mp3_path, ad="ghost.mp3", tur="ses"))
    except Exception:
        pass
    boundary = "----ghostvoice" + os.urandom(6).hex()
    with open(mp3_path, "rb") as f:
        audio = f.read()
    parts = [("chat_id", None, str(chat_id))]
    if caption:
        parts.append(("caption", None, caption[:900]))
    parts.append(("audio", "ghost.mp3", audio))
    body = b""
    for name, fname, data in parts:
        body += ("--%s\r\n" % boundary).encode()
        if fname:
            body += ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                     % (name, fname)).encode()
            body += b"Content-Type: audio/mpeg\r\n\r\n" + data + b"\r\n"
        else:
            body += ('Content-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                     % (name, data)).encode()
    body += ("--%s--\r\n" % boundary).encode()
    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/sendAudio" % token, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
        return bool(resp.get("ok"))
    except Exception:
        return False
    finally:
        try:
            os.remove(mp3_path)
        except OSError:
            pass


def speak_to(chat_id, text):
    """Kısayol: üret + yolla. Sessizce başarısız olur (metin zaten gitti)."""
    path = tts(text)
    if path:
        return send_voice(chat_id, path)
    return False


if __name__ == "__main__":
    import sys
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); sys.exit(0)
    motor = None
    gonder = False
    metin = []
    i = 0
    while i < len(argv):
        if argv[i] == "--motor":
            motor = argv[i + 1]; i += 2
        elif argv[i] == "--gonder":
            gonder = True; i += 1
        else:
            metin.append(argv[i]); i += 1
    yol = tts(" ".join(metin), motor=motor)
    print("motor=%s dosya=%s" % (son_motor, yol))
    if yol and gonder:
        env = config.load_env()
        sahip = env.get("TELEGRAM_OWNER_ID")
        if not sahip:
            with open(os.path.join(config.OPS, "owner.json"), encoding="utf-8") as f:
                sahip = json.load(f)["id"]
        print("gönderildi" if send_voice(sahip, yol) else "gönderilemedi")
