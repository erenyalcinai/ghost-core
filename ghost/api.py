"""Ghost Kokpit API — iOS uygulamasının konuştuğu HTTP yüzeyi.

Ayrı süreç (ghost-api, kullanıcı systemd birimi), 127.0.0.1:8790; dışarıya
nginx `ghost-api.example.com` (Cloudflare-only ufw) üzerinden. Webhook'a
(ghost/webhook.py) EKLENMEDİ: farklı kimlik modeli (cihaz anahtarı + HMAC),
GET ağırlıklı ve uzun yoklama ister, köprü çökse de ayakta kalmalı.

Yazma yolları köprüyü ASLA doğrudan değiştirmez: mesaj → kokpit_inbox,
karar → kokpit_kararlar (tek yazar köprü kalır). Okuma: ghost.db (outbox),
pending.json, heartbeats vb. — bkz. ghost/kokpit.py, ghost/health.py.
"""
import json
import os
import re
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from ghost import (akis, bolgeler, canli, channels, config, duyular, governance, gorevler,
                   health, kokpit, masa, sahip, sahne)

config.load_env()
PORT = int(os.environ.get("GHOST_API_PORT", "8790"))
BODY_MAX = 64 * 1024
PAYLAS_MAX = 1800 * 1024   # /v1/paylas ham görsel (nginx client_max_body_size 2m)
SSE_SURE = 75              # SSE bağlantı ömrü (nginx proxy_read_timeout 90 sn'nin altında)
HIZ_DAKIKA = 240          # cihaz başına dakikada istek tavanı (uzun yoklama dahil)
_hiz = {}
_hiz_lock = threading.Lock()

SITE_META = sahip.site_meta()   # statik başlıklar profilden; canlılık uptime_state'ten


def _komutlar():
    """Kayıt defteri (ghost/komutlar.py) + köprüdeki BOT_COMMANDS (kaynak metinden,
    köprü modülü ağır) — tekrarsız, kayıt defteri önde."""
    from ghost import cc_komutlar, komutlar
    out = [{"komut": k, "aciklama": a, "grup": "ghost"} for k, a in komutlar.liste()]
    gorulen = {o["komut"] for o in out}
    try:
        src = open(os.path.join(config.BASE, "ops", "telegram_bridge.py"), encoding="utf-8").read()
        blok = src.split("BOT_COMMANDS = [", 1)[1].split("\n]", 1)[0]
        for l in blok.splitlines():
            l = l.strip()
            if l.startswith('("'):
                k, a = l.strip("(),").split('",', 1)
                ad = "/" + k.strip('" ')
                if ad not in gorulen:
                    gorulen.add(ad)
                    out.append({"komut": ad, "aciklama": a.strip().strip('"'), "grup": "ghost"})
    except Exception:
        pass
    # Claude Code grubu: skill'ler + çalışan yerleşikler. Ghost'ta aynı adla kayıtlı olan
    # (/arastir, /istihbarat, /denetim — iş olarak skill'i koşturur) ikinci kez listelenmez.
    for o in cc_komutlar.liste():
        if o["komut"] not in gorulen:
            gorulen.add(o["komut"])
            out.append(o)
    return out


def _hiz_asildi(did):
    now = time.time()
    with _hiz_lock:
        q = [t for t in _hiz.get(did, []) if now - t < 60]
        q.append(now)
        _hiz[did] = q
        return len(q) > HIZ_DAKIKA


class H(BaseHTTPRequestHandler):
    server_version = "ghost-api/1"

    def log_message(self, fmt, *a):  # journal'a kısa satır
        sys.stderr.write("%s %s\n" % (time.strftime("%F %T"), fmt % a))

    _ACIK_CACHE = {"at": 0.0, "obj": None}

    def _acik_nabiz(self):
        """Herkese açık nabız: gün sayısı, siteler kaç/kaç ayakta, şu an ne yapıyor.
        Yalnız sayı ve üç kelimelik durum; içerik, kimlik, iş adı YOK. 30 sn bellek önbelleği."""
        c = H._ACIK_CACHE
        now = time.time()
        if c["obj"] is None or now - c["at"] > 30:
            obj = {"gun": max(0, int((now - 1786406400) // 86400)),   # 2026-08-11 UTC'den beri (site ile aynı: tam gün)
                   "siteler_ok": 0, "siteler_n": 0, "durum": "nobette"}
            try:
                with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops", "uptime_state.json"), encoding="utf-8") as f:
                    st = json.load(f)
                vals = [bool(v.get("ok")) if isinstance(v, dict) else bool(v) for v in st.values()]
                obj["siteler_n"], obj["siteler_ok"] = len(vals), sum(vals)
            except Exception:
                pass
            # 2026-10-26 (Sahip: kart hep "sahiple konuşuyor" diyordu): sohbet_durumu() bir dict döner,
            # boş olmadığı için hep doğruydu → 'nobette' hiç görünmüyordu. 'is' ise health._is()'e
            # (AppA İŞ NABZI metrikleri, 'aktif' anahtarı yok) bakıyordu — doğru kaynak iş kuyruğu.
            try:
                if (akis.sohbet_durumu() or {}).get("aktif"):
                    obj["durum"] = "sohbet"
                elif (health._isler() or {}).get("aktif"):
                    obj["durum"] = "is"
            except Exception:
                pass
            c["at"], c["obj"] = now, obj
        b = json.dumps(c["obj"]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "public, max-age=60")
        self.send_header("Access-Control-Allow-Origin", "https://sahip.kim")
        self.end_headers()
        self.wfile.write(b)

    def _json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def _body(self, tavan=BODY_MAX):
        n = int(self.headers.get("Content-Length") or 0)
        if n > tavan:
            return None
        return self.rfile.read(n) if n else b""

    def _ham(self, code, veri, mime):
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(veri)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(veri)

    def _uzuv_uykuda(self, ad):
        return self._json(503, {"hata": "%s görüntüsü yok — uzuv uykuda ya da tünel kapalı" % ad})

    # ------------------------------------------------------------ SSE
    def _sse(self, since_olay, since_outbox, since_sahne):
        """text/event-stream: sohbet olayları + döküm + sahne değişimi tek akışta.
        En çok SSE_SURE sn açık kalır; istemci son id'lerle yeniden bağlanır."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def yaz(ad, veri):
            self.wfile.write(("event: %s\ndata: %s\n\n" % (
                ad, json.dumps(veri, ensure_ascii=False))).encode("utf-8"))
            self.wfile.flush()

        son = time.time() + SSE_SURE
        ping = time.time()
        yaz("merhaba", {"olay": since_olay, "outbox": since_outbox, "sahne": since_sahne})
        while time.time() < son:
            yeni = False
            for ev in akis.olaylar(since_olay, 100):
                since_olay = max(since_olay, ev["id"]); yaz("olay", ev); yeni = True
            for m in kokpit.outbox_oku(since_outbox, 50):
                since_outbox = max(since_outbox, m["id"]); yaz("mesaj", m); yeni = True
            sh = sahne.oku()
            if int(sh.get("seq") or 0) != since_sahne:
                since_sahne = int(sh.get("seq") or 0); yaz("sahne", sh); yeni = True
            if not yeni:
                if time.time() - ping > 15:
                    self.wfile.write(b": ping\n\n"); self.wfile.flush(); ping = time.time()
                time.sleep(0.5)
        yaz("bitti", {"olay": since_olay, "outbox": since_outbox, "sahne": since_sahne})

    def _auth(self, method, body):
        cihaz, neden = kokpit.dogrula_neden(self.headers, method, self.path, body)
        if not cihaz:
            # 401 YALNIZ gerçek kimlik hatası: uygulama her 401'de eşleme ekranına düşer.
            # Tekrar (aynı nonce, iOS yeniden gönderimi) → 409, saat penceresi → 400
            # (2026-09-07: 401'e indirgenen tekrarlar bir gecede 4 kez eşleme istetti).
            if neden == "tekrar":
                self._json(409, {"hata": "istek tekrarı", "neden": neden})
            elif neden == "saat":
                self._json(400, {"hata": "saat penceresi dışı", "neden": neden})
            else:
                self._json(401, {"hata": "kimlik doğrulanamadı", "neden": neden})
            self.log_message("kimlik reddi %s cihaz=%s %s", neden,
                             (self.headers.get("X-Ghost-Device") or "-")[:12], self.path[:80])
            return None
        if _hiz_asildi(cihaz["id"]):
            self._json(429, {"hata": "çok sık"})
            return None
        return cihaz

    # ------------------------------------------------------------ GET
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/v1/ping":
            return self._json(200, {"ok": True, "at": time.strftime("%F %T")})
        if u.path == "/v1/acik/nabiz":        # sahip.kim Ghost kartı — herkese açık, kimliksiz, içeriksiz (2026-10-23)
            return self._acik_nabiz()
        cihaz = self._auth("GET", b"")
        if not cihaz:
            return
        try:
            if u.path == "/v1/nabiz":
                return self._json(200, health.nabiz())
            if u.path == "/v1/ses/token":      # ses ajanı (ElevenLabs) konuşma jetonu — ajan private
                from ghost import ses_ajani
                tok, hata = ses_ajani.token()
                if not tok:
                    return self._json(503, {"hata": hata})
                return self._json(200, {"token": tok})
            if u.path == "/v1/kararlar":
                return self._json(200, {"kararlar": kokpit.bekleyen_kararlar()})
            if u.path == "/v1/outbox":
                since = int(q.get("since", ["0"])[0] or 0)       # -1 → en son `limit` satır
                limit = min(int(q.get("limit", ["50"])[0] or 50), 200)
                bekle = float(q.get("bekle", ["0"])[0] or 0)
                once = q.get("once", [""])[0]
                if once:                                          # "daha eski" sayfası, beklemez
                    rows = kokpit.outbox_oku(0, limit, once=int(once))
                    return self._json(200, {"mesajlar": rows, "son": kokpit.outbox_son(),
                                            "mesgul": akis.sohbet_durumu()})
                rows, mesgul = _outbox_bekle(since, limit, bekle, q.get("durum", [""])[0])
                son = rows[-1]["id"] if rows else max(since, kokpit.outbox_son())
                return self._json(200, {"mesajlar": rows, "son": son, "mesgul": mesgul})
            if u.path.startswith("/v1/medya/"):
                oid = u.path.rsplit("/", 1)[1]
                m = kokpit.medya_yol(int(oid)) if oid.isdigit() else None
                if not m:
                    return self._json(404, {"hata": "medya yok"})
                yol, mime, ad = m
                with open(yol, "rb") as f:
                    veri = f.read()
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(veri)))
                self.send_header("Content-Disposition", "inline; filename=\"%s\"" % ad.replace('"', ""))
                self.send_header("Cache-Control", "private, max-age=86400")   # dosya değişmez
                self.end_headers()
                self.wfile.write(veri)
                return
            if u.path == "/v1/duyular":
                return self._json(200, {"acik": duyular.acik(), "ozet": duyular.ozet(),
                                        "bayat": duyular.BAYAT})
            if u.path == "/v1/gorevler":     # Faz 6: telefona yazılacak işler
                return self._json(200, {"gorevler": gorevler.bekleyenler()})
            if u.path == "/v1/bolgeler":     # coğrafi tetik tanımları (seq değişince yeniden kaydet)
                d = bolgeler.oku()
                return self._json(200, {"bolgeler": d.get("bolgeler", []),
                                        "seq": int(d.get("seq") or 0), "tavan": bolgeler.TAVAN})
            if u.path == "/v1/komutlar":
                return self._json(200, {"komutlar": _komutlar()})
            if u.path == "/v1/siteler":
                out = []
                for s in health._siteler():
                    baslik, proje = SITE_META.get(s["ad"], ("", ""))
                    out.append(dict(s, baslik=baslik, proje=proje))
                return self._json(200, {"siteler": out})
            if u.path == "/v1/akis":
                limit = int(q.get("limit", ["60"])[0] or 60)
                once = (q.get("once", [""])[0] or None)
                kay = [k for k in (q.get("kaynak", [""])[0] or "").split(",") if k]
                rows = akis.zaman_cizelgesi(limit, once, kay or None)
                return self._json(200, {"akis": rows, "once": rows[-1]["ts"] if rows else None})
            if u.path == "/v1/olaylar":
                since = int(q.get("since", ["0"])[0] or 0)
                limit = min(int(q.get("limit", ["100"])[0] or 100), 500)
                bekle = float(q.get("bekle", ["0"])[0] or 0)
                rows = akis.olaylar_bekle(since, limit, bekle)
                son = rows[-1]["id"] if rows else max(since, akis.olay_son())
                return self._json(200, {"olaylar": rows, "son": son})
            if u.path == "/v1/sse":
                try:
                    return self._sse(int(q.get("olay", ["0"])[0] or 0),
                                     int(q.get("outbox", ["0"])[0] or 0),
                                     int(q.get("sahne", ["-1"])[0] or -1))
                except (BrokenPipeError, ConnectionResetError):
                    return                      # istemci kapattı — normal
            if u.path == "/v1/masa":
                # Masa (2026-10-04): ortak kalıcı içerik alanı. Tek istekte düz ağaç (içerik yerine
                # önizleme); since+bekle uzun yoklama; dolap=1 → kaldırılanlar.
                since = int(q.get("since", ["-1"])[0] or -1)
                bekle = float(q.get("bekle", ["0"])[0] or 0)
                s = masa.bekle(since, bekle) if bekle and since >= 0 else masa.seq()
                dolap = q.get("dolap", [""])[0] == "1"
                return self._json(200, {"seq": s, "kartlar": masa.agac("dolap" if dolap else "masa"),
                                        "dolap_sayisi": 0 if dolap else len(masa.agac("dolap"))})
            if u.path.startswith("/v1/masa/"):
                parca = u.path.split("/")[3:]
                if not parca or not parca[0].isdigit():
                    return self._json(404, {"hata": "yok"})
                kid = int(parca[0])
                if len(parca) > 1 and parca[1] == "dosya":
                    d = masa.dosya_yol(kid)
                    if not d:
                        return self._json(404, {"hata": "dosya yok"})
                    yol_, mime, ad = d
                    with open(yol_, "rb") as f:
                        veri = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(veri)))
                    self.send_header("Content-Disposition", "inline; filename=\"%s\"" % ad.replace('"', ""))
                    self.send_header("Cache-Control", "private, max-age=86400")
                    self.end_headers()
                    self.wfile.write(veri)
                    return
                k = masa.oku(kid)
                if not k:
                    return self._json(404, {"hata": "kayıt yok"})
                if k["tur"] == "sahne":
                    try:
                        k["bilesenler"] = json.loads(k.get("icerik") or "[]")
                    except Exception:
                        k["bilesenler"] = []
                    k["icerik"] = None
                k["yol"] = masa.yol(kid)
                k["cocuklar"] = masa.cocuklar(kid) if k["tur"] == "klasor" else []
                return self._json(200, k)
            if u.path == "/v1/sahne":
                if q.get("seq"):                      # tarihçeden belirli sahne
                    d = sahne.oku_seq(int(q["seq"][0] or 0))
                    return self._json(200, d) if d else self._json(404, {"hata": "sahne yok"})
                since = int(q.get("since", ["-1"])[0] or -1)
                bekle = float(q.get("bekle", ["0"])[0] or 0)
                return self._json(200, sahne.bekle(since, bekle) if bekle else sahne.oku())
            if u.path == "/v1/sahne/gecmis":
                return self._json(200, {"gecmis": sahne.gecmis(), "simdi": int(sahne.oku().get("seq") or 0)})
            if u.path == "/v1/sahne/mac.jpg":
                k = canli.mac_kare(boy=canli.boy_sinirla(q.get("boy", [""])[0]))
                return self._ham(200, k[0], k[1]) if k else self._uzuv_uykuda("Mac")
            if u.path == "/v1/sahne/telefon.jpg":
                k = canli.telefon_kare()
                return self._ham(200, k, "image/jpeg") if k else self._uzuv_uykuda("telefon")
            if u.path == "/v1/sahne/telefon.mjpeg":
                a = canli.telefon_akis()
                if not a:
                    return self._uzuv_uykuda("telefon")
                ct, gen = a
                self.send_response(200)
                self.send_header("Content-Type", ct)
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                try:
                    for parca in gen:
                        self.wfile.write(parca)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                return
            if u.path == "/v1/cihazlar":
                return self._json(200, {"cihazlar": kokpit.cihazlar(), "ben": cihaz["id"]})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as e:
            self.log_message("hata %s: %r", u.path, e)
            return self._json(500, {"hata": "sunucu hatası"})
        self._json(404, {"hata": "yok"})

    def _masa_yaz(self, cihaz, path, veri, ham, q):
        """Masa yazma uçları — uygulamadan gelen her şey yazar=sahip; izinler ghost/masa.py'de.
        POST /v1/masa            {tur: klasor|not|baglanti, ad, ebeveyn?, icerik?}
        POST /v1/masa/dosya?ad=&ebeveyn=   ham gövde (görsel/belge)
        POST /v1/masa/<id>       {eylem: duzenle|adlandir|tasi|sirala|dolap|geri|sil|ilet, ...}"""
        def _eb(v):
            return int(v) if v not in (None, "", "kok") and str(v).isdigit() else None
        try:
            if path == "/v1/masa/dosya":
                ad = (q.get("ad", [""])[0] or "dosya").strip()
                kid = masa.dosya_ekle(ad, ham or b"", _eb(q.get("ebeveyn", [""])[0]), yazar="sahip")
                governance.audit("api", "masa", governance.READ, "dosya #%d %s" % (kid, ad[:60]))
                return self._json(200, {"ok": True, "id": kid, "seq": masa.seq()})
            if path == "/v1/masa":
                tur = str(veri.get("tur") or "")
                if tur not in ("klasor", "not", "baglanti"):
                    return self._json(400, {"hata": "tur klasor|not|baglanti"})
                kid = masa.kart_ekle(tur, veri.get("ad") or "", _eb(veri.get("ebeveyn")),
                                     icerik=veri.get("icerik"), yazar="sahip")
                governance.audit("api", "masa", governance.READ, "%s #%d" % (tur, kid))
                return self._json(200, {"ok": True, "id": kid, "seq": masa.seq()})
            parca = path.split("/")[3:]
            if not parca or not parca[0].isdigit():
                return self._json(404, {"hata": "yok"})
            kid = int(parca[0])
            eylem = str(veri.get("eylem") or "")
            sonuc = None
            if eylem == "duzenle":
                masa.duzenle(kid, "sahip", icerik=veri.get("icerik"), ad=veri.get("ad"))
            elif eylem == "adlandir":
                masa.duzenle(kid, "sahip", ad=veri.get("ad") or "")
            elif eylem == "tasi":
                masa.tasi(kid, _eb(veri.get("ebeveyn")), "sahip", veri.get("sira"))
            elif eylem == "sirala":
                masa.sirala(kid, int(veri.get("sira") or 0), "sahip")
            elif eylem == "dolap":
                masa.dolap(kid, "sahip")
            elif eylem == "geri":
                masa.geri(kid, "sahip")
            elif eylem == "sil":
                sonuc = masa.sil(kid, "sahip")
            elif eylem == "ilet":
                if governance.is_frozen():
                    return self._json(423, {"hata": "FREEZE aktif"})
                channels.son_kanal_ayarla("kokpit")
                sonuc = masa.ilet(kid, cihaz["id"])
            else:
                return self._json(400, {"hata": "eylem duzenle|adlandir|tasi|sirala|dolap|geri|sil|ilet"})
            governance.audit("api", "masa", governance.READ, "%s #%d" % (eylem, kid))
            return self._json(200, {"ok": True, "id": kid, "sonuc": sonuc, "seq": masa.seq()})
        except masa.MasaHata as e:
            return self._json(403 if e.yetki else 400, {"hata": str(e)})

    def _paylas(self, cihaz, veri, ham, q):
        if governance.is_frozen():
            return self._json(423, {"hata": "FREEZE aktif"})
        channels.son_kanal_ayarla("kokpit")
        metin, hata = _paylas_kaydet(cihaz, veri, ham, q)
        if hata:
            return self._json(400, {"hata": hata})
        return self._json(200, {"ok": True, "metin": metin})

    # ------------------------------------------------------------ POST
    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        ct = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        # /v1/paylas: JSON değilse ham dosya gövdesi (görsel, PDF, metin, her tür belge)
        ham_gorsel = u.path in ("/v1/paylas", "/v1/masa/dosya") and ct and ct != "application/json"
        body = self._body(PAYLAS_MAX if ham_gorsel else BODY_MAX)
        if body is None:
            return self._json(413, {"hata": "gövde büyük"})
        veri = {}
        if not ham_gorsel:
            try:
                veri = json.loads(body.decode("utf-8")) if body else {}
            except Exception:
                return self._json(400, {"hata": "json değil"})
        if u.path == "/v1/eslesme":
            time.sleep(1.0)  # kaba kuvvete karşı yavaşlat (6 hane, 10 dk, tek kod)
            r = kokpit.esle(str(veri.get("kod", "")), veri.get("ad", ""))
            if not r:
                governance.audit("api", "eslesme", governance.READ, "başarısız kod")
                return self._json(403, {"hata": "kod geçersiz"})
            governance.audit("api", "eslesme", governance.READ, "cihaz eşlendi: %s" % r[0])
            return self._json(200, {"device_id": r[0], "key": r[1]})
        if u.path.startswith("/v1/ses/arac/"):
            # Ses ajanının webhook aracı: çağıran ElevenLabs, cihaz imzası yok. Paylaşılan
            # sır başlığı (GHOST_SES_SECRET) sabit zamanlı karşılaştırılır; yanlışsa 401.
            import hmac as _hmac
            from ghost import ses_ajani
            beklenen = ses_ajani.ses_sirri()
            gelen = self.headers.get("X-Ghost-Ses") or ""
            if not beklenen or not _hmac.compare_digest(gelen, beklenen):
                self.log_message("ses aracı sır reddi %s", u.path[:60])
                return self._json(401, {"hata": "sır yanlış"})
            ad = u.path.rsplit("/", 1)[-1][:32]
            try:
                out = ses_ajani.arac_calistir(ad, veri if isinstance(veri, dict) else {})
            except Exception as e:
                return self._json(500, {"metin": "araç hatası: %s" % e})
            governance.audit("api", "ses_arac", governance.READ, "%s" % ad)
            return self._json(200, out)
        cihaz = self._auth("POST", body)
        if not cihaz:
            return
        try:
            if u.path == "/v1/mesaj":
                metin = (veri.get("metin") or "").strip()
                if not metin:
                    return self._json(400, {"hata": "metin boş"})
                if governance.is_frozen() and metin.split(None, 1)[0].lower() not in (
                        "/unfreeze", "/freeze", "/durum", "/saglik", "/help"):
                    return self._json(423, {"hata": "FREEZE aktif — yalnız /unfreeze, /durum"})
                try:  # imzalı istek: gerçek onay kapısı (ghost/sahne.py GERÇEK ONAY KAYDI)
                    sahne.sahip_onayi_kaydet(metin, "kokpit-mesaj")
                except Exception:
                    pass
                mid = kokpit.outbox_yaz(metin, "sahip")
                channels.son_kanal_ayarla("kokpit")      # cevap uygulamaya push olsun
                kokpit.mesaj_kuyrukla(metin, cihaz["id"], sesli=bool(veri.get("sesli")))
                return self._json(200, {"ok": True, "id": mid})
            if u.path == "/v1/duyular":
                # Ana şalter kapalıysa 403: uygulama bunu görüp göndermeyi durdurur
                # (izin telefonda açık kalsa bile veri akmaz).
                if not duyular.acik():
                    return self._json(403, {"hata": "duyular kapalı", "acik": False})
                guncel = duyular.yaz(veri, cihaz["id"])
                return self._json(200, {"ok": True, "guncel": guncel})
            if u.path == "/v1/gunluk":       # uygulama içi teşhis günlüğü (kaydırma/klavye olayları)
                _gunluk_yaz(cihaz["id"], veri.get("satirlar") or [])
                return self._json(200, {"ok": True})
            if u.path == "/v1/apns":
                ok = kokpit.apns_kaydet(cihaz["id"], veri.get("token", ""), veri.get("env", "development"))
                return self._json(200 if ok else 400, {"ok": ok})
            if u.path == "/v1/etkinlik":   # Live Activity token'ları (start | update)
                jid = veri.get("jid")
                ok = kokpit.la_token_kaydet(cihaz["id"], veri.get("tur", ""), veri.get("token", ""),
                                            int(jid) if str(jid).isdigit() else None)
                return self._json(200 if ok else 400, {"ok": ok})
            if u.path == "/v1/eylem":
                eid = str(veri.get("id") or "").strip()[:64]
                if not eid:
                    return self._json(400, {"hata": "id boş"})
                if governance.is_frozen():
                    return self._json(423, {"hata": "FREEZE aktif"})
                channels.son_kanal_ayarla("kokpit")
                metin = sahne.eylem(eid, veri.get("veri"), veri.get("alanlar"), cihaz["id"])
                return self._json(200, {"ok": True, "metin": metin})
            if u.path == "/v1/paylas":
                return self._paylas(cihaz, veri, body if ham_gorsel else None, q)
            if u.path == "/v1/masa" or u.path.startswith("/v1/masa/"):
                return self._masa_yaz(cihaz, u.path, veri, body if ham_gorsel else None, q)
            if u.path.startswith("/v1/gorevler/"):
                gid = u.path.rsplit("/", 1)[1]
                if not gid.isdigit():
                    return self._json(400, {"hata": "id sayı değil"})
                g = gorevler.sonuclandir(gid, bool(veri.get("ok")), veri.get("mesaj", ""),
                                         veri.get("yerel_id", ""), cihaz["id"])
                if not g:
                    # Zaten sonuçlanmış: ikinci deneme sessizce yutulur (çift yazım olmaz).
                    return self._json(200, {"ok": True, "yinelenen": True})
                gorevler.haber_ver(g)
                return self._json(200, {"ok": True, "durum": g["durum"]})
            if u.path == "/v1/bolge":        # bölgeye giriş/çıkış — Ghost'u DÜŞÜNDÜRÜR
                metin = bolgeler.olay(veri.get("ad", ""), veri.get("olay", ""), cihaz["id"])
                if not metin:
                    # bilinmeyen bölge ya da soğuma penceresi: sessiz, hata değil
                    return self._json(200, {"ok": True, "yutuldu": True})
                kokpit.outbox_yaz("📍 " + metin.split(" Bu bir otomatik")[0].replace("[Bölge] ", ""), "ghost")
                channels.son_kanal_ayarla("kokpit")
                kokpit.mesaj_kuyrukla(metin, cihaz["id"])
                try:  # kural motoru (2026-10-26): bolge:<ad> tetiği, bölge mesajına EK
                    from ghost import kurallar
                    kurallar.tetikle("bolge", veri.get("ad", ""), {"olay": veri.get("olay", "")})
                except Exception:
                    pass
                return self._json(200, {"ok": True})
            if u.path.startswith("/v1/kararlar/"):
                pid = u.path.rsplit("/", 1)[1]
                karar = veri.get("karar")
                if karar not in ("onay", "ret") or not (4 <= len(pid) <= 16):
                    return self._json(400, {"hata": "karar onay|ret"})
                kokpit.karar_kuyrukla(pid, karar == "onay", cihaz["id"])
                return self._json(200, {"ok": True})
        except Exception as e:
            self.log_message("hata %s: %r", u.path, e)
            return self._json(500, {"hata": "sunucu hatası"})
        self._json(404, {"hata": "yok"})


def _mesgul_imza(m):
    return "%d|%s" % (1 if m.get("aktif") else 0, m.get("ozet", ""))


def _outbox_bekle(since, limit, bekle, durum_imza):
    """Uzun yoklama: yeni döküm satırı YA DA sohbet meşguliyeti (aktif/özet) istemcinin
    bildiğinden farklıysa hemen döner; yoksa `bekle` sn tutar. (rows, mesgul)."""
    son = time.time() + max(0, min(float(bekle or 0), 60))
    while True:
        rows = kokpit.outbox_oku(since, limit)
        m = akis.sohbet_durumu()
        if rows or time.time() >= son or (durum_imza and _mesgul_imza(m) != durum_imza):
            return rows, m
        time.sleep(0.5)


def _gunluk_yaz(cihaz, satirlar):
    """Uygulamanın teşhis satırları → ops/kokpit_gunluk.log (cihaz kısaltması + satır).
    2026-09-26: telefondaki kaydırma kusuru Mac'te çıkmıyordu; görmeden düzeltmek
    tahmin oluyordu. 5 MB'ı aşınca sıfırlanır; istek başına 400 satır."""
    yol = os.path.join(config.OPS, "kokpit_gunluk.log")
    try:
        if os.path.exists(yol) and os.path.getsize(yol) > 5_000_000:
            os.remove(yol)
        with open(yol, "a", encoding="utf-8") as f:
            for satir in list(satirlar)[:400]:
                f.write("%s %s %s\n" % (time.strftime("%F %T"), str(cihaz)[:8], str(satir)[:400]))
    except Exception:
        pass


def _paylas_kaydet(cihaz, veri, ham, q):
    """Share Extension: metin/URL JSON ile, görsel ham gövdeyle gelir.
    Görsel ops/kokpit_paylasim/'a yazılır; köprüye "[Paylaşım] …" mesajı düşer."""
    d = os.path.join(config.OPS, "kokpit_paylasim")
    os.makedirs(d, exist_ok=True)
    if ham is not None:
        if not ham:
            return None, "dosya boş"
        gorsel = ham[:2] == b"\xff\xd8" or ham[:8] == b"\x89PNG\r\n\x1a\n"
        # dosya adı istemciden (güvenli hale getirilir); yoksa içerikten uzantı
        ad = re.sub(r"\.{2,}", ".", re.sub(r"[^\w.\-]+", "_", (q.get("ad", [""])[0] or "").strip()))[-80:].strip("._")
        if not ad:
            ad = "gorsel" + (".jpg" if ham[:2] == b"\xff\xd8" else ".png") if gorsel else "belge.bin"
        yol = os.path.join(d, time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(3).hex() + "-" + ad)
        with open(yol, "wb") as f:
            f.write(ham)
        not_ = (q.get("not", [""])[0] or "").strip()[:500]
        tur = "gorsel" if gorsel else "belge"
        metin = "[Paylaşım: %s] %s (%d KB)%s" % ("görsel" if gorsel else "belge", yol,
                                                  len(ham) // 1024, ("\n" + not_) if not_ else "")
        # Döküm: sahibin tarafında görselin/belgenin kendisi; kuyruk: köprü Telegram'daki
        # foto/dosya yoluyla AYNI istemi kurar (check_kokpit → _medya_prompt).
        kokpit.outbox_yaz(not_, "sahip",
                          medya=kokpit.medya_sakla(yol, ad=os.path.basename(yol), tur=tur, kopyala=False))
        # Çoklu gönderi: aynı grup → köprü TEK tur kurar (check_kokpit). Bozuk sayı → tekil yol.
        grup = re.sub(r"[^\w\-]", "", (q.get("grup", [""])[0] or ""))[:40]
        try:
            sira, toplam = int(q.get("sira", ["1"])[0]), int(q.get("toplam", ["1"])[0])
        except (TypeError, ValueError):
            grup, sira, toplam = "", 1, 1
        kokpit.mesaj_kuyrukla(metin, cihaz["id"], yol=yol, tur=tur, ad=ad, not_=not_,
                              grup=grup or None, sira=sira, toplam=toplam)
        governance.audit("api", "paylasim", governance.READ, metin[:120])
        return metin, None
    else:
        tur = veri.get("tur") or "metin"
        icerik = (veri.get("metin") or veri.get("url") or "").strip()
        if not icerik:
            return None, "içerik boş"
        not_ = (veri.get("not") or "").strip()[:500]
        metin = "[Paylaşım: %s] %s%s" % (tur, icerik[:4000], ("\n" + not_) if not_ else "")
    kokpit.outbox_yaz(metin, "sahip")
    kokpit.mesaj_kuyrukla(metin, cihaz["id"])
    governance.audit("api", "paylasim", governance.READ, metin[:120])
    return metin, None


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    srv.daemon_threads = True
    sys.stderr.write("ghost-api 127.0.0.1:%d\n" % PORT)
    srv.serve_forever()


if __name__ == "__main__":
    main()
