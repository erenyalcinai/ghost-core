"""Bayrak devri politikası — SAF, test edilebilir.

SORUN: bayrağın gerçek kaynağı VPS'teki bir dosyaydı; yani "kim dinliyor"
sorusunun cevabı, ölen makinenin üstünde duruyordu. VPS'e ulaşılamayınca Mac
`__unreachable__` görüyor, "bayrak bende değil → dinleme" kuralına takılıp
sonsuza kadar bekliyordu. Sonuç: sunucu ölürse Ghost tamamen susuyor ve
Sahip sebebini bile öğrenemiyordu.

PAZARLIK EDİLEMEZ KISIT: asla İKİ dinleyici olmayacak. Telegram'da iki
istemci aynı anda getUpdates çekerse güncellemeler ikisi arasında rastgele
bölünür, offset'ler çakışır — mesajlar kaybolur veya çift işlenir. BOŞLUK
güvenlidir (mesajlar Telegram'da 24 saat bekler), ÇAKIŞMA değildir. Bu yüzden
her kararda şüphe varsa DİNLEME tarafına düşülür.

ÜÇ MEKANİZMA:

1. MAC DEVRALMASI — VPS'e ardışık erişimsizlik belli süreyi aşarsa Mac
   bayrağı sahiplenir. Ama tek başına SSH hatası yeterli DEĞİL: ağ bölünmesi
   (SSH kopuk ama VPS yaşıyor ve dinliyor) tam da çakışma üretirdi. Bu yüzden
   İKİNCİ BAĞIMSIZ KANIT şart: origin yoklaması (hooks.example.com). Cloudflare
   origin'e ulaşamıyorsa 52x döner — VPS gerçekten düşmüş demektir. Origin
   ayaktaysa Mac devralmaz, yalnız uyarır.

2. VPS BEKLEME PENCERESİ — VPS açılışta hemen sahiplenmez. Kendi baton.json'ı
   bayattır ("vps" yazar, çünkü öldüğünde öyle kalmıştır); ona güvenip dinlemeye
   başlamak, devralmış Mac ile çakışmak demektir. Onun yerine bir pencere
   boyunca susar ve Mac'ten taze heartbeat gelir mi diye bakar. Gelirse yerini
   korur (mevcut 45 sn failover Mac sustuğunda zaten geri alır); gelmezse
   sahiplenir.

   PENCERE > MAC'İN YAZMA PERİYODU OLMALI. Mac sahipken her turda heartbeat
   yazar (tur ≈ long-poll 10 sn + ssh); 90 sn rahat pay bırakır. Pencere kısa
   olursa VPS, Mac'in yazmasına fırsat vermeden sahiplenir → çakışma.

3. OFFSET SIFIRLAMA — devralan makinenin offset'i bayattır. Eski offset'ten
   devam etmek, Telegram'ın sakladığı 24 saatlik mesajları YENİDEN İŞLEMEK
   demektir; eski komutların tekrar çalışması kabul edilemez. Devralmada
   offset en sona alınır, atlanan mesajlar sahibe ÖZETLENİR (sessizce yutmak
   da kabul edilemez).
"""

# VPS'e bu kadar süre ardışık ulaşılamazsa Mac devralmayı düşünür.
MAC_DEVRALMA_SN = 120

# VPS açılışta bu kadar süre susar ve Mac'i dinler.
VPS_BEKLEME_SN = 90

# Cloudflare'in "origin'e ulaşamıyorum" kodları. 52x ailesi VPS'in gerçekten
# düştüğünü gösterir; 200/4xx origin'in cevap verdiği anlamına gelir.
ORIGIN_OLU_KODLAR = (520, 521, 522, 523, 524, 526, 530)


def mac_devralmali(erisimsiz_sn, origin_kod, esik_sn=MAC_DEVRALMA_SN):
    """Mac bayrağı sahiplensin mi?

    erisimsiz_sn : VPS'e kaç saniyedir ardışık ulaşılamıyor
    origin_kod   : hooks yoklamasının HTTP kodu; None = yoklama da yapılamadı
                   (Mac'in kendi interneti yok → devralmak anlamsız, zaten
                   Telegram'a da bağlanamaz)
    """
    if erisimsiz_sn < esik_sn:
        return False
    if origin_kod is None:
        return False                      # Mac'in kendi ağı yok
    return origin_kod in ORIGIN_OLU_KODLAR


def kismi_ariza(erisimsiz_sn, origin_kod, esik_sn=MAC_DEVRALMA_SN):
    """SSH ölü ama origin ayakta → devralma YOK, uyarı VAR.

    Bu ağ bölünmesi senaryosu: VPS yaşıyor ve büyük ihtimalle hâlâ dinliyor.
    Devralmak çakışma üretir. sahibin bilmesi yeter.
    """
    if erisimsiz_sn < esik_sn:
        return False
    return origin_kod is not None and origin_kod not in ORIGIN_OLU_KODLAR


def vps_acilis_karari(gecen_sn, baton, simdi, failover_sn,
                      bekleme_sn=VPS_BEKLEME_SN, devralma_sn=MAC_DEVRALMA_SN):
    """VPS açılışta ne yapsın? 'bekle' | 'devret' | 'sahiplen'

    gecen_sn : açılıştan bu yana kaç saniye geçti
    baton    : baton.json içeriği {'owner','hb'}

    HIZLI YOL — neden gerekli: bekleme penceresi her yeniden başlatmada 90 sn
    sessizlik demekti. Ghost her kod değişikliğinde yenileniyor; bu, günlük
    kullanımda gerçek bir gerileme olurdu. Ama kayıt "vps" diyorsa VE
    heartbeat tazeyse, VPS az önce ayaktaydı — Mac devralmak için kesintisiz
    `devralma_sn` beklemek zorunda olduğundan DEVRALMIŞ OLAMAZ. Bu durumda
    beklemek gereksiz. Pencere yalnızca gerçek kesintide (bayat iz) devreye
    girer; koruma tam da orada gerekliydi.
    """
    sahip = (baton or {}).get("owner")
    hb = int((baton or {}).get("hb", 0) or 0)
    if sahip == "mac" and (simdi - hb) < failover_sn:
        return "devret"                   # Mac canlı ve sahipleniyor — sus
    if sahip == "vps" and 0 < (simdi - hb) < devralma_sn:
        return "sahiplen"                 # taze kendi izim → Mac devralamazdı
    if gecen_sn < bekleme_sn:
        return "bekle"                    # bayat iz: acele etme, Mac'i dinle
    return "sahiplen"


def atlanan_ozeti(mesajlar, sinir=5):
    """Devralmada işlenmeyen mesajları sahibe anlatılacak metne çevir.

    Sessizce yutmak kabul edilemez: kesinti sırasında yazdığı şeyin
    kaybolduğunu bilmeli.
    """
    if not mesajlar:
        return None
    satir = ["⚠️ Devraldım. Kesinti sırasında gelen %d mesaj İŞLENMEDİ "
             "(eski komutların yeniden çalışmaması için):" % len(mesajlar)]
    for m in mesajlar[:sinir]:
        satir.append("• %s" % (m or "")[:80])
    if len(mesajlar) > sinir:
        satir.append("• … +%d tane daha" % (len(mesajlar) - sinir))
    satir.append("Gerekeni tekrar yaz.")
    return "\n".join(satir)
