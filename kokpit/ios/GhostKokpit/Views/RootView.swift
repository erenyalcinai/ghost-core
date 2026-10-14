import SwiftUI

/// Beş sekme, sahibin sırası (2026-09-03): Nabız · Komutlar · Sohbet · Masa · Ayarlar.
/// 2026-10-04: Sahne sekmesi **Masa** oldu (Sahip: Sahne sohbetin kopyasıydı ve bayatlıyordu) —
/// üstte anlık sahne ("Şimdi"), altında Ghost+sahibin kalıcı kartları. Etiket `Sekme.sahne` kaldı
/// (bildirim yönlendirmesi "sahne" → bu sekme).
/// Kararlar · Akış · Siteler sağ üstteki ⋯ menüsünden SAYFA olarak açılır.
///
/// Karar tarihçesi: Faz 0 Nabız·Kararlar·Sohbet·Komutlar·Siteler → Faz 1 Sahne+Akış sekmeye
/// girdi (Komutlar/Siteler/Ayarlar ⋯'ye) → 2026-09-03 Sahip: "kararlar ve akış tek başına
/// sekme kaplamayı hak etmiyor", ikisi ⋯'ye indi; Komutlar ve Ayarlar alt bara çıktı.
/// Bekleyen onay sekme rozetini kaybetti, üç yerden görünür: ⋯ üstünde nokta, Sohbet'in
/// üstünde şerit, karar kartının kendisi zaten butonlu mesaj olarak Sohbet'e düşer (+ APNs).
enum Sekme: Hashable { case nabiz, komutlar, sohbet, sahne, ayarlar }

struct RootView: View {
    @EnvironmentObject var oturum: Oturum

    var body: some View {
        Group {
            if oturum.esli {
                sekmeler
            } else {
                EslesmeView()
            }
        }
        .preferredColorScheme(.dark)
        .tint(Renk.metin)
    }

    /// Seçili Sohbet sekmesine tekrar dokununca sistem en üste kaydırır; biz en alta
    /// (son mesaja) ineriz — SohbetView bildirimi dinler.
    static let sohbetEnAlta = Notification.Name("ghost.sohbet.enalta")

    private var sekmeSecimi: Binding<Sekme> {
        Binding(get: { oturum.sekme }, set: { yeni in
            if yeni == .sohbet && oturum.sekme == .sohbet {
                NotificationCenter.default.post(name: RootView.sohbetEnAlta, object: nil)
            }
            oturum.sekme = yeni
        })
    }

    private var sekmeler: some View {
        TabView(selection: sekmeSecimi) {
            NabizView()
                .tabItem { Label("Nabız", systemImage: "waveform.path.ecg") }
                .tag(Sekme.nabiz)
            KomutlarView()
                .tabItem { Label("Komutlar", systemImage: "terminal") }
                .tag(Sekme.komutlar)
            SohbetView()
                .tabItem { Label("Sohbet", systemImage: "bubble.left.and.bubble.right") }
                .tag(Sekme.sohbet)
            MasaView()
                .tabItem { Label("Masa", systemImage: "square.grid.2x2") }
                .tag(Sekme.sahne)
            AyarlarView()
                .tabItem { Label("Ayarlar", systemImage: "gearshape") }
                .tag(Sekme.ayarlar)
        }
        #if os(iOS)
        .toolbarBackground(Renk.zemin, for: .tabBar)
        .toolbarBackgroundVisibility(.visible, for: .tabBar)
        #endif
        // Tek sunucu: sayfa sheet'i kökte durur; ⋯ menüsü yalnız oturum.sayfa'yı set eder
        // (üç sekmenin üçü de aynı sheet'i sunmaya kalkarsa iOS ikili sunum yaşar).
        .sheet(item: $oturum.sayfa) { p in
            SayfaGorunum(sayfa: p)
                .environmentObject(oturum)
                .preferredColorScheme(.dark)
        }
        .task { await onayNobeti() }
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

    /// Kararlar sekmesi kalktığı için bekleyen onay sayısını kök tutar: ⋯ üstündeki nokta
    /// ve Sohbet şeridi buradan beslenir. Nabız görünürken kendi yoklaması da günceller.
    private func onayNobeti() async {
        while !Task.isCancelled {
            if let k = try? await oturum.api.kararlar() { oturum.bekleyenOnay = k.count }
            try? await Task.sleep(for: .seconds(30))
        }
    }
}
