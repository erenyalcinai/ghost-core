import AppIntents
import Foundation

/// Siri / Kısayollar / Action düğmesi: uygulamayı açmadan Ghost'a konuş.
/// Kimlik Keychain'den (uygulama süreci içinde koşar, arka planda).
struct GhostaSoyle: AppIntent {
    static var title: LocalizedStringResource = "Ghost'a söyle"
    static var description = IntentDescription("Ghost'a bir mesaj ya da komut gönderir; cevap uygulamada ve Telegram'da görünür.")
    static var openAppWhenRun = false

    @Parameter(title: "Mesaj", requestValueDialog: "Ghost'a ne söyleyeyim?")
    var metin: String

    static var parameterSummary: some ParameterSummary {
        Summary("Ghost'a \(\.$metin) söyle")
    }

    func perform() async throws -> some IntentResult & ProvidesDialog {
        guard let api = Oturum.hazirAPI() else {
            return .result(dialog: "Ghost eşlenmemiş. Önce uygulamayı aç.")
        }
        let m = metin.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !m.isEmpty else { return .result(dialog: "Boş mesaj göndermedim.") }
        do {
            try await api.mesajGonder(m)
            return .result(dialog: "Ghost'a iletildi.")
        } catch {
            return .result(dialog: "Gönderilemedi: \(error.localizedDescription)")
        }
    }
}

struct GhostDurum: AppIntent {
    static var title: LocalizedStringResource = "Ghost durumu"
    static var description = IntentDescription("Nabız özetini söyler: her şey yolunda mı, onay bekleyen var mı.")
    static var openAppWhenRun = false

    func perform() async throws -> some IntentResult & ProvidesDialog {
        guard let api = Oturum.hazirAPI() else {
            return .result(dialog: "Ghost eşlenmemiş.")
        }
        do {
            let n = try await api.nabiz()
            var p: [String] = []
            switch (n.ozet ?? "").lowercased() {
            case "iyi": p.append("Her şey yolunda.")
            case "uyari", "uyarı": p.append("Uyarı var.")
            case "kritik": p.append("Kritik durum var.")
            default: p.append("Durum bilinmiyor.")
            }
            if let b = n.isler?.bekleyenOnay, b > 0 { p.append("\(b) onay bekliyor.") }
            if let a = n.isler?.aktif, a > 0 { p.append("\(a) iş çalışıyor.") }
            if n.freeze == true { p.append("Freeze aktif.") }
            return .result(dialog: IntentDialog(stringLiteral: p.joined(separator: " ")))
        } catch {
            return .result(dialog: "Ghost'a ulaşamadım: \(error.localizedDescription)")
        }
    }
}

struct GhostKisayollar: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: GhostaSoyle(),
                    phrases: ["\(.applicationName)'a söyle", "\(.applicationName) mesaj gönder"],
                    shortTitle: "Ghost'a söyle", systemImageName: "mic.fill")
        AppShortcut(intent: GhostDurum(),
                    phrases: ["\(.applicationName) durumu", "\(.applicationName) nasıl"],
                    shortTitle: "Durum", systemImageName: "waveform.path.ecg")
    }
}
