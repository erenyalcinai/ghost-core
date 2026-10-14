import SwiftUI

/// Kararlar: bekleyen kartlar. Sağa kaydır = onay, sola = ret; butonlar da var. 10 sn'de
/// yenilenir. Sekme değil, ⋯ menüsünden açılan sayfa (karar kartı Sohbet'e de düşer).
struct KararlarView: View {
    @EnvironmentObject var oturum: Oturum
    @State private var kararlar: [Karar] = []
    @State private var yuklendi = false
    @State private var hata: String?
    @State private var mesgul: Set<String> = []

    var body: some View {
        NavigationStack {
            List {
                if let h = hata {
                    Text(h).font(.footnote).foregroundStyle(Renk.kritik)
                        .listRowBackground(Renk.zemin)
                }
                if yuklendi && kararlar.isEmpty {
                    VStack(spacing: 8) {
                        Image(systemName: "checkmark.circle").font(.largeTitle).foregroundStyle(Renk.ikincil)
                        Text("Bekleyen karar yok").foregroundStyle(Renk.ikincil)
                    }
                    .frame(maxWidth: .infinity).padding(.top, 60)
                    .listRowBackground(Renk.zemin).listRowSeparator(.hidden)
                }
                ForEach(kararlar) { k in
                    KararKarti(karar: k, mesgul: mesgul.contains(k.id.metin),
                               onay: { Task { await ver(k, onay: true) } },
                               ret: { Task { await ver(k, onay: false) } })
                        .listRowBackground(Renk.zemin)
                        .listRowSeparator(.hidden)
                        .listRowInsets(EdgeInsets(top: 6, leading: 16, bottom: 6, trailing: 16))
                        .swipeActions(edge: .leading, allowsFullSwipe: true) {
                            Button { Task { await ver(k, onay: true) } } label: {
                                Label("Onay", systemImage: "checkmark")
                            }.tint(Color(hex: 0x3A6B4F))
                        }
                        .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                            Button(role: .destructive) { Task { await ver(k, onay: false) } } label: {
                                Label("Ret", systemImage: "xmark")
                            }.tint(Renk.kritik)
                        }
                }
            }
            .listStyle(.plain)
            .ghostZemin()
            .navigationTitle("Kararlar")
            .satirBaslik()
            .toolbar { KapatDugmesi() }
            .yenilenebilir { await yenile() }
            .task {
                while !Task.isCancelled {
                    await yenile()
                    try? await Task.sleep(for: .seconds(10))
                }
            }
        }
    }

    private func yenile() async {
        do {
            kararlar = try await oturum.api.kararlar()
            oturum.bekleyenOnay = kararlar.count
            hata = nil
            yuklendi = true
        } catch is CancellationError {
        } catch {
            hata = error.localizedDescription
            yuklendi = true
        }
    }

    private func ver(_ k: Karar, onay: Bool) async {
        let id = k.id.metin
        guard !mesgul.contains(id) else { return }
        mesgul.insert(id)
        defer { mesgul.remove(id) }
        do {
            try await oturum.api.kararVer(id: id, onay: onay)
            withAnimation { kararlar.removeAll { $0.id.metin == id } }
            oturum.bekleyenOnay = kararlar.count
            hata = nil
        } catch {
            hata = error.localizedDescription
        }
    }
}

struct KararKarti: View {
    var karar: Karar
    var mesgul: Bool
    var onay: () -> Void
    var ret: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Rozet(metin: karar.sinif ?? "karar", renk: Renk.sinif(karar.sinif))
                if let t = karar.tur, !t.isEmpty {
                    Text(t).font(.caption).foregroundStyle(Renk.ikincil)
                }
                Spacer()
                if let ts = karar.ts { Text(ts).font(.caption2).foregroundStyle(Renk.ikincil) }
            }
            Text(karar.metin).foregroundStyle(Renk.metin).font(.body)
            HStack(spacing: 10) {
                Button(action: onay) {
                    Text("Onayla").frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered).tint(Renk.metin)
                Button(action: ret) {
                    Text("Reddet").frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered).tint(Renk.kritik)
            }
            .disabled(mesgul)
            .opacity(mesgul ? 0.5 : 1)
        }
        .kart()
    }
}
