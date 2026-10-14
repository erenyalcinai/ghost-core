import Foundation
import Security

/// Kimlik bilgilerini Keychain'de tutan küçük sarmalayıcı (generic password).
/// Servis adı bundle id; hesap = anahtar adı. Sır Info.plist'ten bir kez okunur,
/// sonrası hep buradan.
enum Keychain {
    static let servis = "com.example.ghost.kokpit"
    /// Uygulama + Share Extension'ın ortak erişim grubu (entitlements: keychain-access-groups).
    /// Uygulamanın varsayılan grubu zaten bu (TEAMID.bundle) — eski kayıtlar görünmeye devam eder.
    static let grup = "TEAMID0000.com.example.ghost.kokpit"

    /// Denenecek sorgu biçimleri, sırayla. iOS: paylaşımlı grup → grupsuz. macOS: veri koruma
    /// keychain'i (iOS'takiyle aynı model; application-identifier ister) → grupsuz veri koruma →
    /// eski dosya keychain'i (ad-hoc imzada bile çalışır). Okuma hepsini dener; yazma ilk
    /// başarana kadar dener — böylece aynı kayıt hangi biçimde yazıldıysa oradan okunur.
    private static var bicimler: [(grup: Bool, dp: Bool)] {
        #if os(macOS)
        return [(true, true), (false, true), (false, false)]
        #else
        return [(true, false), (false, false)]
        #endif
    }

    private static func sorgu(_ anahtar: String, _ b: (grup: Bool, dp: Bool)) -> [String: Any] {
        var s: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                kSecAttrService as String: servis,
                                kSecAttrAccount as String: anahtar]
        if b.grup { s[kSecAttrAccessGroup as String] = grup }
        if b.dp { s[kSecUseDataProtectionKeychain as String] = true }
        return s
    }

    static func oku(_ anahtar: String) -> String? {
        // Önce paylaşımlı grup; entitlement yoksa (eski build) grupsuz dene.
        for b in bicimler {
            var s = sorgu(anahtar, b)
            s[kSecReturnData as String] = true
            s[kSecMatchLimit as String] = kSecMatchLimitOne
            var sonuc: AnyObject?
            if SecItemCopyMatching(s as CFDictionary, &sonuc) == errSecSuccess,
               let veri = sonuc as? Data, let m = String(data: veri, encoding: .utf8) {
                return m
            }
        }
        return nil
    }

    @discardableResult
    static func yaz(_ anahtar: String, _ deger: String) -> Bool {
        let veri = Data(deger.utf8)
        for b in bicimler {
            let durum = SecItemUpdate(sorgu(anahtar, b) as CFDictionary,
                                      [kSecValueData as String: veri] as CFDictionary)
            if durum == errSecSuccess { return true }
            var ekle = sorgu(anahtar, b)
            ekle[kSecValueData as String] = veri
            ekle[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
            if SecItemAdd(ekle as CFDictionary, nil) == errSecSuccess { return true }
        }
        return false
    }

    static func sil(_ anahtar: String) {
        for b in bicimler { SecItemDelete(sorgu(anahtar, b) as CFDictionary) }
    }
}
