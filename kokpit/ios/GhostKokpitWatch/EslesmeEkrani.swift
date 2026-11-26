import SwiftUI

/// Eşleşme: saatte sistem klavyesi yok → altı haneli kod için rakam tuş takımı.
/// Kod Telegram'da `/cihaz cep` ile alınır (10 dk geçerli). Saatte kod kurulu değilse tuş takımı hiç açılmaz.
struct EslesmeEkrani: View {
    @EnvironmentObject var oturum: SaatOturum
    @Environment(\.scenePhase) private var faz
    @State private var kod = ""
    @State private var mesgul = false
    @State private var hata: String?

    var body: some View {
        Group {
            if oturum.durum == .kodYok {
                kodYokGorunumu
            } else {
                tusTakimi
            }
        }
        .background(Renk.zemin)
        .onChange(of: faz) { _, yeni in
            if yeni == .active { oturum.oneGelince() }   // Ayarlar'dan kod koyup döndüyse / kasa şimdi okunabiliyorsa
        }
    }

    private var kodYokGorunumu: some View {
        ScrollView {
            VStack(spacing: 8) {
                Maskot(hal: .yokluk, yukseklik: 44)
                Text("Saate kod koy")
                    .font(.headline).foregroundStyle(Renk.metin)
                Text("Ghost'un anahtarı yalnız kodu olan saatte saklanır. Saatte Ayarlar › Parola'dan kod koy, sonra buraya dön.")
                    .font(.footnote).foregroundStyle(Renk.ikincil)
                    .multilineTextAlignment(.center)
                Button("Yeniden dene") { oturum.oneGelince() }
            }
            .padding(.horizontal, 4)
        }
    }

    private var tusTakimi: some View {
        VStack(spacing: 4) {
            Text(gosterim)
                .font(.system(size: 20, weight: .medium, design: .monospaced))
                .foregroundStyle(Renk.metin)
                .frame(maxWidth: .infinity)
                .accessibilityLabel(Text("Kod: \(kod.count) hane girildi"))
            if let h = hata {
                Text(h).font(.system(size: 11)).foregroundStyle(Renk.kritik)
                    .multilineTextAlignment(.center).lineLimit(3).minimumScaleFactor(0.8)
            } else if kod.isEmpty {
                Text("Telegram: /cihaz cep").font(.system(size: 11)).foregroundStyle(Renk.ikincil)
            }
            Grid(horizontalSpacing: 4, verticalSpacing: 4) {
                ForEach([["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"]], id: \.self) { satir in
                    GridRow {
                        ForEach(satir, id: \.self) { r in tus(r) }
                    }
                }
                GridRow {
                    Color.clear.frame(height: 1)
                    tus("0")
                    Button { sil() } label: {
                        Image(systemName: "delete.left").frame(maxWidth: .infinity, minHeight: 28)
                    }
                    .buttonStyle(.plain)
                    .background(Renk.kart, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                    .disabled(kod.isEmpty || mesgul)
                    .accessibilityLabel(Text("Sil"))
                }
            }
            .opacity(mesgul ? 0.4 : 1)
        }
        .padding(.horizontal, 2)
    }

    /// Girilen haneler + kalan yer tutucular: "12· ···" yerine boşluklu "1 2 · · · ·".
    private var gosterim: String {
        if mesgul { return "eşleniyor…" }
        let haneler = kod.map(String.init) + Array(repeating: "·", count: max(0, 6 - kod.count))
        return haneler.joined(separator: " ")
    }

    private func tus(_ rakam: String) -> some View {
        Button { bas(rakam) } label: {
            Text(rakam).font(.system(size: 18, weight: .medium)).frame(maxWidth: .infinity, minHeight: 28)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Renk.metin)
        .background(Renk.kart, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .disabled(mesgul)
    }

    private func bas(_ rakam: String) {
        guard kod.count < 6, !mesgul else { return }
        hata = nil
        kod += rakam
        Titre.hafif()
        if kod.count == 6 { Task { await esle() } }
    }

    private func sil() {
        guard !kod.isEmpty, !mesgul else { return }
        kod.removeLast()
        hata = nil
    }

    private func esle() async {
        guard kod.count == 6, !mesgul else { return }
        mesgul = true
        defer { mesgul = false }
        do {
            try await oturum.esle(kod: kod)
            Titre.basari()
        } catch let e as EslesmeHata {
            hata = e.errorDescription
            kod = ""
            Titre.hata()
        } catch {
            if let a = error as? APIHata, case .sunucu(let k, _) = a, k == 403 {
                hata = "Kod kabul edilmedi. Yeni kod al."
            } else {
                hata = SaatOturum.kisaHata(error)
            }
            kod = ""
            Titre.hata()
        }
    }
}
