import SwiftUI

/// Mac kökü: kenar çubuğu + içerik. Telefondaki beş sekme ve ⋯ menüsündeki üç sayfa
/// burada tek listede (geniş ekranda gizlemeye gerek yok). Seçim `oturum.sekme` /
/// `oturum.sayfa` üzerinden gider ki bildirimler, ⌘ kısayolları ve görünümlerin
/// kendi "oturum.sayfa = .kararlar" refleksleri aynı yere yazsın.
enum MacBolum: Hashable, CaseIterable, Identifiable {
    case nabiz, komutlar, sohbet, sahne, kararlar, akis, siteler, ayarlar
    var id: Self { self }

    var baslik: String {
        switch self {
        case .nabiz: return "Nabız"
        case .komutlar: return "Komutlar"
        case .sohbet: return "Sohbet"
        case .sahne: return "Masa"
        case .kararlar: return "Kararlar"
        case .akis: return "Akış"
        case .siteler: return "Siteler"
        case .ayarlar: return "Ayarlar"
        }
    }

    var simge: String {
        switch self {
        case .nabiz: return "waveform.path.ecg"
        case .komutlar: return "terminal"
        case .sohbet: return "bubble.left.and.bubble.right"
        case .sahne: return "square.grid.2x2"
        case .kararlar: return "checkmark.seal"
        case .akis: return "list.bullet.rectangle"
        case .siteler: return "globe"
        case .ayarlar: return "gearshape"
        }
    }

    static let ana: [MacBolum] = [.nabiz, .komutlar, .sohbet, .sahne]
    static let sayfalar: [MacBolum] = [.kararlar, .akis, .siteler]

    init(sekme: Sekme, sayfa: Sayfa?) {
        if let p = sayfa {
            switch p {
            case .kararlar: self = .kararlar
            case .akis: self = .akis
            case .siteler: self = .siteler
            case .konus: self = .sohbet      // Echo (2026-09-26) Mac'te ayrı bölüm değil; sohbet yanında açılır
            }
            return
        }
        switch sekme {
        case .nabiz: self = .nabiz
        case .komutlar: self = .komutlar
        case .sohbet: self = .sohbet
        case .sahne: self = .sahne
        case .ayarlar: self = .ayarlar
        }
    }
}

struct MacRootView: View {
    @EnvironmentObject var oturum: Oturum

    var body: some View {
        Group {
            if oturum.esli {
                govde
            } else {
                EslesmeView().frame(maxWidth: 520)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(Renk.zemin)
            }
        }
        .preferredColorScheme(.dark)
        .tint(Renk.metin)
    }

    /// Seçim iki değişkenden türer, yazınca ikisine dağılır.
    private var secim: Binding<MacBolum?> {
        Binding(
            get: { MacBolum(sekme: oturum.sekme, sayfa: oturum.sayfa) },
            set: { yeni in
                guard let yeni else { return }
                switch yeni {
                case .nabiz: oturum.sayfa = nil; oturum.sekme = .nabiz
                case .komutlar: oturum.sayfa = nil; oturum.sekme = .komutlar
                case .sohbet:
                    if oturum.sayfa == nil && oturum.sekme == .sohbet {
                        NotificationCenter.default.post(name: RootView.sohbetEnAlta, object: nil)
                    }
                    oturum.sayfa = nil; oturum.sekme = .sohbet
                case .sahne: oturum.sayfa = nil; oturum.sekme = .sahne
                case .ayarlar: oturum.sayfa = nil; oturum.sekme = .ayarlar
                case .kararlar: oturum.sayfa = .kararlar
                case .akis: oturum.sayfa = .akis
                case .siteler: oturum.sayfa = .siteler
                }
            })
    }

    private var govde: some View {
        NavigationSplitView {
            List(selection: secim) {
                Section {
                    ForEach(MacBolum.ana) { b in satir(b) }
                }
                Section("Sayfalar") {
                    ForEach(MacBolum.sayfalar) { b in satir(b) }
                }
                Section {
                    satir(.ayarlar)
                }
            }
            .listStyle(.sidebar)
            .navigationSplitViewColumnWidth(min: 170, ideal: 190, max: 240)
        } detail: {
            icerik
                // iOS'ta listedeki düğmeler kenarlıksızdır; macOS varsayılanı "bordered" olup
                // her satırı küçük kutuya çeviriyordu. Açıkça stil verenler (.bordered vb.) korunur.
                .buttonStyle(.borderless)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Renk.zemin)
        }
        .task { await nobet() }
        .onReceive(NotificationCenter.default.publisher(for: BildirimDelegesi.sekmeBildirimi)) { n in
            switch n.object as? String {
            case "kararlar": oturum.sayfa = .kararlar
            case "akis": oturum.sayfa = .akis
            case "sohbet": oturum.sayfa = nil; oturum.sekme = .sohbet
            case "sahne": oturum.sayfa = nil; oturum.sekme = .sahne
            case "nabiz": oturum.sayfa = nil; oturum.sekme = .nabiz
            default: break
            }
        }
    }

    private func satir(_ b: MacBolum) -> some View {
        Label {
            HStack {
                Text(b.baslik)
                Spacer()
                if b == .kararlar && oturum.bekleyenOnay > 0 {
                    Text("\(oturum.bekleyenOnay)").font(.caption2.bold()).foregroundStyle(Renk.zemin)
                        .padding(.horizontal, 6).padding(.vertical, 1)
                        .background(Renk.uyari, in: Capsule())
                }
            }
        } icon: { Image(systemName: b.simge) }
        .tag(b)
    }

    @ViewBuilder
    private var icerik: some View {
        switch MacBolum(sekme: oturum.sekme, sayfa: oturum.sayfa) {
        case .nabiz: NabizView()
        case .komutlar: KomutlarView()
        case .sohbet: SohbetView()
        case .sahne: MasaView()
        case .kararlar: KararlarView()
        case .akis: AkisView()
        case .siteler: SitelerView()
        case .ayarlar: AyarlarView()
        }
    }

    /// Nabız + bekleyen onay nöbeti: menü çubuğu ve kenar çubuğu rozeti buradan beslenir;
    /// bekleyen onay ARTARSA yerel bildirim düşer (APNs yok, bkz. MacBildirim).
    private func nobet() async {
        var onceki: Int? = nil
        while !Task.isCancelled {
            if let k = try? await oturum.api.kararlar() {
                let yeni = k.count
                if let o = onceki, yeni > o {
                    BildirimDelegesi.yerel(baslik: "Ghost karar bekliyor",
                                           govde: yeni == 1 ? "1 karar onayını bekliyor." : "\(yeni) karar onayını bekliyor.",
                                           sekme: "kararlar")
                }
                onceki = yeni
                oturum.bekleyenOnay = yeni
            }
            if let n = try? await oturum.api.nabiz() { oturum.nabiz = n }
            try? await Task.sleep(for: .seconds(30))
        }
    }
}
