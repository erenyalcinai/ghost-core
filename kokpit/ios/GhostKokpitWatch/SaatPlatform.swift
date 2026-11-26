import Foundation
import WatchKit

/// Saatin platform köprüsü. Telefon/Mac'in `Views/Platform.swift` dosyası bu hedefe GİRMEZ (UIKit'in saatte olmayan
/// parçalarına dayanır: pano, UIDevice, titreşim üreteçleri); aynı adlar burada saat karşılıklarıyla durur.

/// Dokunsal geri bildirim — gönderim, başarı, hata. Saatte desen özelleştirilemez; sistemin hazır türleri kullanılır.
enum Titre {
    static func hafif() { WKInterfaceDevice.current().play(.click) }
    static func basari() { WKInterfaceDevice.current().play(.success) }
    static func hata() { WKInterfaceDevice.current().play(.failure) }
}

/// Eşleşme kaydında görünen cihaz adı ("Apple Watch").
enum SaatCihaz {
    static var ad: String {
        let a = WKInterfaceDevice.current().name
        return a.isEmpty ? "Apple Watch" : a
    }
}

/// Kullanıcı ayarları (yalnız bu saatte, sır değil).
enum SaatAyar {
    /// "Kendiliğinden gönder": dikte bitince önizleme beklemeden gönder. Varsayılan KAPALI.
    static let kendiligindenGonder = "kendiliginden_gonder"
}
