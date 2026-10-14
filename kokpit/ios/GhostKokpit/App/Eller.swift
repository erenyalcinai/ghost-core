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
                yerel = try uygula(g)
                ok = true
                yazilan += 1
            } catch {
                mesaj = error.localizedDescription
            }
            _ = try? await api.gorevSonuc(id: g.id, ok: ok, mesaj: mesaj, yerelID: yerel)
        }
        if yazilan > 0 { sonDurum = "\(yazilan) görev telefona yazıldı." }
    }

    private func uygula(_ g: Gorev) throws -> String {
        switch g.tur {
        case "hatirlatici": return try hatirlaticiYaz(g.veri)
        case "etkinlik": return try etkinlikYaz(g.veri)
        default: throw EllerHata.bilinmeyen(g.tur)
        }
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

    var errorDescription: String? {
        switch self {
        case .bilinmeyen(let t): return "bilinmeyen görev türü: \(t)"
        case .listeYok(let m): return m
        }
    }
}
