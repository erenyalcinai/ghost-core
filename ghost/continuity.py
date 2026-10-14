"""Süreklilik kancası — oturum hafıza yazmadan kapandıysa iz bırakır.

KÖK SEBEP: Ghost'un hafızaya yazması TAKDİRE BAĞLI. `/ogren` elle çağrılır,
`memory.remember()` ise Ghost hatırlarsa çalışır. Unutulduğunda kimse fark
etmiyordu: `/new` geçmişi siliyor, `/tazele` yalnız son 8 alışverişi devrediyor
— aradaki öğrenilenler sessizce ölüyordu. Kayıp, olduğu anda değil, haftalar
sonra "bunu konuşmuştuk" anında ortaya çıkıyordu.

ÇÖZÜM (dışarıdan alınan tek fikir): iyi niyete güvenme, mekanikleştir.
Oturum sıfırlandığında hafıza İMZASI'na bakılır; oturum anlamlı uzunluktaysa
(min_tur) ve imza hiç değişmediyse bayrak bırakılır. Bayrağı sonraki oturumun
İLK mesajı tüketir — yani uyarı, düzeltmenin mümkün olduğu tek anda görünür.

NEDEN İMZA, NEDEN SADECE SAYAÇ DEĞİL: Ghost'un iki ayrı hafızası var — FTS
veritabanı (`ghost.db`) ve markdown hafıza dosyaları. Yalnız birine bakmak
yanlış alarm üretirdi (diğerine yazılmış olabilir). İmza ikisini birden görür.

NE YAPMAZ: yazmayı kendisi yapmaz, zorlamaz, oturumu kesmez. Sadece
"burada bir şey kaydedilmedi" der. Yazma kararı Ghost'ta, düzeltme sahibin
görebileceği yerde kalır.
"""
import json
import os
import time

from ghost import config


def _state():
    """Yol ÇAĞRI ANINDA çözülür — testler GHOST_OPS ile geçici dizine yönlendirir
    (bkz. ghost/config.py'deki aynı gerekçe). Sabitlenseydi testler gerçek
    ops/'a yazardı."""
    return os.path.join(config.OPS, "continuity.json")

# Markdown hafıza dizini (Claude Code tarafı). Yoksa imzanın o yarısı 0 kalır.
MD_DIR = os.environ.get("GHOST_MD_MEMORY") or os.path.expanduser(
    "~/.claude/projects/-home-ghost-jarvis/memory")

# Bu turdan kısa oturumlar için bayrak bırakılmaz. "tamam / bak bakalım /
# teşekkürler" için hafıza uyarısı üretmek, uyarıyı gürültüye çevirirdi.
MIN_TUR = 6

# OTURUM YAŞARKEN hatırlatma eşiği. Bayrak (oturum sonu) çoğu zaman mezar
# taşıdır: /new ile kapanan oturumda malzeme zaten gitmiştir, geriye yalnız
# "burada bir şey kaybedildi" bilgisi kalır. Asıl kurtarıcı olan bu eşik —
# uyarı geldiğinde konuşma HÂLÂ bağlamda, yazmak mümkün.
HATIRLATMA_ESIK = 12


# --- saf çekirdek (test edilebilir) ----------------------------------------

def karar(turlar, imza_once, imza_sonra, min_tur=MIN_TUR):
    """Bayrak bırakılmalı mı? Saf fonksiyon — yan etkisi yok.

    turlar     : oturumda kaç alışveriş oldu
    imza_once  : oturum başındaki hafıza imzası
    imza_sonra : şimdiki hafıza imzası
    """
    if turlar < min_tur:
        return False
    if imza_once is None or imza_sonra is None:
        return False            # ölçemiyorsak suçlama
    return imza_once == imza_sonra


def hatirlatma_karari(turlar, imza_once, imza_simdi, son_hatirlatma=0,
                      esik=HATIRLATMA_ESIK):
    """Oturum sürerken hatırlatma yapılmalı mı? Saf fonksiyon.

    son_hatirlatma: en son kaçıncı turda hatırlatıldı (0 = hiç). Aynı eşik
    tekrar tekrar ateşlemesin diye; ısrar dürtme değil dırdır olurdu.
    """
    if turlar < esik:
        return False
    if imza_once is None or imza_simdi is None:
        return False
    if imza_once != imza_simdi:
        return False                     # bu oturumda zaten yazıldı — sus
    return (turlar - son_hatirlatma) >= esik


# --- imza ------------------------------------------------------------------

def imza():
    """(fts_kayit_sayisi, md_dosya_sayisi, md_en_son_mtime) — ölçülemezse None."""
    try:
        from ghost import memory
        n = memory.count()
    except Exception:
        return None
    dosya, mtime = 0, 0
    if os.path.isdir(MD_DIR):
        for ad in os.listdir(MD_DIR):
            if not ad.endswith(".md"):
                continue
            dosya += 1
            try:
                mtime = max(mtime, int(os.path.getmtime(os.path.join(MD_DIR, ad))))
            except OSError:
                pass
    return (n, dosya, mtime)


# --- durum -----------------------------------------------------------------

def _oku():
    try:
        return json.load(open(_state(), encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _yaz(d):
    yol = _state()
    tmp = yol + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, yol)


def oturum_basladi(sid):
    """Yeni sohbet oturumu açıldı — hafıza imzasını çıpala."""
    d = _oku()
    if d.get("sid") == sid:
        return                                   # aynı oturum, çıpa duruyor
    d["sid"] = sid
    d["imza"] = imza()
    d["ts"] = int(time.time())
    _yaz(d)


def oturum_bitti(turlar, ipucu=""):
    """Oturum sıfırlandı. Hiçbir şey öğrenilmediyse bayrak bırak.

    Döner: bayrak bırakıldıysa True.
    """
    d = _oku()
    once = d.get("imza")
    once = tuple(once) if isinstance(once, list) else once
    birakildi = karar(turlar, once, imza())
    if birakildi:
        d["bayrak"] = {
            "turlar": turlar,
            "ipucu": (ipucu or "")[:200],
            "ts": int(time.time()),
        }
    d.pop("sid", None)
    d.pop("imza", None)
    _yaz(d)
    return birakildi


def hatirlatma(sid, turlar):
    """Oturum sürerken: yazılmadıysa hatırlat. Metin veya None döner.

    imza() maliyetli değil ama her turda çağırmanın da anlamı yok — eşiğin
    altındayken hiç ölçmüyoruz.
    """
    if turlar < HATIRLATMA_ESIK:
        return None
    d = _oku()
    if d.get("sid") != sid:
        return None                      # çıpa bu oturuma ait değil
    once = d.get("imza")
    once = tuple(once) if isinstance(once, list) else once
    if not hatirlatma_karari(turlar, once, imza(), d.get("son_hatirlatma", 0)):
        return None
    d["son_hatirlatma"] = turlar
    _yaz(d)
    return ("[HAFIZA HATIRLATMASI — bu oturum %d tur oldu ve kalıcı hafızaya "
            "hiçbir şey yazılmadı. Konuşma HÂLÂ elinde: kalıcı olması gereken "
            "bir karar/olgu/tercih geçtiyse ŞİMDİ kaydet (oturum kapanınca "
            "malzeme gider). Kaydedilecek bir şey yoksa sessizce geç — bunu "
            "sahibe anlatma, cevabında bahsetme.]" % turlar)


def bayrak_al():
    """Bekleyen bayrağı döndür ve TÜKET. Yoksa None."""
    d = _oku()
    b = d.pop("bayrak", None)
    if b is None:
        return None
    _yaz(d)
    yas = int((time.time() - b.get("ts", 0)) / 3600)
    return ("[SÜREKLİLİK UYARISI — önceki oturum %d tur sürdü ve hafızaya "
            "hiçbir şey yazılmadı (%d saat önce). Konu: %s. Kalıcı olması "
            "gereken bir şey öğrenildiyse ŞİMDİ kaydet; gerekmiyorsa sessizce "
            "geç, bunu sahibe anlatma.]"
            % (b.get("turlar", 0), yas, b.get("ipucu") or "?"))
