import Foundation
import WatchKit
import UserNotifications

/// Saatin bildirim tarafı: kendi APNs belirtecini alır (`/v1/apns`), "sohbet" kategorisini kaydeder, uygulama öndeyken
/// de bildirimi gösterir, "Cevapla" eylemini saatte işler.
///
/// Kategori "sohbet" — eylem sırası BİLEREK böyle:
///   1. Cevapla (metin girişli; çift dokunma bildirimde İLK eylemi tetikler → dikte açılır) → `/v1/mesaj`
///   2. Aç (uygulamayı öne getirir → son mesajlar)
/// ONAY EYLEMİ YOK: bildirimde onay düğmesi konmaz (karar). Bu dosya onay/karar ucuna hiç istek atmaz;
/// "Cevapla" ile giden metin de cep cihazı mesajıdır (onay sayılmaz).
final class SaatDelegesi: NSObject, WKApplicationDelegate, UNUserNotificationCenterDelegate {
    static let kategoriSohbet = "sohbet"
    static let eylemCevapla = "cevapla"
    static let eylemAc = "ac"
    static let acBildirimi = Notification.Name("ghost.saat.ac")

    /// APNs'in verdiği son belirteç. Gönderildikten sonra da SAKLANIR: aynı süreçte eşleşme kaldırılıp yeniden
    /// kurulursa (yeni cihaz kimliği) belirteç yeni kimliğe de yazılmalı, yoksa saat soğuk açılışa dek bildirim almaz.
    private var sonBelirtec: String?
    /// Belirtecin en son yazıldığı "cihaz kimliği|belirteç" çifti ve zamanı (aynı çift 12 saatte bir yenilenir:
    /// sunucu belirteci silmiş olabilir).
    private var gonderilenCift: String?
    private var gonderimZamani = Date.distantPast
    private var gonderiliyor = false
    private var api: GhostAPI?

    func applicationDidFinishLaunching() {
        let merkez = UNUserNotificationCenter.current()
        merkez.delegate = self
        let cevapla = UNTextInputNotificationAction(identifier: SaatDelegesi.eylemCevapla,
                                                    title: "Cevapla",
                                                    options: [],
                                                    textInputButtonTitle: "Gönder",
                                                    textInputPlaceholder: "Ghost'a söyle")
        let ac = UNNotificationAction(identifier: SaatDelegesi.eylemAc, title: "Aç", options: [.foreground])
        let sohbet = UNNotificationCategory(identifier: SaatDelegesi.kategoriSohbet,
                                            actions: [cevapla, ac],
                                            intentIdentifiers: [],
                                            options: [])
        merkez.setNotificationCategories([sohbet])
    }

    /// Eşleşme tamamlanınca ve uygulama her öne geldiğinde çağrılır: izin + APNs kaydı. İzin sorusu sistemce yalnız
    /// ilk seferde gösterilir; sonraki çağrılar sessizce mevcut kararı döndürür.
    func kaydol(api: GhostAPI) {
        self.api = api
        gonder()        // kimlik değiştiyse eldeki belirteci hemen yeni cihaza yaz
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { verildi, _ in
            guard verildi else { return }
            DispatchQueue.main.async { WKApplication.shared().registerForRemoteNotifications() }
        }
    }

    func didRegisterForRemoteNotifications(withDeviceToken deviceToken: Data) {
        sonBelirtec = deviceToken.hex
        gonder()
    }

    func didFailToRegisterForRemoteNotificationsWithError(_ error: Error) {
        NSLog("APNs kayıt hatası (saat): %@", error.localizedDescription)
    }

    private func gonder() {
        guard let belirtec = sonBelirtec, let api, let kimlik = api.kimlik, kimlik.gecerli, !gonderiliyor else { return }
        let cift = kimlik.deviceID + "|" + belirtec
        if cift == gonderilenCift, Date().timeIntervalSince(gonderimZamani) < 12 * 3600 { return }
        #if DEBUG
        let ortam = "development"
        #else
        let ortam = "production"
        #endif
        gonderiliyor = true
        Task { @MainActor in
            defer { self.gonderiliyor = false }
            do {
                _ = try await api.apnsKaydet(token: belirtec, env: ortam)
                self.gonderilenCift = cift
                self.gonderimZamani = Date()
            } catch {
                NSLog("APNs belirteci gönderilemedi (saat): %@", error.localizedDescription)
            }
        }
    }

    // Uygulama öndeyken de göster (uygulanmazsa watchOS bildirimi SUSTURUR).
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        [.banner, .sound, .list]
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse) async {
        switch response.actionIdentifier {
        case SaatDelegesi.eylemCevapla:
            let metin = ((response as? UNTextInputNotificationResponse)?.userText ?? "")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            guard !metin.isEmpty else { return }
            await SaatDelegesi.cevapGonder(metin)
        case UNNotificationDismissActionIdentifier:
            return
        default:        // "Aç" ya da bildirimin kendisine dokunma
            await MainActor.run {
                NotificationCenter.default.post(name: SaatDelegesi.acBildirimi, object: nil)
            }
        }
    }

    /// Bildirimden dikte edilen cevabı gönder. Uygulama arka planda olabilir: kimlik kasadan okunur
    /// (saat bilekte ve kilidi açıkken okunabilir). Sonuç dokunsal + başarısızsa yerel bildirimle söylenir —
    /// "gönderdim sanıp gitmemiş" olmasın.
    private static func cevapGonder(_ metin: String) async {
        guard let api = SaatOturum.hazirAPI() else {
            await gitmediBildir("Saat eşli değil ya da kilitli.")
            return
        }
        do {
            try await api.mesajGonder(metin)
            await MainActor.run { Titre.basari() }
        } catch APIHata.yetkisiz {
            SaatOturum.oluKimligiSil()      // cihaz iptal edilmiş/kilitlenmiş: ölü kimlik kasada kalmasın
            await gitmediBildir("Eşleşme düşmüş. Uygulamayı açıp yeniden eşle.", yenidenSoyle: false)
        } catch {
            if SaatOturum.tekrarMi(error) {  // ilk deneme sunucuya ulaşmış: gitti say
                await MainActor.run { Titre.basari() }
                return
            }
            await gitmediBildir(SaatOturum.kisaHata(error))
        }
    }

    private static func gitmediBildir(_ neden: String, yenidenSoyle: Bool = true) async {
        await MainActor.run { Titre.hata() }
        let icerik = UNMutableNotificationContent()
        icerik.title = "Cevap gitmedi"
        icerik.body = yenidenSoyle ? neden + " Uygulamayı açıp yeniden söyle." : neden
        let istek = UNNotificationRequest(identifier: "cevap-gitmedi", content: icerik, trigger: nil)
        try? await UNUserNotificationCenter.current().add(istek)
    }
}
