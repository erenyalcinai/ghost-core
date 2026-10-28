"""Sahne — sunucudan çizilen arayüz (docs/kokpit.md 3.3, Faz 1).

Kabuk (iOS) bir kez derlenir; ekranı Ghost VPS'ten JSON olarak basar.
Ghost sohbet içinden çağırır:
    python3 -m ghost.sahne goster '{"baslik":"Trafik","bilesenler":[...]}'
    python3 -m ghost.sahne goster @dosya.json
    python3 -m ghost.sahne nabiz        # gerçek nabız sahnesi (health.nabiz)
    python3 -m ghost.sahne temizle
    python3 -m ghost.sahne sor '<soru>' 'a|b|c'      # seçenekli soru (yalnız Sohbet butonları; Sahne'ye çizmez)
    python3 -m ghost.sahne onay '<başlık>' @diff.txt  # onay + önizleme (Onayla / Vazgeç)
    python3 -m ghost.sahne metinden @rapor.md 'Başlık' [--kart AppA]  # tablo/diff'i sahneye bas (+ Masa'ya kalıcı)
    python3 -m ghost.sahne goster @s.json --kart Raporlar              # sahneyi Masa kartına da kaydet
    python3 -m ghost.sahne gecmis                     # son sahneler (seq ile geri okunur)

Bileşen seti v1 (surum=1): metin · kart · liste · tablo · grafik · gorsel · diff ·
form · eylem · canli · sutun · satir. Bilinmeyen tür kabukta "kabuk güncellemesi
gerekli" kartı olur, çökmez — burada da reddedilmez, yalnız uyarı verilir.

Eylemler (buton/form) uygulamadan POST /v1/eylem ile gelir; köprüye kokpit
inbox'ı üzerinden Sahip mesajı olarak düşer ("[Sahne …]" ön ekiyle) — Ghost onu
sıradan bir mesaj gibi okur, ayrı bir dispatcher yok.
"""
import hashlib
import json
import os
import re
import secrets
import sys
import time

from ghost import config

SURUM = 1
BILESENLER = {
    "metin": ("metin",), "kart": ("baslik",), "liste": ("ogeler",),
    "tablo": ("sutunlar", "satirlar"), "grafik": ("seriler",), "gorsel": ("url",),
    "diff": ("metin",), "form": ("id", "alanlar"), "eylem": ("id", "etiket"),
    "canli": ("kaynak",), "sutun": ("cocuklar",), "satir": ("cocuklar",),
}
CANLI_KAYNAKLAR = ("telefon", "mac")
BOS = {"surum": SURUM, "seq": 0, "ts": "", "baslik": "", "bilesenler": []}


def _yol():
    return os.path.join(config.OPS, "sahne.json")


def dogrula(b, derinlik=0, yol="bilesenler"):
    """Uyarı listesi döndürür (boş = temiz). Hiçbir şeyi reddetmez."""
    uyari = []
    if derinlik > 6:
        return [yol + ": çok derin (>6)"]
    if not isinstance(b, dict):
        return [yol + ": nesne değil"]
    tur = b.get("tur")
    if tur not in BILESENLER:
        return [yol + ": bilinmeyen tür %r (kabuk 'güncelleme gerekli' gösterir)" % tur]
    for alan in BILESENLER[tur]:
        if alan not in b:
            uyari.append("%s: %s için %r eksik" % (yol, tur, alan))
    if tur == "canli" and b.get("kaynak") not in CANLI_KAYNAKLAR:
        uyari.append("%s: canli.kaynak %r; olası: %s" % (yol, b.get("kaynak"), "/".join(CANLI_KAYNAKLAR)))
    if tur == "grafik":
        for s in b.get("seriler") or []:
            if not isinstance(s, dict) or not isinstance(s.get("degerler"), list):
                uyari.append(yol + ": grafik serisi {ad, degerler:[..]} olmalı")
                break
    for i, c in enumerate(b.get("cocuklar") or []):
        uyari += dogrula(c, derinlik + 1, "%s.%s[%d]" % (yol, tur, i))
    return uyari


def oku():
    try:
        with open(_yol(), encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and "bilesenler" in d:
            return d
    except Exception:
        pass
    return dict(BOS)


def goster(bilesenler, baslik="", sessiz=False):
    """Sahneyi yaz → (seq, uyarılar). Atomik; seq monoton artar.
    sessiz: artık etkisiz (döküm satırı hiç yazılmıyor); eski çağrılar için duruyor."""
    if isinstance(bilesenler, dict) and "bilesenler" in bilesenler:
        baslik = baslik or bilesenler.get("baslik", "")
        bilesenler = bilesenler["bilesenler"]
    if isinstance(bilesenler, dict):
        bilesenler = [bilesenler]
    uyari = []
    for i, b in enumerate(bilesenler or []):
        uyari += dogrula(b, 0, "bilesenler[%d]" % i)
    eski = oku()
    seq = int(eski.get("seq") or 0) + 1
    d = {"surum": SURUM, "seq": seq, "ts": time.strftime("%F %T"),
         "baslik": baslik or "", "bilesenler": bilesenler or []}
    _gecmise_koy(eski)          # her yeni sahne öncekini siliyordu; son GECMIS_TAVAN geri alınabilir
    tmp = _yol() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, _yol())
    from ghost import onay as _onay
    if _onay.servis_modu():     # sahneden düşen onay kartı servis için de kapanır (düz "evet" yalnız görünen karta)
        try:
            ids = []
            for b in bilesenler or []:
                _eylem_idleri(b, ids)
            _onay.kartlar_esitle([i for i in ids if str(i).startswith("onay-")])
        except Exception:
            pass
    try:  # akışta iz bırak (Telegram'a gitmez). Döküme "Sahne güncellendi — sekmesine bak"
        # satırı 2026-10-04'te kaldırıldı (Sahip: sohbette zaten söylüyorsun, ikinci satır gürültü;
        # 30 günde 1310 Ghost mesajının 99'u bu satırdı). Sahne'ye bastığını cevabında BİR kez söyle.
        from ghost import akis
        akis.olay_yaz("sahne", {"tur": "sahne", "baslik": baslik or "", "seq": seq})
    except Exception:
        pass
    return seq, uyari


def temizle():
    return goster([], "")[0]


# ---------------------------------------------------------------- tarihçe (2026-09-04)
GECMIS_TAVAN = 8


def _gecmis_yol():
    return os.path.join(config.OPS, "sahne_gecmis.json")


def _gecmise_koy(sahne):
    if not sahne or not sahne.get("bilesenler") or not sahne.get("seq"):
        return
    g = gecmis_tam()
    if g and g[-1].get("seq") == sahne.get("seq"):
        return
    g.append(sahne)
    g = g[-GECMIS_TAVAN:]
    tmp = _gecmis_yol() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(g, f, ensure_ascii=False)
    os.replace(tmp, _gecmis_yol())


def gecmis_tam():
    try:
        with open(_gecmis_yol(), encoding="utf-8") as f:
            g = json.load(f)
        return g if isinstance(g, list) else []
    except Exception:
        return []


def gecmis():
    """[{seq, ts, baslik}] eski → yeni; şu anki sahne dahil değil."""
    return [{"seq": x.get("seq"), "ts": x.get("ts", ""), "baslik": x.get("baslik", "")} for x in gecmis_tam()]


def oku_seq(seq):
    """Tarihçedeki (ya da şu anki) sahneyi seq ile getir; yoksa None."""
    d = oku()
    if int(d.get("seq") or 0) == int(seq):
        return d
    for x in gecmis_tam():
        if int(x.get("seq") or 0) == int(seq):
            return x
    return None


# ---------------------------------------------------------------- refleksler (2026-09-04)
# Sahne açıldığından beri 9 kez basılmıştı, hepsi elle (/sahne). Bileşen eksik değildi,
# refleks eksikti. Üç refleks: iş sonucundan otomatik sahne, seçenekli soru, onay+önizleme.
_TABLO_SATIR = re.compile(r"^\s*\|.*\|\s*$")
_TABLO_AYRAC = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_DIFF_BLOK = re.compile(r"```diff\n(.*?)```", re.S)
TABLO_TAVAN = 40


def _tablolar(metin):
    out, satirlar = [], (metin or "").splitlines()
    i = 0
    while i < len(satirlar):
        if _TABLO_SATIR.match(satirlar[i]) and i + 1 < len(satirlar) and _TABLO_AYRAC.match(satirlar[i + 1]):
            bas = [h.strip() for h in satirlar[i].strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < len(satirlar) and _TABLO_SATIR.match(satirlar[i]):
                r = [h.strip() for h in satirlar[i].strip().strip("|").split("|")]
                rows.append((r + [""] * len(bas))[:len(bas)])
                i += 1
            if rows:
                out.append({"tur": "tablo", "sutunlar": bas, "satirlar": rows[:TABLO_TAVAN]})
            continue
        i += 1
    return out


def metinden(metin, baslik=""):
    """Bir iş/rapor metninden sahne bileşenleri: markdown tablolar + ```diff blokları.
    Düz metin sahne olmaz (gürültü); hiçbiri yoksa None."""
    b = []
    for d in _DIFF_BLOK.findall(metin or ""):
        b.append({"tur": "diff", "metin": d.strip()[:6000]})
    b.extend(_tablolar(metin))
    if not b:
        return None
    if baslik:
        b.insert(0, {"tur": "metin", "metin": baslik})
    return b


def isten(sonuc, baslik=""):
    """İş bitince köprü çağırır: sahneye değer içerik varsa basar → seq, yoksa None."""
    b = metinden(sonuc, "")
    if not b:
        return None
    seq, _ = goster(b, baslik or "İş sonucu")
    try:  # iş yolunda Ghost'un cevabı sahneden habersiz; tek kısa ipucu burada
        from ghost import kokpit
        kokpit.outbox_yaz("📊 %s — tablo/diff Sahne'de." % (baslik or "İş sonucu"), "ghost")
    except Exception:
        pass
    return seq


def sor(soru, secenekler, eylem_id=None, baslik="Soru"):
    """Seçenekli soru: Sohbet'te butonlu Ghost mesajı (Kokpit + Telegram aynı butonlar).
    2026-10-04'e kadar aynı soru Sahne'ye de kart olarak çiziliyordu; kaldırıldı (Sahip:
    "sorduğun soruyu çizip bir de Sahne'ye bak diyorsun"). Ölçüm: 30 günde 36 soru kartı,
    Sahip 34'ünü sohbette düz metinle cevapladı, 2'sini butonla; Sahne kopyası cevaptan sonra
    bayat kalıyordu. Sahne yalnız sohbete sığmayan içerik (tablo/diff/canlı/onay önizlemesi) taşır.
    Cevap düz metin ya da '[Sahne eylem: …]' olarak Ghost'a düşer; Nabız 'Benden ne bekliyor?'
    butonlu Ghost mesajını listeler. Döküm satır id'sini döndürür."""
    from ghost import kokpit
    secenekler = [str(x).strip() for x in (secenekler or []) if str(x).strip()][:6]
    if not secenekler:
        raise ValueError("en az bir seçenek gerekli")
    return kokpit.outbox_yaz(soru, "ghost", butonlar=[[(sc, sc) for sc in secenekler[i:i + 2]]
                                                      for i in range(0, len(secenekler), 2)]) or 0


def servis_esitle():
    """KÖPRÜ ana sürecinden çağrılır (servis başka süreci reddeder): sahnede görünmeyen açık kartlar serviste kapanır.
    Model artık bunu yapamaz (4. tur K1); sahne.goster'deki çağrı modelde sessizce reddedilir, köprü tick'i tamamlar."""
    from ghost import onay as _onay
    if not _onay.servis_modu():
        return
    ids = []
    for b in oku().get("bilesenler") or []:
        _eylem_idleri(b, ids)
    try:
        _onay.kartlar_esitle([i for i in ids if str(i).startswith("onay-")])
    except Exception:
        pass


def komut_ozeti(komut):
    """Kapı istisnası için komutun parmak izi — onay kimliği ve ayıracı (; : / && : / #) çıkarılmış, boşluk
    sadeleştirilmiş metnin sha256'sının ilk 16 hanesi. Kapıdaki _komut_ozeti ile AYNI olmalı."""
    k = re.sub(r"\s*(?:;\s*:|&&\s*:|#)?\s*\bonay-[0-9a-f]{16}\b", "", komut or "")
    k = re.sub(r"\s+", " ", k).strip().rstrip(";").strip()
    return hashlib.sha256(k.encode("utf-8")).hexdigest()[:16]


def onay_komut(baslik, komut, aciklama=""):
    """Kapının onay istediği komut için kart (tür 'kapi'): Sahip komutun KENDİSİNİ görür; onay yalnız bu komuta
    geçer. Kullanım: kart → Sahip onaylar → aynı komut + ' ; : onay-<id>'."""
    eid = "onay-" + secrets.token_hex(8)
    ozet = komut_ozeti(komut)
    onizleme = "Komut:\n%s\n\nKomut özeti: %s" % (komut, ozet)
    onay(baslik, onizleme, eylem_id=eid, aciklama=aciklama or "Bu komut koşsun mu? (Onay yalnız bu komuta, bir kez.)",
         tur="kapi", baglam=ozet)
    return eid


def onay(baslik, onizleme, eylem_id=None, aciklama="", tur="genel", baglam=None):
    """Onay + önizleme: diff/özet sahnede, altında Onayla / Vazgeç. Cevap Ghost'a mesaj
    olarak gelir; Ghost 'Onayla' görmeden ilerlemez. (Köprünün pending.json'una dışarıdan
    yazılmaz — tek yazar köprüdür, yarış yok.)"""
    from ghost import kokpit
    # Rastgele id (2026-10-27 denetimi): saat damgası tahmin edilebilir, metinle taklit edilirdi.
    eid = eylem_id or ("onay-" + secrets.token_hex(8))
    diff_mi = onizleme.lstrip().startswith(("---", "+++", "@@", "diff "))
    b = [{"tur": "kart", "baslik": baslik, "metin": aciklama or "Aşağıdakini uygulayayım mı?", "durum": "uyari"},
         {"tur": "diff" if diff_mi else "metin", "metin": onizleme[:8000]},
         {"tur": "satir", "cocuklar": [
             {"tur": "eylem", "id": eid, "etiket": "Onayla", "veri": "onayla"},
             {"tur": "eylem", "id": eid, "etiket": "Vazgeç", "veri": "vazgeç", "stil": "tehlike"}]}]
    seq, _ = goster(b, baslik)
    _onizleme_kaydet(eid, onizleme, tur, baglam)
    kokpit.outbox_yaz("%s — önizleme Sahne'de. Onaylıyor musun?" % baslik, "ghost",
                      butonlar=[[("Onayla", "[Sahne eylem: %s] onayla" % eid),
                                 ("Vazgeç", "[Sahne eylem: %s] vazgeç" % eid)]])
    return seq


def bekle(since_seq=0, bekle=0):
    """Uzun yoklama: seq değişene ya da süre dolana kadar tut."""
    son = time.time() + max(0, min(float(bekle or 0), 60))
    while True:
        d = oku()
        if int(d.get("seq") or 0) != int(since_seq) or time.time() >= son:
            return d
        time.sleep(0.5)


def eylem(eylem_id, veri=None, alanlar=None, cihaz=None):
    """Uygulamadan gelen buton/form → Sahip mesajı olarak köprü kuyruğuna."""
    from ghost import kokpit
    if alanlar:
        satirlar = ["[Sahne form: %s]" % eylem_id]
        for k, v in (alanlar.items() if isinstance(alanlar, dict) else []):
            satirlar.append("%s: %s" % (k, v))
        metin = "\n".join(satirlar)
    else:
        metin = "[Sahne eylem: %s] %s" % (eylem_id, veri or "")
        sahip_onayi_kaydet(metin, "kokpit-eylem")    # imzalı istekten geldi (api /v1/eylem)
    kokpit.outbox_yaz(metin, "sahip")
    kokpit.mesaj_kuyrukla(metin, cihaz)
    cozumle(eylem_id, veri)
    return metin


EYLEM_DESEN = re.compile(r"^\[Sahne eylem: ([^\]]+)\]\s*(.*)$", re.S)


def _eylem_var(b, eid):
    if not isinstance(b, dict):
        return False
    if b.get("tur") == "eylem" and b.get("id") == eid:
        return True
    return any(_eylem_var(c, eid) for c in b.get("cocuklar") or [])


def _eylemsiz(b, eid):
    """Bileşen ağacından o id'li butonları düş; boş kalan satır/sütun da düşer."""
    if b.get("tur") == "eylem" and b.get("id") == eid:
        return None
    if b.get("cocuklar"):
        c = [x for x in (_eylemsiz(x, eid) for x in b["cocuklar"]) if x]
        if not c:
            return None
        b = dict(b, cocuklar=c)
    return b


def cozumle(eylem_id, veri=None):
    """Cevaplanmış soru/onay sahnede 'bekliyor' gibi kalmasın (2026-09-23, Sahip: 'kararı
    chatten verdim, sahne hâlâ soruyor'). Sahnede o id'li buton varsa: butonlar düşer,
    kart cevabı ve saati gösterir, durum onay→iyi / vazgeç→bilinmiyor. Sahnede yoksa
    (eski soru, başka sahne) hiçbir şey yapmaz. Sessiz yazar: döküme satır düşmez."""
    d = oku()
    b = d.get("bilesenler") or []
    if not eylem_id or not any(_eylem_var(x, eylem_id) for x in b):
        return False
    cevap = (str(veri or "").strip() or "cevaplandı")
    vazgecti = cevap.lower().startswith(("vazge", "hayır", "iptal"))
    yeni = [x for x in (_eylemsiz(x, eylem_id) for x in b) if x]
    for x in yeni:
        if x.get("tur") == "kart":
            x["durum"] = "bilinmiyor" if vazgecti else "iyi"
            x["metin"] = "%s · %s" % ("Vazgeçildi" if vazgecti else "Cevap: " + cevap, time.strftime("%H:%M"))
            break
    else:
        yeni.insert(0, {"tur": "kart", "baslik": d.get("baslik") or "Soru",
                        "metin": "Cevap: %s · %s" % (cevap, time.strftime("%H:%M")),
                        "durum": "bilinmiyor" if vazgecti else "iyi"})
    goster(yeni, d.get("baslik") or "", sessiz=True)
    return True


def cozumle_metinden(metin):
    """'[Sahne eylem: id] cevap' biçimindeki Sahip mesajından çözümle; değilse False."""
    m = EYLEM_DESEN.match((metin or "").strip())
    return bool(m) and cozumle(m.group(1).strip(), m.group(2).strip())


_ONAY_EVET = re.compile(r"^\W*(onayla|onaylıyorum|onay|evet|tamam|olur|ok|okey|yap|devam|uygula|yayınla)\b", re.I)
# Gerçek onay kaydı için KATI: mesajın tamamı kısa bir evet olmalı ("tamam, bekle", "olur mu?",
# "devam etme" onay DEĞİL). Görünüm (cozumle_serbest) gevşek kalıbı kullanmaya devam eder.
_ONAY_KESIN = re.compile(r"^\W*(onayla|onaylıyorum|onay|evet|tamam|olur|ok|okey|yap|devam|uygula|yayınla)"
                         r"(\s+(onaylıyorum|yap|gönder|uygula|devam))?[\s.!👍✅]*$", re.I)
_ONAY_HAYIR = re.compile(r"^\W*(vazgeç|vazgec|hayır|hayir|iptal|yapma|dur|bekle|olmaz|şimdi değil)\b", re.I)


def _eylem_idleri(b, out):
    if not isinstance(b, dict):
        return out
    if b.get("tur") == "eylem" and b.get("id"):
        out.append(b["id"])
    for c in b.get("cocuklar") or []:
        _eylem_idleri(c, out)
    return out


def cozumle_serbest(metin):
    """sahibin DÜZ METİN mesajı sahnedeki bekleyen butonu kapatır (2026-10-04, Sahip: "konu
    aksa gitse bile sahne sabit kalıyor"). Ölçüm: cevapların %94'ü düz metin, sahne hep bayattı.
    - 'soru-*' butonu: her Sahip mesajı cevaptır (sohbette zaten cevaplandı) → kart cevabı gösterir.
    - 'onay-*' butonu: yalnız açık evet/hayır kalıbı kapatır (geri alınmaz iş; 'bence şöyle…'
      gibi tartışma mesajı onayı KAPATMAZ). Bu yalnız sahne görünümüdür — köprünün gerçek onay
      kuyruğuna (pending.json) dokunmaz, o kapı ayrı.
    Döndürür: kapanan eylem id ya da None."""
    m = (metin or "").strip()
    if not m or m.startswith("[Sahne"):
        return None
    ids = []
    for b in oku().get("bilesenler") or []:
        _eylem_idleri(b, ids)
    for eid in dict.fromkeys(ids):
        if eid.startswith("soru-"):
            if cozumle(eid, m[:80]):
                return eid
        elif eid.startswith("onay-"):
            if _ONAY_HAYIR.match(m):
                return eid if cozumle(eid, "vazgeç") else None
            if _ONAY_EVET.match(m):
                return eid if cozumle(eid, "onayla") else None
    return None



# ----------------------------------------------------------------------------
# GERÇEK ONAY KAYDI (2026-10-27 güvenlik denetimi). Modelin sohbette "onayla" GÖRMESİ
# onay değildir — o metin bir mailin, sayfanın, araç çıktısının içinden de gelebilir.
# Onay yalnız sahibin gerçek giriş kapılarında kaydedilir: imzalı Kokpit isteği
# (/v1/eylem, /v1/mesaj) ve sahibi doğrulanmış Telegram mesajı. Geri alınmaz işi yapan
# betik (ör. ops/gmail_gonder.py) bu kayda bakar; model kaydı yazamaz, çünkü bu
# fonksiyonu yalnız o kapılar çağırır.
# ----------------------------------------------------------------------------
ONAY_OMUR_SN = 24 * 3600


def _onay_yolu():
    return os.path.join(config.OPS, "sahne_onaylar.json")


def _onaylar():
    try:
        with open(_onay_yolu(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _onaylar_yaz(d):
    simdi = time.time()
    d = {k: v for k, v in d.items() if simdi - float(v.get("ts") or v.get("olusturma") or 0) < 7 * 86400}
    yol = _onay_yolu()
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix=".sahne_onaylar.", dir=os.path.dirname(yol))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, yol)


def _kilitli(fn):
    """Onay kaydı oku-değiştir-yaz: API, köprü ve kanca ayrı süreçler — dosya kilidiyle sırala."""
    import fcntl
    with open(_onay_yolu() + ".kilit", "a") as k:
        fcntl.flock(k, fcntl.LOCK_EX)
        try:
            return fn()
        finally:
            fcntl.flock(k, fcntl.LOCK_UN)


def onizleme_ozeti(onizleme):
    return hashlib.sha256((onizleme or "").encode("utf-8")).hexdigest()


def _onizleme_kaydet(eid, onizleme, tur="genel", baglam=None):
    """Kart açılırken sahibe GÖSTERİLEN önizlemenin özeti kayda yazılır (bağımsız denetim Y3, 2026-10-28):
    onaylanan iş, onaydan sonra değiştirilmiş bir taslak olamasın. Bekleyen giriş 'ts' taşımaz → geçerli
    onay sayılmaz (kapı da onay_gecerli de ts'e bakar); Sahip onaylayınca sahip_onayi_kaydet ts ekler.
    Onay servisi kuruluysa (Faz 1, 2026-10-28) önizleme ORADA saklanır ve ilk kayıt kilitlenir."""
    from ghost import onay as _onay
    if _onay.servis_modu():
        try:
            _onay.kart_ac(eid, onizleme, tur, baglam)
        except _onay.ServisHatasi:
            pass
        return
    def yaz():
        d = _onaylar()
        if not (d.get(eid) or {}).get("ts"):
            d[eid] = {"olusturma": time.time(), "onizleme_sha": onizleme_ozeti(onizleme),
                      "kirpildi": len(onizleme or "") > 8000, "tur": tur, "baglam": baglam}
            _onaylar_yaz(d)
    try:
        _kilitli(yaz)
    except Exception:
        pass


def onay_onizleme_sha(eid):
    """Onaylanan kartın önizleme özeti (kırpılmışsa ya da yoksa None) — onaylı işi yapan betik kendi
    önizlemesini yeniden kurup karşılaştırır."""
    from ghost import onay as _onay
    if _onay.servis_modu():
        return _onay.onizleme_sha(eid)
    v = _onaylar().get(str(eid or "")) or {}
    return None if v.get("kirpildi") else v.get("onizleme_sha")


def sahip_onayi_kaydet(metin, kanal, uid=None):
    """sahibin GERÇEK mesajı bir onay-* kartını onaylıyorsa kaydet. outbox_yaz'dan ÖNCE
    çağrılmalı (o, kartın butonlarını kapatır). Döndürür: onaylanan eid ya da None.
    Onay servisi kuruluysa: Telegram kanalı servise sorulur (çağıranın köprü ana süreci, uid'in
    sahip olduğunu servis kendisi doğrular); Kokpit kanallarını servis imzayı doğrularken yazmıştır."""
    from ghost import onay as _onay
    if _onay.servis_modu():
        if kanal != "telegram":
            return None
        try:
            return _onay.sahip_onayi_telegram(metin, uid)
        except _onay.ServisHatasi:
            return None
    m = (metin or "").strip()
    eid = None
    e = EYLEM_DESEN.match(m)
    if e:
        if e.group(1).strip().startswith("onay-") and e.group(2).strip().lower() == "onayla":
            eid = e.group(1).strip()
    elif _ONAY_KESIN.match(m):
        ids = []
        for b in oku().get("bilesenler") or []:
            _eylem_idleri(b, ids)
        bekleyen = [i for i in dict.fromkeys(ids) if i.startswith("onay-")]
        if len(bekleyen) == 1:      # birden çok açık kart varsa düz "evet" hiçbirini onaylamaz
            eid = bekleyen[0]
    if not eid:
        return None
    def yaz():
        d = _onaylar()
        onceki = d.get(eid) or {}
        if onceki.get("tuketildi"):       # tüketilmiş onay ikinci "onayla" ile yeniden açılmaz
            return False
        d[eid] = {"ts": time.time(), "kanal": str(kanal)[:40], "tuketildi": False}
        if onceki.get("onizleme_sha"):
            d[eid].update(onizleme_sha=onceki["onizleme_sha"], kirpildi=bool(onceki.get("kirpildi")))
        if onceki.get("tur"):            # kapı komut bağlamı onayla birlikte korunur (2. tur bulgu 1)
            d[eid].update(tur=onceki["tur"], baglam=onceki.get("baglam"))
        _onaylar_yaz(d)
        return True
    try:
        if not _kilitli(yaz):
            return None
    except Exception:
        return None
    return eid


def onay_gecerli(eid, omur_sn=ONAY_OMUR_SN):
    """eid sahibin gerçek kapısından onaylanmış, süresi dolmamış ve tüketilmemiş mi?"""
    from ghost import onay as _onay
    if _onay.servis_modu():
        return _onay.gecerli(eid)
    v = _onaylar().get(str(eid or ""))
    return bool(v) and not v.get("tuketildi") and time.time() - float(v.get("ts") or 0) < omur_sn


def onay_tuket(eid):
    """Tek kullanımlık: onaylı iş yapıldıktan sonra aynı onay ikinci kez geçmez."""
    from ghost import onay as _onay
    if _onay.servis_modu():      # SURUM 2: özetsiz tüketim yok; işe bağlı yol onay_tuket_dogrulayarak
        return False
    def yaz():
        d = _onaylar()
        if str(eid) in d:
            d[str(eid)]["tuketildi"] = True
            d[str(eid)]["tuketim_ts"] = time.time()
            _onaylar_yaz(d)
    _kilitli(yaz)


def onay_tuket_dogrulayarak(eid, onizleme_sha, tur="genel"):
    """Atomik: onay geçerli VE sahibe gösterilen önizlemenin özeti eşleşiyor → tüket, True.
    (Servis kipinde tek çağrı; yerelde geçerli → özet → tüket sırası.)"""
    from ghost import onay as _onay
    if _onay.servis_modu():
        return _onay.tuket(eid, onizleme_sha, tur)
    if not onay_gecerli(eid) or not onizleme_sha or onay_onizleme_sha(eid) != onizleme_sha:
        return False
    onay_tuket(eid)
    return True


def onay_az_once_tuketildi(eid, pencere_sn=120):
    """Geri alınmaz işi yapan alt fonksiyon için: bu onay GERÇEK kayıttan az önce mi tüketildi?
    (Üst fonksiyon onay_gecerli → onay_tuket yapar; alt fonksiyon doğrudan çağrılırsa geçmez.)"""
    from ghost import onay as _onay
    if _onay.servis_modu():
        return _onay.az_once_tuketildi(eid, pencere_sn)
    v = _onaylar().get(str(eid or ""))
    return bool(v) and v.get("tuketildi") and time.time() - float(v.get("tuketim_ts") or 0) < pencere_sn


def _siteler_tablosu(n):
    """Siteler: erişim + gerçek dış ziyaret (site_nabzi varsa), yoksa yalnız erişim."""
    sn = n.get("site_nabzi") or {}
    if sn.get("var"):
        return {"tur": "tablo", "baslik": "Siteler · 7g %d · 24s %d dış ziyaret" % (
            sn.get("toplam_7g", 0), sn.get("toplam_24s", 0)),
                "sutunlar": ["site", "durum", "7g", "24s", "kaynak"],
                "satirlar": [[st.get("ad", ""), "iyi" if st.get("ok") else "kritik", st.get("ziyaret_7g", 0),
                              st.get("ziyaret_24s", 0), st.get("kaynak", "")] for st in sn.get("siteler") or []]}
    return {"tur": "tablo", "baslik": "Siteler", "sutunlar": ["site", "durum"],
            "satirlar": [[st.get("ad", ""), "iyi" if st.get("ok") else "kritik"] for st in n.get("siteler") or []]}


def hazir_nabiz():
    """Gerçek nabız sahnesi: health.nabiz() → kartlar + tablo (Telegram metni yerine ekran)."""
    from ghost import health
    n = health.nabiz()
    durum = {"iyi": "iyi", "uyari": "uyari", "kritik": "kritik"}.get(n.get("ozet"), "bilinmiyor")
    isler = n.get("isler") or {}
    b = [
        {"tur": "kart", "baslik": "Ghost · %s" % n.get("at", ""), "durum": durum,
         "metin": "%s · model %s%s" % (
             {"iyi": "Her şey yolunda", "uyari": "Uyarı var", "kritik": "Kritik durum"}.get(durum, "Durum bilinmiyor"),
             n.get("model", "?"), " · FREEZE" if n.get("freeze") else "")},
        {"tur": "satir", "cocuklar": [
            {"tur": "kart", "baslik": "Aktif iş", "metin": str(isler.get("aktif", 0))},
            {"tur": "kart", "baslik": "Onay bekleyen", "metin": str(isler.get("bekleyen_onay", 0)),
             "durum": "uyari" if isler.get("bekleyen_onay") else "iyi"}]},
        {"tur": "liste", "baslik": "Makineler",
         "ogeler": [{"metin": m.get("ad", ""), "detay": m.get("detay", ""), "durum": m.get("durum", "")}
                    for m in n.get("makineler") or []]},
        _siteler_tablosu(n),
        {"tur": "tablo", "baslik": "Nöbetçiler", "sutunlar": ["nöbetçi", "son", "yaş (sn)"],
         "satirlar": [[nb.get("ad", ""), (nb.get("son") or "")[11:16], nb.get("yas_sn")]
                      for nb in n.get("nobetciler") or []]},
    ]
    bag = n.get("baglam") or {}
    if bag.get("yuzde") is not None:
        b.append({"tur": "kart", "baslik": "Bağlam", "metin": "%%%s · %s tur" % (bag.get("yuzde"), bag.get("tur"))})
    return {"baslik": "Nabız", "bilesenler": b}


def main(argv):
    cmd = argv[0] if argv else "oku"
    if cmd == "goster":
        if len(argv) < 2:
            print("kullanım: goster '<json>' | goster @dosya [--kart <Kart>]"); return 2
        kart = None
        if "--kart" in argv:
            i = argv.index("--kart")
            kart = argv[i + 1] if i + 1 < len(argv) else None
            argv = argv[:i] + argv[i + 2:]
        ham = argv[1]
        if ham.startswith("@"):
            ham = open(ham[1:], encoding="utf-8").read()
        d = json.loads(ham)
        seq, uyari = goster(d)
        print("sahne seq=%d" % seq)
        for u in uyari:
            print("uyarı:", u)
        if kart:   # kalıcı kopya Masa'ya (butonlu/sorulu sahne kaydedilmez — o anlık)
            from ghost import masa
            b = d.get("bilesenler", d) if isinstance(d, dict) else d
            print("masa #%d → %s" % (masa.sahne_kaydet(b, (d.get("baslik") if isinstance(d, dict) else "") or "Sahne",
                                                      kart, kaynak="sahne seq %d" % seq), kart))
    elif cmd == "nabiz":
        seq, uyari = goster(hazir_nabiz())
        print("nabız sahnesi seq=%d uyarı=%d" % (seq, len(uyari)))
    elif cmd == "sor":
        if len(argv) < 3:
            print("kullanım: sor '<soru>' 'seçenek1|seçenek2|…' [başlık]"); return 2
        print("soru sohbete düştü (döküm #%s); Sahne'ye çizilmedi" % sor(argv[1], argv[2].split("|"), baslik=argv[3] if len(argv) > 3 else "Soru"))
    elif cmd == "onay":
        if len(argv) < 3:
            print("kullanım: onay '<başlık>' '<diff|önizleme metni>' | onay '<başlık>' @dosya [açıklama]"); return 2
        on = argv[2]
        if on.startswith("@"):
            on = open(on[1:], encoding="utf-8").read()
        print("onay sahnesi seq=%d" % onay(argv[1], on, aciklama=argv[3] if len(argv) > 3 else ""))
    elif cmd == "onay-komut":
        if len(argv) < 3:
            print("kullanım: onay-komut '<başlık>' '<komut>' [açıklama]  → Sahip onaylayınca: <komut> ; : onay-<id>")
            return 2
        komut = argv[2]
        if komut.startswith("@"):          # @dosya: komut metni dosyadan (uzun/korunan yol içeren komutlar)
            komut = open(komut[1:], encoding="utf-8").read().strip()
        eid = onay_komut(argv[1], komut, aciklama=argv[3] if len(argv) > 3 else "")
        print("onay kartı: %s — Sahip onaylayınca AYNI komutu sonuna ' ; : %s' ekleyerek koş" % (eid, eid))
    elif cmd == "metinden":
        # --kart <Kart>: aynı raporu Masa'ya da kalıcı yayınla (2026-10-04). Sahne anlık ekrandır,
        # Masa kalıcı: dosyaysa tam markdown (yayinla), değilse tablolar sahne kaydı olur.
        kart = None
        if "--kart" in argv:
            i = argv.index("--kart")
            kart = argv[i + 1] if i + 1 < len(argv) else None
            argv = argv[:i] + argv[i + 2:]
        ham = argv[1] if len(argv) > 1 else sys.stdin.read()
        dosya = ham[1:] if ham.startswith("@") else None
        if dosya:
            ham = open(dosya, encoding="utf-8").read()
        baslik = argv[2] if len(argv) > 2 else "Rapor"
        seq = isten(ham, baslik)
        print("sahne seq=%s" % seq if seq else "sahneye değer içerik yok (tablo/diff bulunamadı)")
        if kart:
            from ghost import masa
            if dosya:
                kid, ne = masa.yayinla(dosya, kart, baslik if baslik != "Rapor" else None)
            else:
                kid, ne = masa.kart_ekle("rapor", baslik, masa.klasor_bul(kart, olustur=True),
                                         icerik=ham, yazar="ghost"), "yeni"
            print("masa #%d %s → %s" % (kid, ne, kart))
    elif cmd == "gecmis":
        for g in gecmis():
            print("%s  seq=%s  %s" % (g["ts"], g["seq"], g["baslik"]))
    elif cmd == "temizle":
        print("sahne temiz, seq=%d" % temizle())
    elif cmd == "oku":
        print(json.dumps(oku(), ensure_ascii=False, indent=1))
    else:
        print(__doc__); return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
