import SwiftUI
import AVFoundation
import QuickLook

/// Dökümdeki medya balonu: görsel satır içi (dokun → tam ekran), ses → oynat/durdur,
/// video/belge → indir ve Quick Look ile aç. Dosya `/v1/medya/<id>` (imzalı) ile gelir.
struct MedyaBalonu: View {
    @EnvironmentObject var oturum: Oturum
    let mesajID: Int
    let medya: Medya
    @State private var onizleme: URL?
    @State private var indiriliyor = false
    @State private var hata: String?

    var body: some View {
        Group {
            switch medya.tur {
            case "gorsel":
                Button { Task { await ac() } } label: {
                    ImzaliGorsel(yol: "/v1/medya/\(mesajID)")
                }
                .buttonStyle(.plain)
            case "ses":
                SesOynatici(mesajID: mesajID)
            default:
                Button { Task { await ac() } } label: {
                    HStack(spacing: 10) {
                        Image(systemName: medya.tur == "video" ? "play.rectangle" : "doc")
                            .font(.title3).foregroundStyle(Renk.ikincil)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(medya.ad ?? (medya.tur == "video" ? "video" : "belge"))
                                .font(.subheadline).foregroundStyle(Renk.metin).lineLimit(1)
                            Text(boyutMetni).font(.caption2).foregroundStyle(Renk.ikincil)
                        }
                        Spacer(minLength: 0)
                        if indiriliyor { ProgressView().tint(Renk.ikincil) }
                        else { Image(systemName: "arrow.down.circle").foregroundStyle(Renk.ikincil) }
                    }
                    .padding(10)
                    .background(Renk.zemin.opacity(0.6), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                }
                .buttonStyle(.plain)
                .disabled(indiriliyor)
            }
            if let h = hata { Text(h).font(.caption2).foregroundStyle(Renk.kritik) }
        }
        .quickLookPreview($onizleme)
    }

    private var boyutMetni: String {
        guard let b = medya.boyut else { return medya.tur }
        return b < 1_000_000 ? "\(b / 1024) KB" : String(format: "%.1f MB", Double(b) / 1_048_576)
    }

    /// İndir → geçici dosya (doğru uzantı, Quick Look türü buradan anlar) → önizleme.
    @MainActor
    private func ac() async {
        indiriliyor = true
        defer { indiriliyor = false }
        do {
            let veri = try await oturum.api.medya(mesajID)
            let ad = (medya.ad ?? "medya").components(separatedBy: CharacterSet(charactersIn: "/\\")).last ?? "medya"
            let url = FileManager.default.temporaryDirectory
                .appendingPathComponent("ghost-medya-\(mesajID)-" + ad)
            try veri.write(to: url, options: .atomic)
            onizleme = url
            hata = nil
        } catch {
            hata = error.localizedDescription
        }
    }
}

/// Sesli yanıt: tek dokunuşla oynat/durdur. AVAudioPlayer hoparlörden çalar.
struct SesOynatici: View {
    @EnvironmentObject var oturum: Oturum
    let mesajID: Int
    @StateObject private var oynatici = SesDurumu()

    var body: some View {
        Button {
            Task { await oynatici.degistir { try await oturum.api.medya(mesajID) } }
        } label: {
            HStack(spacing: 10) {
                Image(systemName: oynatici.caliyor ? "stop.circle.fill" : "play.circle.fill")
                    .font(.system(size: 28)).foregroundStyle(Renk.metin)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Sesli yanıt").font(.subheadline).foregroundStyle(Renk.metin)
                    Text(oynatici.durum).font(.caption2).foregroundStyle(Renk.ikincil)
                }
                Spacer(minLength: 0)
                if oynatici.yukleniyor { ProgressView().tint(Renk.ikincil) }
            }
            .padding(8)
            .background(Renk.zemin.opacity(0.6), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        }
        .buttonStyle(.plain)
    }
}

@MainActor
final class SesDurumu: NSObject, ObservableObject, AVAudioPlayerDelegate {
    @Published var caliyor = false
    @Published var yukleniyor = false
    @Published var durum = "dokun → dinle"
    private var oynatici: AVAudioPlayer?

    func degistir(_ indir: () async throws -> Data) async {
        if caliyor { oynatici?.stop(); caliyor = false; durum = "durdu"; return }
        if oynatici == nil {
            yukleniyor = true
            defer { yukleniyor = false }
            do {
                let veri = try await indir()
                #if os(iOS)
                try AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio)
                try AVAudioSession.sharedInstance().setActive(true)
                #endif
                let p = try AVAudioPlayer(data: veri)
                p.delegate = self
                oynatici = p
            } catch {
                durum = "açılamadı: " + error.localizedDescription
                return
            }
        }
        oynatici?.currentTime = 0
        if oynatici?.play() == true {
            caliyor = true
            durum = String(format: "%.0f sn", oynatici?.duration ?? 0)
        }
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in self.caliyor = false; self.durum = "bitti" }
    }
}
