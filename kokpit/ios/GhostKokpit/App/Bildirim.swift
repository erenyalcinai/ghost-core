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
    /// 2026-11-26: açılışta önce bitmiş işlerin kartları kapatılır (etkinlikleriToparla), yaşayanların belirteci
    /// yeniden bildirilir; bitmiş/kapanmış karta izleyici bağlanmaz (sunucuda yetim satır açmasın).
    private func etkinlikTokenlariniIzle() {
        Task.detached {
            if let t = Activity<IsEtkinligi>.pushToStartToken {
                await BildirimDelegesi.tokenGonder("start", t.hex, nil)
            }
            for await veri in Activity<IsEtkinligi>.pushToStartTokenUpdates {
                await BildirimDelegesi.tokenGonder("start", veri.hex, nil)
            }
        }
        Task.detached {
            await BildirimDelegesi.etkinlikleriToparla()
            for a in Activity<IsEtkinligi>.activities where BildirimDelegesi.yasiyor(a) { BildirimDelegesi.izle(a) }
            for await a in Activity<IsEtkinligi>.activityUpdates { BildirimDelegesi.izle(a) }
        }
    }

    nonisolated private static func izle(_ a: Activity<IsEtkinligi>) {
        Task.detached {
            for await veri in a.pushTokenUpdates {
                guard yasiyor(a) else { return }
                await tokenGonder("update", veri.hex, a.attributes.jid)
            }
        }
    }

    nonisolated private static func yasiyor(_ a: Activity<IsEtkinligi>) -> Bool {
        a.activityState == .active || a.activityState == .stale
    }

    /// İş kartı toparlayıcısı (2026-11-26, "iş bitince kart kapanmıyor"): sunucunun end push'una bağımlı kalmadan
    /// yaşayan her kartın işini /v1/nabiz'deki iş listesiyle karşılaştırır; bitmiş işin kartını hemen kapatır,
    /// süren işin güncelleme belirtecini yeniden bildirir. Açılışta, öne gelişte ve sessiz push'ta koşar.
    /// Sunucuya ulaşılamazsa hiçbir kartı kapatmaz.
    nonisolated static func etkinlikleriToparla() async {
        let acik = Activity<IsEtkinligi>.activities.filter(yasiyor)
        guard !acik.isEmpty, let api = GhostAPI.keychaindan(), let isler = try? await api.nabiz().isler else { return }
        for a in acik {
            if let durum = IsKartiKarar.bitisDurumu(jid: a.attributes.jid, isler: isler) {
                await kapat(a, durum)
            } else if let t = a.pushToken {
                await tokenGonder("update", t.hex, a.attributes.jid)
            }
        }
    }

    /// Kartı yerelde bitir ve kilit ekranından hemen kaldır (sonuç zaten "İş #N bitti" bildiriminde).
    nonisolated static func kapat(_ a: Activity<IsEtkinligi>, _ durum: String) async {
        var s = a.content.state
        s.durum = durum
        if s.bitti == nil { s.bitti = Int(Date().timeIntervalSince1970) }
        await a.end(ActivityContent(state: s, staleDate: nil), dismissalPolicy: .immediate)
    }

    /// Sunucunun yedek sessiz push'u {"etkinlik_bitti": <iş no>} (ghost/apns.py etkinlik_yedek_kapat).
    nonisolated static func isNumarasi(_ deger: Any?) -> Int? {
        if let n = deger as? Int { return n }
        if let n = deger as? NSNumber { return n.intValue }
        if let s = deger as? String { return Int(s) }
        return nil
    }

    nonisolated private static func tokenGonder(_ tur: String, _ token: String, _ jid: Int?) async {
        guard let api = GhostAPI.keychaindan() else { return }
        do { _ = try await api.etkinlikToken(tur: tur, token: token, jid: jid) }
        catch { NSLog("etkinlik token gönderilemedi (%@): %@", tur, error.localizedDescription) }
    }

    /// Sessiz push (Faz 6): kuyrukta telefona yazılacak görev var — arka planda boşalt.
    /// 2026-11-26: iş numarası taşıyan sessiz push o işin kartını kapatır, sonra kartlar toparlanır.
    /// Teslim garantisi yok; öne gelme ve arka plan tazelemesi yedek tetikler.
    func application(_ application: UIApplication,
                     didReceiveRemoteNotification userInfo: [AnyHashable: Any],
                     fetchCompletionHandler tamamlandi: @escaping (UIBackgroundFetchResult) -> Void) {
        let bitenIs = BildirimDelegesi.isNumarasi(userInfo["etkinlik_bitti"])
        Task { @MainActor in
            if let jid = bitenIs {
                for a in Activity<IsEtkinligi>.activities where a.attributes.jid == jid && BildirimDelegesi.yasiyor(a) {
                    await BildirimDelegesi.kapat(a, "bitti")
                }
                await BildirimDelegesi.etkinlikleriToparla()
            }
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
