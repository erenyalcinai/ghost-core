import SwiftUI

/// Ghost'un cevabı HAM Markdown gelir (Telegram'a giden kopya düzleştirilir, uygulama
/// kendi çizer). Küçük ve dayanıklı: kod çiti → tek parça monospaced blok; `#` başlık →
/// kalın satır; `- ` madde → •; geri kalanı satır içi Markdown (kalın, eğik, `kod`, bağlantı).
/// Çözümleme başarısız olursa düz metin — asla boş ya da çökük görünmez.
struct MarkdownMetin: View {
    let metin: String
    var renk: Color = Renk.metin

    private enum Blok: Hashable {
        case kod(String)
        case baslik(String)
        case paragraf(String)
        case tablo([String], [[String]])
        case alinti(String)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(Array(bloklar.enumerated()), id: \.offset) { _, b in
                switch b {
                case .kod(let k):
                    ScrollView(.horizontal, showsIndicators: false) {
                        Text(k).font(.caption.monospaced()).foregroundStyle(renk)
                            .padding(10)
                    }
                    .background(Renk.zemin.opacity(0.6), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                case .baslik(let t):
                    Text(satirIci(t)).font(.headline).foregroundStyle(renk)
                case .paragraf(let t):
                    Text(satirIci(t)).foregroundStyle(renk)
                case .alinti(let t):
                    HStack(alignment: .top, spacing: 8) {
                        Rectangle().fill(Renk.ikincil.opacity(0.6)).frame(width: 3)
                        Text(satirIci(t)).foregroundStyle(renk.opacity(0.85))
                    }
                case .tablo(let bas, let satirlar):
                    ScrollView(.horizontal, showsIndicators: false) {
                        Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 5) {
                            GridRow {
                                ForEach(Array(bas.enumerated()), id: \.offset) { _, h in
                                    Text(satirIci(h)).font(.caption.weight(.semibold)).foregroundStyle(Renk.ikincil)
                                }
                            }
                            Divider().overlay(Renk.ikincil.opacity(0.3))
                            ForEach(Array(satirlar.enumerated()), id: \.offset) { _, r in
                                GridRow {
                                    ForEach(Array(r.enumerated()), id: \.offset) { _, h in
                                        Text(satirIci(h)).font(.subheadline.monospacedDigit()).foregroundStyle(renk)
                                    }
                                }
                            }
                        }
                        .padding(8)
                    }
                    .background(Renk.zemin.opacity(0.6), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                }
            }
        }
    }

    /// "| a | b |" satırını hücrelere böl.
    private static func hucreler(_ s: String) -> [String] {
        var t = s.trimmingCharacters(in: .whitespaces)
        if t.hasPrefix("|") { t.removeFirst() }
        if t.hasSuffix("|") { t.removeLast() }
        return t.components(separatedBy: "|").map { $0.trimmingCharacters(in: .whitespaces) }
    }

    private static func tabloAyraci(_ s: String) -> Bool {
        let t = s.trimmingCharacters(in: .whitespaces)
        guard t.contains("-") else { return false }
        return t.allSatisfy { "|-: ".contains($0) }
    }

    private var bloklar: [Blok] {
        var out: [Blok] = []
        var kod: [String]? = nil
        var paragraf: [String] = []
        func paragrafiKapat() {
            let t = paragraf.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
            if !t.isEmpty { out.append(.paragraf(t)) }
            paragraf = []
        }
        let satirlar = metin.components(separatedBy: "\n")
        var i = 0
        while i < satirlar.count {
            let ham = satirlar[i]
            let s = ham.trimmingCharacters(in: .whitespaces)
            i += 1
            // Tablo: "| a | b |" + ayraç satırı → grid (sahnede de var; sohbette ham çubuk görünmesin).
            if kod == nil, s.hasPrefix("|"), i < satirlar.count, Self.tabloAyraci(satirlar[i]) {
                paragrafiKapat()
                let bas = Self.hucreler(s)
                var rows: [[String]] = []
                i += 1
                while i < satirlar.count, satirlar[i].trimmingCharacters(in: .whitespaces).hasPrefix("|") {
                    let r = Self.hucreler(satirlar[i])
                    rows.append(Array((r + Array(repeating: "", count: bas.count)).prefix(bas.count)))
                    i += 1
                }
                out.append(.tablo(bas, rows))
                continue
            }
            if kod == nil, s.hasPrefix(">") {
                paragrafiKapat()
                var alinti = [String(s.dropFirst()).trimmingCharacters(in: .whitespaces)]
                while i < satirlar.count, satirlar[i].trimmingCharacters(in: .whitespaces).hasPrefix(">") {
                    alinti.append(String(satirlar[i].trimmingCharacters(in: .whitespaces).dropFirst()).trimmingCharacters(in: .whitespaces))
                    i += 1
                }
                out.append(.alinti(alinti.joined(separator: "\n")))
                continue
            }
            if s.hasPrefix("```") {
                if var k = kod {                       // çit kapanıyor
                    while k.last?.isEmpty == true { k.removeLast() }
                    out.append(.kod(k.joined(separator: "\n")))
                    kod = nil
                } else {                               // çit açılıyor
                    paragrafiKapat()
                    kod = []
                }
                continue
            }
            if kod != nil { kod?.append(ham); continue }
            if s.hasPrefix("#") {
                paragrafiKapat()
                out.append(.baslik(s.drop(while: { $0 == "#" }).trimmingCharacters(in: .whitespaces)))
                continue
            }
            if s.isEmpty { paragrafiKapat(); continue }
            var satir = ham
            if let r = satir.range(of: #"^\s*[-*•]\s+"#, options: .regularExpression) {
                satir.replaceSubrange(r, with: "•  ")
            }
            paragraf.append(satir)
        }
        if let k = kod { out.append(.kod(k.joined(separator: "\n"))) }  // kapanmamış çit
        paragrafiKapat()
        return out
    }

    /// Satır içi Markdown; satır sonları korunur. Çözülemezse düz metin.
    private func satirIci(_ t: String) -> AttributedString {
        var o = AttributedString.MarkdownParsingOptions()
        o.interpretedSyntax = .inlineOnlyPreservingWhitespace
        o.failurePolicy = .returnPartiallyParsedIfPossible
        return (try? AttributedString(markdown: t, options: o)) ?? AttributedString(t)
    }
}
