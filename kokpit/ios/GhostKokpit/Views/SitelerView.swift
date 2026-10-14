import SwiftUI

/// Siteler: liste, yeşil/kırmızı nokta, dokun → Safari.
struct SitelerView: View {
    @EnvironmentObject var oturum: Oturum
    @State private var siteler: [Site] = []
    @State private var hata: String?
    @State private var yuklendi = false

    var body: some View {
        NavigationStack {
            List {
                if let h = hata {
                    Text(h).font(.footnote).foregroundStyle(Renk.kritik).listRowBackground(Renk.zemin)
                }
                if yuklendi && siteler.isEmpty && hata == nil {
                    Text("Site yok").foregroundStyle(Renk.ikincil).listRowBackground(Renk.zemin)
                }
                ForEach(siteler) { s in
                    satir(s).listRowBackground(Renk.zemin)
                        .listRowSeparatorTint(Renk.ikincil.opacity(0.3))
                }
            }
            .listStyle(.plain)
            .ghostZemin()
            .navigationTitle("Siteler")
            .satirBaslik()
            .toolbar { KapatDugmesi() }
            .yenilenebilir { await yenile() }
            .task {
                while !Task.isCancelled {
                    await yenile()
                    try? await Task.sleep(for: .seconds(60))
                }
            }
        }
    }

    @ViewBuilder
    private func satir(_ s: Site) -> some View {
        let icerik = HStack(spacing: 12) {
            Nokta(renk: Renk.durum(ok: s.ok), boyut: 10)
            VStack(alignment: .leading, spacing: 3) {
                Text(s.ad).foregroundStyle(Renk.metin)
                if let alt = [s.baslik, s.proje].compactMap({ $0 }).filter({ !$0.isEmpty }).first {
                    Text(alt).font(.footnote).foregroundStyle(Renk.ikincil).lineLimit(1)
                }
            }
            Spacer()
            Image(systemName: "arrow.up.right").font(.footnote).foregroundStyle(Renk.ikincil)
        }
        .padding(.vertical, 4)
        if let u = s.url.flatMap({ URL(string: $0) }) ?? URL(string: "https://" + s.ad) {
            Link(destination: u) { icerik }
        } else {
            icerik
        }
    }

    private func yenile() async {
        do {
            siteler = try await oturum.api.siteler()
            hata = nil
            yuklendi = true
        } catch is CancellationError {
        } catch {
            hata = error.localizedDescription
            yuklendi = true
        }
    }
}
