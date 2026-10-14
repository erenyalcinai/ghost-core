import AppKit
import UserNotifications

/// macOS bildirim delegesi. iOS'taki adaşıyla (App/Bildirim.swift) aynı adı ve aynı
/// `sekmeBildirimi` sözleşmesini taşır ki RootView/SohbetView değişmeden derlensin.
///
/// Fark: APNs YOK (Mac paketi ayrı bundle id, köprü yalnız iOS token'larına push'lar).
/// Onay/karar bildirimi MacRootView'ın nöbetinden YEREL bildirim olarak düşer —
/// uygulama açıkken çalışır, bu bilinçli: Mac kapalıyken telefon zaten haber verir.
final class BildirimDelegesi: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    static let sekmeBildirimi = Notification.Name("ghost.sekme")
    var api: GhostAPI?
    private var izinli = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        UNUserNotificationCenter.current().delegate = self
    }

    /// Son pencere kapanınca uygulama menü çubuğunda yaşamaya devam eder.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    /// Eşleme tamamlandıktan sonra: yerel bildirim izni.
    func kaydol() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { verildi, _ in
            self.izinli = verildi
        }
    }

    /// Yerel bildirim; `sekme` dokununca açılacak bölüm ("kararlar", "sohbet", …).
    static func yerel(baslik: String, govde: String, sekme: String?) {
        let icerik = UNMutableNotificationContent()
        icerik.title = baslik
        icerik.body = govde
        icerik.sound = .default
        if let s = sekme { icerik.userInfo = ["sekme": s] }
        let istek = UNNotificationRequest(identifier: "ghost-" + UUID().uuidString, content: icerik, trigger: nil)
        UNUserNotificationCenter.current().add(istek)
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        [.banner, .sound, .badge]
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse) async {
        if let s = response.notification.request.content.userInfo["sekme"] as? String {
            await MainActor.run {
                NSApplication.shared.activate(ignoringOtherApps: true)
                NotificationCenter.default.post(name: BildirimDelegesi.sekmeBildirimi, object: s)
            }
        }
    }
}
