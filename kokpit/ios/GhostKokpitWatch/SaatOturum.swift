import Foundation
import SwiftUI

/// Eşleşme neden olmadı — ekranda gösterilen metinler buradan çıkar.
enum EslesmeHata: LocalizedError {
    case kodYok                 // saatte kod kurulu değil
    case turYanlis              // sunucu "cep" demedi: kod telefon türünde
    case yanitBozuk             // anahtar biçimi tutmuyor
    case kasa(OSStatus)         // kasa yazmadı

    var errorDescription: String? {
        switch self {
        case .kodYok:
            return "Saatte kod yok. Önce saate kod koy (Ayarlar › Parola), sonra eşle."
        case .turYanlis:
            return "Bu kod telefon türünde. Telegram'da /cihaz cep ile kod al. Az önceki kaydı /cihazlar ile iptal et."
        case .yanitBozuk:
            return "Eşleşme cevabı eksik geldi. Yeni kod al."
        case .kasa(let d):
            return "Anahtar saate yazılamadı (\(d)). Saatin kodu kurulu mu? Az önceki kaydı /cihazlar ile iptal et, yeni kod al."
        }
    }
}

/// Saat oturumu: kimlik kasada mı, eşli miyiz, API nesnesi.
///
/// Telefonun `Oturum`'undan bilerek ayrı: (1) saat YALNIZ "cep" türünde eşleşir (tam cihaz olursa söylenen "tamam"
/// onay sayılırdı), (2) anahtar yalnız kod kuruluyken yazılır/okunur, (3) derleme anahtarı (Info.plist kimliği) yolu
/// yoktur — kimlik tek kaynaktan, eşleşmeden gelir. Sahiplik anahtarı (key0) olmayan kimlikle HİÇ istek atılmaz.
@MainActor
final class SaatOturum: ObservableObject {
    enum Durum: Equatable { case kodYok, esliDegil, esli }

    @Published var durum: Durum = .esliDegil
    /// Bekleyen karar sayısı (yalnız sayı; kart ekranı sonraki aşama).
    @Published var bekleyenKarar: Int = 0
    /// Nabız özeti: iyi | uyari | kritik (ana ekranın maskot hâli buna bakar).
    @Published var nabizOzet: String?
    @Published var freeze: Bool = false
    /// Son ağ hatası (ana ekran durum satırı). nil = son istek başarılı.
    @Published var agHatasi: String?

    let api: GhostAPI
    let taban: URL

    nonisolated static let varsayilanTaban = "https://ghost-api.example.com"

    init() {
        let tabanMetin = SaatOturum.plist("GhostAPIBase") ?? SaatOturum.varsayilanTaban
        taban = URL(string: tabanMetin) ?? URL(string: SaatOturum.varsayilanTaban)!
        let kimlik = SaatOturum.kasadanKimlik()
        api = GhostAPI(base: taban, kimlik: kimlik)
        if kimlik != nil {
            durum = .esli
        } else {
            durum = SaatKasa.kodKurulu() ? .esliDegil : .kodYok
        }
        api.yetkisizOldu = { [weak self] in
            Task { @MainActor in self?.yetkiDustu() }
        }
    }

    /// Kasadaki kimlik; üç parça da (kimlik, anahtar, sahiplik anahtarı) yerinde ve biçimi doğruysa.
    nonisolated private static func kasadanKimlik() -> Kimlik? {
        guard let id = SaatKasa.oku(SaatKasa.anahtarID),
              let key = SaatKasa.oku(SaatKasa.anahtarKey),
              let key0 = SaatKasa.oku(SaatKasa.anahtarKey0) else { return nil }
        let k = Kimlik(deviceID: id, key: key, key0: key0)
        return (k.gecerli && k.key0Bytes != nil) ? k : nil
    }

    /// Arka planda (bildirim eylemi) çalışan kod için: kasadaki kimlikle bağımsız API. Saat kilitliyse nil döner.
    nonisolated static func hazirAPI() -> GhostAPI? {
        guard let k = kasadanKimlik() else { return nil }
        let tabanMetin = plist("GhostAPIBase") ?? varsayilanTaban
        guard let url = URL(string: tabanMetin) else { return nil }
        return GhostAPI(base: url, kimlik: k)
    }

    /// Uygulama her öne geldiğinde çağrılır; kasa GERÇEĞİN kaynağıdır, bellekteki kimlik ona uydurulur:
    ///  - eşli görünüyoruz ama kasada kimlik yok (saatin kodu kaldırıldı → sistem kayıtları sildi): bellekteki
    ///    anahtar bırakılır, eşleşme ekranına dönülür;
    ///  - eşli görünmüyoruz ama kasada kimlik var (süreç saat kilitliyken başladı, kasa o an okunamadı): kimlik
    ///    yüklenir, gereksiz yeniden eşleşme istenmez;
    ///  - kimlik yok: kod durumu yenilenir (kullanıcı Ayarlar'a gidip kod koymuş olabilir).
    func oneGelince() {
        if let k = SaatOturum.kasadanKimlik() {
            if durum != .esli || api.kimlik != k {
                api.kimlik = k
                durum = .esli
            }
            return
        }
        if durum == .esli {
            api.kimlik = nil
            bekleyenKarar = 0
        }
        durum = SaatKasa.kodKurulu() ? .esliDegil : .kodYok
    }

    /// 6 haneli kodla eşle. Sunucu `tur: "cep"` + `key0` vermezse eşleşme REDDEDİLİR ve hiçbir şey saklanmaz.
    func esle(kod: String) async throws {
        guard SaatKasa.kodKurulu() else {
            durum = .kodYok
            throw EslesmeHata.kodYok
        }
        let y = try await api.eslesme(kod: kod, ad: SaatCihaz.ad)
        guard y.tur == "cep" else { throw EslesmeHata.turYanlis }
        let k = Kimlik(deviceID: y.deviceId, key: y.key, key0: y.key0)
        guard k.gecerli, k.key0Bytes != nil, let key0 = y.key0 else { throw EslesmeHata.yanitBozuk }
        for (ad, deger) in [(SaatKasa.anahtarID, k.deviceID), (SaatKasa.anahtarKey, k.key), (SaatKasa.anahtarKey0, key0)] {
            let d = SaatKasa.yaz(ad, deger)
            if d != errSecSuccess {
                SaatKasa.hepsiniSil()       // yarım kimlik kalmasın
                throw EslesmeHata.kasa(d)
            }
        }
        api.kimlik = k
        agHatasi = nil
        durum = .esli
    }

    /// Kimliği sil, eşleşme ekranına dön (Ayarlar › "Eşleşmeyi kaldır"). Sunucudaki kayıt Telegram'dan iptal edilir.
    func sifirla() {
        SaatKasa.hepsiniSil()
        api.kimlik = nil
        bekleyenKarar = 0
        durum = SaatKasa.kodKurulu() ? .esliDegil : .kodYok
    }

    /// 401: sunucu yalnız GERÇEK kimlik hatasında verir (cihaz iptal/kilitli ya da imza tutmuyor; tekrar 409, saat
    /// penceresi 400, servis düşük 503 ayrı gelir). Kimlik artık işe yaramaz: bellekten de kasadan da silinir —
    /// yoksa her açılışta yeniden yüklenip aynı 401'i alırdı ve bildirim cevabı ölü kimlikle denenirdi.
    private func yetkiDustu() {
        SaatKasa.hepsiniSil()
        api.kimlik = nil
        bekleyenKarar = 0
        durum = SaatKasa.kodKurulu() ? .esliDegil : .kodYok
    }

    /// Arka plandaki (bildirim eylemi) istek 401 aldıysa: ölü kimliği kasadan sil.
    nonisolated static func oluKimligiSil() {
        SaatKasa.hepsiniSil()
    }

    /// Ana ekran için: nabız özeti + bekleyen karar sayısı. Hata durumu `agHatasi`'na yazılır.
    func durumuYenile() async {
        guard durum == .esli else { return }
        do {
            let n = try await api.nabiz()
            nabizOzet = n.ozet
            freeze = n.freeze ?? false
            bekleyenKarar = n.bekleyen?.kararlar?.count ?? 0
            agHatasi = nil
        } catch APIHata.yetkisiz {
            return
        } catch {
            agHatasi = SaatOturum.kisaHata(error)
        }
    }

    /// Mesaj gönderiminde 409 "istek tekrarı" BAŞARIDIR: sistem aynı isteği (aynı nonce) ikinci kez yollamış,
    /// ilki sunucuya ulaşmış demektir. Hata gösterilirse kullanıcı yeniden söyler ve mesaj çiftlenir.
    nonisolated static func tekrarMi(_ e: Error) -> Bool {
        if let a = e as? APIHata, case .sunucu(let kod, _) = a { return kod == 409 }
        return false
    }

    /// Hata metni bileğe sığacak kadar kısa.
    nonisolated static func kisaHata(_ e: Error) -> String {
        if let u = e as? URLError {
            switch u.code {
            case .notConnectedToInternet, .networkConnectionLost, .dataNotAllowed: return "Bağlantı yok"
            case .timedOut: return "Zaman aşımı"
            default: return "Ağ hatası"
            }
        }
        if let a = e as? APIHata, case .sunucu(let kod, _) = a {
            switch kod {
            case 400: return "Saat ayarı tutmuyor (400)"
            case 423: return "Ghost dondurulmuş"
            case 429: return "Çok sık, biraz bekle"
            case 503: return "Onay servisi yanıt vermiyor"
            default: return "Sunucu \(kod)"
            }
        }
        return String((e.localizedDescription).prefix(60))
    }

    nonisolated private static func plist(_ anahtar: String) -> String? {
        guard let s = Bundle.main.object(forInfoDictionaryKey: anahtar) as? String else { return nil }
        let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty || t.hasPrefix("$(") { return nil }
        return t
    }
}
