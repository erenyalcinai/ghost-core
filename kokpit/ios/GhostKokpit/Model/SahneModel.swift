import Foundation
import Combine

/// "Şimdi" (Sahne) veri katmanı — Faz 3.12 8b (2026-10-30): önceden SimdiBolumu görünümünün içinde
/// 4 @State ve yoklama döngüsü birlikteydi; görünüm yalnız çizer. Uzun yoklamayla seq değişince yenilenir,
/// `eski` doluysa önceki sahne gösterilir (canlı sahne arkada akar).
@MainActor
final class SahneModel: ObservableObject {
    @Published var sahne: SahneZarf?
    @Published var eski: SahneZarf?
    @Published var gecmis: [SahneGecmisOge] = []
    @Published var hata: String?
    private var api: GhostAPI?

    func baglan(_ api: GhostAPI) { self.api = api }

    var gosterilen: SahneZarf? { eski ?? sahne }
    var bos: Bool { (gosterilen?.bilesenler ?? []).isEmpty }
    /// Bölüm başlığı: "Şimdi" ya da "Şimdi · <sahne başlığı>".
    var baslik: String {
        let b = gosterilen?.baslik ?? ""
        return b.isEmpty || bos ? "Şimdi" : "Şimdi · " + b
    }
    /// Önceki sahne açıkken ve canlı sahne onu geçtiyse "yeni sahne var" uyarısı.
    var yeniSahneVar: Bool { eski.map { (sahne?.seq ?? 0) > $0.seq } ?? false }

    func simdiyeDon() { eski = nil }

    func eskiyiAc(_ seq: Int) async {
        guard let api else { return }
        do {
            let z = try await api.sahneSeq(seq)
            eski = (z.seq == sahne?.seq) ? nil : z
        } catch {
            hata = "Önceki sahne okunamadı: " + error.localizedDescription
        }
    }

    func gecmisiYenile() async {
        guard let api else { return }
        if let g = try? await api.sahneGecmis() { gecmis = g.gecmis }
    }

    func dongu() async {
        await yenile(bekle: 0)
        await gecmisiYenile()
        while !Task.isCancelled {
            let t = Date()
            await yenile(bekle: 25)
            if Date().timeIntervalSince(t) < 1 { try? await Task.sleep(for: .seconds(1)) }
        }
    }

    func yenile(bekle: Int) async {
        guard let api else { return }
        do {
            let z = try await api.sahne(since: sahne?.seq ?? -1, bekle: bekle)
            if z.seq != sahne?.seq {
                sahne = z
                await gecmisiYenile()           // yeni sahne = önceki tarihçeye girdi
            }
            hata = nil
        } catch is CancellationError {
        } catch APIHata.yetkisiz {
        } catch {
            if Task.isCancelled || (error as? URLError)?.code == .cancelled { return }
            hata = "Bağlantı: " + error.localizedDescription
            try? await Task.sleep(for: .seconds(3))
        }
    }
}

/// Grafik bileşeninin çizime hazır noktası (seri × etiket × değer).
struct GrafikNoktasi: Identifiable {
    let id: String; let seri: String; let x: String; let y: Double; let sira: Int
}

extension Bilesen {
    /// `seriler` + `etiketler` → düz nokta listesi; etiket yoksa 1..n.
    var grafikNoktalari: [GrafikNoktasi] {
        var out: [GrafikNoktasi] = []
        for (si, s) in (seriler ?? []).enumerated() {
            let ad = s.ad ?? "seri \(si + 1)"
            for (i, y) in s.degerler.enumerated() {
                let x = (etiketler ?? []).indices.contains(i) ? etiketler![i] : String(i + 1)
                out.append(GrafikNoktasi(id: "\(si)-\(i)", seri: ad, x: x, y: y, sira: i))
            }
        }
        return out
    }
}
