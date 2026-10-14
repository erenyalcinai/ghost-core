import SwiftUI

/// Ghost marka paleti (koyu). Tek vurgu rengi yok; durum yalnız küçük nokta/şeritle.
enum Renk {
    static let zemin = Color(hex: 0x0B0C0E)
    static let kart = Color(hex: 0x15171A)
    static let kartUst = Color(hex: 0x1D1F23)
    static let metin = Color(hex: 0xEDEAE4)
    static let ikincil = Color(hex: 0x5C5A57)
    static let iyi = Color(hex: 0xEDEAE4)
    static let uyari = Color(hex: 0xD9B45B)
    static let kritik = Color(hex: 0xC9463D)

    /// "iyi|uyari|kritik|bilinmiyor" ve benzeri durum metnini renge çevir.
    static func durum(_ s: String?) -> Color {
        switch (s ?? "").lowercased() {
        case "iyi", "ok", "bitti", "tamam": return iyi
        case "uyari", "uyarı", "bekliyor", "bekleyen": return uyari
        case "kritik", "hata", "basarisiz", "başarısız": return kritik
        default: return ikincil
        }
    }

    static func durum(ok: Bool?) -> Color {
        guard let ok else { return ikincil }
        return ok ? iyi : kritik
    }

    /// Karar sınıfı rozeti rengi.
    static func sinif(_ s: String?) -> Color {
        switch (s ?? "").lowercased() {
        case "permanent": return kritik
        case "spend": return uyari
        case "reversible": return metin
        default: return ikincil
        }
    }
}

extension Color {
    init(hex: UInt32) {
        self.init(.sRGB,
                  red: Double((hex >> 16) & 0xFF) / 255,
                  green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255,
                  opacity: 1)
    }
}

/// Yuvarlatılmış kart: derinlik yok, yalnız ince zemin farkı.
struct KartBicimi: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Renk.kart, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

extension View {
    func kart() -> some View { modifier(KartBicimi()) }

    /// Liste/scroll zeminini marka rengine çek.
    func ghostZemin() -> some View {
        self.scrollContentBackground(.hidden).background(Renk.zemin)
    }
}

struct Nokta: View {
    var renk: Color
    var boyut: CGFloat = 8
    var body: some View { Circle().fill(renk).frame(width: boyut, height: boyut) }
}

struct Rozet: View {
    var metin: String
    var renk: Color
    var body: some View {
        Text(metin.uppercased())
            .font(.caption2.weight(.semibold))
            .foregroundStyle(renk)
            .padding(.horizontal, 7).padding(.vertical, 3)
            .overlay(RoundedRectangle(cornerRadius: 6).stroke(renk.opacity(0.5), lineWidth: 1))
    }
}

struct BolumBaslik: View {
    var metin: String
    var body: some View {
        Text(metin)
            .font(.footnote.weight(.medium))
            .foregroundStyle(Renk.ikincil)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 6)
    }
}
