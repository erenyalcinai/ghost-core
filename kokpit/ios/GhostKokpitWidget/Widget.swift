import ActivityKit
import SwiftUI
import WidgetKit

// MARK: Nabız widget'ı

struct NabizGiris: TimelineEntry {
    let date: Date
    let nabiz: Nabiz?
    let hata: String?
}

struct NabizSaglayici: TimelineProvider {
    func placeholder(in context: Context) -> NabizGiris {
        NabizGiris(date: Date(), nabiz: nil, hata: nil)
    }

    func getSnapshot(in context: Context, completion: @escaping (NabizGiris) -> Void) {
        Task { completion(await cek()) }
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<NabizGiris>) -> Void) {
        Task {
            let g = await cek()
            let sonraki = Calendar.current.date(byAdding: .minute, value: 15, to: Date())!
            completion(Timeline(entries: [g], policy: .after(sonraki)))
        }
    }

    private func cek() async -> NabizGiris {
        guard let api = GhostAPI.keychaindan() else {
            return NabizGiris(date: Date(), nabiz: nil, hata: "eşlenmemiş")
        }
        do {
            return NabizGiris(date: Date(), nabiz: try await api.nabiz(), hata: nil)
        } catch {
            return NabizGiris(date: Date(), nabiz: nil, hata: "ulaşılamadı")
        }
    }
}

struct NabizWidgetView: View {
    @Environment(\.widgetFamily) private var aile
    let giris: NabizGiris

    private var renk: Color { giris.nabiz == nil ? Renk.ikincil : Renk.durum(giris.nabiz?.ozet) }
    private var onay: Int { giris.nabiz?.isler?.bekleyenOnay ?? 0 }
    private var aktif: Int { giris.nabiz?.isler?.aktif ?? 0 }
    private var ozet: String {
        if let h = giris.hata { return h }
        switch (giris.nabiz?.ozet ?? "").lowercased() {
        case "iyi": return "Her şey yolunda"
        case "uyari", "uyarı": return "Uyarı var"
        case "kritik": return "Kritik durum"
        default: return "Bilinmiyor"
        }
    }

    var body: some View {
        switch aile {
        case .accessoryCircular:
            ZStack {
                AccessoryWidgetBackground()
                VStack(spacing: 1) {
                    Image(systemName: "waveform.path.ecg").font(.caption)
                    Text(onay > 0 ? "\(onay)" : "✓").font(.headline)
                }
            }
        case .accessoryRectangular:
            HStack(spacing: 8) {
                Circle().fill(renk).frame(width: 8, height: 8)
                VStack(alignment: .leading, spacing: 1) {
                    Text("Ghost · " + ozet).font(.headline)
                    Text(onay > 0 ? "\(onay) onay bekliyor" : (aktif > 0 ? "\(aktif) iş çalışıyor" : "Bekleyen yok"))
                        .font(.caption)
                }
            }
        default:
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    Circle().fill(renk).frame(width: 10, height: 10)
                    Text("Ghost").font(.headline).foregroundStyle(Renk.metin)
                    Spacer()
                    if giris.nabiz?.freeze == true { Text("FREEZE").font(.caption2.bold()).foregroundStyle(Renk.kritik) }
                }
                Text(ozet).font(.subheadline).foregroundStyle(Renk.metin.opacity(0.9))
                Spacer(minLength: 0)
                HStack(spacing: 12) {
                    sayac(onay, "onay", onay > 0 ? Renk.uyari : Renk.metin)
                    sayac(aktif, "iş", Renk.metin)
                    if aile == .systemMedium { Spacer(); makineler }
                }
                Text(giris.date, style: .time).font(.caption2).foregroundStyle(Renk.ikincil)
            }
            .containerBackground(Renk.zemin, for: .widget)
        }
    }

    private func sayac(_ n: Int, _ etiket: String, _ r: Color) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("\(n)").font(.title2.weight(.semibold).monospacedDigit()).foregroundStyle(r)
            Text(etiket).font(.caption2).foregroundStyle(Renk.ikincil)
        }
    }

    private var makineler: some View {
        VStack(alignment: .leading, spacing: 3) {
            ForEach(giris.nabiz?.makineler ?? []) { m in
                HStack(spacing: 5) {
                    Circle().fill(Renk.durum(m.durum)).frame(width: 6, height: 6)
                    Text(m.ad).font(.caption2).foregroundStyle(Renk.ikincil).lineLimit(1)
                }
            }
        }
    }
}

struct NabizWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "GhostNabiz", provider: NabizSaglayici()) { g in
            NabizWidgetView(giris: g)
        }
        .configurationDisplayName("Ghost nabız")
        .description("Durum, bekleyen onay ve çalışan iş sayısı.")
        .supportedFamilies([.systemSmall, .systemMedium, .accessoryCircular, .accessoryRectangular])
    }
}

// MARK: Live Activity — iş kartı

struct IsEtkinlikWidget: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: IsEtkinligi.self) { ctx in
            kilitEkrani(ctx)
        } dynamicIsland: { ctx in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    Label("İş #\(ctx.attributes.jid)", systemImage: simge(ctx.state.durum)).font(.caption)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Text(ctx.state.durum).font(.caption).foregroundStyle(durumRenk(ctx.state.durum))
                }
                DynamicIslandExpandedRegion(.bottom) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(ctx.attributes.tur).font(.caption2).foregroundStyle(.secondary)
                        Text(ctx.state.ozet).font(.caption).lineLimit(2)
                        sure(ctx.state)
                    }
                }
            } compactLeading: {
                Image(systemName: simge(ctx.state.durum)).foregroundStyle(durumRenk(ctx.state.durum))
            } compactTrailing: {
                Text("#\(ctx.attributes.jid)").font(.caption2)
            } minimal: {
                Image(systemName: simge(ctx.state.durum)).foregroundStyle(durumRenk(ctx.state.durum))
            }
        }
    }

    private func kilitEkrani(_ ctx: ActivityViewContext<IsEtkinligi>) -> some View {
        HStack(spacing: 12) {
            Image(systemName: simge(ctx.state.durum)).font(.title2).foregroundStyle(durumRenk(ctx.state.durum))
            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    Text("Ghost · İş #\(ctx.attributes.jid)").font(.headline)
                    Spacer()
                    Text(ctx.state.durum).font(.caption).foregroundStyle(durumRenk(ctx.state.durum))
                }
                Text(ctx.attributes.tur).font(.caption).foregroundStyle(.secondary)
                Text(ctx.state.ozet).font(.subheadline).lineLimit(2)
                sure(ctx.state)
            }
        }
        .padding(14)
        .activityBackgroundTint(Renk.zemin)
        .activitySystemActionForegroundColor(Renk.metin)
    }

    @ViewBuilder private func sure(_ s: IsEtkinligi.ContentState) -> some View {
        let bas = Date(timeIntervalSince1970: TimeInterval(s.basladi))
        if let b = s.bitti {
            Text("\(max(0, b - s.basladi)) sn sürdü").font(.caption2).foregroundStyle(.secondary)
        } else {
            Text(bas, style: .timer).font(.caption2.monospacedDigit()).foregroundStyle(.secondary)
        }
    }

    private func simge(_ d: String) -> String {
        switch d {
        case "bitti": return "checkmark.circle.fill"
        case "hata", "iptal": return "xmark.circle.fill"
        case "denetimde": return "eye.circle"
        default: return "gearshape.2"
        }
    }

    private func durumRenk(_ d: String) -> Color {
        switch d {
        case "bitti": return Renk.iyi
        case "hata", "iptal": return Renk.kritik
        case "denetimde": return Renk.uyari
        default: return Renk.metin
        }
    }
}

@main
struct GhostWidgetPaketi: WidgetBundle {
    var body: some Widget {
        NabizWidget()
        IsEtkinlikWidget()
    }
}
