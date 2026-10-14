import SwiftUI

/// Akış: üstte "şimdi" (SSE'den canlı sohbet olayları), altta birleşik zaman çizelgesi
/// (audit + işler + sohbet). Satırın bağlantısı varsa dokununca kaynağa gider.
struct AkisView: View {
    @EnvironmentObject var oturum: Oturum
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.openURL) private var openURL
    @State private var ogeler: [AkisOgesi] = []
    @State private var canli: [Olay] = []
    @State private var filtre: String = ""      // "" | audit | is | sohbet
    @State private var hata: String?
    @State private var acik: Set<String> = []
    @State private var sonOlay = 0
    @State private var sonOutbox = 0
    @State private var sonSahne = -1

    private static let filtreler: [(String, String)] = [
        ("", "Hepsi"), ("audit", "Denetim"), ("is", "İşler"), ("sohbet", "Sohbet")]

    var body: some View {
        NavigationStack {
            List {
                if let h = hata {
                    Text(h).font(.footnote).foregroundStyle(Renk.uyari).listRowBackground(Renk.zemin)
                }
                if !canli.isEmpty && (filtre.isEmpty || filtre == "sohbet") {
                    Section {
                        ForEach(canli.suffix(12).reversed()) { o in
                            olaySatir(o).listRowBackground(Renk.zemin)
                                .listRowSeparatorTint(Renk.ikincil.opacity(0.3))
                        }
                    } header: {
                        HStack(spacing: 6) {
                            Circle().fill(Renk.uyari).frame(width: 6, height: 6)
                            Text("Şimdi").foregroundStyle(Renk.ikincil)
                        }
                    }
                }
                Section {
                    if ogeler.isEmpty && hata == nil {
                        Text("Henüz kayıt yok").foregroundStyle(Renk.ikincil).listRowBackground(Renk.zemin)
                    }
                    ForEach(ogeler) { o in
                        satir(o).listRowBackground(Renk.zemin)
                            .listRowSeparatorTint(Renk.ikincil.opacity(0.3))
                    }
                    if !ogeler.isEmpty {
                        Button("Daha eski") { Task { await dahaEski() } }
                            .font(.footnote).foregroundStyle(Renk.ikincil)
                            .frame(maxWidth: .infinity).listRowBackground(Renk.zemin)
                    }
                } header: {
                    Text("Geçmiş").foregroundStyle(Renk.ikincil)
                }
            }
            .listStyle(.plain)
            .ghostZemin()
            .navigationTitle("Akış")
            .satirBaslik()
            .toolbar { KapatDugmesi() }
            .toolbar {
                ToolbarItem(placement: .ghostSag) {
                    Menu {
                        ForEach(AkisView.filtreler, id: \.0) { f in
                            Button {
                                filtre = f.0
                                Task { await yenile() }
                            } label: {
                                if filtre == f.0 { Label(f.1, systemImage: "checkmark") } else { Text(f.1) }
                            }
                        }
                    } label: {
                        Image(systemName: "line.3.horizontal.decrease.circle")
                    }
                }
            }
            .yenilenebilir { await yenile() }
            .task(id: scenePhase) {
                guard scenePhase == .active else { return }
                await withTaskGroup(of: Void.self) { g in
                    g.addTask { await yenileDongusu() }
                    g.addTask { await sseDongusu() }
                }
            }
        }
    }

    // MARK: Satırlar

    private func kaynakSimge(_ k: String?) -> String {
        switch k ?? "" {
        case "audit": return "checkmark.shield"
        case "is": return "hammer"
        case "sahne": return "rectangle.on.rectangle"
        default: return (k ?? "").hasPrefix("is#") ? "hammer" : "bubble.left"
        }
    }

    private func satir(_ o: AkisOgesi) -> some View {
        Button {
            if let b = o.baglanti, let u = URL(string: b) { openURL(u) }
            else { if acik.contains(o.id) { acik.remove(o.id) } else { acik.insert(o.id) } }
        } label: {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: kaynakSimge(o.kaynak))
                    .font(.footnote).foregroundStyle(Renk.sinif(o.sinif)).frame(width: 18).padding(.top, 3)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text(o.baslik ?? o.tur ?? "").font(.subheadline).foregroundStyle(Renk.metin).lineLimit(1)
                        Spacer()
                        if o.baglanti != nil {
                            Image(systemName: "arrow.up.right").font(.caption2).foregroundStyle(Renk.ikincil)
                        }
                    }
                    if let d = o.detay, !d.isEmpty {
                        Text(d).font(.footnote).foregroundStyle(Renk.ikincil)
                            .multilineTextAlignment(.leading)      // macOS düğme etiketi ortalıyordu
                            .lineLimit(acik.contains(o.id) ? nil : 2)
                    }
                    Text(kisaSaat(o.ts)).font(.caption2.monospacedDigit()).foregroundStyle(Renk.ikincil.opacity(0.8))
                }
            }
            .padding(.vertical, 4)
        }
    }

    private func olaySatir(_ o: Olay) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: olaySimge(o)).font(.footnote)
                .foregroundStyle(o.hata == true ? Renk.kritik : Renk.ikincil).frame(width: 18).padding(.top, 3)
            VStack(alignment: .leading, spacing: 3) {
                Text(olayBaslik(o)).font(.subheadline).foregroundStyle(Renk.metin).lineLimit(1)
                if let d = olayDetay(o), !d.isEmpty {
                    Text(d).font(.footnote).foregroundStyle(Renk.ikincil).lineLimit(3)
                }
                Text("\(o.kaynak ?? "") · \(kisaSaat(o.ts))")
                    .font(.caption2.monospacedDigit()).foregroundStyle(Renk.ikincil.opacity(0.8))
            }
        }
        .padding(.vertical, 4)
    }

    private func olaySimge(_ o: Olay) -> String {
        switch o.tur ?? "" {
        case "arac": return "wrench"
        case "arac_sonuc": return o.hata == true ? "xmark.octagon" : "arrow.turn.down.right"
        case "sonuc": return "flag.checkered"
        case "baslangic": return "play"
        case "sahne": return "rectangle.on.rectangle"
        default: return "text.bubble"
        }
    }

    private func olayBaslik(_ o: Olay) -> String {
        switch o.tur ?? "" {
        case "arac": return "araç · " + (o.ad ?? "")
        case "arac_sonuc": return o.hata == true ? "araç hatası" : "araç sonucu"
        case "sonuc": return "tur bitti" + (o.hata == true ? " (hata)" : "")
        case "baslangic": return "tur başladı · " + (o.model ?? "")
        case "sahne": return "sahne · " + (o.baslik ?? "")
        default: return "Ghost"
        }
    }

    private func olayDetay(_ o: Olay) -> String? {
        switch o.tur ?? "" {
        case "arac", "arac_sonuc": return o.ozet
        case "sonuc":
            var p: [String] = []
            if let s = o.sureMs { p.append("\(s / 1000) sn") }
            if let t = o.turSayisi { p.append("\(t) tur") }
            return p.joined(separator: " · ")
        default: return o.metin
        }
    }

    // MARK: Veri

    @MainActor
    private func yenileDongusu() async {
        while !Task.isCancelled {
            await yenile()
            try? await Task.sleep(for: .seconds(30))
        }
    }

    @MainActor
    private func yenile() async {
        do {
            let z = try await oturum.api.akis(limit: 80, kaynak: filtre.isEmpty ? nil : filtre)
            ogeler = z.akis
            hata = nil
        } catch is CancellationError {
        } catch APIHata.yetkisiz {
        } catch {
            if Task.isCancelled { return }
            hata = "Bağlantı: " + error.localizedDescription
        }
    }

    @MainActor
    private func dahaEski() async {
        guard let son = ogeler.last?.ts else { return }
        do {
            let z = try await oturum.api.akis(once: son, limit: 80, kaynak: filtre.isEmpty ? nil : filtre)
            let var_ = Set(ogeler.map(\.id))
            ogeler.append(contentsOf: z.akis.filter { !var_.contains($0.id) })
        } catch {
            hata = error.localizedDescription
        }
    }

    @MainActor
    private func sseDongusu() async {
        let dec = JSONDecoder()
        while !Task.isCancelled {
            do {
                for try await ev in oturum.api.sse(olay: sonOlay, outbox: sonOutbox, sahne: sonSahne) {
                    switch ev.ad {
                    case "olay":
                        if let o = try? dec.decode(Olay.self, from: ev.veri) {
                            sonOlay = max(sonOlay, o.id)
                            if !canli.contains(where: { $0.id == o.id }) { canli.append(o) }
                            if canli.count > 60 { canli.removeFirst(canli.count - 60) }
                        }
                    case "mesaj":
                        if let m = try? dec.decode(Mesaj.self, from: ev.veri) { sonOutbox = max(sonOutbox, m.id) }
                    case "sahne":
                        if let s = try? dec.decode(SahneZarf.self, from: ev.veri) { sonSahne = s.seq }
                    case "merhaba", "bitti":
                        if let d = try? JSONSerialization.jsonObject(with: ev.veri) as? [String: Int] {
                            sonOlay = max(sonOlay, d["olay"] ?? 0)
                            sonOutbox = max(sonOutbox, d["outbox"] ?? 0)
                            sonSahne = d["sahne"] ?? sonSahne
                        }
                    default: break
                    }
                }
            } catch is CancellationError {
                return
            } catch APIHata.yetkisiz {
                return
            } catch {
                if Task.isCancelled || (error as? URLError)?.code == .cancelled { return }
                try? await Task.sleep(for: .seconds(3))
            }
            if Task.isCancelled { return }
            try? await Task.sleep(for: .milliseconds(300))
        }
    }
}

/// "2026-09-02 13:05:11" → "13:05" (bugünse) ya da "02.09 13:05".
func kisaSaat(_ ts: String?) -> String {
    guard let ts, ts.count >= 16 else { return ts ?? "" }
    let gun = String(ts.prefix(10)), saat = String(ts.dropFirst(11).prefix(5))
    let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"
    if f.string(from: Date()) == gun { return saat }
    let p = gun.split(separator: "-")
    return p.count == 3 ? "\(p[2]).\(p[1]) \(saat)" : ts
}
