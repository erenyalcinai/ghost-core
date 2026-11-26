"""APNs — kokpit uygulamasına push bildirimi (docs/kokpit.md Faz 2).

AYRI MODÜL: ghost/notify.py Telegram/ntfy yedek bildirim rayıdır (eski, testli);
APNs onun yanına üçüncü kanal olarak burada durur, onu ezmez.

Anahtar: Apple portal 2026-09-02, "Ghost Kokpit APNs" (Key ID secrets.env
APNS_KEY_ID, Team TEAMID0000, Sandbox & Production, Team Scoped). Cihaz
token'ları kokpit_devices.apns_token'da; ortam (development/production)
uygulamanın imzasına göre cihazdan gelir (Xcode dev build → sandbox).

Taşıma: curl --http2 (VPS curl'ü HTTP/2 destekliyor; urllib desteklemiyor).
JWT: PyJWT ES256, 50 dk önbellek. Testte (GHOST_TEST) ağa çıkılmaz.

CLI:  python3 -m ghost.apns gonder "başlık" "gövde"      # tüm aktif cihazlar
      python3 -m ghost.apns durum
Ne zaman: köprü send()'inde butonlu (onay isteyen) mesaj; nöbetçi alarmı;
biten iş. Her mesaja push YOK — gürültü kanalı öldürür.
"""
import json
import os
import re
import subprocess
import sys
import time

from ghost import config

_ENV = config.load_env()          # .env + secrets.env (dict döner, os.environ'a YAZMAZ)
_jwt_cache = {"tok": None, "t": 0}


def _ayar(ad, varsayilan=""):
    return os.environ.get(ad) or _ENV.get(ad) or varsayilan
HOST = {"development": "https://api.sandbox.push.apple.com",
        "production": "https://api.push.apple.com"}


def hazir():
    from ghost import vekil     # sır vekili kuruluysa .p8 vekilde, JWT ondan (Faz 2)
    if vekil.aktif(None, "APNS_KEY_PATH") and _ayar("APNS_KEY_ID") and _ayar("APNS_TEAM_ID"):
        return True
    return bool(_ayar("APNS_KEY_ID") and _ayar("APNS_TEAM_ID")
                and os.path.exists(_ayar("APNS_KEY_PATH")))


def _jwt():
    if _jwt_cache["tok"] and time.time() - _jwt_cache["t"] < 50 * 60:
        return _jwt_cache["tok"]
    from ghost import vekil
    tok = vekil.apns_jwt()
    if tok:
        _jwt_cache.update(tok=tok, t=time.time())
        return tok
    import jwt  # PyJWT
    with open(_ayar("APNS_KEY_PATH")) as f:
        key = f.read()
    tok = jwt.encode({"iss": _ayar("APNS_TEAM_ID"), "iat": int(time.time())},
                     key, algorithm="ES256", headers={"kid": _ayar("APNS_KEY_ID")})
    _jwt_cache.update(tok=tok, t=time.time())
    return tok


ONEM = ("passive", "active", "time-sensitive")


# ---------------------------------------------------------------- cihaz başına konu (Kokpit Watch, 2026-11-23)
# APNs konusu = bildirimi alacak UYGULAMANIN paket kimliği. Saat uygulaması ayrı pakettir, kendi belirtecini alır;
# telefonun konusuyla gönderilen bildirimi Apple "DeviceTokenNotForTopic" ile reddeder. Konuyu İSTEMCİ SEÇMEZ:
# sunucudaki cihaz sınıfı kaydından (`ghost/cihaz_sinif.py`, açık alan) okunur; kayıt yoksa cihaz türünden (onay
# servisi: tam | cep) türetilir — bildirim belirteci kaydetmiş cep cihazı = saat (Cardputer belirteç kaydetmez).
# Tür okunamazsa cihaz telefon sayılır (eski davranış).
def konu_ana():
    return _ayar("APNS_TOPIC", "com.example.ghost.kokpit")


def konu_saat():
    return _ayar("APNS_TOPIC_SAAT", konu_ana() + ".watchkitapp")


def cihaz_turleri():
    """{cihaz id: "tam" | "cep"} — kaynak kokpit.cihazlar() (onay servisi). Asla fırlatmaz; hata → {}."""
    try:
        from ghost import kokpit
        return {c["id"]: (c.get("tur") or "tam") for c in kokpit.cihazlar() if c.get("durum", "aktif") == "aktif"}
    except Exception:
        return {}


def cihaz_sinifi(cihaz_id, turler=None, kayit=None):
    """Belirteç kaydetmiş cihazın bildirim sınıfı: "saat" | "telefon".
    2026-11-24: ÖNCE cihaz kaydındaki açık alan (`ghost/cihaz_sinif.py`): sınıfı "saat" olan saat konusunu alır,
    başka bir sınıf kayıtlıysa (cardputer) cihaz ASLA saat sayılmaz. Açık alan yoksa geçiş kuralı sürer (yama 01:
    belirteçli cep cihazı = saat) — saat istemcisi sınıfını kendi bildirmeye başlayınca bu dal ölü kalır."""
    if kayit is None:
        try:
            from ghost import cihaz_sinif
            kayit = cihaz_sinif.oku()
        except Exception:
            kayit = {}
    acik = kayit.get(cihaz_id)
    if acik:
        return "saat" if acik == "saat" else "telefon"
    turler = cihaz_turleri() if turler is None else turler
    return "saat" if turler.get(cihaz_id) == "cep" else "telefon"


def konu_sec(sinif):
    return konu_saat() if sinif == "saat" else konu_ana()


def onem_sec(collapse=None, veri=None):
    """Faz 1.12 (2026-10-29, denetim 06 #7) + Sahip aynı gün "bildirimler çok yoruyor": onay/karar kartı NORMAL (active)
    düzeyde — Odak'ı delmez; sohbet cevabı (collapse "sohbet") da active (Sahip 2026-10-30: ilk satır balonu sessiz, tam cevap
    gelince banner gelmeliydi — 1.12 onu da passive yapmıştı); gerisi (alarm, iş özeti) passive (bildirim merkezinde durur,
    ses/titreşim yok). time-sensitive kullanılmaz."""
    c = (collapse or "")
    if c.startswith(("karar", "onay")) or (veri or {}).get("sekme") == "kararlar":
        return "active"
    if c == "sohbet":   # 2026-10-30 Sahip "tam mesaj gelince bildirim gelmiyor": sohbet CEVABI istenen şeydir → banner
        return "active"
    return "passive"


# ---------------------------------------------------------------- bildirim gövdesi (Kokpit Watch, 2026-11-23)
# Bildirim cevabın İLK PARAGRAFINI taşır (eskiden ilk 200 harf: cümle ortasında, Markdown işaretleriyle kesiliyordu).
# Bilekte okunacak şey bu; devamı varsa sonuna DEVAM_EKI gelir. Tablo ve kod bloğu bildirime girmez.
GOVDE_TAVAN = 600                       # ilk paragraf bundan uzunsa kelime sınırında kesilir
DEVAM_EKI = " … devamı telefonda"       # saatte
DEVAM_EKI_TELEFON = " …"                # telefonda (herkese() çevirir)
KATEGORI_SOHBET = "sohbet"              # saat uygulamasının kaydettiği kategori (Cevapla + Aç); telefona GİTMEZ
GOVDE_YUK_TAVAN = GOVDE_TAVAN + 40      # yuk() son sigortası (APNs yükü 4 KB; 640 harf ≈ en çok 2,5 KB)
_KOD_BLOGU = re.compile(r"```.*?(```|\Z)", re.S)
_SATIR_ICI = (
    (re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"),             # görsel → alt metin
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),              # bağlantı → metni
    (re.compile(r"\*\*(.+?)\*\*", re.S), r"\1"),                # kalın
    (re.compile(r"__(.+?)__", re.S), r"\1"),
    (re.compile(r"~~(.+?)~~", re.S), r"\1"),
    (re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])"), r"\1"),   # eğik (2 * 3 bozulmaz)
    (re.compile(r"`([^`\n]*)`"), r"\1"),                        # satır içi kod
)


def duz_metin(paragraf):
    """Tek paragrafı Markdown işaretlerinden temizle → (düz metin, yalnız başlık mıydı)."""
    satirlar, baslik = [], True
    for s in paragraf.splitlines():
        s = s.strip()
        if not s or s.startswith("|") or re.fullmatch(r"[-=*_]{3,}", s):
            continue                                            # tablo satırı / ayraç bildirime girmez
        m = re.match(r"#{1,6}\s+(.*)", s)
        if m:
            s = m.group(1).strip()
        else:
            baslik = False
        s = re.sub(r"^>\s?", "", s)
        s = re.sub(r"^[-*+]\s+", "• ", s)
        for desen, yerine in _SATIR_ICI:
            s = desen.sub(yerine, s)
        s = re.sub(r"[ \t]{2,}", " ", s).strip()
        if s:
            satirlar.append(s)
    return "\n".join(satirlar), bool(satirlar) and baslik


def bildirim_govdesi(metin, tavan=GOVDE_TAVAN):
    """Cevabın ilk paragrafı, işaretlerden temiz; devamı varsa DEVAM_EKI. Boş metin → ""."""
    ham = _KOD_BLOGU.sub("\n\n", (metin or "").replace("\r\n", "\n"))
    kodlu = ham != (metin or "").replace("\r\n", "\n")
    temiz = [duz_metin(p) for p in re.split(r"\n\s*\n", ham)]
    temiz = [(t, b) for t, b in temiz if t]
    if not temiz:
        return "(tablo ya da kod — telefonda)" if (metin or "").strip() else ""
    govde, kalan = temiz[0][0], temiz[1:]
    if temiz[0][1] and kalan:               # ilk paragraf yalnız başlıksa altındaki paragrafla birlikte okunur
        govde, kalan = govde + "\n" + kalan[0][0], kalan[1:]
    atlanan = len([p for p in re.split(r"\n\s*\n", ham) if p.strip()]) != len(temiz)   # süzülen tablo paragrafı
    devam = bool(kalan) or kodlu or atlanan
    if len(govde) > tavan:
        kesik = govde[:tavan]
        bosluk = max(kesik.rfind(" "), kesik.rfind("\n"))
        govde = (kesik[:bosluk] if bosluk > tavan * 0.6 else kesik).rstrip(" ,;:\n")
        devam = True
    return govde + (DEVAM_EKI if devam else "")


def telefon_govdesi(govde):
    """Aynı gövdenin telefon hâli: sondaki "… devamı telefonda" yerine yalnız " …"."""
    g = govde or ""
    return g[:-len(DEVAM_EKI)] + DEVAM_EKI_TELEFON if g.endswith(DEVAM_EKI) else g


def yuk(baslik, govde, veri=None, ses=True, rozet=None, onem=None, kategori=None):
    aps = {"alert": {"title": baslik[:80], "body": govde[:GOVDE_YUK_TAVAN]}}
    if kategori:                            # saat uygulaması bu kategoriye "Cevapla" eylemini bağlar
        aps["category"] = str(kategori)[:40]
    onem = onem or onem_sec(None, veri)
    if onem in ONEM:
        aps["interruption-level"] = onem
    if ses and onem != "passive":
        aps["sound"] = "default"
    if rozet is not None:
        aps["badge"] = int(rozet)
    p = {"aps": aps}
    if veri:
        p.update(veri)
    return p


def uyari_basliklari(jwt_, konu=None, collapse=None):
    """Görünür (alert) bildirimin curl başlıkları — saf işlev (ağ yok), testte doğrudan sınanır."""
    hdr = ["-H", "authorization: bearer " + jwt_,
           "-H", "apns-topic: " + (konu or konu_ana()),
           "-H", "apns-push-type: alert", "-H", "apns-priority: 10",
           "-H", "content-type: application/json"]
    if collapse:
        hdr += ["-H", "apns-collapse-id: " + collapse[:64]]
    return hdr


def gonder(token, env, baslik, govde, veri=None, ses=True, rozet=None, collapse=None, onem=None, konu=None,
           kategori=None):
    """Tek cihaza push → (ok, durum_kodu, apns_yanit). Asla fırlatmaz. konu: APNs konusu (verilmezse telefonunki).
    kategori: bildirim kategorisi (yalnız saate verilir; herkese() seçer)."""
    onem = onem or onem_sec(collapse, veri)
    if os.environ.get("GHOST_TEST"):
        return False, 0, "test"
    if not hazir():
        return False, 0, "APNs anahtarı yok"
    url = "%s/3/device/%s" % (HOST.get(env or "development", HOST["development"]), token)
    hdr = uyari_basliklari(_jwt(), konu, collapse)
    try:
        r = subprocess.run(["curl", "-sS", "--http2", "-m", "15", "-o", "-", "-w", "\n%{http_code}",
                            "-X", "POST", *hdr, "-d",
                            json.dumps(yuk(baslik, govde, veri, ses, rozet, onem, kategori=kategori),
                                       ensure_ascii=False), url],
                           capture_output=True, text=True, timeout=25)
        out = r.stdout.strip().rsplit("\n", 1)
        kod = int(out[-1]) if out and out[-1].isdigit() else 0
        govde_y = out[0] if len(out) > 1 else ""
        return kod == 200, kod, (govde_y or r.stderr)[:200]
    except Exception as e:
        return False, 0, repr(e)


def sessiz(token, env, veri=None):
    """Sessiz (background) push: uyandır, gösterme. Faz 6 — kuyrukta telefona
    yazılacak görev olduğunda uygulamayı arka planda koşturmak için.

    Alert push'tan üç farkı var ve üçü de zorunlu: push-type `background`,
    öncelik 5 (Apple 10'u sessizde REDDEDER), gövdede yalnız `content-available`.
    Teslim garantisi YOK — iOS kısar; bu yüzden çağıran taraf hep yedekli olmalı
    (öne gelince + SSE + arka plan tazelemesi)."""
    if os.environ.get("GHOST_TEST"):
        return False, 0, "test"
    if not hazir():
        return False, 0, "APNs anahtarı yok"
    url = "%s/3/device/%s" % (HOST.get(env or "development", HOST["development"]), token)
    yuk_ = {"aps": {"content-available": 1}}
    if veri:
        yuk_.update(veri)
    hdr = ["-H", "authorization: bearer " + _jwt(),
           "-H", "apns-topic: " + _ayar("APNS_TOPIC", "com.example.ghost.kokpit"),
           "-H", "apns-push-type: background", "-H", "apns-priority: 5",
           "-H", "content-type: application/json"]
    try:
        r = subprocess.run(["curl", "-sS", "--http2", "-m", "15", "-o", "-", "-w", "\n%{http_code}",
                            "-X", "POST", *hdr, "-d", json.dumps(yuk_, ensure_ascii=False), url],
                           capture_output=True, text=True, timeout=25)
        out = r.stdout.strip().rsplit("\n", 1)
        kod = int(out[-1]) if out and out[-1].isdigit() else 0
        return kod == 200, kod, (out[0] if len(out) > 1 else r.stderr)[:200]
    except Exception as e:
        return False, 0, repr(e)


def sessiz_herkese(veri=None):
    """Sessiz push yalnız TELEFONA: kuyruk (hatırlatıcı/etkinlik yazma) telefonun işi; saat belirtecine telefon
    konusuyla gönderim reddedilir, saatin de boşaltacağı kuyruk yok."""
    from ghost import kokpit
    turler = cihaz_turleri()
    return [dict(cihaz=c["id"], ok=sessiz(c["apns_token"], c["apns_env"], veri)[0])
            for c in kokpit.apns_cihazlar() if cihaz_sinifi(c["id"], turler) != "saat"]


ALARM_ON = ("🚨", "⚠️", "❌", "🔴", "⛔", "🆘")


def alarm_mi(text):
    """Nöbetçi/alarm metni mi? Başındaki işarete bakar — her bilgi mesajı push değildir."""
    t = (text or "").lstrip()
    return t.startswith(ALARM_ON)


def alarm(text, veri=None):
    """Alarm metnini push'la (notify.telegram / send_telegram kancası). Asla fırlatmaz."""
    try:
        if not alarm_mi(text):
            return []
        ilk = text.strip().splitlines()[0][:80]
        return herkese("Ghost alarm", text.strip()[:300], veri or {"sekme": "nabiz"},
                       collapse="alarm:" + ilk[:40])
    except Exception:
        return []


# ---------------------------------------------------------------- Live Activity
def _la_gonder(token, env, aps, event):
    """ActivityKit push: apns-push-type liveactivity, topic <bundle>.push-type.liveactivity."""
    if os.environ.get("GHOST_TEST"):
        return False, 0, "test"
    if not hazir():
        return False, 0, "APNs anahtarı yok"
    url = "%s/3/device/%s" % (HOST.get(env or "development", HOST["development"]), token)
    hdr = ["-H", "authorization: bearer " + _jwt(),
           "-H", "apns-topic: " + _ayar("APNS_TOPIC", "com.example.ghost.kokpit") + ".push-type.liveactivity",
           "-H", "apns-push-type: liveactivity", "-H", "apns-priority: 10",
           "-H", "content-type: application/json"]
    try:
        r = subprocess.run(["curl", "-sS", "--http2", "-m", "15", "-o", "-", "-w", "\n%{http_code}",
                            "-X", "POST", *hdr, "-d", json.dumps({"aps": aps}, ensure_ascii=False), url],
                           capture_output=True, text=True, timeout=25)
        out = r.stdout.strip().rsplit("\n", 1)
        kod = int(out[-1]) if out and out[-1].isdigit() else 0
        return kod == 200, kod, (out[0] if len(out) > 1 else r.stderr)[:200]
    except Exception as e:
        return False, 0, repr(e)


def la_durum(jid, tur, durum, ozet="", basladi=None, bitti=None):
    """content-state — Swift IsEtkinligi.ContentState ile bire bir."""
    return {"durum": durum, "ozet": (ozet or "")[:120],
            "basladi": basladi or int(time.time()), "bitti": bitti}


# 2026-11-26 (Sahip "ekran widget'ı iş bitince de kapanmıyor"; teşhis dossier/kokpit/canli-etkinlik-kapanmiyor-2026-11-26.md):
#  - aynı telefon her yeniden eşlemede yeni cihaz kaydı açıyor, push-to-start belirteci eski kayıtlarda da kalıyordu
#    (ölçüm: 4 aktif kayıt, belirteç dördünde aynı) → her iş başında aynı telefona 4 start push. Belirteç başına BİR push.
#  - iOS 18+ start yükünde `input-push-token: 1` (Apple: "Starting and updating Live Activities with ActivityKit push
#    notifications") → sistem uygulamayı uyandırıp etkinliğin güncelleme belirtecini verir.
#  - `stale-date`: iş tavanı (motor.IS_TAVAN 6 sa) + pay; bu tarihe dek bitiş gelmezse kart "sonuç bilinmiyor" der.
#  - bitişte kart HEMEN kalkar (dismissal-date geçmişte): sonucu zaten "İş #N bitti" bildirimi taşıyor; 15 dk kalan
#    "bitti" kartı sahibe "kapanmıyor" gibi görünüyordu.
#  - iş için güncelleme belirteci yoksa (ölçüm: işlerin ~%43'ünde gelmemiş) ya da end push'u reddedildiyse telefona
#    iş numarasıyla sessiz push → uygulama etkinliği yerelde kapatır (Bildirim.swift). Teslim garantisi yok; asıl
#    toparlayıcı uygulamanın açılışta / öne gelişte yaptığı karşılaştırmadır (/v1/nabiz isler.aktif_idler).
#  - her Live Activity push'u ops/apns.jsonl'e (tur=la); belirteç değeri YAZILMAZ.
LA_BAYAT_SN = 6 * 3600 + 10 * 60
LA_KAPANIS_GECMIS_SN = 1        # dismissal-date = şimdi - 1 → kilit ekranından hemen kalkar (Apple: geçmiş tarih)


def _neden(yanit):
    """APNs hata gövdesinden yalnız `reason` (belirteç ya da başka değer taşımaz)."""
    try:
        return str((json.loads(yanit or "{}") or {}).get("reason") or "")[:60]
    except Exception:
        return ""


def _la_kaydet(olay, cihaz, jid, ok, kod, yanit=""):
    """ops/apns.jsonl satırı: tur=la · olay start|end|yedek · cihaz · jid · ok · kod (+ APNs reason). Asla fırlatmaz."""
    try:
        satir = {"ts": time.strftime("%F %T"), "tur": "la", "olay": olay, "cihaz": cihaz, "jid": int(jid),
                 "ok": bool(ok), "kod": kod}
        if not ok:
            satir["neden"] = _neden(yanit) if kod else re.sub(r"[0-9a-fA-F]{32,}", "…", str(yanit or ""))[:60]
        with open(os.path.join(config.OPS, "apns.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(satir, ensure_ascii=False) + "\n")
    except Exception:
        pass


def etkinlik_baslat(jid, tur, ozet=""):
    """Push-to-start: kayıtlı start token'ı olan cihazlarda Live Activity açar — aynı belirtece TEK push."""
    from ghost import kokpit
    sonuc = []
    simdi = int(time.time())
    st = la_durum(jid, tur, "çalışıyor", ozet)
    goruldu = set()
    for c in kokpit.la_start_tokenlar():
        if c["token"] in goruldu:          # aynı telefonun eski eşleme kaydı: ikinci etkinlik açma
            continue
        goruldu.add(c["token"])
        aps = {"timestamp": simdi, "event": "start",
               "content-state": st, "attributes-type": "IsEtkinligi",
               "attributes": {"jid": int(jid), "tur": (tur or "iş")[:40]},
               "input-push-token": 1,
               "stale-date": simdi + LA_BAYAT_SN,
               "alert": {"title": "İş #%d başladı" % int(jid), "body": (ozet or tur or "")[:100]}}
        ok, kod, y = _la_gonder(c["token"], c["env"], aps, "start")
        sonuc.append({"cihaz": c["id"], "ok": ok, "kod": kod, "yanit": y})
        _la_kaydet("start", c["id"], jid, ok, kod, y)
        if kod == 410:
            kokpit.la_start_sil(c["id"])
    return sonuc


def etkinlik_bitir(jid, tur, durum, ozet="", basladi=None):
    """Update token'ı gelen etkinlikleri kapat (event=end). Token yoksa ya da end reddedildiyse YEDEK: telefona iş
    numarasını taşıyan sessiz push (uygulama etkinliği kendisi kapatır)."""
    from ghost import kokpit
    sonuc = []
    simdi = int(time.time())
    st = la_durum(jid, tur, durum, ozet, basladi, simdi)
    basarili = 0
    for e in kokpit.la_update_tokenlar(jid):
        aps = {"timestamp": simdi, "event": "end", "content-state": st,
               "dismissal-date": simdi - LA_KAPANIS_GECMIS_SN}
        ok, kod, y = _la_gonder(e["token"], e["env"], aps, "end")
        sonuc.append({"cihaz": e["device_id"], "ok": ok, "kod": kod, "yanit": y})
        _la_kaydet("end", e["device_id"], jid, ok, kod, y)
        basarili += 1 if ok else 0
    kokpit.la_update_sil(jid)
    if not basarili:
        sonuc.extend(etkinlik_yedek_kapat(jid))
    return sonuc


def etkinlik_yedek_kapat(jid):
    """Sessiz push {"etkinlik_bitti": jid} yalnız telefonlara (saatte Live Activity yok). Sonuç listesi."""
    from ghost import kokpit
    sonuc = []
    turler = cihaz_turleri()
    for c in kokpit.apns_cihazlar():
        if cihaz_sinifi(c["id"], turler) == "saat":
            continue
        ok, kod, y = sessiz(c["apns_token"], c["apns_env"], {"etkinlik_bitti": int(jid)})
        sonuc.append({"cihaz": c["id"], "ok": ok, "kod": kod, "yanit": y, "yedek": True})
        _la_kaydet("yedek", c["id"], jid, ok, kod, y)
    return sonuc


def herkese(baslik, govde, veri=None, collapse=None, onem=None):
    """Kayıtlı token'ı olan tüm aktif cihazlara. Sonuç listesi; 410 (Unregistered) token'ı siler."""
    from ghost import kokpit
    sonuc = []
    turler = cihaz_turleri()
    for c in kokpit.apns_cihazlar():
        sinif = cihaz_sinifi(c["id"], turler)
        saat = sinif == "saat"
        # "… devamı telefonda" eki ve "sohbet" kategorisi (Cevapla eylemi) YALNIZ saate: telefon bildiriminde ek " …"
        # olur (zaten telefondasın), kategori gitmez — telefonun bildirim davranışı gövde metni dışında değişmez.
        ok, kod, y = gonder(c["apns_token"], c["apns_env"], baslik, govde if saat else telefon_govdesi(govde), veri,
                            collapse=collapse, onem=onem, konu=konu_sec(sinif),
                            kategori=KATEGORI_SOHBET if (saat and collapse == "sohbet") else None)
        sonuc.append({"cihaz": c["id"], "sinif": sinif, "ok": ok, "kod": kod, "yanit": y})
        try:  # Faz 1.13 (denetim 02 e2): her push ops/apns.jsonl'e — teslim oranı ölçülebilsin
            from ghost import config as _cfg
            with open(os.path.join(_cfg.OPS, "apns.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.strftime("%F %T"), "cihaz": c["id"], "baslik": baslik[:60],
                                    "collapse": collapse, "onem": onem or onem_sec(collapse, veri), "ok": ok, "kod": kod},
                                   ensure_ascii=False) + "\n")
        except Exception:
            pass
        if kod == 410 or (kod == 400 and "BadDeviceToken" in y):
            kokpit.apns_sil(c["id"])
    return sonuc


def main(argv):
    if not argv or argv[0] == "durum":
        from ghost import kokpit
        print("anahtar:", "hazır" if hazir() else "YOK", "| kayıtlı cihaz:", len(kokpit.apns_cihazlar()))
        return 0
    if argv[0] == "gonder" and len(argv) >= 3:
        for s in herkese(argv[1], argv[2]):
            print(json.dumps(s, ensure_ascii=False))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
