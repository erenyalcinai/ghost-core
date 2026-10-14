import SwiftUI

/// ⋯ menüsünden açılan sayfalar. Sekme değil: seyrek bakılan ya da bir kez bakılıp
/// kapatılan ekranlar. Sheet'i RootView sunar, buradaki menü yalnız `oturum.sayfa` yazar.
enum Sayfa: String, Identifiable, Hashable {
    case kararlar, akis, siteler, konus
    var id: String { rawValue }
}

/// Sağ üstteki "üç nokta". Her sekmenin ortak aracı — Komutlar ve Ayarlar alt bara
/// çıktığı için burada yalnız Kararlar, Akış ve Siteler kaldı.
struct AracMenusu: ViewModifier {
    @EnvironmentObject var oturum: Oturum

    func body(content: Content) -> some View {
        content.toolbar {
            ToolbarItem(placement: .ghostSag) {
                Menu {
                    // Konuş (2026-09-26): ses ajanı — Ghost'un hızlı sesli yüzü, gerçek zamanlı.
                    Button { oturum.sayfa = .konus } label: { Label("Echo", systemImage: "waveform.circle") }
                    Divider()
                    Button { oturum.sayfa = .kararlar } label: {
                        Label(oturum.bekleyenOnay > 0 ? "Kararlar (\(oturum.bekleyenOnay))" : "Kararlar",
                              systemImage: "checkmark.seal")
                    }
                    Button { oturum.sayfa = .akis } label: { Label("Akış", systemImage: "list.bullet.rectangle") }
                    Divider()
                    Button { oturum.sayfa = .siteler } label: { Label("Siteler", systemImage: "globe") }
                } label: {
                    Image(systemName: "ellipsis.circle")
                        .overlay(alignment: .topTrailing) {
                            // Bekleyen onay sekme rozetini kaybetti; nokta onun yerine geçer.
                            if oturum.bekleyenOnay > 0 {
                                Circle().fill(Renk.uyari).frame(width: 8, height: 8).offset(x: 4, y: -2)
                            }
                        }
                        .accessibilityLabel(oturum.bekleyenOnay > 0
                                            ? "Menü, \(oturum.bekleyenOnay) karar bekliyor" : "Menü")
                }
                .menuIndicator(.hidden)
            }
        }
    }
}

extension View {
    func aracMenusu() -> some View { modifier(AracMenusu()) }
}

/// ⋯ sayfasının içeriği. Hepsi kendi NavigationStack'ini taşır ve "Kapat" düğmesi koyar.
struct SayfaGorunum: View {
    @EnvironmentObject var oturum: Oturum
    let sayfa: Sayfa

    var body: some View {
        switch sayfa {
        case .kararlar: KararlarView()
        case .akis: AkisView()
        case .siteler: SitelerView()
        case .konus: KonusView()
        }
    }
}

/// Sheet'lerin ortak "Kapat" düğmesi — sayfayı kökten kapatır.
struct KapatDugmesi: ToolbarContent {
    @EnvironmentObject var oturum: Oturum
    var body: some ToolbarContent {
        ToolbarItem(placement: .ghostSol) {
            #if os(iOS)
            Button("Kapat") { oturum.sayfa = nil }
            #else
            EmptyView()     // Mac'te sayfalar kenar çubuğunda; kapatacak sheet yok
            #endif
        }
    }
}
