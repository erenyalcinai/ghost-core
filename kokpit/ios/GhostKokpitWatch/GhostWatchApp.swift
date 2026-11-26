import SwiftUI
import WatchKit

/// Ghost Kokpit — saat uygulaması (aşama 1: eşleşme, ana ekran, söyle, son mesajlar, bildirim).
///
/// Sınırlar (tasarım `dossier/donanim/apple-watch-se3/00-sentez.md`):
///  - Sunucuya "cep cihazı" türüyle eşleşir; söylenen/yazılan düz metin onay sayılmaz (kural onay servisinde).
///  - Bu aşamada onay/karar ucuna istek atan HİÇBİR kod yoktur (kart ekranı sonraki aşama).
///  - Soket yok, sahte antrenman oturumu yok, arka plan çalışmasına güvenilmez: öndeyken HTTP uzun yoklama,
///    bilek inince bildirim.
@main
struct GhostWatchApp: App {
    @WKApplicationDelegateAdaptor private var delege: SaatDelegesi
    @StateObject private var oturum = SaatOturum()
    @StateObject private var sohbet = SaatSohbet()

    var body: some Scene {
        WindowGroup {
            KokGorunum(delege: delege)
                .environmentObject(oturum)
                .environmentObject(sohbet)
                .preferredColorScheme(.dark)
        }
    }
}

/// Kök: eşli değilse eşleşme, eşliyse ana ekran + gezinti. Öne/arkaya geçişte yoklamayı açıp kapatır.
struct KokGorunum: View {
    let delege: SaatDelegesi
    @EnvironmentObject var oturum: SaatOturum
    @EnvironmentObject var sohbet: SaatSohbet
    @Environment(\.scenePhase) private var faz
    @State private var yol: [Rota] = []

    var body: some View {
        Group {
            if oturum.durum == .esli {
                NavigationStack(path: $yol) {
                    AnaEkran(yol: $yol)
                        .navigationDestination(for: Rota.self) { rota in
                            switch rota {
                            case .soyle(let metin): SoyleEkrani(metin: metin)
                            case .mesajlar: MesajlarEkrani()
                            case .mesaj(let id): MesajEkrani(mesajID: id)
                            case .ayarlar: AyarlarEkrani()
                            }
                        }
                }
            } else {
                EslesmeEkrani()
            }
        }
        // Yoklama yalnız eşli VE öndeyken: kimlik (faz, durum) değişince görev iptal edilip yeniden kurulur.
        .task(id: YoklamaKimligi(etkin: faz == .active, esli: oturum.durum == .esli)) {
            if faz == .active { oturum.oneGelince() }       // kasa gerçeğin kaynağı: kimlik hâlâ orada mı / şimdi okunuyor mu
            guard faz == .active, oturum.durum == .esli else {
                sohbet.dur()
                if oturum.durum != .esli { sohbet.temizle(); yol = [] }
                return
            }
            delege.kaydol(api: oturum.api)
            sohbet.basla(api: oturum.api)
            while !Task.isCancelled {
                await oturum.durumuYenile()
                try? await Task.sleep(for: .seconds(30))
            }
        }
        .onChange(of: faz) { _, yeni in
            if yeni != .active { sohbet.dur() }
        }
        .onReceive(NotificationCenter.default.publisher(for: SaatDelegesi.acBildirimi)) { _ in
            if oturum.durum == .esli { yol = [.mesajlar] }     // bildirime dokununca / "Aç": son mesajlar
        }
    }
}

private struct YoklamaKimligi: Equatable {
    var etkin: Bool
    var esli: Bool
}

/// Ayarlar: tek gerçek ayar "kendiliğinden gönder"; gerisi durum ve eşleşmeyi kaldırma.
struct AyarlarEkrani: View {
    @EnvironmentObject var oturum: SaatOturum
    @AppStorage(SaatAyar.kendiligindenGonder) private var kendiliginden = false
    @State private var silmeOnayi = false

    var body: some View {
        List {
            Section {
                Toggle("Kendiliğinden gönder", isOn: $kendiliginden)
            } footer: {
                Text("Açıkken söylediğin metin önizleme beklemeden gider.")
            }
            Section("Bağlantı") {
                satir("Sunucu", oturum.taban.host ?? oturum.taban.absoluteString)
                satir("Cihaz", String((oturum.api.kimlik?.deviceID ?? "—").prefix(8)))
                satir("Tür", "cep (düz metin onay sayılmaz)")
                satir("Durum", oturum.agHatasi ?? "bağlı")
            }
            Section {
                Button("Eşleşmeyi kaldır", role: .destructive) { silmeOnayi = true }
            } footer: {
                Text("Anahtar bu saatten silinir. Sunucudaki kaydı Telegram'da /cihazlar ile iptal et.")
            }
        }
        .navigationTitle("Ayarlar")
        .confirmationDialog("Eşleşme kaldırılsın mı?", isPresented: $silmeOnayi, titleVisibility: .visible) {
            Button("Kaldır", role: .destructive) { oturum.sifirla() }
            Button("Vazgeç", role: .cancel) {}
        }
    }

    private func satir(_ ad: String, _ deger: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(ad).font(.caption2).foregroundStyle(Renk.ikincil)
            Text(deger).font(.footnote).foregroundStyle(Renk.metin).lineLimit(2)
        }
    }
}
