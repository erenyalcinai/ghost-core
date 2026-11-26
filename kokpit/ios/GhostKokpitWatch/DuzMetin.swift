import Foundation

/// Ghost'un Markdown cevabını bilekte okunur düz metne çevirir (yalnız Foundation; SwiftUI yok → Mac'te
/// `swiftc` ile tek başına sınanabilir). Kural: kalın/eğik/kod işaretleri düşer, madde "• " olur,
/// tablo ve kod/diff bloğu bileğe GİRMEZ — yerine tek satır "telefonda" işareti konur.
/// Sunucudaki `apns.bildirim_govdesi` ile aynı temizlik; bildirimde gördüğün ilk paragraf burada da aynıdır.
enum DuzMetin {
    struct Parca: Identifiable, Equatable {
        let id: Int
        let metin: String
        /// true → içerik saatte gösterilmiyor (tablo, kod, diff); metin yalnız işarettir.
        let telefonda: Bool
    }

    private struct Ham {
        var metin: String
        var telefonda: Bool
        var baslik: Bool
    }

    static let tabloIsareti = "▦ tablo — telefonda"
    static let kodIsareti = "▤ kod — telefonda"
    static let diffIsareti = "▤ diff — telefonda"

    /// Cevabı paragraflara böl. İlk öğe "ilk paragraf"tır (yalnız başlıksa altındaki paragrafla birleşir).
    static func parcala(_ ham: String) -> [Parca] {
        var parcalar: [Ham] = []
        var birikim: [String] = []
        var birikimBaslik = true
        var kodda = false
        var kodDiff = false
        var tabloda = false

        func kapat() {
            if !birikim.isEmpty {
                parcalar.append(Ham(metin: birikim.joined(separator: "\n"), telefonda: false, baslik: birikimBaslik))
            }
            birikim = []
            birikimBaslik = true
        }

        for hamSatir in ham.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n") {
            let s = hamSatir.trimmingCharacters(in: .whitespaces)
            if s.hasPrefix("```") {
                if kodda {
                    parcalar.append(Ham(metin: kodDiff ? diffIsareti : kodIsareti, telefonda: true, baslik: false))
                    kodda = false
                } else {
                    kapat()
                    tabloda = false
                    kodda = true
                    kodDiff = s.lowercased().hasPrefix("```diff") || s.lowercased().hasPrefix("```patch")
                }
                continue
            }
            if kodda { continue }
            if s.hasPrefix("|") {
                if !tabloda {
                    kapat()
                    parcalar.append(Ham(metin: tabloIsareti, telefonda: true, baslik: false))
                    tabloda = true
                }
                continue
            }
            tabloda = false
            if s.isEmpty {
                kapat()
                continue
            }
            if ayracMi(s) { continue }
            var satir = s
            if let b = baslikMetni(satir) {
                satir = b
            } else {
                birikimBaslik = false
            }
            if satir.hasPrefix(">") {
                satir = String(satir.dropFirst()).trimmingCharacters(in: .whitespaces)
            }
            if let m = maddeMetni(satir) { satir = "• " + m }
            satir = satirIci(satir)
            if !satir.isEmpty { birikim.append(satir) }
        }
        if kodda {                                         // kapanmamış blok
            parcalar.append(Ham(metin: kodDiff ? diffIsareti : kodIsareti, telefonda: true, baslik: false))
        }
        kapat()

        // Yalnız başlıktan oluşan ilk paragraf, altındaki metinle birlikte okunur.
        if parcalar.count >= 2, parcalar[0].baslik, !parcalar[0].telefonda, !parcalar[1].telefonda {
            let birlesik = Ham(metin: parcalar[0].metin + "\n" + parcalar[1].metin, telefonda: false, baslik: false)
            parcalar.removeFirst(2)
            parcalar.insert(birlesik, at: 0)
        }
        return parcalar.enumerated().map { Parca(id: $0.offset, metin: $0.element.metin, telefonda: $0.element.telefonda) }
    }

    /// Listede gösterilen özet: ilk paragraf + devamı var mı.
    static func ilkParagraf(_ ham: String) -> (metin: String, devamVar: Bool) {
        let p = parcala(ham)
        guard let ilk = p.first else { return ("", false) }
        return (ilk.metin, p.count > 1)
    }

    // MARK: Satır yardımcıları

    private static func ayracMi(_ s: String) -> Bool {
        guard s.count >= 3, let ilk = s.first, "-=*_".contains(ilk) else { return false }
        return s.allSatisfy { $0 == ilk }
    }

    private static func baslikMetni(_ s: String) -> String? {
        guard s.hasPrefix("#") else { return nil }
        let diyez = s.prefix { $0 == "#" }
        guard diyez.count <= 6 else { return nil }
        let kalan = s.dropFirst(diyez.count)
        guard kalan.first == " " else { return nil }
        return kalan.trimmingCharacters(in: .whitespaces)
    }

    private static func maddeMetni(_ s: String) -> String? {
        guard s.count > 2, let ilk = s.first, "-*+".contains(ilk) else { return nil }
        let ikinci = s[s.index(after: s.startIndex)]
        guard ikinci == " " else { return nil }
        return String(s.dropFirst(2)).trimmingCharacters(in: .whitespaces)
    }

    /// Satır içi işaretler (sıra önemli: görsel/bağlantı → kalın → eğik → kod).
    private static let desenler: [(String, String)] = [
        ("!\\[([^\\]]*)\\]\\([^)]*\\)", "$1"),
        ("\\[([^\\]]+)\\]\\([^)]*\\)", "$1"),
        ("\\*\\*(.+?)\\*\\*", "$1"),
        ("__(.+?)__", "$1"),
        ("~~(.+?)~~", "$1"),
        ("(?<![\\w*])\\*(?=\\S)([^*\\n]+?)(?<=\\S)\\*(?![\\w*])", "$1"),
        ("`([^`\\n]*)`", "$1"),
    ]

    static func satirIci(_ s: String) -> String {
        var t = s
        for (desen, yerine) in desenler {
            t = t.replacingOccurrences(of: desen, with: yerine, options: .regularExpression)
        }
        while t.contains("  ") { t = t.replacingOccurrences(of: "  ", with: " ") }
        return t.trimmingCharacters(in: .whitespaces)
    }
}
