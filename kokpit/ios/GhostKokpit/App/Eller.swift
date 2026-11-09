import Foundation
import EventKit
import UIKit

/// Eller (Faz 6) — Ghost'un telefona YAZDIĞI işler. Faz 5 duyularla tek yön kurulmuştu:
/// telefon anlatır, Ghost dinler. Burası ters yön.
///
/// Ghost sunucuda bir görev bırakır (`/v1/gorevler`), bu sınıf onu iOS'un kendi
/// sistemine yazar ve **sonucu geri bildirir**:
/// - `hatirlatici` → EKReminder (alarmlı; Ghost, VPS ya da ağ olmasa da çalar)
/// - `etkinlik`    → EKEvent (takvimde, 15 dk önce uyarı)
/// - `hatirlatici_sil` / `etkinlik_sil` → Ghost'un DAHA ÖNCE yazdığını kaldırır (2026-11-04). Kayıt
///   yazarken geri bildirdiğimiz kimlikle bulunur; kimlik eşitlemede değişmişse yalnız AYNI başlık + AYNI
///   dakikadaki kayıt aranır — sahibin elle kurduğu bir şeye dokunulmaz. Bulunamazsa ("zaten-yok") iş
///   tamam sayılır: Sahip silmiş ya da tamamlamıştır.
///
/// Neden sonucu geri bildirmek şart: "gönderdim" ile "telefona yazıldı" aynı şey
/// değildir. Varsayılıp geçilirse Sahip olmayan bir hatırlatıcıya güvenir — sessiz
/// başarısızlık, hatanın en kötü türü.
///
/// Tetikler (üçü de yedekli, hiçbiri tek başına garanti değil): sessiz push,
/// uygulama öne gelince, arka plan tazeleme görevi.
@MainActor
final class EllerYoneticisi: ObservableObject {
    static let paylasilan = EllerYoneticisi()
    static let anahtar = "duyu.eller"

    @Published private(set) var acik = UserDefaults.standard.bool(forKey: anahtar)
    @Published private(set) var sonDurum = ""

    private let depo = EKEventStore()
    private var calisiyor = false

    private init() {}

    /// Ayarlar'daki anahtar. Açarken hem hatırlatıcı hem takvim YAZMA izni istenir;
    /// biri bile verilmezse anahtar kendini geri kapatır (yalancı "açık" hâli yok).
    func degistir(_ yeni: Bool) {
        UserDefaults.standard.set(yeni, forKey: EllerYoneticisi.anahtar)
        acik = yeni
        guard yeni else { sonDurum = ""; return }
        Task {
            do {
                let h = try await depo.requestFullAccessToReminders()
                let t = try await depo.requestFullAccessToEvents()
                if !h || !t {
                    kapat("izin verilmedi (" + (h ? "takvim" : "hatırlatıcı") + " reddedildi)")
                    return
                }
                sonDurum = "Hazır — Ghost hatırlatıcı ve etkinlik yazabilir."
                await kuyrugaBak()
            } catch {
                kapat("izin alınamadı: " + error.localizedDescription)
            }
        }
    }

    private func kapat(_ sebep: String) {
        UserDefaults.standard.set(false, forKey: EllerYoneticisi.anahtar)
        acik = false
        sonDurum = sebep
    }

    // MARK: Kuyruk

    /// Bekleyen görevleri çek, sırayla uygula, her birinin sonucunu yaz.
    /// Sunucu tarafı yalnız 'bekliyor' görevini sonuçlandırır; bu çağrı iki kez
    /// koşsa bile aynı hatırlatıcı iki kez yazılmaz.
    func kuyrugaBak() async {
        guard acik, !calisiyor, let api = GhostAPI.keychaindan() else { return }
        calisiyor = true
        defer { calisiyor = false }
        guard let zarf = try? await api.gorevler(), !zarf.gorevler.isEmpty else { return }
        var yazilan = 0
        for g in zarf.gorevler {
            var ok = false
            var mesaj = ""
            var yerel = ""
            do {
                yerel = try await uygula(g)
                ok = true
                yazilan += 1
            } catch {
                mesaj = error.localizedDescription
            }
            _ = try? await api.gorevSonuc(id: g.id, ok: ok, mesaj: mesaj, yerelID: yerel)
        }
        if yazilan > 0 { sonDurum = "\(yazilan) görev telefona yazıldı." }
    }

    private func uygula(_ g: Gorev) async throws -> String {
        switch g.tur {
        case "hatirlatici": return try hatirlaticiYaz(g.veri)
        case "etkinlik": return try etkinlikYaz(g.veri)
        case "hatirlatici_sil": return try await hatirlaticiSil(g.veri)
        case "etkinlik_sil": return try etkinlikSil(g.veri)
        default: throw EllerHata.bilinmeyen(g.tur)
        }
    }

    private func hatirlaticiSil(_ v: GorevVeri) async throws -> String {
        guard let id = v.yerelId, !id.isEmpty else { throw EllerHata.kimlikYok }
        if let r = depo.calendarItem(withIdentifier: id) as? EKReminder {
            try depo.remove(r, commit: true)
            return id
        }
        // Kimlik iCloud eşitlemesinde değişebilir: yalnız aynı başlık + aynı dakikadaki açık hatırlatıcı.
        guard let h = v.hedefTs else { return "zaten-yok" }
        let ne = Date(timeIntervalSince1970: h)
        let p = depo.predicateForIncompleteReminders(
            withDueDateStarting: ne.addingTimeInterval(-60), ending: ne.addingTimeInterval(60), calendars: nil)
        let baslik = v.ad
        let kimlikler: [String] = await withCheckedContinuation { devam in
            depo.fetchReminders(matching: p) { liste in
                devam.resume(returning: (liste ?? []).filter { $0.title == baslik }.map { $0.calendarItemIdentifier })
            }
        }
        let eslesen = kimlikler.compactMap { depo.calendarItem(withIdentifier: $0) as? EKReminder }
        guard !eslesen.isEmpty else { return "zaten-yok" }
        for r in eslesen { try depo.remove(r, commit: false) }
        try depo.commit()
        return eslesen[0].calendarItemIdentifier
    }

    private func etkinlikSil(_ v: GorevVeri) throws -> String {
        guard let id = v.yerelId, !id.isEmpty else { throw EllerHata.kimlikYok }
        if let e = depo.event(withIdentifier: id) {
            try depo.remove(e, span: .thisEvent, commit: true)
            return id
        }
        guard let h = v.hedefTs else { return "zaten-yok" }
        let ne = Date(timeIntervalSince1970: h)
        let p = depo.predicateForEvents(withStart: ne.addingTimeInterval(-60), end: ne.addingTimeInterval(60), calendars: nil)
        let eslesen = depo.events(matching: p).filter { $0.title == v.ad && abs($0.startDate.timeIntervalSince(ne)) < 60 }
        guard !eslesen.isEmpty else { return "zaten-yok" }
        for e in eslesen { try depo.remove(e, span: .thisEvent, commit: false) }
        try depo.commit()
        return eslesen[0].eventIdentifier ?? id
    }

    private func hatirlaticiYaz(_ v: GorevVeri) throws -> String {
        guard let liste = depo.defaultCalendarForNewReminders() else {
            throw EllerHata.listeYok("Hatırlatıcılar uygulamasında varsayılan liste yok")
        }
        let ne = Date(timeIntervalSince1970: v.ts)
        let r = EKReminder(eventStore: depo)
        r.title = v.ad
        r.calendar = liste
        if let n = v.not, !n.isEmpty { r.notes = n }
        r.dueDateComponents = Calendar.current.dateComponents(
            [.year, .month, .day, .hour, .minute], from: ne)
        r.addAlarm(EKAlarm(absoluteDate: ne))     // dueDate tek başına ÇALMAZ, alarm şart
        try depo.save(r, commit: true)
        return r.calendarItemIdentifier
    }

    private func etkinlikYaz(_ v: GorevVeri) throws -> String {
        guard let tak = depo.defaultCalendarForNewEvents else {
            throw EllerHata.listeYok("Yazılabilir varsayılan takvim yok")
        }
        let e = EKEvent(eventStore: depo)
        e.title = v.ad
        e.calendar = tak
        e.startDate = Date(timeIntervalSince1970: v.ts)
        e.endDate = Date(timeIntervalSince1970: v.bitTs ?? (v.ts + 3600))
        if let y = v.yer, !y.isEmpty { e.location = y }
        if let n = v.not, !n.isEmpty { e.notes = n }
        e.addAlarm(EKAlarm(relativeOffset: -900))
        try depo.save(e, span: .thisEvent, commit: true)
        return e.eventIdentifier ?? ""
    }
}

enum EllerHata: LocalizedError {
    case bilinmeyen(String)
    case listeYok(String)
    case kimlikYok

    var errorDescription: String? {
        switch self {
        case .bilinmeyen(let t): return "bilinmeyen görev türü: \(t)"
        case .listeYok(let m): return m
        case .kimlikYok: return "silinecek kaydın kimliği yok"
        }
    }
}
