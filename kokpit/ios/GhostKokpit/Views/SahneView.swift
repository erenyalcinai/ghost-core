import SwiftUI
import Charts

/// "Şimdi" — Ghost'un anlık sahnesi. 2026-10-04'e kadar bu dosya kendi sekmesi olan SahneView'du;
/// Sahip kararıyla sekme "Masa" oldu (MasaView), Sahne onun üst katmanı: tablo/diff/grafik/canlı
/// ekran/onay önizlemesi burada belirir, kalıcı içerik altındaki kartlarda yaşar.
/// Uzun yoklamayla seq değişince yenilenir. Başlığa dokun → katlanır (ayar kalıcı).
/// Saat simgesi → önceki sahneler (son 8); `eski` doluysa o gösterilir, canlı sahne arkada akar.
struct SimdiBolumu: View {
    @EnvironmentObject var oturum: Oturum
    @Environment(\.scenePhase) private var scenePhase
    @AppStorage("masa.simdi.acik") private var acik = true
    @State private var sahne: SahneZarf?
    @State private var hata: String?
    @State private var eski: SahneZarf?
    @State private var gecmis: [SahneGecmisOge] = []

    private var gosterilen: SahneZarf? { eski ?? sahne }
    private var bos: Bool { (gosterilen?.bilesenler ?? []).isEmpty }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) { acik.toggle() }
                } label: {
                    HStack(spacing: 6) {
                        BolumBaslik(metin: baslik)
                        Image(systemName: acik ? "chevron.down" : "chevron.right")
                            .font(.caption.weight(.semibold)).foregroundStyle(Renk.ikincil).padding(.top, 6)
                    }
                }
                .buttonStyle(.plain)
                Menu {
                    if gecmis.isEmpty { Text("Önceki sahne yok") }
                    ForEach(gecmis.reversed()) { g in
                        Button { Task { await eskiyiAc(g.seq) } } label: {
                            Label("\((g.baslik ?? "").isEmpty ? "Sahne" : g.baslik!) · \((g.ts ?? "").suffix(8).prefix(5))",
                                  systemImage: "rectangle.on.rectangle")
                        }
                    }
                    if eski != nil {
                        Divider()
                        Button("Şimdiye dön") { withAnimation { eski = nil } }
                    }
                } label: {
                    Image(systemName: "clock.arrow.circlepath").font(.footnote).foregroundStyle(Renk.ikincil).padding(.top, 6)
                }
                .menuIndicator(.hidden)
                .accessibilityLabel("Önceki sahneler")
            }
            if acik {
                if let h = hata {
                    Text(h).font(.footnote).foregroundStyle(Renk.uyari).kart()
                }
                if let e = eski {
                    HStack(spacing: 8) {
                        Image(systemName: "clock.arrow.circlepath").foregroundStyle(Renk.uyari)
                        Text("Önceki sahne · seq \(e.seq)" + ((sahne?.seq ?? 0) > e.seq ? " · yeni sahne var" : ""))
                            .font(.footnote).foregroundStyle(Renk.metin)
                        Spacer()
                        Button("Şimdiye dön") { withAnimation { eski = nil } }
                            .font(.footnote.weight(.medium)).foregroundStyle(Renk.metin)
                    }
                    .kart()
                }
                if let s = gosterilen, !s.bilesenler.isEmpty {
                    ForEach(Array(s.bilesenler.enumerated()), id: \.offset) { _, b in
                        BilesenView(b: b)
                    }
                    if let ts = s.ts, !ts.isEmpty {
                        Text("seq \(s.seq) · \(ts)").font(.caption2).foregroundStyle(Renk.ikincil)
                    }
                } else {
                    HStack(spacing: 8) {
                        Image(systemName: "rectangle.dashed").foregroundStyle(Renk.ikincil)
                        Text(sahne == nil ? "Bağlanıyor…" : "Sahne boş — Ghost tablo, diff ya da canlı ekran gösterince burada belirir.")
                            .font(.footnote).foregroundStyle(Renk.ikincil)
                    }
                    .kart()
                }
            }
        }
        .task(id: scenePhase) {
            guard scenePhase == .active else { return }
            await dongu()
        }
    }

    private var baslik: String {
        let b = gosterilen?.baslik ?? ""
        return b.isEmpty || bos ? "Şimdi" : "Şimdi · " + b
    }

    @MainActor
    private func eskiyiAc(_ seq: Int) async {
        do {
            let z = try await oturum.api.sahneSeq(seq)
            withAnimation { eski = (z.seq == sahne?.seq) ? nil : z }
        } catch {
            hata = "Önceki sahne okunamadı: " + error.localizedDescription
        }
    }

    @MainActor
    private func gecmisiYenile() async {
        if let g = try? await oturum.api.sahneGecmis() { gecmis = g.gecmis }
    }

    @MainActor
    private func dongu() async {
        await yenile(bekle: 0)
        await gecmisiYenile()
        while !Task.isCancelled {
            let t = Date()
            await yenile(bekle: 25)
            if Date().timeIntervalSince(t) < 1 { try? await Task.sleep(for: .seconds(1)) }
        }
    }

    @MainActor
    private func yenile(bekle: Int) async {
        do {
            let z = try await oturum.api.sahne(since: sahne?.seq ?? -1, bekle: bekle)
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

/// Sahne'nin tepesinde KALICI şerit (Sahip, 2026-09-04: "sahne ekranında mac ve telefonu
/// göremiyorum"): Mac ve iPhone ekranı iki küçük kartta canlı, dokununca tam ekran.
/// Ghost'un bastığı sahne bunun altına gelir; şerit başlığa dokununca katlanır (ayar kalıcı)
/// ve katlıyken hiç yoklama yapmaz. Tam ekran açıkken kartlar durur — aynı uzuvdan iki akış çekilmez.
struct UzuvlarSeridi: View {
    @AppStorage("sahne.uzuvlar.acik") private var acik = true
    @State private var tamEkran: UzuvSecimi?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                withAnimation(.easeInOut(duration: 0.2)) { acik.toggle() }
            } label: {
                HStack(spacing: 6) {
                    BolumBaslik(metin: "Uzuvlar")
                    Image(systemName: acik ? "chevron.down" : "chevron.right")
                        .font(.caption.weight(.semibold)).foregroundStyle(Renk.ikincil).padding(.top, 6)
                }
            }
            .buttonStyle(.plain)
            if acik {
                HStack(alignment: .top, spacing: 10) {
                    if Duzen.macKarti {
                        UzuvKarti(kaynak: "mac", ad: "Mac", simge: "appbopcomputer",
                                  duraklat: tamEkran != nil) { tamEkran = UzuvSecimi(id: "mac") }
                    }
                    UzuvKarti(kaynak: "telefon", ad: "iPhone", simge: "iphone",
                              duraklat: tamEkran != nil) { tamEkran = UzuvSecimi(id: "telefon") }
                }
            }
        }
        .tamEkranKapak(item: $tamEkran) { CanliTamEkran(kaynak: $0.id) }
    }
}

struct UzuvSecimi: Identifiable { let id: String }

/// Şerit kartı: küçük canlı kare + ad + durum noktası (kare geliyor / uzuv uykuda).
struct UzuvKarti: View {
    let kaynak: String
    let ad: String
    let simge: String
    var duraklat = false
    let dokun: () -> Void
    @State private var canli: Bool?

    var body: some View {
        Button(action: dokun) {
            VStack(alignment: .leading, spacing: 6) {
                if duraklat {
                    ZStack {
                        RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Renk.kart)
                        Image(systemName: simge).font(.title2).foregroundStyle(Renk.ikincil)
                    }
                    .frame(maxWidth: .infinity).frame(height: 150)
                } else {
                    // 2026-09-06: 3 sn'de bir iki kare (~1 Mbit/sn yükleme) Mac'in zayıf Wi-Fi uplink'ini
                    // dolduruyor, tünel keepalive'ları kuyrukta kalıp kopuyordu. Şerit kartı 8 sn; tam ekran yine canlı.
                    CanliGorunum(kaynak: kaynak, yukseklik: 150, kucuk: true, aralik: 8) { canli = $0 }
                }
                HStack(spacing: 6) {
                    Nokta(renk: canli == nil ? Renk.ikincil : (canli! ? Renk.iyi : Renk.kritik), boyut: 6)
                    Image(systemName: simge).font(.caption).foregroundStyle(Renk.ikincil)
                    Text(ad).font(.subheadline.weight(.medium)).foregroundStyle(Renk.metin)
                    Spacer()
                    Image(systemName: "arrow.up.left.and.arrow.down.right")
                        .font(.caption2).foregroundStyle(Renk.ikincil)
                }
                .padding(.horizontal, 2)
            }
        }
        .buttonStyle(.plain)
        .frame(maxWidth: .infinity)
    }
}

/// Bileşen ağacını çizer; bilinmeyen tür → "kabuk güncellemesi gerekli" kartı.
struct BilesenView: View {
    @EnvironmentObject var oturum: Oturum
    let b: Bilesen

    var body: some View {
        switch b.tur {
        case "metin":
            Text(b.metin ?? "").foregroundStyle(Renk.metin).textSelection(.enabled)
        case "kart":
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    if let d = b.durum { Nokta(renk: Renk.durum(d)) }
                    Text(b.baslik ?? "").font(.headline).foregroundStyle(Renk.metin)
                }
                if let m = b.metin, !m.isEmpty {
                    Text(m).font(.subheadline).foregroundStyle(Renk.metin.opacity(0.85))
                }
                cocuklar
            }
            .kart()
        case "liste":
            VStack(alignment: .leading, spacing: 6) {
                if let t = b.baslik { BolumBaslik(metin: t) }
                ForEach(Array((b.ogeler ?? []).enumerated()), id: \.offset) { _, o in
                    HStack(alignment: .top, spacing: 8) {
                        if let d = o.durum { Nokta(renk: Renk.durum(d)).padding(.top, 6) }
                        else { Text("•").foregroundStyle(Renk.ikincil) }
                        VStack(alignment: .leading, spacing: 2) {
                            Text(o.metin).foregroundStyle(Renk.metin)
                            if let d = o.detay { Text(d).font(.footnote).foregroundStyle(Renk.ikincil) }
                        }
                    }
                }
            }
            .kart()
        case "tablo":
            tablo
        case "grafik":
            grafik
        case "gorsel":
            gorsel
        case "diff":
            diff
        case "form":
            FormGorunum(b: b)
        case "eylem":
            EylemDugmesi(b: b)
        case "canli":
            VStack(alignment: .leading, spacing: 6) {
                if let t = b.baslik { BolumBaslik(metin: t) }
                CanliGorunum(kaynak: b.kaynak ?? "mac", yukseklik: CGFloat(b.yukseklik ?? 360))
            }
        case "sutun":
            VStack(alignment: .leading, spacing: 10) { cocuklar }
        case "satir":
            HStack(alignment: .top, spacing: 10) { cocuklar }
        default:
            VStack(alignment: .leading, spacing: 4) {
                Text("Kabuk güncellemesi gerekli").font(.subheadline).foregroundStyle(Renk.uyari)
                Text("Bilinmeyen bileşen: \(b.tur)").font(.footnote).foregroundStyle(Renk.ikincil)
            }
            .kart()
        }
    }

    @ViewBuilder private var cocuklar: some View {
        ForEach(Array((b.cocuklar ?? []).enumerated()), id: \.offset) { _, c in
            BilesenView(b: c)
        }
    }

    private var tablo: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let t = b.baslik { BolumBaslik(metin: t) }
            ScrollView(.horizontal, showsIndicators: false) {
                Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 6) {
                    if let s = b.sutunlar {
                        GridRow {
                            ForEach(Array(s.enumerated()), id: \.offset) { _, h in
                                Text(h).font(.caption.weight(.semibold)).foregroundStyle(Renk.ikincil)
                            }
                        }
                        Divider().overlay(Renk.ikincil.opacity(0.3))
                    }
                    ForEach(Array((b.satirlar ?? []).enumerated()), id: \.offset) { _, r in
                        GridRow {
                            ForEach(Array(r.enumerated()), id: \.offset) { _, h in
                                Text(h.metin).font(.subheadline.monospacedDigit()).foregroundStyle(Renk.metin)
                            }
                        }
                    }
                }
            }
        }
        .kart()
    }

    private struct Nokta2D: Identifiable {
        let id: String; let seri: String; let x: String; let y: Double; let sira: Int
    }

    private var noktalar: [Nokta2D] {
        var out: [Nokta2D] = []
        for (si, s) in (b.seriler ?? []).enumerated() {
            let ad = s.ad ?? "seri \(si + 1)"
            for (i, y) in s.degerler.enumerated() {
                let x = (b.etiketler ?? []).indices.contains(i) ? b.etiketler![i] : String(i + 1)
                out.append(Nokta2D(id: "\(si)-\(i)", seri: ad, x: x, y: y, sira: i))
            }
        }
        return out
    }

    private var grafik: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let t = b.baslik { BolumBaslik(metin: t) }
            Chart(noktalar) { n in
                if b.tip == "cubuk" {
                    BarMark(x: .value("x", n.x), y: .value("y", n.y))
                        .foregroundStyle(by: .value("seri", n.seri))
                } else {
                    LineMark(x: .value("x", n.x), y: .value("y", n.y))
                        .foregroundStyle(by: .value("seri", n.seri))
                        .interpolationMethod(.monotone)
                }
            }
            .chartLegend((b.seriler ?? []).count > 1 ? .visible : .hidden)
            .chartForegroundStyleScale(range: [Renk.metin, Renk.uyari, Renk.kritik, Renk.ikincil])
            .frame(height: CGFloat(b.yukseklik ?? 180))
        }
        .kart()
    }

    private var gorsel: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let t = b.baslik { BolumBaslik(metin: t) }
            if let u = b.url, u.hasPrefix("/") {
                ImzaliGorsel(yol: u)
            } else if let u = b.url, let url = URL(string: u) {
                AsyncImage(url: url) { p in
                    p.resizable().scaledToFit()
                } placeholder: { ProgressView().tint(Renk.ikincil) }
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            }
            if let m = b.metin { Text(m).font(.footnote).foregroundStyle(Renk.ikincil) }
        }
    }

    private var diff: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let t = b.baslik { BolumBaslik(metin: t) }
            ScrollView(.horizontal, showsIndicators: false) {
                VStack(alignment: .leading, spacing: 1) {
                    ForEach(Array((b.metin ?? "").components(separatedBy: "\n").enumerated()), id: \.offset) { _, s in
                        Text(s.isEmpty ? " " : s)
                            .font(.caption.monospaced())
                            .foregroundStyle(diffRenk(s))
                            .padding(.horizontal, 6).padding(.vertical, 1)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(diffRenk(s).opacity(s.hasPrefix("+") || s.hasPrefix("-") ? 0.10 : 0))
                    }
                }
            }
        }
        .kart()
    }

    private func diffRenk(_ s: String) -> Color {
        if s.hasPrefix("+++") || s.hasPrefix("---") { return Renk.ikincil }
        if s.hasPrefix("+") { return Color(hex: 0x7BC47F) }
        if s.hasPrefix("-") { return Renk.kritik }
        if s.hasPrefix("@@") { return Renk.uyari }
        return Renk.metin
    }
}

/// API'den imzalı tek görsel (ör. /v1/sahne/mac.jpg) — bir kez çekilir.
struct ImzaliGorsel: View {
    @EnvironmentObject var oturum: Oturum
    let yol: String
    @State private var img: PlatformImage?
    @State private var hata: String?

    /// Önbellekteki görsel İLK karede gelsin: `.task` bir kare sonra koşar, o karede
    /// "yükleniyor…" (30 pt) çizilip sonra görsel (≤ gorselTavan) açılıyordu — sohbette
    /// üstteki satır büyüyünce liste kayıyordu (2026-09-26).
    init(yol: String) {
        self.yol = yol
        _img = State(initialValue: GorselOnbellek.paylasilan.object(forKey: yol as NSString))
    }

    var body: some View {
        Group {
            if let i = img {
                Image(platform: i).resizable().scaledToFit()
                    .frame(maxHeight: Duzen.gorselTavan)
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            } else {
                Text(hata ?? "yükleniyor…").font(.footnote).foregroundStyle(Renk.ikincil).kart()
            }
        }
        .task(id: yol) {
            if let c = GorselOnbellek.paylasilan.object(forKey: yol as NSString) { img = c; return }
            do {
                let veri = try await oturum.api.kare(yol)
                if let i = PlatformImage(data: veri) {
                    GorselOnbellek.paylasilan.setObject(i, forKey: yol as NSString, cost: veri.count)
                    img = i
                } else { hata = "görsel çözülemedi" }
            } catch { hata = error.localizedDescription }
        }
    }
}

struct EylemDugmesi: View {
    @EnvironmentObject var oturum: Oturum
    let b: Bilesen
    @State private var durum: String?
    @State private var gonderiliyor = false

    var body: some View {
        Button {
            Task { await gonder() }
        } label: {
            HStack(spacing: 6) {
                if gonderiliyor { ProgressView().tint(Renk.ikincil) }
                Text(durum ?? (b.etiket ?? "Eylem")).font(.subheadline.weight(.medium))
            }
            .frame(maxWidth: .infinity)
        }
        .buttonStyle(.bordered)
        .tint(b.stil == "tehlike" ? Renk.kritik : Renk.metin)
        .disabled(gonderiliyor)
    }

    @MainActor
    private func gonder() async {
        gonderiliyor = true
        defer { gonderiliyor = false }
        do {
            try await oturum.api.eylem(id: b.eylemID ?? b.etiket ?? "eylem", veri: b.veri ?? b.etiket)
            durum = "Gönderildi ✓"
            try? await Task.sleep(for: .seconds(1.5))
            durum = nil
        } catch {
            durum = "Hata: " + error.localizedDescription
        }
    }
}

struct FormGorunum: View {
    @EnvironmentObject var oturum: Oturum
    let b: Bilesen
    @State private var degerler: [String: String] = [:]
    @State private var durum: String?
    @State private var gonderiliyor = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let t = b.baslik { Text(t).font(.headline).foregroundStyle(Renk.metin) }
            if let m = b.metin { Text(m).font(.footnote).foregroundStyle(Renk.ikincil) }
            ForEach(b.alanlar ?? [], id: \.ad) { a in
                VStack(alignment: .leading, spacing: 4) {
                    Text(a.etiket ?? a.ad).font(.caption).foregroundStyle(Renk.ikincil)
                    TextField(a.etiket ?? a.ad, text: baglama(a), axis: a.tip == "uzun" ? .vertical : .horizontal)
                        .lineLimit(a.tip == "uzun" ? 3...6 : 1...1)
                        #if os(iOS)
                        .keyboardType(a.tip == "sayi" ? .decimalPad : .default)
                        #endif
                        .foregroundStyle(Renk.metin)
                        .padding(.horizontal, 10).padding(.vertical, 8)
                        .background(Renk.kartUst, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                }
            }
            Button {
                Task { await gonder() }
            } label: {
                Text(durum ?? "Gönder").font(.subheadline.weight(.medium)).frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(Renk.metin)
            .foregroundStyle(Renk.zemin)
            .disabled(gonderiliyor)
        }
        .kart()
    }

    private func baglama(_ a: SahneAlan) -> Binding<String> {
        Binding(get: { degerler[a.ad] ?? a.varsayilan ?? "" },
                set: { degerler[a.ad] = $0 })
    }

    @MainActor
    private func gonder() async {
        gonderiliyor = true
        defer { gonderiliyor = false }
        var alanlar: [String: String] = [:]
        for a in b.alanlar ?? [] { alanlar[a.ad] = degerler[a.ad] ?? a.varsayilan ?? "" }
        do {
            try await oturum.api.eylem(id: b.eylemID ?? "form", alanlar: alanlar)
            durum = "Gönderildi ✓"
            try? await Task.sleep(for: .seconds(1.5))
            durum = nil
        } catch {
            durum = "Hata: " + error.localizedDescription
        }
    }
}
