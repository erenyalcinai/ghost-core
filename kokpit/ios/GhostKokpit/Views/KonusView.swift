import SwiftUI
import AVFoundation
#if canImport(ElevenLabs)
import ElevenLabs
#endif

/// Konuş — Ghost'un HIZLI sesli yüzü (2026-09-26, Sahip: "sesli konuşma ajanını yapmadık").
///
/// Ghost'un bir turu 10 sn–dakikalar; sesli sohbet 1 sn altını ister. Bu ekran Ghost'un
/// kendisiyle değil, dar görevli bir ses ajanıyla (ElevenLabs Agents, WebRTC) konuşur:
/// ajan durumu/son mesajları sunucudaki araçlardan okur, iş isteğini Ghost'un Sohbet
/// kuyruğuna bırakır ("Ghost'a verdim"). Ghost bitirince cevap Sohbet'e ve sesli düşer.
/// Jeton sunucudan (`/v1/ses/token`), ElevenLabs anahtarı telefonda YOK.
struct KonusView: View {
    @EnvironmentObject var oturum: Oturum
    @Environment(\.dismiss) private var kapat

    var body: some View {
        NavigationStack {
            Group {
                #if canImport(ElevenLabs)
                KonusGovde()
                #else
                VStack(spacing: 10) {
                    Image(systemName: "waveform.slash").font(.largeTitle).foregroundStyle(Renk.ikincil)
                    Text("Echo yalnız iPhone'da.").foregroundStyle(Renk.ikincil)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                #endif
            }
            .background(Renk.zemin)
            .navigationTitle("Echo")
            .satirBaslik()
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Kapat") { kapat() } }
            }
        }
    }
}

#if canImport(ElevenLabs)
@MainActor
final class KonusModel: ObservableObject {
    @Published var sohbet: Conversation?
    @Published var hata: String?
    @Published var baglaniyor = false
    private var baglantiGorevi: Task<Void, Never>?

    func baslat(api: GhostAPI) {
        guard sohbet == nil, !baglaniyor else { return }
        baglaniyor = true; hata = nil
        baglantiGorevi = Task { [weak self] in
            guard let self else { return }
            do {
                try Self.sesOturumuHazirla()
                let jeton = try await api.sesToken()
                let c = try await ElevenLabs.startConversation(conversationToken: jeton)
                self.sohbet = c
            } catch is CancellationError {
            } catch {
                self.hata = error.localizedDescription
            }
            self.baglaniyor = false
        }
    }

    /// Ses oturumunu konuşmaya hazırla — her açılışta, LiveKit'ten ÖNCE.
    /// Kök neden (2026-09-26 12:54, 4 deneme, "Audio engine returned error code: -3001"):
    /// -3001 = LiveKit webrtc çatalında kAudioEnginePlayoutStartError (hoparlör çıkışı başlamadı).
    /// Sohbet'teki dikte (`Konusma`) kategoriyi `.record/.measurement` yapar, sesli mesaj
    /// oynatma `.playback`; LiveKit `AudioSessionEngineObserver` kategoriyi yalnız KENDİ
    /// gereksinimi değişince yazar, dışarıdan değişeni görmez → ilk konuşma çalışır, dikteden
    /// sonra ikincisi çıkışsız kategoride motor başlatamaz. Burada açıkça play+record'a çekiyoruz.
    static func sesOturumuHazirla() throws {
        let s = AVAudioSession.sharedInstance()
        try s.setCategory(.playAndRecord, mode: .voiceChat, options: [.defaultToSpeaker, .allowBluetoothHFP])
        try s.setActive(true)
    }

    func bitir() {
        baglantiGorevi?.cancel(); baglantiGorevi = nil
        let c = sohbet
        sohbet = nil; baglaniyor = false
        Task { await c?.endConversation() }
    }
}

private struct KonusGovde: View {
    @EnvironmentObject var oturum: Oturum
    @StateObject private var model = KonusModel()

    var body: some View {
        VStack(spacing: 0) {
            if let c = model.sohbet {
                KonusAktif(sohbet: c) { model.bitir() }
            } else {
                VStack(spacing: 18) {
                    Spacer()
                    Image(systemName: "waveform.circle.fill")
                        .font(.system(size: 72)).foregroundStyle(Renk.metin)
                        .symbolEffect(.pulse, isActive: model.baglaniyor)
                    Text(model.baglaniyor ? "Bağlanıyor…" : "Echo ile konuş")
                        .font(.headline).foregroundStyle(Renk.metin)
                    Text("Ghost'un yankısı: durum sorar, son mesajları dinlersin; iş verirsen Ghost'a iletir.")
                        .font(.footnote).foregroundStyle(Renk.ikincil)
                        .multilineTextAlignment(.center).padding(.horizontal, 32)
                    if let h = model.hata {
                        Text(h).font(.caption).foregroundStyle(Renk.kritik)
                            .multilineTextAlignment(.center).padding(.horizontal, 24)
                    }
                    Button {
                        model.baslat(api: oturum.api)
                    } label: {
                        Label(model.baglaniyor ? "Bağlanıyor" : "Başla", systemImage: "mic.fill")
                            .font(.headline).frame(maxWidth: 220).padding(.vertical, 6)
                    }
                    .buttonStyle(.borderedProminent).tint(Renk.metin).foregroundStyle(Renk.zemin)
                    .disabled(model.baglaniyor)
                    Spacer()
                }
                .frame(maxWidth: .infinity)
            }
        }
        .onDisappear { model.bitir() }
    }
}

/// Aktif oturum: durum şeridi + canlı döküm + Bitir. `Conversation` ObservableObject;
/// messages/state/agentState yayınlanır, görünüm kendiliğinden tazelenir.
private struct KonusAktif: View {
    @ObservedObject var sohbet: Conversation
    var bitir: () -> Void

    private var durumMetni: String {
        switch sohbet.state {
        case .idle: return "hazır"
        case .connecting: return "bağlanıyor…"
        case .active: return ajanDurumu
        case .ended: return "bitti"
        case .error: return "hata"
        }
    }

    /// SDK'nın ajan durumu (dinliyor/konuşuyor). Enum adları sürümle oynayabilir;
    /// açıklamadan türetiyoruz ki derleme kırılmasın.
    private var ajanDurumu: String {
        let s = String(describing: sohbet.agentState).lowercased()
        if s.contains("speak") { return "Echo konuşuyor" }
        if s.contains("listen") { return "dinliyor" }
        if s.contains("think") { return "düşünüyor" }
        return s
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Circle().fill(ajanDurumu.contains("konuş") ? Renk.uyari : Renk.iyi)
                    .frame(width: 8, height: 8)
                Text(durumMetni).font(.footnote).foregroundStyle(Renk.ikincil)
                Spacer()
            }
            .padding(.horizontal, 16).padding(.vertical, 10)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    ForEach(sohbet.messages) { m in
                        let benim = String(describing: m.role).lowercased().contains("user")
                        HStack {
                            if benim { Spacer(minLength: 40) }
                            Text(m.content)
                                .foregroundStyle(Renk.metin)
                                .padding(.horizontal, 12).padding(.vertical, 8)
                                .background(benim ? Renk.kartUst : Renk.kart,
                                            in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                            if !benim { Spacer(minLength: 40) }
                        }
                        .id(m.id)
                    }
                }
                .padding(.horizontal, 12).padding(.vertical, 8)
            }
            .defaultScrollAnchor(.bottom)
            Button(role: .destructive) { bitir() } label: {
                Label("Bitir", systemImage: "phone.down.fill")
                    .font(.headline).frame(maxWidth: 220).padding(.vertical, 6)
            }
            .buttonStyle(.borderedProminent).tint(Renk.kritik)
            .padding(.vertical, 16)
        }
    }
}
#endif
