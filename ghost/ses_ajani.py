"""Ses ajanı "Echo" — Ghost'un HIZLI sesli yüzü (2026-09-26, Sahip: "sesli konuşma ajanını yapmadık").
Adı Echo (Sahip kararı 2026-09-26 12:55: "madem sesli sohbet sen değilsin, başka ad; İngilizce olmalı").

Ghost'un bir turu 10 sn–dakikalar; sesli sohbet 1 sn altını ister. Bu yüzden ikinci, dar
görevli bir organ: ElevenLabs Agents (barındırılan; STT+LLM+TTS+söz kesme onların tarafında)
+ webhook araçları bizim API'de (durum, siteler, ürün, makineler, bugün, hafıza, son mesajlar, görev). Ajan:
  • durumu/son mesajları ARAÇTAN okur ve söyler (uydurmaz);
  • derin iş gelince kendisi yapmaz, `ghost_gorev` ile Sohbet kuyruğuna bırakır ve
    "Ghost'a verdim" der. Görev Kokpit'ten yazılmış mesajla AYNI yoldan Ghost'a düşer.
Ghost bitirince cevabı zaten voice.py ile seslendirip Kokpit'e düşürür (ikinci yön).

Kurulum (bir kez, ElevenLabs API anahtarıyla):
  python3 -m ghost.ses_ajani kur          # gizli anahtar + 3 araç + ajan → ops/ses_ajani.json
  python3 -m ghost.ses_ajani guncelle     # istem/araç değişince: eksik aracı kurar, olanı ve ajanı günceller
  python3 -m ghost.ses_ajani durum        # kayıtlı kimlikler
  python3 -m ghost.ses_ajani konus "..."  # metin kipinde canlı test (WebSocket, sesi yok)
Kokpit: GET /v1/ses/token (cihaz imzalı) → {token} → ElevenLabs Swift SDK startConversation.
Araç uçları: POST /v1/ses/arac/<durum|son_mesajlar|gorev>, başlık X-Ghost-Ses = GHOST_SES_SECRET
(secrets.env; ElevenLabs tarafında workspace secret). Cihaz imzası YOK — çağıran ElevenLabs.
Fiyat: ajan dakikası ~8¢ (Creator'da dakika havuzu ayrı) + LLM. Ölç, dakika artarsa kendi hat.
"""
import json
import os
import secrets as _secrets
import sys
import time
import urllib.error
import urllib.request

from ghost import config, sahip

API = "https://api.elevenlabs.io/v1"
KAYIT = os.path.join(config.OPS, "ses_ajani.json")
APP_HOST = "https://ghost-api.example.com"
LLM = "claude-haiku-4-5"          # düşük gecikme; ElevenLabs LLM enum'unda doğrulandı 2026-09-26
TTS_MODEL = "eleven_flash_v2_5"

_ISTEM_SABLON = """Sen Echo'sun: Ghost'un yankısı, hızlı sesli yüzü. Ghost, {SAHIP_TAM}'ın kişisel yapay zekâ altyapısı. Konuştuğun kişi {SAHIP}. Şu an saat {{system__time}} (İstanbul).

Kim olduğun:
- Adın Echo. Ghost'un kendisi DEĞİLSİN; kim olduğun sorulursa "Echo, Ghost'un yankısı" de. Ghost derin işleri dakikalar süren turlarla yapar. Sen anlık konuşursun. "Ghost" derken üçüncü şahıs kullan: "Ghost'a verdim", "Ghost şu an şunu yapıyor".
- Türkçe konuş. Kısa cümleler, telefonda dinleniyor. Bir cevap en fazla iki-üç cümle. Liste okuma, madde sayma yok. Sayıları sözle söyle ("yirmi üç commit").
- Samimi ama ciddi. Yaltaklanma, "harika soru" deme, {SAHIP}'in fikrini övme.

Bildiklerin (araçsız, güvenle söyleyebilirsin):
- Ghost'un YAPABİLDİKLERİ: kod yazar, test eder, commit atar (main'e commit yayın demektir); siteleri yönetir (Cloudflare Pages/Workers, DNS); GitHub, Sentry, PostHog, App Store Connect, AppA veritabanını okur; Mac'i uzaktan kullanır (komut, ekran, tıklama, {SAHIP}'in Mac Notlar ve Fotoğraflarını okur); kendi iPhone'unu sürer; {SAHIP}'in telefonuna gerçek iOS hatırlatıcısı ve takvim etkinliği yazar; Kokpit'e ve Telegram'a mesaj, foto, video, tablo, diff düşürür; görsel ve video üretir; sesli cevap verir; web'de gezer (headless tarayıcı); e-posta ALIR (ghost@example.com); {SAHIP}'in konumunu, takvimini, telefon pilini görür; araştırma yapar, rapor çıkarır.
- Ghost'un YAPAMADIKLARI: e-posta GÖNDEREMEZ; telefonla ARAYAMAZ (telefon hattı kurulmadı); para harcayamaz, ödeme yapamaz; App Store'a kendi başına gönderemez; uyuyan Mac'i uyandıramaz; {SAHIP}'in iPhone'una dokunamaz (yalnız hatırlatıcı/takvim yazar); fiziksel iş yapamaz (şarj etmek, SIM takmak, bir şey taşımak). Bunlar istenirse dürüstçe "Ghost bunu yapamaz" de, Ghost'a görev diye GÖNDERME.
- SENİN yapabildiklerin: durum, siteler, ürün sayıları, makineler, bugün olanlar, geçmiş konuşma/kararlar sorularını araçla okuyup söylemek; iş isteğini Ghost'a iletmek; küçük sohbet. Sen arama yapamazsın, dosya ve ekran göremezsin, kod yazamazsın, görüşme bitince unutursun (kalıcı hafıza Ghost'undur; "hatırlıyor musun" için `ghost_hafiza`).
- Makineler: {MAKINELER}
- Ürünler: {URUNLER}

Katı kurallar:
1. Olgular araçtan gelir. Durum, ne yapıyor, bugün ne oldu, bekleyen var mı → `ghost_durum`. Siteler, ziyaret, trafik, site ayakta mı → `ghost_siteler`. AppA/ürün sayıları (kullanıcı, abone, hata, build) → `ghost_urun`. Mac, telefon, tünel, VPS, kota, nöbetçi → `ghost_makineler`. Bugün ne yapıldı, commitler, {SAHIP}'in takvimi, telefon görevleri → `ghost_bugun`. Son konuşma → `ghost_son_mesajlar`. Geçmişte ne konuşuldu, ne karar verildi, "hatırlıyor musun" → `ghost_hafiza` (anahtar kelimeyle). Araç ne diyorsa onu söyle; araçta olmayan olguyu söyleme, tahmin etme. Araç hata verirse "şu an okuyamadım" de.
2. Bir şey YAPILMASI isteniyorsa (kod, deploy, araştırma, dosya, site, uygulama, telefon, Mac, hatırlatıcı kurma, rapor, herhangi bir iş) kendin yapmaya kalkma; `ghost_gorev` ile Ghost'a ilet. Görevi {SAHIP}'in söylediği gibi, tek net cümle yaz. İlettikten sonra NE ilettiğini aynen söyle: "Ghost'a verdim: sitelerin trafiğini çıkar. Bitince Kokpit'e düşer." {SAHIP} yanlışsa düzeltir. Yapılmamış işi yapılmış gibi ASLA anlatma.
3. Görev vermeden önce iki soru: (a) Gerçekten iş mi, yoksa laf mı? "Şarj et tabii" ironidir, "bence şöyle olmalı" fikirdir, "ne yapalım" sohbettir — bunlar görev DEĞİL. (b) Ghost'un yapabildiği bir şey mi? Değilse söyle, gönderme. Emin değilsen tek kısa soru sor; iki seçenekten fazlasını sesle sunma.
4. Geri alınmaz iş (silme, para, deploy, store'a gönderim) istenirse: yine Ghost'a ilet ama görev metnine onay cümlesi EKLEME; Ghost kendi onay kapısını çalıştırır. Sen onay VEREMEZSİN.
5. YANLIŞ DUYMA: sesi yazıya çeviren motor Türkçede kelimeleri karıştırır ("sitelerin durumu" → "stajyer eleman", "üç bin bir hatası" → "stresi", "ElevenLabs" → "Eloven Labs"). Duyduğun cümle {SAHIP}'in dünyasıyla ({DUNYA}) ilgisizse ya da anlamsızsa büyük olasılıkla yanlış duydun: tahmin ettiğin anlamı SORARAK doğrula ("Sitelerin durumunu mu sordun?"). Anlamsız metni asla görev yapma, asla araştırmaya gönderme.
6. Transkript boş, "..." ya da yalnız "hı, hmm, şey" ise {SAHIP} düşünüyordur: `skip_turn` kullan, sessiz kal. "Orada mısın" diye sıkıştırma; {SAHIP} isterse konuşur.
7. Küçük sohbet, selam, nasıl gidiyor, saat kaç: araçsız, doğal ve kısa cevapla.
8. {SAHIP}'in konumu, takvimi, pili `ghost_durum` cevabında varsa kullanabilirsin; yoksa bilmiyorsun de. Pil düşükse bir kez söyle, her turda tekrarlama.
"""

# Kişisel satırlar profilden (ops/sahip.json "ses"): ad, makineler, ürünler, dünya sözlüğü.
_ses = (sahip.profil().get("ses") or {})
ISTEM = (_ISTEM_SABLON
         .replace("{SAHIP_TAM}", sahip.ad())
         .replace("{SAHIP}", sahip.profil().get("ad_kisa") or sahip.ad())
         .replace("{MAKINELER}", _ses.get("makineler") or "VPS (Ghost'un beyni, 7/24); Mac (Ghost işçisi); telefon.")
         .replace("{URUNLER}", _ses.get("urunler") or "profilde tanımlı değil; sayı ve durum sorulursa araç çağır.")
         .replace("{DUNYA}", _ses.get("dunya") or "Ghost, Kokpit, siteler, Mac, telefon, kod, deploy, commit"))

ILK_MESAJ = "Echo burada, dinliyorum."


def _key():
    k = (config.load_env().get("ELEVENLABS_API_KEY") or "").strip()
    if not k:
        raise SystemExit("ELEVENLABS_API_KEY yok (secrets.env)")
    return k


def _call(method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "xi-api-key": _key(), "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"hata": raw[:600]}


def kayit_oku():
    try:
        return json.load(open(KAYIT, encoding="utf-8"))
    except Exception:
        return {}


def _kayit_yaz(d):
    tmp = KAYIT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, KAYIT)


def ses_sirri():
    """Webhook araçlarının başlık sırrı. secrets.env'de GHOST_SES_SECRET; yoksa üretir ve ekler."""
    env = config.load_env()
    s = (env.get("GHOST_SES_SECRET") or "").strip()
    if s:
        return s
    s = _secrets.token_hex(24)
    with open(os.path.join(config.BASE, "secrets.env"), "a", encoding="utf-8") as f:
        f.write("\n# Ses ajanı webhook başlığı (X-Ghost-Ses), ghost/ses_ajani.py üretti\nGHOST_SES_SECRET=%s\n" % s)
    return s


# ------------------------------------------------------------------ araç tanımları

def _arac(ad, aciklama, govde=None, timeout=20):
    """ElevenLabs webhook aracı gövdesi. Sır başlığı workspace secret'tan gelir."""
    return {"tool_config": {
        "type": "webhook", "name": ad, "description": aciklama,
        "response_timeout_secs": timeout,
        "api_schema": {
            "url": "%s/v1/ses/arac/%s" % (APP_HOST, ad.replace("ghost_", "")),
            "method": "POST", "content_type": "application/json",
            "request_headers": {"X-Ghost-Ses": {"type": "secret", "secret_id": "__SECRET__"}},
            "request_body_schema": govde or {"type": "object", "properties": {}, "required": []},
        }}}


# Ses→yazı motoruna öğretilen özel sözcükler (Türkçe transkript bunları karıştırıyordu:
# "sitelerin durumu"→"stajyer eleman", "ElevenLabs"→"Eloven Labs"; 2026-09-26 dökümleri).
ASR_ANAHTAR = [
    "Ghost", "Echo", "Kokpit", "Sahne", "Nabız",
    "ElevenLabs", "Cloudflare", "PostHog", "Sentry", "GitHub", "TestFlight", "App Store",
    "Xcode", "Flutter", "commit", "deploy", "build", "push", "pull request", "webhook",
    "VPS", "Mac", "MacBook", "iPhone", "YubiKey", "WDA", "Telegram", "Higgsfield",
    "nöbetçi", "hatırlatıcı",
    "siteler", "site ziyareti", "trafik", "abone", "kullanıcı", "kota", "tünel",
] + sahip.asr_sozluk()   # ürün/yer adları profilden (ops/sahip.json "asr_sozluk")

ARACLAR = [
    _arac("ghost_durum",
          "Ghost'un ŞU ANKİ durumu: altyapı sağlığı, Ghost şu an bir iş yapıyor mu, bugün özet, "
          "sahipten bekleyen karar/soru var mı, sahibin konum/takvim/pil bağlamı. 'Ne durumdayız', "
          "'ne yapıyorsun', 'bekleyen var mı' gibi sorularda ÖNCE bunu çağır."),
    _arac("ghost_siteler",
          "Siteler: hangileri ayakta, son 7 gün ve son 24 saat GERÇEK dış ziyaret sayıları (sahip ve "
          "bot ayıklanmış), ziyaretin nereden geldiği. 'Siteler ne durumda', 'site ziyareti', 'trafik', "
          "'kaç kişi girmiş', 'example.com/app-a.example.com/app-b.example.com/yalco' sorularında çağır."),
    _arac("ghost_urun",
          "AppA ürün sayıları: App Store vitrin durumu, kullanıcı, ödeyen abone, aktif kullanıcı, "
          "açık hata, build, testçi ve dünden fark. 'Kaç kullanıcı', 'abone var mı', 'AppA ne durumda', "
          "'hata var mı', 'build çıktı mı' sorularında çağır."),
    _arac("ghost_makineler",
          "Makineler ve bağlantılar: VPS yük/disk/bellek, Mac ve iPhone bağlı mı, tünel sağlıklı mı, "
          "Ghost servisleri, nöbetçiler, Claude kotası yüzdesi. 'Mac bağlı mı', 'telefon görünüyor mu', "
          "'kota ne kadar', 'sunucu nasıl', 'tünel kopuk mu' sorularında çağır."),
    _arac("ghost_bugun",
          "Bugün olanlar: bugünkü commit listesi (saat + başlık), kaç mesaj/eylem, sahibin bugünkü "
          "takvimi, telefona yazılmış hatırlatıcı/görevler. 'Bugün ne yaptın', 'hangi commitler', "
          "'bugün takvimde ne var', 'hatırlatıcı kurulu mu' sorularında çağır."),
    _arac("ghost_hafiza",
          "Ghost'un kalıcı hafızasında ve geçmiş sohbette anahtar kelimeyle arama. 'Hatırlıyor musun', "
          "'geçen gün ne konuştuk', 'şu konuda ne karar verdik', 'en son şunu sormuştum' sorularında "
          "çağır. Kelimeleri Türkçe kök haliyle ver (ör. 'site ziyaret', 'Yalco', 'YubiKey').",
          {"type": "object", "properties": {"sorgu": {"type": "string",
                                                      "description": "1-4 anahtar kelime, Türkçe"}},
           "required": ["sorgu"]}),
    _arac("ghost_son_mesajlar",
          "Ghost ile Sahip arasındaki son sohbet mesajları (en yeni sonda). 'Son ne dedi', 'az önce ne "
          "konuştuk', 'şu iş ne oldu', 'Ghost cevap verdi mi' sorularında çağır.",
          {"type": "object", "properties": {"adet": {"type": "integer",
                                                     "description": "Kaç mesaj (1-15), varsayılan 6"}},
           "required": []}),
    _arac("ghost_gorev",
          "sahibin sesle verdiği İŞİ Ghost'a iletir (Sohbet kuyruğuna yazar). Kod, araştırma, deploy, "
          "dosya, hatırlatıcı, Mac/telefon işi, site, rapor: yapılması istenen iş buradan gider. "
          "Sen yapma, Ghost yapar. ÖNCE emin ol: gerçekten iş mi (ironi/fikir/sohbet değil) ve Ghost'un "
          "yapabildiği bir şey mi? Yanlış duymuş olabileceğin anlamsız cümleyi GÖNDERME, sor. "
          "Görev metni sahibin söylediği iş, tek net cümle. Cevapta ilettiğin metni sahibe aynen söyle.",
          {"type": "object", "properties": {"gorev": {"type": "string",
                                                      "description": "Ghost'a iletilecek iş, Türkçe, tek cümle"}},
           "required": ["gorev"]}, timeout=15),
]
ARAC_ADLARI = [a["tool_config"]["name"] for a in ARACLAR]


# ------------------------------------------------------------------ araç UÇLARI (api.py çağırır)

def arac_calistir(ad, veri):
    """POST /v1/ses/arac/<ad> gövdesi → ajanın okuyacağı KISA düz metin (LLM'e gider)."""
    if ad == "durum":
        return {"metin": durum_metni()}
    if ad == "siteler":
        return {"metin": siteler_metni()}
    if ad == "urun":
        return {"metin": urun_metni()}
    if ad == "makineler":
        return {"metin": makineler_metni()}
    if ad == "bugun":
        return {"metin": bugun_metni()}
    if ad == "hafiza":
        return {"metin": hafiza_metni(str(veri.get("sorgu") or ""))}
    if ad == "son_mesajlar":
        try:
            n = max(1, min(15, int(veri.get("adet") or 6)))
        except (TypeError, ValueError):
            n = 6
        return {"metin": son_mesajlar_metni(n)}
    if ad == "gorev":
        g = " ".join((veri.get("gorev") or "").split())
        if len(g) < 4:
            return {"metin": "Görev metni boş ya da çok kısa geldi; Ghost'a iletilmedi. sahibe ne istediğini tekrar sor."}
        from ghost import channels, kokpit
        metin = "[Sesle, ses ajanı üzerinden] " + g[:1500]
        mid = kokpit.outbox_yaz(metin, "sahip")
        channels.son_kanal_ayarla("kokpit")
        kokpit.mesaj_kuyrukla(metin, None, sesli=True)
        return {"metin": "Ghost'un kuyruğuna yazıldı (no %s), iletilen metin: \"%s\". Ghost bitirince cevabı "
                         "Kokpit'e ve sesli olarak düşer. sahibe ilettiğin metni aynen söyle; yapıldı DEME."
                         % (mid, g[:200])}
    return {"metin": "bilinmeyen araç"}


def _nabiz():
    from ghost import health
    return health.nabiz()


def siteler_metni(n=None):
    """Siteler: ayakta mı + gerçek dış ziyaret (site_nabzi, sahip/bot ayıklı)."""
    try:
        n = n or _nabiz()
    except Exception as e:
        return "Siteler okunamadı: %s" % e
    p = []
    st = n.get("siteler") or []
    if st:
        kotu = [s.get("ad") for s in st if not s.get("ok")]
        p.append("%d site izleniyor, hepsi ayakta." % len(st) if not kotu
                 else "%d site izleniyor; ERİŞİLEMEYEN: %s." % (len(st), ", ".join(str(k) for k in kotu)))
    sn = n.get("site_nabzi") or {}
    if sn.get("var"):
        p.append("Gerçek dış ziyaret (sahip ve bot ayıklı, %s): son 7 günde %s, son 24 saatte %s%s." % (
            "ölçüm %s dakika önce" % sn.get("yas_dk", "?"), sn.get("toplam_7g", "?"), sn.get("toplam_24s", "?"),
            "; ölçüm BAYAT" if sn.get("bayat") else ""))
        sir = sorted((s for s in sn.get("siteler") or []), key=lambda s: -(s.get("ziyaret_7g") or 0))
        parca = []
        for s in sir:
            z7, z24 = s.get("ziyaret_7g") or 0, s.get("ziyaret_24s") or 0
            kay = (s.get("kaynak") or "").split(" · ")[0].replace("www.", "")
            parca.append("%s %d (24 saat %d%s)" % (s.get("ad"), z7, z24, ", kaynak " + kay if kay and z7 else ""))
        if parca:
            p.append("Site site 7 gün: " + "; ".join(parca) + ".")
    else:
        p.append("Ziyaret ölçümü yok.")
    return " ".join(p)[:1800]


def urun_metni(n=None):
    """AppA iş metrikleri (isnabzi): vitrin, kullanıcı, ödeyen, aktif, hata, build, testçi."""
    try:
        n = n or _nabiz()
    except Exception as e:
        return "Ürün sayıları okunamadı: %s" % e
    i = n.get("is") or {}
    if not i.get("var"):
        return "Ürün ölçümü yok."
    p = ["AppA (lansman öncesi), ölçüm %s%s:" % (i.get("tarih", "?"),
         " (BAYAT, %s gün)" % i["yas_gun"] if (i.get("yas_gun") or 0) > 1 else "")]
    parca = []
    for m in i.get("metrikler") or []:
        ad, g = m.get("ad") or m.get("anahtar"), m.get("goster", m.get("deger"))
        fark = m.get("fark")
        parca.append("%s %s%s" % (ad, g, " (dünden %+d)" % fark if isinstance(fark, (int, float)) and fark else ""))
    p.append("; ".join(parca) + ".")
    return " ".join(p)[:1200]


def makineler_metni(n=None):
    """Makineler, bağlantı (Mac/telefon/tünel), servisler, nöbetçiler, kota."""
    try:
        n = n or _nabiz()
    except Exception as e:
        return "Makineler okunamadı: %s" % e
    p = []
    mk = n.get("makineler") or []
    if mk:
        p.append("Makineler: " + "; ".join("%s %s%s" % (m.get("ad"), m.get("durum"),
                 " (" + m["detay"] + ")" if m.get("detay") and m.get("durum") != "iyi" else "") for m in mk) + ".")
        vps = next((m for m in mk if m.get("ad") == "VPS"), None)
        if vps and vps.get("detay"):
            p.append("VPS: %s." % vps["detay"])
    b = n.get("baglanti") or {}
    if b:
        p.append("Bağlantı: Mac %s, telefon %s, tünel %s." % (
            "bağlı" if b.get("mac") else "KOPUK", "bağlı" if b.get("telefon") else "KOPUK", b.get("tunel") or "?"))
    nb = n.get("nobetciler") or []
    if nb:
        kotu = [x.get("ad") for x in nb if not x.get("ok")]
        p.append("Nöbetçiler: %d/%d sağlıklı%s." % (len(nb) - len(kotu), len(nb),
                 "; sorunlu " + ", ".join(str(k) for k in kotu) if kotu else ""))
    k = n.get("kota") or {}
    if k.get("var"):
        lim = [("%s yüzde %s" % (l.get("ad"), l.get("yuzde"))) + (", sıfırlanma " + l["sifirlanma_metin"]
               if l.get("sifirlanma_metin") else "") for l in (k.get("limitler") or [])[:3]]
        p.append("Claude kotası (%s): %s." % (k.get("plan", "?"), "; ".join(lim)))
    if n.get("freeze"):
        p.append("DİKKAT: Ghost FREEZE'de, yalnız okuma.")
    return " ".join(p)[:1800]


def bugun_metni(n=None):
    """Bugün: commitler, sayılar, sahibin takvimi, telefon görevleri."""
    try:
        n = n or _nabiz()
    except Exception as e:
        return "Bugün okunamadı: %s" % e
    b = n.get("bugun") or {}
    p = []
    say = b.get("sayilar") or {}
    if say:
        p.append("Bugün (%s): %s commit, %s mesaj, %s eylem." % (b.get("tarih", ""), say.get("commit", 0),
                                                                 say.get("mesaj", 0), say.get("eylem", 0)))
    cm = b.get("commitler") or []
    if cm:
        p.append("Commitler (yeniden eskiye): " + "; ".join(
            "%s %s" % (c.get("saat", ""), (c.get("baslik") or "").split(" — ")[0][:90]) for c in cm[:6]) + ".")
    try:
        from ghost import duyular
        t = duyular.bugun_metin()
    except Exception:
        t = ""
    if t:
        p.append(t.replace("📅", "Takvim:").strip())
    try:
        from ghost import gorevler
        g = gorevler.metin(4)
    except Exception:
        g = ""
    if g:
        g = " ".join(g.replace("📲", "").replace("✅", "yazıldı:").replace("⏳", "bekliyor:")
                     .replace("❌", "başarısız:").split())
        p.append(g[:400] + ".")
    return " ".join(p)[:1800] or "Bugün için kayıt yok."


def _tr_kucuk(s):
    return (s or "").replace("I", "ı").replace("İ", "i").lower()


def hafiza_metni(sorgu, k=5):
    """Kalıcı hafıza (memory.recall, FTS) + sohbet dökümü (kokpit outbox) anahtar kelime araması.
    Bütün kelimeler geçen kayıtlar önce, yeniden eskiye; yoksa FTS'nin OR eşleşmeleri."""
    sorgu = " ".join((sorgu or "").split())[:80]
    if len(sorgu) < 2:
        return "Arama kelimesi boş geldi; sahibe neyi hatırlamasını istediğini sor."
    kel = [w for w in _tr_kucuk(sorgu).split() if len(w) >= 3] or [_tr_kucuk(sorgu)]
    p = []
    try:
        from ghost import memory
        rows = memory.recall(sorgu, 40)
    except Exception:
        rows = []
    if rows:
        tam = [r for r in rows if all(w in _tr_kucuk(str(r[2])) for w in kel)]
        sec = sorted(tam or rows, key=lambda r: str(r[0]), reverse=True)[:k]
        p.append("Hafıza (%d kayıt%s, yeniden eskiye): " % (len(sec), "" if tam else ", kısmi eşleşme") + " | ".join(
            "%s [%s] %s" % (str(ts)[:10], kind, " ".join(str(t).split())[:180]) for ts, kind, t in sec))
    try:
        from ghost import kokpit
        c = kokpit._db()
        try:
            kos = " AND ".join("lower(metin) LIKE ?" for _ in kel)
            rows = c.execute("SELECT ts, kaynak, metin FROM kokpit_outbox WHERE %s ORDER BY id DESC LIMIT 60" % kos,
                             ["%" + w + "%" for w in kel]).fetchall()
        finally:
            c.close()
        bul = []
        for ts, kaynak, metin in rows:
            if not all(w in _tr_kucuk(metin) for w in kel):      # SQL lower() Türkçe İ/I bilmez; ikinci süzgeç
                continue
            kim = "Sahip" if (kaynak or "").lower() == "sahip" else "Ghost"
            bul.append("%s %s %s: %s" % ((ts or "")[:10], (ts or "")[11:16], kim, " ".join((metin or "").split())[:180]))
            if len(bul) >= 4:
                break
        if bul:
            p.append("Sohbet dökümü (en yeni önce): " + " | ".join(bul))
    except Exception:
        pass
    if not p:
        return "'%s' için hafızada ve son sohbette kayıt bulunamadı." % sorgu
    return " ".join(p)[:2200]


_FIIL = (("bash", "komut çalıştırıyor"), ("edit", "kod yazıyor"), ("write", "dosya yazıyor"),
         ("read", "dosya okuyor"), ("grep", "kodda arıyor"), ("glob", "kodda arıyor"), ("agent", "alt ajan koşturuyor"),
         ("playwright", "tarayıcıda geziyor"), ("github", "GitHub'a bakıyor"), ("sentry", "Sentry'ye bakıyor"),
         ("websearch", "web'de arıyor"), ("webfetch", "web'den okuyor"))


def _is_fiili(ozet):
    """akis özeti "Bash · <ham komut>" → sesle okunur fiil (ham komut/yol asla sese gitmez)."""
    arac = ((ozet or "").split(" · ", 1)[0]).strip().lower()
    for anahtar, fiil in _FIIL:
        if anahtar in arac:
            return fiil
    return "bir iş üzerinde"


def _sure_metni(sn):
    sn = int(sn or 0)
    if sn < 90:
        return "%d saniyedir" % sn
    if sn < 5400:
        return "%d dakikadır" % round(sn / 60)
    return "%d saattir" % round(sn / 3600)


def durum_metni():
    from ghost import health
    try:
        n = health.nabiz()
    except Exception as e:
        return "Durum okunamadı: %s" % e
    p = []
    alt = n.get("altyapi") or {}
    p.append("Altyapı: %s." % (alt.get("satir") or alt.get("ozet") or n.get("ozet") or "bilinmiyor"))
    sorun = alt.get("sorunlar") or []
    if sorun:
        p.append("Sorunlar: " + "; ".join(str(s) for s in sorun[:3]) + ".")
    try:
        from ghost import akis
        m = akis.sohbet_durumu()
    except Exception:
        m = {}
    if m.get("aktif"):
        p.append("Ghost şu an çalışıyor (%s): %s." % (_sure_metni(m.get("sure_sn")), _is_fiili(m.get("ozet"))))
    else:
        p.append("Ghost şu an boşta.")
    b = n.get("bugun") or {}
    sat = [s.get("metin") for s in (b.get("satirlar") or []) if s.get("metin")]
    if sat:
        p.append("Bugün: " + "; ".join(sat[:4]) + ".")
    bek = n.get("bekleyen") or {}
    k, s = bek.get("kararlar") or [], bek.get("sorular") or []
    if k:
        p.append("sahipten bekleyen %d karar: %s." % (len(k), "; ".join(
            (x.get("metin") or str(x)).replace("\n", " ")[:80] for x in k[:2])))
    if s:
        p.append("Cevapsız %d soru: %s." % (len(s), "; ".join((x.get("metin") or str(x))[:80] for x in s[:2])))
    if not k and not s:
        p.append("sahipten bekleyen bir şey yok.")
    try:
        from ghost import duyular
        ds = duyular.satir()
    except Exception:
        ds = ""
    if ds:
        ds = ds.strip()
        if "):" in ds:                      # "[SAHİBİN BAĞLAMI (...): konum ... . Konu ilgisizse ...]" → yalnız içerik
            ds = ds.split("):", 1)[1]
        ds = ds.split("Konu ilgisizse", 1)[0].strip(" .]")
        if ds:
            p.append("sahibin bağlamı: %s." % ds[:200])
    if n.get("freeze"):
        p.append("DİKKAT: Ghost FREEZE'de, yalnız okuma.")
    return " ".join(p)[:1800]


def son_mesajlar_metni(n=6):
    from ghost import kokpit
    rows = kokpit.outbox_oku(-1, n)
    if not rows:
        return "Henüz mesaj yok."
    out = []
    for m in rows:
        kim = "Sahip" if (m.get("kaynak") or "").lower() == "sahip" else "Ghost"
        t = (m.get("metin") or "").strip().replace("\n", " ")
        if not t and m.get("medya"):
            t = "[medya]"
        out.append("%s (%s): %s" % (kim, (m.get("ts") or "")[11:16], t[:220]))
    return "\n".join(out)[:2500]


# ------------------------------------------------------------------ kurulum

def _sir_kur():
    code, js = _call("POST", "/convai/secrets", {"type": "new", "name": "ghost-ses-%d" % int(time.time()),
                                                 "value": ses_sirri()})
    if code >= 300:
        raise SystemExit("workspace secret kurulamadı (%s): %s" % (code, js))
    return js["secret_id"]


def _araclar_esitle(secret_id, mevcut=None):
    """ARACLAR listesini ElevenLabs'e eşitler: kayıtta olmayan aracı kurar, olanı yerinde
    günceller (açıklama/şema değişince ajan yeni davranışı görsün). → {ad: tool_id}"""
    ids = dict(mevcut or {})
    for a in ARACLAR:
        ad = a["tool_config"]["name"]
        gov = json.loads(json.dumps(a).replace("__SECRET__", secret_id))
        if ids.get(ad):
            code, js = _call("PATCH", "/convai/tools/" + ids[ad], gov)
            if code >= 300:
                raise SystemExit("araç güncellenemedi %s (%s): %s" % (ad, code, js))
        else:
            code, js = _call("POST", "/convai/tools", gov)
            if code >= 300:
                raise SystemExit("araç kurulamadı %s (%s): %s" % (ad, code, js))
            ids[ad] = js["id"]
    return ids


def _ajan_govde(tool_ids):
    env = config.load_env()
    voice = (env.get("ELEVENLABS_VOICE_ID") or "").strip()
    return {
        "name": "Echo",
        "conversation_config": {
            "agent": {
                "first_message": ILK_MESAJ,
                "language": "tr",
                "prompt": {"prompt": ISTEM, "llm": LLM, "temperature": 0.3,
                           "tool_ids": [tool_ids[ad] for ad in ARAC_ADLARI if ad in tool_ids],
                           "timezone": "Europe/Istanbul",
                           # skip_turn: "..." / "hmm" transkriptinde sessiz kalabilsin ("orada mısın" dürtmesi yok)
                           "built_in_tools": {"skip_turn": {
                               "type": "system", "name": "skip_turn",
                               "description": "Sahip düşünüyor, transkript boş/'...'/'hmm' ya da 'bir saniye' dedi: konuşma, sırayı atla.",
                               "params": {"system_tool_type": "skip_turn"}}}},
            },
            # ASR: özel sözcük listesi (Türkçe transkript alan adlarını karıştırıyordu)
            "asr": {"keywords": ASR_ANAHTAR},
            # 7 sn sessizlikte ajan kendiliğinden "orada mısın" diyordu; Sahip düşünürken beklesin
            "turn": {"turn_timeout": 25.0},
            "tts": {"model_id": TTS_MODEL, "voice_id": voice, "stability": 0.5, "similarity_boost": 0.75},
            "conversation": {"max_duration_seconds": 900},
        },
        "platform_settings": {"auth": {"enable_auth": True}},
    }


def kur():
    k = kayit_oku()
    if k.get("agent_id"):
        print("zaten kurulu:", k["agent_id"], "— 'guncelle' kullan"); return 0
    sid = _sir_kur()
    tools = _araclar_esitle(sid)
    code, js = _call("POST", "/convai/agents/create", _ajan_govde(tools))
    if code >= 300:
        raise SystemExit("ajan kurulamadı (%s): %s" % (code, json.dumps(js, ensure_ascii=False)[:800]))
    k = {"agent_id": js["agent_id"], "secret_id": sid, "tools": tools, "llm": LLM,
         "kuruldu": time.strftime("%F %T")}
    _kayit_yaz(k)
    print(json.dumps(k, ensure_ascii=False, indent=1))
    return 0


def guncelle():
    k = kayit_oku()
    if not k.get("agent_id"):
        raise SystemExit("kurulu değil; önce 'kur'")
    k["tools"] = _araclar_esitle(k["secret_id"], k.get("tools"))
    _kayit_yaz(k)                       # araç kimlikleri önce diske: ajan PATCH'i çökse de kayıp yok
    gov = _ajan_govde(k["tools"])
    code, js = _call("PATCH", "/convai/agents/" + k["agent_id"], gov)
    if code >= 300:
        raise SystemExit("güncelleme başarısız (%s): %s" % (code, json.dumps(js, ensure_ascii=False)[:800]))
    k["guncellendi"] = time.strftime("%F %T"); _kayit_yaz(k)
    print("ajan güncellendi", k["agent_id"])
    return 0


def token():
    """Kokpit için konuşma jetonu (WebRTC). Ajan private: yalnız bu yol açar."""
    k = kayit_oku()
    if not k.get("agent_id"):
        return None, "ses ajanı kurulu değil"
    code, js = _call("GET", "/convai/conversation/token?agent_id=" + k["agent_id"], timeout=20)
    if code >= 300 or not js.get("token"):
        return None, "jeton alınamadı (%s): %s" % (code, json.dumps(js, ensure_ascii=False)[:300])
    return js["token"], None


def imzali_ws():
    k = kayit_oku()
    code, js = _call("GET", "/convai/conversation/get-signed-url?agent_id=" + k["agent_id"], timeout=20)
    if code >= 300:
        raise SystemExit("signed url alınamadı (%s): %s" % (code, js))
    return js["signed_url"]


def konus(metinler, bekle=25):
    """Metin kipinde canlı sınama: WebSocket'e yazı gönder, ajanın cevabını ve araç çağrılarını yazdır."""
    import asyncio
    try:
        import websockets
    except ImportError:
        raise SystemExit("pip3 install --user --break-system-packages websockets")

    async def _run():
        async with websockets.connect(imzali_ws(), max_size=4_000_000) as ws:
            await ws.send(json.dumps({"type": "conversation_initiation_client_data",
                                      "conversation_config_override": {"conversation": {"text_only": True}}}))
            sira = list(metinler)
            son = time.time()
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=bekle)
                except asyncio.TimeoutError:
                    break
                ev = json.loads(raw)
                t = ev.get("type")
                if t == "ping":
                    await ws.send(json.dumps({"type": "pong", "event_id": ev["ping_event"]["event_id"]}))
                elif t == "conversation_initiation_metadata":
                    print("[bağlandı]", ev["conversation_initiation_metadata_event"]["conversation_id"])
                elif t == "agent_response":
                    print("AJAN:", ev["agent_response_event"]["agent_response"])
                    if sira:
                        m = sira.pop(0); print("SAHİP:", m)
                        await ws.send(json.dumps({"type": "user_message", "text": m, "source_medium": "text"}))
                    else:
                        await asyncio.sleep(1.5); break
                elif t == "agent_tool_response":
                    e = ev.get("agent_tool_response") or ev.get("agent_tool_response_event") or ev
                    print("  [araç]", e.get("tool_name"), e.get("status") or ("hata" if e.get("is_error") else "ok"))
                elif t in ("user_transcript", "audio", "interruption", "vad_score",
                           "internal_tentative_agent_response", "agent_chat_response_part"):
                    pass
                else:
                    print("  [olay]", t, json.dumps(ev, ensure_ascii=False)[:200])
                son = time.time()
    asyncio.run(_run())


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__); sys.exit(0)
    if a[0] == "kur":
        sys.exit(kur())
    if a[0] == "guncelle":
        sys.exit(guncelle())
    if a[0] == "durum":
        print(json.dumps(kayit_oku(), ensure_ascii=False, indent=1)); sys.exit(0)
    if a[0] == "konus":
        konus(a[1:] or ["Selam, durum ne?"]); sys.exit(0)
    if a[0] == "arac":
        print(json.dumps(arac_calistir(a[1], json.loads(a[2]) if len(a) > 2 else {}), ensure_ascii=False, indent=1)); sys.exit(0)
    print("bilinmeyen komut", a[0]); sys.exit(1)
