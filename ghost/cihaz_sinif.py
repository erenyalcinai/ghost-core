"""Cihaz sınıfı kaydı — eşlenmiş cihazın NE olduğu, açık bir alan olarak (Kokpit Watch hazırlığı, 2026-11-24).

NEDEN: onay servisi cihazın yalnız TÜRÜNÜ bilir (tam | cep = yetki kademesi). "Cep" bir yetki kademesidir, cihazın ne
olduğunu söylemez: Cardputer da saat de cep türünde eşlenir. İki yer bunu varsayımla çözüyordu: görev kuyruğu
("cep görevini yoklayan ilk cep cihazı alır") ve APNs ("bildirim belirteci kaydetmiş cep cihazı = saat"). Bu modül
varsayımın yerine geçen kayıttır: cihaz kimliği → sınıf.

  cardputer : `gorevler.CEP_TURLER` görevlerini (kızılötesi, LoRa, kart, konum…) anlayan el cihazı
  saat      : Apple Watch (bildirim konusu saat uygulamasının paketi; cep görevlerinin HİÇBİRİNİ anlamaz)

Kayıt `durum` katmanında (ghost.db, ad `cihaz_sinif`): {"<cihaz id>": {"sinif": "...", "ts": epoch, "kaynak": "..."}}.
Onay servisine ve cihaz tablosuna dokunmaz; yetki vermez, yalnız yönlendirir (hangi görev/bildirim hangi cihaza).
Sınıfı KAYITSIZ cihaz hiçbir sınıfın işini almaz (bkz. `gorevler.cep_al`); tek istisna kaydın HİÇ yazılmadığı ve en çok
bir cep cihazının eşli olduğu kurulumdur (eski davranış: tek cihaz varken karışacak bir şey yoktur). Kayıt bir kez
yazıldıktan sonra — son sınıf silinse bile — istisna bir daha açılmaz (`sil` anahtarı silmez, içini boşaltır).

Kim yazar: şema göçü 3 (geçmişte cep görevi sonuçlandırmış cihaz = cardputer), `python3 -m ghost.cep sinif …`
(sahibin oturumu) ve — ayrı yamayla — bildirim belirtecini kaydederken kendini "saat" diye bildiren cep cihazı.
"""
import time

from ghost import durum

AD = "cihaz_sinif"
SINIFLAR = ("cardputer", "saat")


def yukle():
    """Kaydı oku → (kayıt, başlatıldı). Üç hâl AYRI tutulur (bağımsız inceleme bulgu 2: okuma hatası 'boş kayıt' gibi
    görünürse görev kuyruğu açık tarafa düşüyordu):
      ({…}, True)   kayıt var (içi boş olsa da: son sınıf silinmiş olabilir) → çağıran KATI davranır
      ({},  False)  kayıt hiç yazılmamış (taze kurulum) → çağıran eski davranışa izin verebilir
      (None, None)  okunamadı (veritabanı/göç hatası) → çağıran KAPALI tarafa düşmeli
    Asla fırlatmaz."""
    try:
        d = durum.oku(AD)
    except Exception:
        return None, None
    if d is None:
        return {}, False
    cikti = {}
    for cid, v in (d.items() if isinstance(d, dict) else ()):
        s = v.get("sinif") if isinstance(v, dict) else v
        if s in SINIFLAR:
            cikti[str(cid)] = s
    return cikti, True


def oku():
    """{cihaz id: sınıf}; okunamazsa {}. GÜVENLİK KARARI veren çağıran bunu değil `yukle()`yi kullanır (hata ile boşu
    ayırmak için); bu kısa yol gösterim ve bildirim konusu gibi 'bilinmiyorsa eski kural' diyebilen yerler içindir."""
    return yukle()[0] or {}


def sinif(cihaz_id):
    return oku().get(str(cihaz_id or ""))


def yaz(cihaz_id, sinif_, kaynak="cli"):
    """Sınıfı kaydet → (ok, hata)."""
    cid = str(cihaz_id or "").strip()
    if not cid:
        return False, "cihaz kimliği boş"
    if sinif_ not in SINIFLAR:
        return False, "sınıf %s olmalı: %r" % ("|".join(SINIFLAR), sinif_)

    def _g(eski):
        d = dict(eski) if isinstance(eski, dict) else {}
        d[cid] = {"sinif": sinif_, "ts": time.time(), "kaynak": str(kaynak or "")[:20]}
        return d
    durum.guncelle(AD, _g, {})
    return True, None


def sil(cihaz_id):
    """Kaydı kaldır → kayıt var mıydı."""
    cid = str(cihaz_id or "").strip()
    vardi = []

    def _g(eski):
        d = dict(eski) if isinstance(eski, dict) else {}
        vardi.append(d.pop(cid, None) is not None)
        return d
    durum.guncelle(AD, _g, {})
    return bool(vardi and vardi[0])
