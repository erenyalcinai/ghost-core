import SwiftUI
import UIKit
import UserNotifications
import ActivityKit
import WidgetKit

/// APNs kaydı (Faz 2): izin iste → token al → /v1/apns'e yaz. Bildirime dokununca
/// ilgili sekmeye geç ("sekme" alanı). Uygulama öndeyken de banner göster.
final class BildirimDelegesi: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    static let sekmeBildirimi = Notification.Name("ghost.sekme")
    /// Token API'ye gitmeden önce oturum hazır olmayabilir; bekletilir.
    var bekleyenToken: String?
    var api: GhostAPI?

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        etkinlikTokenlariniIzle()
        // Faz 5: arka plan tazeleme görevi UYGULAMA AÇILIŞI BİTMEDEN kaydedilmeli
        // (BGTaskScheduler kuralı), yoksa çalışma anında çöker.
        DuyuYoneticisi.arkaPlaniKaydet()
        // SLC uygulamayı arka planda uyandırdıysa launchOptions'ta .location olur;
        // her iki halde de duyu dinlemesini ayağa kaldırmak güvenli (kapalı duyu sessizdir).
        Task { @MainActor in DuyuYoneticisi.paylasilan.baslat() }
        return true
    }

    /// Live Activity: push-to-start token'ı ve açılan her etkinliğin güncelleme token'ı
    /// sunucuya gider (köprü iş başlayınca start, bitince end push'lar).
    private func etkinlikTokenlariniIzle() {
        Task.detached {
            for await veri in Activity<IsEtkinligi>.pushToStartTokenUpdates {
                await BildirimDelegesi.tokenGonder("start", veri.hex, nil)
            }
        }
        Task.detached {
            for a in Activity<IsEtkinligi>.activities { BildirimDelegesi.izle(a) }
            for await a in Activity<IsEtkinligi>.activityUpdates { BildirimDelegesi.izle(a) }
        }
    }

    private static func izle(_ a: Activity<IsEtkinligi>) {
        Task.detached {
            for await veri in a.pushTokenUpdates {
                await tokenGonder("update", veri.hex, a.attributes.jid)
            }
        }
    }

    private static func tokenGonder(_ tur: String, _ token: String, _ jid: Int?) async {
        guard let api = GhostAPI.keychaindan() else { return }
        do { _ = try await api.etkinlikToken(tur: tur, token: token, jid: jid) }
        catch { NSLog("etkinlik token gönderilemedi (%@): %@", tur, error.localizedDescription) }
    }

    /// Sessiz push (Faz 6): kuyrukta telefona yazılacak görev var — arka planda boşalt.
    /// Teslim garantisi yok; öne gelme ve arka plan tazelemesi yedek tetikler.
    func application(_ application: UIApplication,
                     didReceiveRemoteNotification userInfo: [AnyHashable: Any],
                     fetchCompletionHandler tamamlandi: @escaping (UIBackgroundFetchResult) -> Void) {
        Task { @MainActor in
            await EllerYoneticisi.paylasilan.kuyrugaBak()
            tamamlandi(.newData)
        }
    }

    /// Eşleme tamamlandıktan sonra çağrılır: izin + kayıt.
    func kaydol() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { verildi, _ in
            guard verildi else { return }
            DispatchQueue.main.async { UIApplication.shared.registerForRemoteNotifications() }
        }
    }

    func application(_ application: UIApplication,
                     didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let token = deviceToken.hex
        bekleyenToken = token
        gonder()
    }

    func application(_ application: UIApplication,
                     didFailToRegisterForRemoteNotificationsWithError error: Error) {
        NSLog("APNs kayıt hatası: %@", error.localizedDescription)
    }

    func gonder() {
        guard let t = bekleyenToken, let api, api.kimlik?.gecerli == true else { return }
        #if DEBUG
        let env = "development"
        #else
        let env = "production"
        #endif
        Task {
            do {
                let _: OkYanit = try await api.apnsKaydet(token: t, env: env)
                bekleyenToken = nil
            } catch {
                NSLog("APNs token gönderilemedi: %@", error.localizedDescription)
            }
        }
    }

    // Öndeyken de göster
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        [.banner, .sound, .badge]
    }

    // Dokununca sekmeye git
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse) async {
        if let s = response.notification.request.content.userInfo["sekme"] as? String {
            NotificationCenter.default.post(name: BildirimDelegesi.sekmeBildirimi, object: s)
        }
    }
}
