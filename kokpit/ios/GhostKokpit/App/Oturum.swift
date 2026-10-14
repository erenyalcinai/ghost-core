import Foundation
import SwiftUI

/// Uygulama oturumu: kimlik nerede, eşli miyiz, API nesnesi.
///
/// Kimlik sırası: 1) Keychain, 2) Info.plist (Secrets.xcconfig'ten gelir; ilk açılışta
/// Keychain'e kopyalanır). İkisi de boşsa eşleme ekranı; 401'de de oraya düşülür.
@MainActor
final class Oturum: ObservableObject {
    @Published var esli: Bool = false
    /// Bekleyen onay sayısı — ⋯ üstündeki nokta ve Sohbet şeridi (RootView nöbeti,
    /// Nabız ve Kararlar yoklamaları günceller).
    @Published var bekleyenOnay: Int = 0
    /// Seçili sekme ve ⋯ menüsünden açık sayfa: kök tutar ki bildirim, komut listesi
    /// ve karar şeridi hepsi aynı yere yazsın.
    @Published var sekme: Sekme = .nabiz
    @Published var sayfa: Sayfa?
    /// Nabız'ın son anlık görüntüsü — Ayarlar sayfası sekmeden bağımsız açıldığı için burada.
    @Published var nabiz: Nabiz?
    let api: GhostAPI
    let taban: URL

    nonisolated static let anahtarID = "device_id"
    nonisolated static let anahtarKey = "device_key"
    nonisolated static let varsayilanTaban = "https://ghost-api.example.com"

    init() {
        let tabanMetin = Oturum.plist("GhostAPIBase") ?? Oturum.varsayilanTaban
        taban = URL(string: tabanMetin) ?? URL(string: Oturum.varsayilanTaban)!

        var kimlik: Kimlik?
        if let id = Keychain.oku(Oturum.anahtarID), let key = Keychain.oku(Oturum.anahtarKey) {
            kimlik = Kimlik(deviceID: id, key: key)
        } else if let id = Oturum.plist("GhostDeviceID"), let key = Oturum.plist("GhostDeviceKey") {
            kimlik = Kimlik(deviceID: id, key: key)
            if kimlik?.gecerli == true {
                Keychain.yaz(Oturum.anahtarID, id)
                Keychain.yaz(Oturum.anahtarKey, key)
            }
        }
        let gecerli = kimlik?.gecerli == true
        api = GhostAPI(base: taban, kimlik: gecerli ? kimlik : nil)
        esli = gecerli
        api.yetkisizOldu = { [weak self] in
            Task { @MainActor in self?.esli = false }
        }
    }

    /// 6 haneli kodla eşle; dönen kimliği Keychain'e yaz.
    func esle(kod: String, ad: String) async throws {
        let y = try await api.eslesme(kod: kod, ad: ad)
        let k = Kimlik(deviceID: y.deviceId, key: y.key)
        guard k.gecerli else { throw APIHata.cozumleme("eşleme yanıtı geçersiz (anahtar 64 hex değil)") }
        Keychain.yaz(Oturum.anahtarID, k.deviceID)
        Keychain.yaz(Oturum.anahtarKey, k.key)
        api.kimlik = k
        esli = true
    }

    /// Kimliği sil, eşleme ekranına dön.
    func sifirla() {
        Keychain.sil(Oturum.anahtarID)
        Keychain.sil(Oturum.anahtarKey)
        api.kimlik = nil
        esli = false
    }

    /// App Intents / arka plan için: Keychain'deki kimlikle bağımsız bir API nesnesi (eşli değilse nil).
    nonisolated static func hazirAPI() -> GhostAPI? { GhostAPI.keychaindan() }

    /// Info.plist değeri; boş ya da çözülmemiş "$(...)" ise nil.
    nonisolated private static func plist(_ anahtar: String) -> String? {
        guard let s = Bundle.main.object(forInfoDictionaryKey: anahtar) as? String else { return nil }
        let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty || t.hasPrefix("$(") { return nil }
        return t
    }
}
