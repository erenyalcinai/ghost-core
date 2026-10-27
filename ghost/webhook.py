"""Webhook alıcısı — dünya Ghost'a anında haber verir.

127.0.0.1:8787'de dinler; dışarıya Cloudflare Tunnel açar (hooks.<domain>).
Kimlik: X-Ghost-Token başlığı .env'deki WEBHOOK_SECRET ile eşleşmeli
(GitHub tarzı imzalı kaynaklar Faz 2+'da kaynak-özel doğrulama alır).

POST /hook/<kaynak>  gövde: JSON ya da düz metin.
Akış: olay veriyoluna kalıcı kayıt + sahibe kısa bildirim; 'job' alanı varsa
requests kuyruğuna claude işi bırakılır (webhook → otonom iş köprüsü).
iOS Kestirmesi/Siri de buraya konuşur: {"text": "..."} → sohbet işine döner.
Dışarıdan gelen işler "gate" bayrağıyla kuyruklanır: köprü, guard/sınıflandırma
riskli derse Telegram'da onay sorar — secret tek başına otonom yetki vermez.
"""
import hmac
import json
import os
import threading
import time
import uuid
from email.header import decode_header
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ghost import bus, config, governance, notify

HOST = "127.0.0.1"
PORT = int(os.environ.get("GHOST_HOOK_PORT", "8787"))
MAX_BODY = 256 * 1024


HIZ_TAVAN = 20        # kaynak başına saatte bu kadar metin işi; üstü 429 + tek uyarı
HIZ_PENCERE_SN = 3600


def _hiz_asildi(source, simdi=None):
    """Sır sızarsa Ghost'un komuta kanalı sınırsız olmasın: kaynak başına saatlik tavan.
    Durum dosyada (ops/webhook_hiz.json); tavana ilk çarpışta sahibe uyarı."""
    simdi = simdi or time.time()
    yol = os.path.join(config.OPS, "webhook_hiz.json")
    try:
        with open(yol, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    zamanlar = [t for t in d.get(source, []) if simdi - t < HIZ_PENCERE_SN]
    asildi = len(zamanlar) >= HIZ_TAVAN
    if not asildi:
        zamanlar.append(simdi)
    d[source] = zamanlar
    uyarildi = d.get("_uyari", {}).get(source, 0)
    if asildi and simdi - uyarildi > HIZ_PENCERE_SN:
        d.setdefault("_uyari", {})[source] = simdi
        governance.audit("webhook", source, governance.READ, "hız tavanı: %d/saat" % HIZ_TAVAN)
        _uyar("⚠️ Webhook '%s': saatte %d metin işi tavanı aşıldı, fazlası reddediliyor. "
              "Sen değilsen sır sızmış olabilir → WEBHOOK_SECRET'ı yenile." % (source, HIZ_TAVAN))
    tmp = yol + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, yol)
    return asildi


def _uyar(metin):
    if os.environ.get("GHOST_TEST"):
        return
    try:
        import subprocess
        import sys
        subprocess.Popen([sys.executable, os.path.join(config.BASE, "ops", "send_telegram.py")],
                         stdin=subprocess.PIPE).communicate(metin.encode(), timeout=20)
    except Exception:
        pass


def _queue_job(prompt, kind, announce=None, classify=None):
    """Dış kaynaklı istek → olay veriyolu (gate=True: onay kapısından geçer)."""
    from ghost import bus
    bus.istek(prompt, kind, "webhook", announce=announce, gate=True, classify=classify)


def _decode_subject(s):
    """RFC 2047 MIME konu başlığını insan diline çevir
    (=?UTF-8?q?... Telegram'a ham basılmasın)."""
    try:
        return "".join(
            t.decode(enc or "utf-8", "replace") if isinstance(t, bytes) else t
            for t, enc in decode_header(s)).strip() or s
    except Exception:
        return s


_seen_lock = threading.Lock()


def _email_dup(msgid):
    """Aynı Message-ID daha önce işlendi mi? Aynı mail birden çok yoldan
    gelebiliyor (example-mail→ghost@ doğrudan + Gmail oto-yönlendirmesinin geri
    getirdiği kopya) — ikinci kopya işlenmez, döngü burada kırılır."""
    if not msgid:
        return False
    path = os.path.join(config.OPS, "email_seen.json")
    now = time.time()
    with _seen_lock:
        try:
            with open(path, encoding="utf-8") as f:
                seen = json.load(f)
        except Exception:
            seen = {}
        seen = {k: t for k, t in seen.items() if now - t < 7 * 86400}
        dup = msgid in seen
        seen[msgid] = now
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(seen, f)
            os.replace(tmp, path)
        except OSError:
            pass
    return dup


SMS_SPOOL = "sms_spool.jsonl"
SMS_NABIZ = "sms_nabiz.json"


def _handle_sms(payload):
    """Gelen SMS: kaydet + bildir. İş AÇMAZ (bkz. handle_hook'taki gerekçe).

    Nabız (ping) sessizdir: ray canlı mı diye günde bir gelen yoklama,
    Telegram'ı kirletmemeli. Sessiz bozulmayı yakalamak için ops/sms_watch.py
    bu damgaya bakar — ray ölürse SMS gelmez ve kimse fark etmezdi.
    """
    if not isinstance(payload, dict):
        payload = {"raw": str(payload)[:2000]}
    simdi = time.strftime("%F %T")
    try:
        with open(os.path.join(config.OPS, SMS_NABIZ), "w",
                  encoding="utf-8") as f:
            json.dump({"ts": int(time.time()), "iso": simdi}, f)
    except OSError:
        pass
    if payload.get("ping"):
        governance.audit("webhook", "sms", governance.READ, "nabız")
        return 200, "pong"

    gonderen = str(payload.get("from") or payload.get("sender") or "?")[:80]
    metin = str(payload.get("body") or payload.get("text") or "")[:1000]
    rec = json.dumps({"ts": simdi, "from": gonderen, "text": metin},
                     ensure_ascii=False)
    try:
        with _seen_lock:
            with open(os.path.join(config.OPS, SMS_SPOOL), "a",
                      encoding="utf-8") as f:
                f.write(rec + "\n")
    except OSError:
        pass
    governance.audit("webhook", "sms", governance.READ,
                     "sms: %s | %s" % (gonderen, metin[:80]))
    threading.Thread(
        target=notify.telegram,
        args=("📱 SMS — %s\n%s" % (gonderen, metin[:400]),),
        daemon=True).start()
    return 200, "ok"


def handle_hook(source, payload, secret_ok):
    """(status_code, cevap). Test edilebilir saf işleyici."""
    if not secret_ok:
        return 403, "yetkisiz"
    if governance.is_frozen():
        return 503, "frozen"
    bus.emit("webhook", source, payload if isinstance(payload, dict)
             else {"raw": str(payload)[:2000]})
    if source == "sms":
        # SMS ASLA İŞ AÇMAZ — bu satır güvenlik sınırı, kolaylık değil.
        # Aşağıdaki 'text' dalı gelen metni doğrudan işe çeviriyor; SMS içeriği
        # ise DÜNYAYA AÇIK: numarayı bilen herkes yazabilir. "deploy yap" yazan
        # bir SMS iş kuyruğuna düşseydi, Ghost'un komuta kanalı bir telefon
        # numarasına indirgenmiş olurdu. Bu yüzden SMS yalnızca kaydedilir ve
        # bildirilir; ne yapılacağına Sahip karar verir.
        return _handle_sms(payload)
    if isinstance(payload, dict) and payload.get("olay"):
        # Duyu yolu (2026-10-26): iOS Kısayollar otomasyonu {"olay": "sarj_takildi"}
        # gibi bir OLAY bildirir, komut değil. Olay kaydedilir, eşleşen kurallar
        # koşar (ghost/kurallar.py); kural yoksa yalnız kayıt + ilk görülmede haber.
        if _hiz_asildi(source):
            return 429, "hız sınırı"
        olay = str(payload["olay"])[:80]
        governance.audit("webhook", source, governance.READ, "olay: %s" % olay)
        try:
            from ghost import kurallar
            kurallar.olay_kaydet(olay, payload, kaynak=source)
            kurallar.tetikle("olay", olay, payload)
        except Exception:
            pass
        return 200, "olay"
    if isinstance(payload, dict) and payload.get("text"):
        # Siri/Kestirme yolu: gelen metin doğrudan işe döner
        if _hiz_asildi(source):
            return 429, "hız sınırı"
        governance.audit("webhook", source,
                         governance.classify(str(payload["text"])),
                         str(payload)[:200])
        _queue_job(payload["text"], "kestirme",
                   "🎙 Kestirme komutu alındı, işleniyor")
    elif source == "email" and isinstance(payload, dict) and payload.get("raw"):
        # E-posta ANLIK İŞ AÇMAZ — mail başına bir model koşusu token
        # yakıyordu (çöp pazarlama maili dahil). Mail spool'a birikir; sabah
        # brifingi (morning_brief.sh → ops/email_digest.py) hepsini TEK ucuz
        # koşuda topluca triage edip dijest olarak yollar.
        msgid = str(payload.get("msgid") or "").strip()
        subj = _decode_subject(str(payload.get("subject") or "(konu yok)"))
        frm = str(payload.get("from") or "?")
        if _email_dup(msgid):
            governance.audit("webhook", source, governance.READ,
                             "yinelenen kopya atlandı: %s | %s"
                             % (frm[:80], subj[:80]))
            return 200, "dup"
        rec = json.dumps({"ts": time.strftime("%F %T"), "from": frm,
                          "subject": subj, "msgid": msgid,
                          "raw": str(payload.get("raw"))[:100000]},
                         ensure_ascii=False)
        with _seen_lock:
            with open(os.path.join(config.OPS, "email_spool.jsonl"),
                      "a", encoding="utf-8") as f:
                f.write(rec + "\n")
        governance.audit("webhook", source, governance.READ,
                         "spool'a alındı: %s | %s" % (frm[:100], subj[:100]))
    elif isinstance(payload, dict) and payload.get("job"):
        governance.audit("webhook", source,
                         governance.classify(str(payload["job"])),
                         str(payload)[:200])
        _queue_job(str(payload["job"]), "webhook",
                   "🪝 %s webhook işi başladı" % source)
    else:
        governance.audit("webhook", source, governance.READ,
                         str(payload)[:200])
        # bildirim ağa çıkar — cevabı bekletmesin
        threading.Thread(target=notify.telegram,
                         args=("🪝 Webhook: %s\n%s" % (source, str(payload)[:300]),),
                         daemon=True).start()
    return 200, "ok"


class _Handler(BaseHTTPRequestHandler):
    timeout = 30  # yarım bırakılan istek sunucuyu süresiz bekletmesin

    def log_message(self, fmt, *args):  # stdout'a değil kendi logumuza
        pass

    def do_POST(self):
        env = config.load_env()
        secret = env.get("WEBHOOK_SECRET", "")
        given = self.headers.get("X-Ghost-Token", "")
        secret_ok = bool(secret) and hmac.compare_digest(given, secret)
        try:
            length = min(int(self.headers.get("Content-Length", 0) or 0), MAX_BODY)
        except ValueError:
            length = 0
        try:
            raw = self.rfile.read(length).decode("utf-8", "ignore") if length else ""
        except OSError:
            return  # istemci koptu/oyalıyor — thread'i serbest bırak
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {"raw": raw[:2000]}
        source = self.path.strip("/").split("/")[-1] or "bilinmeyen"
        code, msg = handle_hook(source, payload, secret_ok)
        body = msg.encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # sağlık ucu (tunnel testi)
        body = b"ghost-hooks ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    # ThreadingHTTPServer: tek yavaş/kötü niyetli istemci (slowloris) diğer
    # webhook'ları bloklayamaz — her bağlantı kendi thread'inde.
    srv = ThreadingHTTPServer((HOST, PORT), _Handler)
    srv.daemon_threads = True
    governance.audit("webhook", "startup", governance.READ,
                     "%s:%d" % (HOST, PORT))
    srv.serve_forever()


if __name__ == "__main__":
    main()
