import SwiftUI

/// Komutlar: liste; dokun → "/komut" sohbete gider (ve Sohbet sekmesine geçilir).
/// Argüman istiyorsa küçük prompt. 2026-09-03'ten beri sekme (Nabız ile Sohbet arasında).
/// 2026-09-04: iki bölüm — Ghost komutları ve Claude Code (skill'ler + /cost /context /compact);
/// grubu sunucu söyler (`grup`), uygulama yalnız ayırır. İkisi de aynı yoldan sohbete gider.
struct KomutlarView: View {
    @EnvironmentObject var oturum: Oturum
    @State private var komutlar: [Komut] = []
    @State private var hata: String?
    @State private var bekleyen: Komut?
    @State private var arguman = ""
    @State private var bilgi: String?

    var body: some View {
        NavigationStack {
            List {
                if let h = hata {
                    Text(h).font(.footnote).foregroundStyle(Renk.kritik).listRowBackground(Renk.zemin)
                }
                bolum("Ghost", komutlar.filter { $0.grup != "claude" })
                bolum("Claude Code", komutlar.filter { $0.grup == "claude" },
                      alt: "Sohbet oturumunda doğrudan koşar — skill'ler ve yerleşikler.")
            }
            .listStyle(.plain)
            .ghostZemin()
            .navigationTitle("Komutlar")
            .satirBaslik()
            .aracMenusu()
            .yenilenebilir { await yenile() }
            .task { await yenile() }
            .overlay(alignment: .bottom) {
                if let b = bilgi {
                    Text(b).font(.footnote).foregroundStyle(Renk.zemin)
                        .padding(.horizontal, 14).padding(.vertical, 8)
                        .background(Renk.metin, in: Capsule())
                        .padding(.bottom, 12)
                        .transition(.opacity)
                }
            }
            .alert(bekleyen?.komut ?? "Argüman", isPresented: Binding(
                get: { bekleyen != nil },
                set: { if !$0 { bekleyen = nil } })) {
                TextField("argüman", text: $arguman)
                Button("Gönder") {
                    if let k = bekleyen {
                        let a = arguman.trimmingCharacters(in: .whitespacesAndNewlines)
                        Task { await gonder(a.isEmpty ? k.komut : k.komut + " " + a) }
                    }
                    bekleyen = nil
                }
                Button("Vazgeç", role: .cancel) { bekleyen = nil }
            } message: {
                Text(bekleyen?.aciklama ?? "")
            }
        }
    }

    @ViewBuilder
    private func bolum(_ baslik: String, _ liste: [Komut], alt: String? = nil) -> some View {
        if !liste.isEmpty {
            Section {
                ForEach(liste) { k in
                    Button { sec(k) } label: {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(k.komut).font(.body.monospaced()).foregroundStyle(Renk.metin)
                            if let a = k.aciklama, !a.isEmpty {
                                Text(a).font(.footnote).foregroundStyle(Renk.ikincil)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                    .listRowBackground(Renk.zemin)
                    .listRowSeparatorTint(Renk.ikincil.opacity(0.3))
                }
            } header: {
                VStack(alignment: .leading, spacing: 2) {
                    Text(baslik).font(.footnote.weight(.medium)).foregroundStyle(Renk.ikincil)
                    if let alt { Text(alt).font(.caption2).foregroundStyle(Renk.ikincil.opacity(0.8)) }
                }
                .textCase(nil)
            }
        }
    }

    /// Açıklamada <…> ya da […] varsa argüman ister sayıyoruz.
    private func argumanIster(_ k: Komut) -> Bool {
        let a = k.aciklama ?? ""
        return a.contains("<") || a.contains("[") || k.komut.contains(" ")
    }

    private func sec(_ k: Komut) {
        if argumanIster(k) {
            arguman = ""
            bekleyen = k
        } else {
            Task { await gonder(k.komut) }
        }
    }

    private func gonder(_ metin: String) async {
        do {
            try await oturum.api.mesajGonder(metin)
            hata = nil
            withAnimation { bilgi = "Gönderildi: " + metin }
            try? await Task.sleep(for: .seconds(1.2))
            withAnimation { bilgi = nil }
            oturum.sekme = .sohbet
        } catch {
            hata = error.localizedDescription
        }
    }

    private func yenile() async {
        do {
            komutlar = try await oturum.api.komutlar()
            hata = nil
        } catch is CancellationError {
        } catch {
            hata = error.localizedDescription
        }
    }
}
