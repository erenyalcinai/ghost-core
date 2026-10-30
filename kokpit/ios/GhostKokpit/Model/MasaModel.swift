import Foundation
import Combine
import CoreTransferable
import UniformTypeIdentifiers

/// Masa veri katmanı — Faz 3.12 8b (2026-10-30): ağaç/durum mantığı MasaView'dan buraya indi
/// (görünüm 817 satırdı). Paylaş/Widget hedefleri de bu dosyayı derler: SwiftUI/Views bağımlılığı YOK
/// (dokunsal geri bildirim `geriBildirim` ile görünümden bağlanır).

enum MasaHedef: Hashable { case klasor(Int), oge(Int), dolap }

enum MasaDiyalog: Identifiable {
    case adlandir(MasaKart), tasi(MasaKart), sil(MasaKart)
    var id: String {
        switch self {
        case .adlandir(let k): return "a\(k.id)"
        case .tasi(let k): return "t\(k.id)"
        case .sil(let k): return "s\(k.id)"
        }
    }
}

// MARK: Model

/// Tek istekte düz ağaç; seq değişince uzun yoklama döner. Eylemler sunucuya gider, liste yenilenir.
@MainActor
final class MasaModel: ObservableObject {
    @Published var kartlar: [MasaKart] = []
    @Published var seq: Int = -1
    @Published var dolapSayisi = 0
    @Published var hata: String?
    @Published var mesgul = false
    /// Menüden açılan diyaloglar (adlandır / taşı / sil) KÖKTE sunulur: Menu/contextMenu içeriğine
    /// asılan .alert/.sheet menü kapanınca yok olur, hiç açılmaz — durum burada, sunum MasaDiyaloglari'nda.
    @Published var diyalog: MasaDiyalog?
    @Published var yeniAd = ""
    private var api: GhostAPI?
    var apiErisim: GhostAPI? { api }          // Faz 1.13b: PDF paylaşımı (MasaPdf) indirme için
    /// Eylem sonucu dokunsal geri bildirim (true: başarı, false: hata) — görünüm bağlar (Titre Views katmanında).
    var geriBildirim: ((Bool) -> Void)?

    func baglan(_ api: GhostAPI) { self.api = api }

    func cocuklar(_ ebeveyn: Int?) -> [MasaKart] {
        kartlar.filter { $0.ebeveyn == ebeveyn }
    }
    func kart(_ id: Int) -> MasaKart? { kartlar.first { $0.id == id } }
    /// Taşıma hedefleri: tüm klasörler (kök dahil); `haric` alt ağacı dışarıda kalır.
    func klasorler(haric: Int? = nil) -> [MasaKart] {
        var yasak = Set<Int>()
        if let h = haric {
            var kuyruk = [h]
            while let k = kuyruk.popLast() {
                yasak.insert(k)
                kuyruk += kartlar.filter { $0.ebeveyn == k }.map(\.id)
            }
        }
        return kartlar.filter { $0.klasor && !yasak.contains($0.id) }
    }
    func kirinti(_ id: Int) -> String {
        var out: [String] = []
        var cur: Int? = id
        var guard_ = 0
        while let c = cur, let k = kart(c), guard_ < 32 { out.insert(k.ad, at: 0); cur = k.ebeveyn; guard_ += 1 }
        return out.joined(separator: " › ")
    }

    func dongu() async {
        await yenile()
        while !Task.isCancelled {
            let t = Date()
            await yenile(bekle: 25)
            if Date().timeIntervalSince(t) < 1 { try? await Task.sleep(for: .seconds(1)) }
        }
    }

    func yenile(bekle: Int = 0) async {
        guard let api else { return }
        do {
            let z = try await api.masa(since: bekle > 0 ? seq : -1, bekle: bekle)
            if z.seq != seq || bekle == 0 {
                kartlar = z.kartlar
                seq = z.seq
                dolapSayisi = z.dolapSayisi ?? 0
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

    /// Sunucu eylemi; izin hatası (403) olduğu gibi gösterilir — kural uygulamada değil sunucuda.
    @discardableResult
    func calistir(_ is_: @escaping (GhostAPI) async throws -> MasaYazYanit) async -> MasaYazYanit? {
        guard let api else { return nil }
        mesgul = true
        defer { mesgul = false }
        do {
            let y = try await is_(api)
            await yenile()
            geriBildirim?(true)
            return y
        } catch {
            hata = (error as? APIHata)?.errorDescription ?? error.localizedDescription
            geriBildirim?(false)
            return nil
        }
    }

    func dosyaYukle(_ veri: Data, ad: String, mime: String, ebeveyn: Int?) async {
        guard veri.count <= 1_800_000 else { hata = "\(ad) çok büyük (\(veri.count / 1024) KB; sınır 1800 KB)."; return }
        await calistir { try await $0.masaDosyaYukle(veri, ad: ad, mime: mime, ebeveyn: ebeveyn) }
    }
}

enum TurSimge {
    static func ad(_ tur: String) -> String {
        switch tur {
        case "klasor": return "folder"
        case "not": return "note.text"
        case "rapor": return "doc.richtext"
        case "sahne": return "tablecells"
        case "gorsel": return "photo"
        case "dosya": return "doc"
        case "baglanti": return "link"
        default: return "questionmark.square"
        }
    }
    static func etiket(_ tur: String) -> String {
        switch tur {
        case "klasor": return "klasör"
        case "not": return "not"
        case "rapor": return "rapor"
        case "sahne": return "sahne kaydı"
        case "gorsel": return "görsel"
        case "dosya": return "belge"
        case "baglanti": return "bağlantı"
        default: return tur
        }
    }
}

enum Tarih {
    /// "2026-10-04 19:27:03" → bugünse "19:27", değilse "4 Eki".
    static func kisa(_ s: String?) -> String {
        guard let s, s.count >= 16 else { return "" }
        let gun = String(s.prefix(10)), saat = String(s.dropFirst(11).prefix(5))
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"
        if f.string(from: Date()) == gun { return saat }
        guard let d = f.date(from: gun) else { return gun }
        let g = DateFormatter(); g.locale = Locale(identifier: "tr_TR"); g.dateFormat = "d MMM"
        return g.string(from: d)
    }
}

/// Masa kartının PDF hâli — Paylaş sayfası istediğinde sunucudan indirilir, geçici dosya olarak verilir (Faz 1.13b).
struct MasaPdf: Transferable {
    let id: Int
    let ad: String
    let api: GhostAPI?

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(exportedContentType: .pdf) { item in
            SentTransferredFile(try await item.indir())
        }
    }

    func indir() async throws -> URL {
        guard let api else { throw URLError(.userAuthenticationRequired) }
        let veri = try await api.masaPdf(id)
        let guvenli = ad.map { $0.isLetter || $0.isNumber || "-_ .".contains($0) ? $0 : "_" }
        var dosyaAdi = String(String(guvenli).prefix(60)).trimmingCharacters(in: .whitespaces)
        if dosyaAdi.isEmpty { dosyaAdi = "masa-\(id)" }
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(dosyaAdi + ".pdf")
        try veri.write(to: url, options: .atomic)
        return url
    }
}

