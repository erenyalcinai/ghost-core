import Foundation

// API v1 sözleşmesinin Swift karşılıkları. Sunucu hâlâ yazılıyor; alanların
// çoğu opsiyonel ki eksik/yeni bir alan tüm ekranı düşürmesin.

/// Sunucu id'yi kimi yerde sayı, kimi yerde metin döner; ikisini de kabul et.
struct EsnekKimlik: Decodable, Hashable, CustomStringConvertible {
    let metin: String
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let n = try? c.decode(Int.self) { metin = String(n) }
        else { metin = try c.decode(String.self) }
    }
    var description: String { metin }
}

// MARK: Nabız

struct Nabiz: Decodable {
    var at: String?
    var ozet: String?            // iyi | uyari | kritik
    var freeze: Bool?
    var guardAcik: Bool?
    var model: String?
    var makineler: [Makine]?
    var siteler: [Site]?
    var nobetciler: [Nobetci]?
    var isler: Isler?
    var baglam: Baglam?
    var diskBosGb: Double?
    var bellekBosMb: Double?
    var kanal: String?           // birincil kanal: telegram | kokpit
    var sesli: Bool?             // /sesli on
    var duyular: DuyuOzet?       // Faz 5: konum/takvim/cihaz (izinli)
    // Üç soru (2026-09-04): benden ne bekliyor · bugün ne yaptı · iş nasıl. Altyapı tek satır.
    var bekleyen: Bekleyen?
    var bugun: Bugun?
    var is_: IsNabzi?
    var altyapi: Altyapi?
    /// Siteler (2026-09-06): erişim + gerçek dış ziyaret (7 gün / 24 saat), sahip+bot ayıklı.
    var siteNabzi: SiteNabzi?
    /// Uzuv bağlantısı canlı (2026-09-06): Mac hızlı hat + telefon WDA + tünel nöbetçi notu.
    var baglanti: Baglanti?
    /// Kota (2026-09-07, Sahip: "dolar değil, Claude uygulamasındaki gibi kota"): oturum (5 sa),
    /// haftalık ve model bazlı (Fable) limitlerin yüzdesi + sıfırlanma. `ghost/kota.py`.
    var kota: Kota?

    enum CodingKeys: String, CodingKey {
        case at, ozet, freeze, model, makineler, siteler, nobetciler, isler, baglam, kanal, sesli
        case duyular, bekleyen, bugun, altyapi, baglanti, kota
        case siteNabzi = "site_nabzi"
        case is_ = "is"
        case guardAcik = "guard"
        case diskBosGb = "disk_bos_gb"
        case bellekBosMb = "bellek_bos_mb"
    }
}

/// Nabız'daki "Sahip" satırı — telefonun kendi gönderdiği bağlamın sunucudaki özeti.
struct DuyuOzet: Decodable {
    var var_: Bool?
    var acik: Bool?
    var yer: String?
    var konumYas: String?
    var konumTaze: Bool?
    var etkinlikSayisi: Int?
    var siradaki: String?
    var pil: Double?
    var sarj: Bool?
    var ag: String?
    var cihazTaze: Bool?

    enum CodingKeys: String, CodingKey {
        case acik, yer, siradaki, pil, sarj, ag
        case var_ = "var"
        case konumYas = "konum_yas"
        case konumTaze = "konum_taze"
        case etkinlikSayisi = "etkinlik_sayisi"
        case cihazTaze = "cihaz_taze"
    }
}

/// "Benden ne bekliyor?" — bekleyen kararlar + Ghost'un cevapsız soruları.
struct Bekleyen: Decodable {
    var kararlar: [Karar]?
    var sorular: [Soru]?
}

struct Soru: Decodable, Identifiable {
    var id: EsnekKimlik
    var ts: String?
    var metin: String
    var butonlu: Bool?
}

/// "Ghost bugün ne yaptı?" — deterministik özet (commit, iş, eylem, telefon).
struct Bugun: Decodable {
    var tarih: String?
    var satirlar: [BugunSatir]?
    var sayilar: [String: Int]?
}

struct BugunSatir: Decodable, Identifiable, Hashable {
    var simge: String?
    var metin: String
    var id: String { metin }
}

/// "İş nasıl?" — biz_state metrikleri + düne göre fark.
struct IsNabzi: Decodable {
    var var_: Bool?
    var tarih: String?
    var yasGun: Int?
    var oncekiTarih: String?
    var metrikler: [Metrik]?
    enum CodingKeys: String, CodingKey {
        case tarih, metrikler
        case var_ = "var"
        case yasGun = "yas_gun"
        case oncekiTarih = "onceki_tarih"
    }
}

struct Metrik: Decodable, Identifiable {
    var anahtar: String
    var ad: String
    var tur: String?             // sayi | metin
    var goster: EsnekMetin?
    var fark: Double?
    var yon: String?             // iyi | kotu
    var onceki: EsnekMetin?
    var id: String { anahtar }
}

/// Siteler bölümü: `ghost/trafik.py` (PostHog, saatte bir nöbetçi ölçer). `var` yoksa yalnız erişim.
struct SiteNabzi: Decodable {
    var var_: Bool?
    var olculdu: String?
    var yasDk: Int?
    var gun: Int?
    var toplam7g: Int?
    var toplam24s: Int?
    var bayat: Bool?
    var imzaUyari: Int?
    var siteler: [SiteSatir]?

    enum CodingKeys: String, CodingKey {
        case olculdu, gun, bayat, siteler
        case var_ = "var"
        case yasDk = "yas_dk"
        case toplam7g = "toplam_7g"
        case toplam24s = "toplam_24s"
        case imzaUyari = "imza_uyari"
    }
}

struct SiteSatir: Decodable, Identifiable {
    var ad: String
    var url: String?
    var ok: Bool?
    var ziyaret7g: Int?
    var kimlik7g: Int?
    var ziyaret24s: Int?
    var kaynak: String?
    var id: String { ad }

    enum CodingKeys: String, CodingKey {
        case ad, url, ok, kaynak
        case ziyaret7g = "ziyaret_7g"
        case kimlik7g = "kimlik_7g"
        case ziyaret24s = "ziyaret_24s"
    }
}

/// Kota bölümü: abonelik limitleri (Claude uygulamasındaki "Kullanım" ekranının karşılığı).
struct Kota: Decodable {
    var var_: Bool?
    var olculdu: String?
    var yasDk: Int?
    var bayat: Bool?
    var plan: String?
    var hata: String?
    var limitler: [KotaLimit]?

    enum CodingKeys: String, CodingKey {
        case olculdu, bayat, plan, hata, limitler
        case var_ = "var"
        case yasDk = "yas_dk"
    }
}

struct KotaLimit: Decodable, Identifiable {
    var ad: String
    var tur: String?             // session | weekly_all | weekly_scoped
    var yuzde: Int?
    var onem: String?            // normal | warning | critical
    var sifirlanma: String?      // ISO 8601
    var sifirlanmaMetin: String? // sunucuda yerel saate çevrilmiş kısa metin
    var aktif: Bool?
    var id: String { ad }

    enum CodingKeys: String, CodingKey {
        case ad, tur, yuzde, onem, sifirlanma, aktif
        case sifirlanmaMetin = "sifirlanma_metin"
    }
}

struct Baglanti: Decodable {
    var mac: Bool?
    var telefon: Bool?
    var tunel: String?
    var tunelAt: String?
    var ts: Int?
    enum CodingKeys: String, CodingKey { case mac, telefon, tunel, ts; case tunelAt = "tunel_at" }
}

struct Altyapi: Decodable {
    var durum: String?
    var satir: String?
    var sorunlar: [String]?
}

struct Makine: Decodable, Identifiable {
    var ad: String
    var durum: String?           // iyi | uyari | kritik | bilinmiyor
    var detay: String?
    var id: String { ad }
}

struct Nobetci: Decodable, Identifiable {
    var ad: String
    var son: String?
    var yasSn: Int?
    var ok: Bool?
    var id: String { ad }
    enum CodingKeys: String, CodingKey { case ad, son, ok; case yasSn = "yas_sn" }
}

struct Isler: Decodable {
    var aktif: Int?
    var bekleyenOnay: Int?
    var ozet: [IsOzet]?
    enum CodingKeys: String, CodingKey { case aktif, ozet; case bekleyenOnay = "bekleyen_onay" }
}

struct IsOzet: Decodable, Identifiable {
    var id: EsnekKimlik
    var durum: String?
    var tur: String?
    var prompt: String?
}

struct Baglam: Decodable {
    var yuzde: Double?
    var tur: Int?
    var maliyet: Double?
}

// MARK: Kararlar

struct Karar: Decodable, Identifiable {
    var id: EsnekKimlik
    var sinif: String?           // permanent | spend | reversible | read
    var tur: String?
    var metin: String
    var ts: String?
}

// MARK: Sohbet (outbox)

struct Buton: Decodable, Hashable {
    var etiket: String
    var veri: String             // "ok:<id>" | "no:<id>" | serbest
}

/// Dökümdeki görsel/video/ses/belge; dosyanın kendisi `/v1/medya/<mesaj id>` ile gelir.
struct Medya: Decodable, Hashable {
    var tur: String              // gorsel | video | ses | belge
    var ad: String?
    var boyut: Int?
    var mime: String?
}

struct Mesaj: Decodable, Identifiable, Equatable {   // Equatable: balon yeniden çizim kapısı (MesajBalonu ==)
    var id: Int
    var ts: String?
    var metin: String
    var kaynak: String?          // ghost | sahip
    var butonlar: [[Buton]]?
    var medya: Medya?
    var sahipten: Bool { (kaynak ?? "").lowercased() == "sahip" }
}

/// Sohbet şeridi çalışıyor mu (Telegram'daki "typing" karşılığı) — son araç çağrısı özetle.
struct Mesgul: Decodable {
    var aktif: Bool
    var ozet: String?
    var ts: String?
    /// Turun başından beri geçen saniye (SUNUCU sayar). Sayaç bundan kurulur; istemci
    /// kendi saatini tutunca sekme değişimi/uygulama açılışında sıfırlanıyordu (2026-09-26).
    var sureSn: Double?
    var imza: String { (aktif ? "1" : "0") + "|" + (ozet ?? "") }

    enum CodingKeys: String, CodingKey { case aktif, ozet, ts, sureSn = "sure_sn" }
}

// MARK: Komutlar / Siteler

struct Komut: Decodable, Identifiable {
    var komut: String
    var aciklama: String?
    var grup: String?            // ghost | claude (Claude Code skill'i/yerleşiği)
    var id: String { komut }
}

struct Site: Decodable, Identifiable {
    var ad: String
    var url: String?
    var ok: Bool?
    var baslik: String?
    var proje: String?
    var id: String { ad }
}

// MARK: Zarflar

struct PingYanit: Decodable { var ok: Bool; var at: String? }
struct EslesmeYanit: Decodable {
    var deviceId: String
    var key: String
    enum CodingKeys: String, CodingKey { case key; case deviceId = "device_id" }
}
struct OkYanit: Decodable { var ok: Bool; var id: Int? }
struct KararlarZarf: Decodable { var kararlar: [Karar] }
struct DuyuYanit: Decodable { var ok: Bool; var guncel: [String]? }

// MARK: Faz 6 — eller (Ghost → telefon) ve bölgeler

/// Ghost'un telefona yazdırmak istediği iş. `veri` türe göre değişir ama iki alan ortak:
/// `ad` (başlık) ve `ts` (epoch — sunucu zaten doğruladı, geçmiş zaman gelmez).
struct Gorev: Decodable, Identifiable {
    var id: Int
    var tur: String              // hatirlatici | etkinlik
    var veri: GorevVeri
}

struct GorevVeri: Decodable {
    var ad: String
    var ts: Double
    var bitTs: Double?
    var not: String?
    var yer: String?
    var neZamanMetin: String?

    enum CodingKeys: String, CodingKey {
        case ad, ts, yer
        case not = "not"
        case bitTs = "bit_ts"
        case neZamanMetin = "ne_zaman_metin"
    }
}

struct GorevlerZarf: Decodable { var gorevler: [Gorev] }

/// Coğrafi tetik dairesi. `seq` sunucudaki tanım sürümü — değişmediyse uygulama
/// izlemeyi yeniden kurmaz (CLLocationManager'ı boşuna dövmemek için).
struct Bolge: Decodable, Identifiable {
    var ad: String
    var enlem: Double
    var boylam: Double
    var yaricap: Double
    var giris: Bool?
    var cikis: Bool?
    var id: String { ad }
}

struct BolgelerZarf: Decodable { var bolgeler: [Bolge]; var seq: Int?; var tavan: Int? }
struct DuyuDurum: Decodable { var acik: Bool?; var ozet: DuyuOzet? }
struct OutboxZarf: Decodable { var mesajlar: [Mesaj]; var son: Int?; var mesgul: Mesgul? }
struct KomutlarZarf: Decodable { var komutlar: [Komut] }
struct SitelerZarf: Decodable { var siteler: [Site] }

// MARK: Akış (Faz 1)

struct AkisOgesi: Decodable, Identifiable, Hashable {
    var id: String
    var ts: String?
    var kaynak: String?          // audit | is | sohbet | is#N | sahne
    var tur: String?
    var baslik: String?
    var detay: String?
    var sinif: String?
    var baglanti: String?
}

struct AkisZarf: Decodable { var akis: [AkisOgesi]; var once: String? }

/// Sohbet olayı (events tablosu, type=chat). Alanlar türe göre dolar.
struct Olay: Decodable, Identifiable, Hashable {
    var id: Int
    var ts: String?
    var kaynak: String?
    var tur: String?             // baslangic | metin | arac | arac_sonuc | sonuc | sahne
    var metin: String?
    var ad: String?
    var ozet: String?
    var hata: Bool?
    var model: String?
    var baslik: String?
    var sureMs: Int?
    var turSayisi: Int?
    var maliyet: Double?
    enum CodingKeys: String, CodingKey {
        case id, ts, kaynak, tur, metin, ad, ozet, hata, model, baslik, maliyet
        case sureMs = "sure_ms"
        case turSayisi = "tur_sayisi"
    }
}

struct OlaylarZarf: Decodable { var olaylar: [Olay]; var son: Int? }

// MARK: Sahne (Faz 1) — sunucudan çizilen arayüz, bileşen seti v1

/// Sayı, bool ya da metin — hepsi metin olarak okunur (tablo hücreleri).
struct EsnekMetin: Decodable, Hashable {
    let metin: String
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let i = try? c.decode(Int.self) { metin = String(i) }
        else if let d = try? c.decode(Double.self) { metin = String(d) }
        else if let b = try? c.decode(Bool.self) { metin = b ? "evet" : "hayır" }
        else if c.decodeNil() { metin = "" }
        else { metin = try c.decode(String.self) }
    }
}

/// Liste öğesi: düz metin ya da {metin, detay, durum}.
struct SahneOge: Decodable, Hashable {
    var metin: String
    var detay: String?
    var durum: String?
    enum CodingKeys: String, CodingKey { case metin, detay, durum }
    init(from decoder: Decoder) throws {
        if let c = try? decoder.singleValueContainer(), let s = try? c.decode(String.self) {
            metin = s; detay = nil; durum = nil; return
        }
        let c = try decoder.container(keyedBy: CodingKeys.self)
        metin = (try? c.decode(String.self, forKey: .metin)) ?? ""
        detay = try? c.decode(String.self, forKey: .detay)
        durum = try? c.decode(String.self, forKey: .durum)
    }
}

struct SahneSeri: Decodable, Hashable {
    var ad: String?
    var degerler: [Double]
}

struct SahneAlan: Decodable, Hashable {
    var ad: String
    var etiket: String?
    var tip: String?             // metin | sayi | uzun
    var varsayilan: String?
}

/// Tek bileşen; tür bilinmiyorsa kabuk "güncelleme gerekli" kartı basar, çökmez.
struct Bilesen: Decodable, Hashable {
    var tur: String
    var eylemID: String?
    var metin: String?
    var baslik: String?
    var durum: String?
    var url: String?
    var etiket: String?
    var veri: String?
    var stil: String?            // eylem: normal | tehlike
    var kaynak: String?          // canli: telefon | mac
    var tip: String?             // grafik: cizgi | cubuk
    var ogeler: [SahneOge]?
    var sutunlar: [String]?
    var satirlar: [[EsnekMetin]]?
    var seriler: [SahneSeri]?
    var etiketler: [String]?
    var alanlar: [SahneAlan]?
    var cocuklar: [Bilesen]?
    var yukseklik: Double?
    enum CodingKeys: String, CodingKey {
        case tur, metin, baslik, durum, url, etiket, veri, stil, kaynak, tip
        case ogeler, sutunlar, satirlar, seriler, etiketler, alanlar, cocuklar, yukseklik
        case eylemID = "id"
    }
}

struct SahneZarf: Decodable {
    var surum: Int?
    var seq: Int
    var ts: String?
    var baslik: String?
    var bilesenler: [Bilesen]
}

struct SahneGecmisOge: Decodable, Identifiable {
    var seq: Int
    var ts: String?
    var baslik: String?
    var id: Int { seq }
}
struct SahneGecmisZarf: Decodable { var gecmis: [SahneGecmisOge]; var simdi: Int? }

/// SSE satırı: `event:` adı + `data:` gövdesi.
struct SseOlay { var ad: String; var veri: Data }

/// GET /v1/ses/token — ses ajanı (ElevenLabs) konuşma jetonu; ajan private, jetonu sunucu üretir.
struct SesTokenYanit: Decodable { var token: String }

// MARK: Masa (2026-10-04) — ortak kalıcı içerik alanı (Sahne sekmesinin yerini aldı)

/// Tek kayıt: klasor · not · rapor · sahne · gorsel · dosya · baglanti. Listede içerik yerine
/// `onizleme` gelir; tam kayıt `GET /v1/masa/<id>` ile (sahne → `bilesenler`, klasör → `cocuklar`).
struct MasaKart: Decodable, Identifiable, Hashable {
    var id: Int
    var ebeveyn: Int?
    var tur: String
    var ad: String
    var icerik: String?
    var onizleme: String?
    var mime: String?
    var boyut: Int?
    var kaynak: String?
    var yazar: String                  // ghost | sahip
    var sira: Int?
    var durum: String?                 // masa | dolap
    var sabit: Bool?
    var ts: String?
    var guncelleme: String?
    var guncelleyen: String?
    var cocukSayisi: Int?
    var sonIcerik: String?
    var bilesenler: [Bilesen]?
    var yol: [MasaKirinti]?
    var cocuklar: [MasaKart]?
    enum CodingKeys: String, CodingKey {
        case id, ebeveyn, tur, ad, icerik, onizleme, mime, boyut, kaynak, yazar, sira, durum, sabit
        case ts, guncelleme, guncelleyen, bilesenler, yol, cocuklar
        case cocukSayisi = "cocuk_sayisi"
        case sonIcerik = "son_icerik"
    }
    var klasor: Bool { tur == "klasor" }
    var ghostun: Bool { yazar == "ghost" }
}

struct MasaKirinti: Decodable, Hashable { var id: Int; var ad: String }

struct MasaZarf: Decodable {
    var seq: Int
    var kartlar: [MasaKart]
    var dolapSayisi: Int?
    enum CodingKeys: String, CodingKey { case seq, kartlar; case dolapSayisi = "dolap_sayisi" }
}

struct MasaYazYanit: Decodable { var ok: Bool; var id: Int?; var sonuc: String?; var seq: Int? }
