import Foundation
import Security

/// Saatin kimlik kasası. Telefonun `Keychain` sarmalayıcısından AYRI (o AfterFirstUnlock + paylaşımlı grup kullanır):
/// saatte kayıtlar `kSecAttrAccessibleWhenPasscodeSetThisDeviceOnly` ile yazılır — saatte kod yoksa HİÇ yazılamaz,
/// kod kaldırılırsa sistem kayıtları siler, saat bilekten çıkıp kilitlenince okunamaz, yedeğe/başka cihaza taşınmaz.
///
/// Simülatörde kod kurulamadığı için (yalnız `targetEnvironment(simulator)` derlemesinde) WhenUnlockedThisDeviceOnly
/// kullanılır; bu dal cihaz derlemesine GİRMEZ (derleme zamanı koşulu).
enum SaatKasa {
    static let servis = "com.example.ghost.kokpit.watchkitapp"
    static let anahtarID = "device_id"
    static let anahtarKey = "device_key"
    static let anahtarKey0 = "device_key0"
    private static let yoklamaAdi = "kod_yoklama"

    private static var erisim: CFString {
        #if targetEnvironment(simulator)
        return kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        #else
        return kSecAttrAccessibleWhenPasscodeSetThisDeviceOnly
        #endif
    }

    private static func sorgu(_ ad: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: servis,
         kSecAttrAccount as String: ad]
    }

    static func oku(_ ad: String) -> String? {
        var s = sorgu(ad)
        s[kSecReturnData as String] = true
        s[kSecMatchLimit as String] = kSecMatchLimitOne
        var sonuc: AnyObject?
        guard SecItemCopyMatching(s as CFDictionary, &sonuc) == errSecSuccess,
              let veri = sonuc as? Data else { return nil }
        return String(data: veri, encoding: .utf8)
    }

    /// Yazar; başarısızsa OSStatus döner (kod yoksa sistem bu sınıfa kayıt kabul etmez).
    @discardableResult
    static func yaz(_ ad: String, _ deger: String) -> OSStatus {
        SecItemDelete(sorgu(ad) as CFDictionary)
        var e = sorgu(ad)
        e[kSecValueData as String] = Data(deger.utf8)
        e[kSecAttrAccessible as String] = erisim
        return SecItemAdd(e as CFDictionary, nil)
    }

    static func sil(_ ad: String) {
        SecItemDelete(sorgu(ad) as CFDictionary)
    }

    static func hepsiniSil() {
        for ad in [anahtarID, anahtarKey, anahtarKey0, yoklamaAdi] { sil(ad) }
    }

    /// Saatte kod kurulu mu? Ayrı bir API'ye güvenmek yerine kasanın kendisine sorulur: "kod kuruluyken" sınıfına
    /// deneme kaydı yazılabiliyorsa kod vardır. Eşleşmeden ÖNCE çağrılır; false ise eşleşme başlatılmaz.
    static func kodKurulu() -> Bool {
        let durum = yaz(yoklamaAdi, "1")
        sil(yoklamaAdi)
        return durum == errSecSuccess
    }
}
