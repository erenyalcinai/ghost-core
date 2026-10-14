import Foundation
import CoreLocation
import EventKit
import UIKit
import Network
import BackgroundTasks

/// Duyular (Faz 5) — sahibin telefonundan Ghost'a giden izinli bağlam:
/// **konum**, **takvim**, **cihaz durumu**.
///
/// Tasarım kararları:
/// - Her duyunun AYRI anahtarı var (`DuyuAyar`), varsayılan KAPALI. Anahtarı açmak
///   iOS izin sorusunu tetikler; izin reddedilirse anahtar kendini geri kapatır —
///   "açık görünüp veri göndermeyen" yalancı durum olmasın.
/// - Konum için **significant location change** kullanılır: şehir/semt hassasiyeti,
///   pil maliyeti yok denecek kadar az, uygulama kapalıyken bile uyanır. Sürekli
///   GPS'e gerek yok; Ghost'un sorusu "hangi semtte" — "hangi sokakta" değil.
/// - Ters coğrafi çözümleme TELEFONDA yapılır (CLGeocoder); sunucuya yer adı gider,
///   koordinat yalnız /nerede'de görünsün diye eklenir. Geocoder kotalı olduğu için
///   yalnız 300 m'den fazla hareket edildiğinde çağrılır.
/// - Takvimden yalnız başlık/saat/yer alınır. Not, katılımcı, URL, ek HİÇ okunmaz.
/// - Gönderi kısıtlıdır (`asgariAra`); zorla=true yalnız Sahip bir anahtarı
///   değiştirdiğinde ya da arka plan görevi koştuğunda geçer.
@MainActor
final class DuyuYoneticisi: NSObject, ObservableObject {
    static let paylasilan = DuyuYoneticisi()
    static let arkaPlanKimligi = "com.example.ghost.kokpit.duyu"

    /// Uygulama kapalıyken de aynı ayarları okuyabilmek için UserDefaults.
    enum Ayar: String, CaseIterable {
        case konum = "duyu.konum"
        case takvim = "duyu.takvim"
        case cihaz = "duyu.cihaz"

        var baslik: String {
            switch self {
            case .konum: return "Konum"
            case .takvim: return "Takvim"
            case .cihaz: return "Telefon durumu"
            }
        }
        var aciklama: String {
            switch self {
            case .konum: return "Hangi semtte olduğun. Semt hassasiyeti, sürekli GPS yok."
            case .takvim: return "Bugünün etkinlikleri: başlık, saat, yer. Not/katılımcı okunmaz."
            case .cihaz: return "Pil, şarj, düşük güç, ağ — Ghost telefonun halini bilsin."
            }
        }
        var simge: String {
            switch self {
            case .konum: return "location"
            case .takvim: return "calendar"
            case .cihaz: return "iphone.gen3"
            }
        }
        var acik: Bool { UserDefaults.standard.bool(forKey: rawValue) }
        func yaz(_ v: Bool) { UserDefaults.standard.set(v, forKey: rawValue) }
    }

    @Published private(set) var konumAcik = Ayar.konum.acik
    @Published private(set) var takvimAcik = Ayar.takvim.acik
    @Published private(set) var cihazAcik = Ayar.cihaz.acik
    /// Son gönderi sonucu — Ayarlar'da tek satır geri bildirim.
    @Published private(set) var sonDurum: String = ""
    @Published private(set) var sunucuAcik = true

    private let konumcu = CLLocationManager()
    private let takvimDeposu = EKEventStore()
    private let agIzleyici = NWPathMonitor()
    private var agTuru = ""
    private var sonKonum: CLLocation?
    private var sonCozulen: CLLocation?      // en son ters çözümlenen nokta
    private var sonYer = ""
    private var sonUlke = ""
    private var sonGonderi = Date.distantPast
    private var gonderiyor = false
    /// Sunucudaki bölge tanım sürümü; değişmediyse izleme yeniden kurulmaz.
    private var bolgeSeq = -1

    /// İki gönderi arası asgari süre — SLC art arda tetiklerse sunucu dövülmesin.
    private let asgariAra: TimeInterval = 600
    /// Ters çözümleme için asgari yer değiştirme (m).
    private let asgariMesafe: CLLocationDistance = 300

    private override init() {
        super.init()
        konumcu.delegate = self
        konumcu.desiredAccuracy = kCLLocationAccuracyHundredMeters
        konumcu.pausesLocationUpdatesAutomatically = true
        agIzleyici.pathUpdateHandler = { [weak self] yol in
            let t: String
            if yol.status != .satisfied { t = "çevrimdışı" }
            else if yol.usesInterfaceType(.wifi) { t = "wifi" }
            else if yol.usesInterfaceType(.cellular) { t = "hücresel" }
            else if yol.usesInterfaceType(.wiredEthernet) { t = "kablolu" }
            else { t = "bilinmiyor" }
            Task { @MainActor in self?.agTuru = t }
        }
        agIzleyici.start(queue: DispatchQueue(label: "ghost.ag"))
        NotificationCenter.default.addObserver(
            self, selector: #selector(takvimDegisti),
            name: .EKEventStoreChanged, object: takvimDeposu)
    }

    // MARK: Yaşam döngüsü

    /// Uygulama açılışında (ve SLC ile arka planda uyandırıldığında) çağrılır.
    func baslat() {
        if konumAcik { konumcu.startMonitoringSignificantLocationChanges() }
        arkaPlanPlanla()
        Task {
            await bildir()
            await bolgeleriTazele()
        }
    }

    /// Anahtar değişti: izni iste, gerekirse dinlemeyi başlat/durdur, hemen bildir.
    func degistir(_ ayar: Ayar, _ acik: Bool) {
        ayar.yaz(acik)
        yansit()
        switch ayar {
        case .konum:
            if acik {
                konumIzniIste()
            } else {
                konumcu.stopMonitoringSignificantLocationChanges()
                sonKonum = nil; sonYer = ""; sonUlke = ""
            }
        case .takvim:
            if acik { Task { await takvimIzniIste() } }
        case .cihaz:
            UIDevice.current.isBatteryMonitoringEnabled = acik
        }
        Task { await bildir(zorla: true) }
    }

    private func yansit() {
        konumAcik = Ayar.konum.acik
        takvimAcik = Ayar.takvim.acik
        cihazAcik = Ayar.cihaz.acik
    }

    // MARK: İzinler

    /// Önce "kullanırken", verilirse "her zaman" — iOS ikinci soruyu ancak
    /// birincisi verildikten sonra sorar. "Her zaman" olmadan uygulama kapalıyken
    /// konum gelmez; reddedilirse duyu yine çalışır, sadece uygulama açıkken.
    private func konumIzniIste() {
        switch konumcu.authorizationStatus {
        case .notDetermined:
            konumcu.requestWhenInUseAuthorization()
        case .authorizedWhenInUse:
            konumcu.requestAlwaysAuthorization()
            konumcu.startMonitoringSignificantLocationChanges()
        case .authorizedAlways:
            konumcu.startMonitoringSignificantLocationChanges()
        default:
            // Reddedildi/kısıtlı: anahtarı açık bırakmak yalan olur.
            Ayar.konum.yaz(false); yansit()
            sonDurum = "Konum izni reddedilmiş — Ayarlar › Gizlilik'ten açman gerekiyor."
        }
    }

    private func takvimIzniIste() async {
        do {
            let verildi = try await takvimDeposu.requestFullAccessToEvents()
            if !verildi {
                Ayar.takvim.yaz(false); yansit()
                sonDurum = "Takvim izni verilmedi."
            }
        } catch {
            Ayar.takvim.yaz(false); yansit()
            sonDurum = "Takvim izni alınamadı: " + error.localizedDescription
        }
    }

    @objc private func takvimDegisti() {
        guard takvimAcik else { return }
        Task { await bildir(zorla: true) }
    }

    // MARK: Toplama

    private func konumPaketi() async -> [String: Any]? {
        guard konumAcik, let l = sonKonum else { return nil }
        await yerCoz(l)
        var p: [String: Any] = ["enlem": l.coordinate.latitude,
                                "boylam": l.coordinate.longitude,
                                "dogruluk": max(0, l.horizontalAccuracy)]
        if !sonYer.isEmpty { p["yer"] = sonYer }
        if !sonUlke.isEmpty { p["ulke"] = sonUlke }
        return p
    }

    /// Kotalı servis: yalnız `asgariMesafe`den fazla hareket edilmişse çağır.
    private func yerCoz(_ l: CLLocation) async {
        if let onceki = sonCozulen, l.distance(from: onceki) < asgariMesafe, !sonYer.isEmpty { return }
        guard let yer = try? await CLGeocoder().reverseGeocodeLocation(l).first else { return }
        let parcalar = [yer.subLocality, yer.locality ?? yer.subAdministrativeArea,
                        yer.administrativeArea].compactMap { $0 }
        var gorulen = Set<String>()
        sonYer = parcalar.filter { gorulen.insert($0).inserted }.prefix(2).joined(separator: ", ")
        if sonYer.isEmpty { sonYer = yer.name ?? "" }
        sonUlke = yer.country ?? ""
        sonCozulen = l
    }

    private func takvimPaketi() -> [String: Any]? {
        guard takvimAcik, EKEventStore.authorizationStatus(for: .event) == .fullAccess else { return nil }
        let tak = Calendar.current
        let bas = tak.startOfDay(for: Date())
        guard let bit = tak.date(byAdding: .day, value: 1, to: bas) else { return nil }
        let olaylar = takvimDeposu.events(matching:
            takvimDeposu.predicateForEvents(withStart: bas, end: bit, calendars: nil))
        let saat = DateFormatter()
        saat.dateFormat = "HH:mm"
        let gun = DateFormatter()
        gun.dateFormat = "yyyy-MM-dd"
        let liste: [[String: Any]] = olaylar
            .sorted { ($0.startDate ?? bas) < ($1.startDate ?? bas) }
            .prefix(12)
            .map { e in
                var d: [String: Any] = ["ad": e.title ?? "(başlıksız)", "tamgun": e.isAllDay]
                if !e.isAllDay, let b = e.startDate { d["bas"] = saat.string(from: b) }
                if !e.isAllDay, let b = e.endDate { d["bit"] = saat.string(from: b) }
                if let y = e.location, !y.isEmpty { d["yer"] = String(y.prefix(60)) }
                if let c = e.calendar?.title { d["takvim"] = c }
                return d
            }
        return ["gun": gun.string(from: bas), "etkinlikler": liste]
    }

    private func cihazPaketi() -> [String: Any]? {
        guard cihazAcik else { return nil }
        let c = UIDevice.current
        c.isBatteryMonitoringEnabled = true
        let isi: String
        switch ProcessInfo.processInfo.thermalState {
        case .nominal: isi = "normal"
        case .fair: isi = "ılık"
        case .serious: isi = "sıcak"
        case .critical: isi = "kritik"
        @unknown default: isi = ""
        }
        var p: [String: Any] = [
            "sarj": c.batteryState == .charging || c.batteryState == .full,
            "dusuk_guc": ProcessInfo.processInfo.isLowPowerModeEnabled,
            "ag": agTuru, "isi": isi,
            "model": DuyuYoneticisi.modelKimligi, "ios": c.systemVersion]
        if c.batteryLevel >= 0 { p["pil"] = Double(c.batteryLevel) }
        return p
    }

    static let modelKimligi: String = {
        var s = utsname()
        uname(&s)
        return withUnsafePointer(to: &s.machine) {
            $0.withMemoryRebound(to: CChar.self, capacity: 1) { String(cString: $0) }
        }
    }()

    // MARK: Gönderi

    /// Açık duyuları topla ve `/v1/duyular`a yaz. Hiçbir anahtar açık değilse hiç
    /// ağa çıkmaz — kapalı duyu sessiz duyudur.
    @discardableResult
    func bildir(zorla: Bool = false) async -> Bool {
        guard konumAcik || takvimAcik || cihazAcik else { return false }
        if gonderiyor { return false }
        if !zorla, Date().timeIntervalSince(sonGonderi) < asgariAra { return false }
        guard let api = GhostAPI.keychaindan() else { return false }
        gonderiyor = true
        defer { gonderiyor = false }

        var govde: [String: Any] = [:]
        if let k = await konumPaketi() { govde["konum"] = k }
        if let t = takvimPaketi() { govde["takvim"] = t }
        if let c = cihazPaketi() { govde["cihaz"] = c }
        guard !govde.isEmpty else { return false }

        do {
            let y = try await api.duyularGonder(govde)
            sonGonderi = Date()
            sunucuAcik = true
            let g = y.guncel ?? []
            sonDurum = g.isEmpty ? "Gönderildi (yeni veri yok)." : "Gönderildi: " + g.joined(separator: ", ")
            return true
        } catch APIHata.sunucu(403, _) {
            // Ghost tarafında /duyular kapat çekilmiş: telefonda izin dursun, veri akmasın.
            sunucuAcik = false
            sonDurum = "Ghost duyuları kapatmış (/duyular ac ile açılır)."
            return false
        } catch {
            sonDurum = "Gönderilemedi: " + error.localizedDescription
            return false
        }
    }

    // MARK: Arka plan görevi

    /// Uygulama açılmadan da takvimin tazelenmesi için: iOS uygun gördükçe koşar
    /// (garanti yok, en iyi çaba). Konum zaten SLC ile kendi uyandırır.
    static func arkaPlaniKaydet() {
        BGTaskScheduler.shared.register(forTaskWithIdentifier: arkaPlanKimligi, using: nil) { gorev in
            let is_ = Task { @MainActor in
                await DuyuYoneticisi.paylasilan.bildir(zorla: true)
                await DuyuYoneticisi.paylasilan.bolgeleriTazele()
                await EllerYoneticisi.paylasilan.kuyrugaBak()
                DuyuYoneticisi.paylasilan.arkaPlanPlanla()
                gorev.setTaskCompleted(success: true)
            }
            gorev.expirationHandler = { is_.cancel() }
        }
    }

    func arkaPlanPlanla() {
        guard konumAcik || takvimAcik || cihazAcik else { return }
        let istek = BGAppRefreshTaskRequest(identifier: DuyuYoneticisi.arkaPlanKimligi)
        istek.earliestBeginDate = Date(timeIntervalSinceNow: 3600)
        try? BGTaskScheduler.shared.submit(istek)
    }
}

// MARK: - Bölgeler (coğrafi tetik, Faz 6)

extension DuyuYoneticisi {
    /// Ghost'un tanımladığı daireleri iOS'a bölge olarak kaydet. Bölge izlemesi
    /// **"Her zaman" konum izni ister**; yalnız "kullanırken" verildiyse iOS
    /// tetiklemez — o durumda sessizce hiçbir şey kurmayız (yalancı kurulum yok).
    func bolgeleriTazele() async {
        guard konumAcik, konumcu.authorizationStatus == .authorizedAlways,
              let api = GhostAPI.keychaindan(),
              let z = try? await api.bolgeler() else { return }
        let seq = z.seq ?? 0
        if seq == bolgeSeq { return }
        for r in konumcu.monitoredRegions { konumcu.stopMonitoring(for: r) }
        for b in z.bolgeler where (b.giris ?? true) || (b.cikis ?? false) {
            let bolge = CLCircularRegion(
                center: CLLocationCoordinate2D(latitude: b.enlem, longitude: b.boylam),
                radius: b.yaricap, identifier: b.ad)
            bolge.notifyOnEntry = b.giris ?? true
            bolge.notifyOnExit = b.cikis ?? false
            konumcu.startMonitoring(for: bolge)
        }
        bolgeSeq = seq
        sonDurum = z.bolgeler.isEmpty ? "Bölge tanımı yok." : "\(z.bolgeler.count) bölge izleniyor."
    }

    /// Giriş/çıkış haberi. Soğuma ve bilinmeyen bölge süzgeci SUNUCUDA — telefon
    /// olduğu gibi bildirir, kararı Ghost verir.
    fileprivate func bolgeHaberi(_ ad: String, _ olay: String) {
        Task {
            guard let api = GhostAPI.keychaindan() else { return }
            try? await api.bolgeOlay(ad: ad, olay: olay)
            await bildir(zorla: true)      // varış anının konumu da gitsin
        }
    }
}

// MARK: - CLLocationManagerDelegate

extension DuyuYoneticisi: CLLocationManagerDelegate {
    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let durum = manager.authorizationStatus
        Task { @MainActor in
            switch durum {
            case .authorizedWhenInUse:
                // Birinci soru geçildi; "her zaman" olmadan uygulama kapalıyken konum gelmez.
                manager.requestAlwaysAuthorization()
                manager.startMonitoringSignificantLocationChanges()
            case .authorizedAlways:
                manager.startMonitoringSignificantLocationChanges()
                bolgeSeq = -1                     // izin yeni geldi: bölgeleri kur
                await bolgeleriTazele()
            case .denied, .restricted:
                Ayar.konum.yaz(false); yansit()
                sonDurum = "Konum izni yok — iOS Ayarlar › Ghost'tan verilebilir."
            default:
                break
            }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let l = locations.last else { return }
        Task { @MainActor in
            sonKonum = l
            // SLC arka planda uyandırdıysa gönderiyi geciktirme: yer değişimi haberin ta kendisi.
            await bildir(zorla: true)
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didEnterRegion region: CLRegion) {
        let ad = region.identifier
        Task { @MainActor in bolgeHaberi(ad, "giris") }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didExitRegion region: CLRegion) {
        let ad = region.identifier
        Task { @MainActor in bolgeHaberi(ad, "cikis") }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        NSLog("duyu konum hatası: %@", error.localizedDescription)
    }
}
