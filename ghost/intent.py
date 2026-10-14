"""Niyet katmanı — serbest dilden kayıt-oluşturan komutları yakala.

'yarın 9'da toplantıyı hatırlat' → hatırlatma önerisi (onay butonuyla).
Felsefe: MUHAFAZAKÂR ol — emin değilsen None döndür, mesaj sohbete gitsin.
Kayıt oluşturan hiçbir şey onaysız kurulmaz (yanlış ateşleme sigortası).

Not: Türkçe 'İ'.lower() uzunluk değiştirir; bu yüzden indeks hesabı yapmadan
re.IGNORECASE ile özgün metin üzerinde çalışılır.
"""
import itertools
import re

from ghost import schedule

URL_RE = re.compile(
    r"https?://\S+|(?:[\w-]+\.)+(?:com|net|org|io|ai|app|dev|tr|co|me|xyz)"
    r"(?:/\S*)?", re.IGNORECASE)
_REMIND = re.compile(r"hat[ıi]rlat", re.IGNORECASE)
_WATCH = re.compile(r"\b(izle(meye)?|takip\s+et)\b", re.IGNORECASE)
_REMEMBER = re.compile(r"^(unutma|not\s+al|akl[ıi]nda\s+tut)[:,]?\s+(\S.*)$",
                       re.IGNORECASE | re.DOTALL)
_GOAL = re.compile(r"^(hedef(im)?|yeni\s+hedef)\s*[: ]\s*(\S.*)$",
                   re.IGNORECASE | re.DOTALL)
_FILLER = re.compile(r"^(beni|bana|lütfen|lutfen)$", re.IGNORECASE)


def _find_spec(tokens):
    """En uzun ayrıştırılabilen zaman ifadesi: (kullanılan_indeksler, spec).

    KUSUR (29 Ağustos 2026'da bulundu, aylardır oradaydı): eskiden yalnız
    BİTİŞİK pencereler denenirdi. "yarın sabah 09:00 toplantıyı hatırlat"
    cümlesinde "yarın sabah 09:00" ayrıştırılamaz, "yarın sabah" ayrıştırılamaz;
    geriye tek başına "09:00" kalırdı. Yani araya giren tek bir dolgu kelimesi
    ("sabah") GÜNÜ sessizce düşürüyor, hatırlatma 24 saat ERKEN kuruluyordu
    (kontrollü saatle doğrulandı: 29 Ağu 09:00 yerine 30 Ağu 09:00 olmalıydı).
    Artık sırayı bozmayan alt kümeler denenir: "yarın" + "09:00" bulunur.
    """
    tokens = tokens[:8]                 # kombinasyon sayısını sınırla (2^8)
    for n in range(len(tokens), 0, -1):
        for idx in itertools.combinations(range(len(tokens)), n):
            spec = " ".join(tokens[i] for i in idx)
            if schedule.parse_spec(spec):
                return (set(idx), spec)
    return None


def detect(text):
    """Niyet dict'i ya da None. dict: {kind, payload, spec?, preview}."""
    t = (text or "").strip()
    if not t or t.startswith("/") or len(t) > 400:
        return None

    m = _REMEMBER.match(t)
    if m:
        payload = m.group(2).strip()
        return {"kind": "ogren", "payload": payload,
                "preview": "🧠 Hafızaya yazayım mı?\n“%s”" % payload[:200]}

    m = _GOAL.match(t)
    if m:
        payload = m.group(3).strip()
        return {"kind": "hedef", "payload": payload,
                "preview": "🎯 Duran hedef olarak kurayım mı? (boş kaldıkça "
                           "üzerinde çalışırım)\n“%s”" % payload[:200]}

    if _REMIND.search(t):
        tokens = t.split()
        hit = _find_spec(tokens)
        if hit:
            kullanilan, spec = hit
            rest = [tok for j, tok in enumerate(tokens)
                    if j not in kullanilan and not _REMIND.search(tok)
                    and not _FILLER.match(tok)]
            payload = " ".join(rest).strip(" ,.") or "Hatırlatma"
            return {"kind": "hatirlat", "spec": spec, "payload": payload,
                    "preview": "⏰ Hatırlatma kurayım mı?\nZaman: %s\nNot: %s"
                               % (spec, payload[:150])}
        return None  # 'hatırlat' var ama zaman anlaşılmadı → sohbet sorar

    if re.match(r"^her\s", t, re.IGNORECASE):
        tokens = t.split()
        hit = _find_spec(tokens)
        if hit and 0 in hit[0]:  # spec baştan başlamalı: 'her gün 08:30 ...'
            kullanilan, spec = hit
            payload = " ".join(t for j, t in enumerate(tokens)
                               if j not in kullanilan).strip(" ,.")
            if len(payload) >= 5:
                return {"kind": "gorev", "spec": spec, "payload": payload,
                        "preview": "📋 Zamanlı görev kurayım mı?\nZaman: %s\n"
                                   "Görev: %s" % (spec, payload[:150])}

    if _WATCH.search(t):
        um = URL_RE.search(t)
        if um:
            url = um.group(0)
            if not url.startswith("http"):
                url = "https://" + url
            return {"kind": "izle", "payload": url,
                    "preview": "👁 Monitör kurayım mı? (30 dk'da bir değişim "
                               "kontrolü)\n%s" % url}

    return None
