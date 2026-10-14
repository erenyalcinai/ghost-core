import SwiftUI
import PhotosUI
import QuickLook
import UniformTypeIdentifiers

/// Masa (2026-10-04, Sahip kararı): Sahne sekmesinin yerini aldı. Sahne "fikir olarak güzel ama
/// gereksiz kullanılıyordu" — sohbetin kopyası, bayat. Şimdi bu ekran iki katman:
///   1. **Şimdi** — Ghost'un anlık sahnesi (tablo/diff/canlı/onay önizlemesi). Eski Sahne gövdesi.
///   2. **Masa** — Ghost ile sahibin ORTAK, KALICI kartları (App B modeli: kart = kap; not, rapor,
///      görsel, belge, alt kart). Ghost raporlarını buraya yayınlar; Sahip not/fotoğraf/belge ekler.
/// Yapı bozulmaz: Masa bir yayın katmanıdır, Ghost'un çalışma dosyaları değil (ghost/masa.py).
/// Sahip Ghost'un yayınını düzenleyemez (yanına not ekler ya da "Ghost'a ilet"); "Sil" Ghost kaydını
/// Dolab'a kaldırır. Kurallar sunucuda; uygulama 403'ü olduğu gibi gösterir.
struct MasaView: View {
    @EnvironmentObject var oturum: Oturum
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var model = MasaModel()

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    UzuvlarSeridi()
                    SimdiBolumu()
                    if let h = model.hata {
                        Text(h).font(.footnote).foregroundStyle(Renk.uyari).kart()
                    }
                    KlasorIcerik(ebeveyn: nil, model: model)
                }
                .padding()
            }
            .ghostZemin()
            .navigationTitle("Masa")
            .satirBaslik()
            .aracMenusu()
            .toolbar {
                ToolbarItem(placement: .ghostSol) {
                    MasaEkleMenusu(ebeveyn: nil, model: model)
                }
            }
            .modifier(MasaDiyaloglari(model: model))
            .navigationDestination(for: MasaHedef.self) { h in
                switch h {
                case .klasor(let id): KlasorSayfa(id: id, model: model)
                case .oge(let id): OgeSayfa(id: id, model: model)
                case .dolap: DolapSayfa(model: model)
                }
            }
            .yenilenebilir { await model.yenile() }
            .task(id: scenePhase) {
                guard scenePhase == .active else { return }
                model.baglan(oturum.api)
                await model.dongu()
            }
        }
    }
}

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
            Titre.hafif()
            return y
        } catch {
            hata = (error as? APIHata)?.errorDescription ?? error.localizedDescription
            Titre.hata()
            return nil
        }
    }

    func dosyaYukle(_ veri: Data, ad: String, mime: String, ebeveyn: Int?) async {
        guard veri.count <= 1_800_000 else { hata = "\(ad) çok büyük (\(veri.count / 1024) KB; sınır 1800 KB)."; return }
        await calistir { try await $0.masaDosyaYukle(veri, ad: ad, mime: mime, ebeveyn: ebeveyn) }
    }
}

// MARK: Klasör içeriği (kök ve alt klasör aynı görünüm)

/// Klasörler karo (iki sütun), diğer kayıtlar satır. Boşsa kısa yönlendirme.
struct KlasorIcerik: View {
    let ebeveyn: Int?
    @ObservedObject var model: MasaModel

    private var klasorler: [MasaKart] { model.cocuklar(ebeveyn).filter(\.klasor) }
    private var ogeler: [MasaKart] { model.cocuklar(ebeveyn).filter { !$0.klasor } }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if ebeveyn == nil {
                HStack {
                    BolumBaslik(metin: "Masa")
                    Spacer()
                    NavigationLink(value: MasaHedef.dolap) {
                        Label(model.dolapSayisi > 0 ? "Dolap · \(model.dolapSayisi)" : "Dolap",
                              systemImage: "archivebox")
                            .font(.footnote).foregroundStyle(Renk.ikincil)
                    }
                }
            }
            if !klasorler.isEmpty {
                LazyVGrid(columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)], spacing: 10) {
                    ForEach(klasorler) { k in
                        NavigationLink(value: MasaHedef.klasor(k.id)) { KlasorKaro(kart: k, model: model) }
                            .buttonStyle(.plain)
                            .contextMenu { KartMenusu(kart: k, model: model) }
                    }
                }
            }
            ForEach(ogeler) { o in
                NavigationLink(value: MasaHedef.oge(o.id)) { OgeSatiri(kart: o) }
                    .buttonStyle(.plain)
                    .contextMenu { KartMenusu(kart: o, model: model) }
            }
            if klasorler.isEmpty && ogeler.isEmpty && model.seq >= 0 {
                VStack(spacing: 6) {
                    Image(systemName: "square.grid.2x2").font(.title2).foregroundStyle(Renk.ikincil)
                    Text(ebeveyn == nil ? "Masa boş" : "Bu kart boş").foregroundStyle(Renk.metin)
                    Text("+ ile not, fotoğraf, belge ya da klasör ekle. Ghost raporlarını buraya yayınlar.")
                        .font(.footnote).foregroundStyle(Renk.ikincil).multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity).padding(.vertical, 20)
            }
        }
    }
}

struct KlasorKaro: View {
    let kart: MasaKart
    @ObservedObject var model: MasaModel

    private var icindekiler: [MasaKart] { model.cocuklar(kart.id) }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Image(systemName: "folder.fill").foregroundStyle(Renk.uyari.opacity(0.9))
                Text(kart.ad).font(.headline).foregroundStyle(Renk.metin).lineLimit(1)
            }
            // App B: kapakta içindekilerin özeti — ilk üç satır
            VStack(alignment: .leading, spacing: 3) {
                ForEach(icindekiler.prefix(3)) { c in
                    HStack(spacing: 5) {
                        Image(systemName: TurSimge.ad(c.tur)).font(.caption2).foregroundStyle(Renk.ikincil)
                        Text(c.ad).font(.caption).foregroundStyle(Renk.metin.opacity(0.8)).lineLimit(1)
                    }
                }
                if icindekiler.isEmpty {
                    Text("boş").font(.caption).foregroundStyle(Renk.ikincil)
                } else if icindekiler.count > 3 {
                    Text("+\(icindekiler.count - 3)").font(.caption2).foregroundStyle(Renk.ikincil)
                }
            }
            Spacer(minLength: 0)
            Text(Tarih.kisa(kart.sonIcerik ?? kart.guncelleme)).font(.caption2).foregroundStyle(Renk.ikincil)
        }
        .padding(12)
        .frame(maxWidth: .infinity, minHeight: 124, alignment: .topLeading)
        .background(Renk.kart, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

struct OgeSatiri: View {
    let kart: MasaKart
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: TurSimge.ad(kart.tur)).font(.title3).foregroundStyle(Renk.ikincil).frame(width: 24)
            VStack(alignment: .leading, spacing: 3) {
                Text(kart.ad).font(.subheadline.weight(.medium)).foregroundStyle(Renk.metin).lineLimit(2)
                if let o = kart.onizleme, !o.isEmpty {
                    Text(o).font(.caption).foregroundStyle(Renk.ikincil).lineLimit(2)
                }
                HStack(spacing: 6) {
                    Rozet(metin: kart.ghostun ? "ghost" : "sen", renk: kart.ghostun ? Renk.ikincil : Renk.uyari)
                    Text(TurSimge.etiket(kart.tur)).font(.caption2).foregroundStyle(Renk.ikincil)
                    Text(Tarih.kisa(kart.guncelleme)).font(.caption2).foregroundStyle(Renk.ikincil)
                }
            }
            Spacer(minLength: 0)
            Image(systemName: "chevron.right").font(.caption).foregroundStyle(Renk.ikincil)
        }
        .padding(12)
        .background(Renk.kart, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
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

// MARK: Alt klasör sayfası

struct KlasorSayfa: View {
    let id: Int
    @ObservedObject var model: MasaModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if let k = model.kart(id) {
                    Text(model.kirinti(id)).font(.caption).foregroundStyle(Renk.ikincil)
                    KlasorIcerik(ebeveyn: k.id, model: model)
                } else {
                    Text("Bu kart artık yok.").foregroundStyle(Renk.ikincil).kart()
                }
            }
            .padding()
        }
        .ghostZemin()
        .navigationTitle(model.kart(id)?.ad ?? "Kart")
        .satirBaslik()
        .toolbar {
            ToolbarItem(placement: .ghostSag) {
                HStack(spacing: 14) {
                    MasaEkleMenusu(ebeveyn: id, model: model)
                    if let k = model.kart(id) {
                        Menu { KartMenusu(kart: k, model: model) } label: { Image(systemName: "ellipsis.circle") }
                            .menuIndicator(.hidden)
                    }
                }
            }
        }
    }
}

// MARK: Dolap

struct DolapSayfa: View {
    @ObservedObject var model: MasaModel
    @EnvironmentObject var oturum: Oturum
    @State private var dolap: [MasaKart] = []
    @State private var hata: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                Text("Dolap: biten ya da bekleyen kartlar. Silinmez, depodur; \"Masaya al\" ile geri döner.")
                    .font(.footnote).foregroundStyle(Renk.ikincil)
                if let h = hata { Text(h).font(.footnote).foregroundStyle(Renk.uyari) }
                ForEach(dolap) { k in
                    HStack {
                        OgeSatiri(kart: k)
                    }
                    .contextMenu {
                        Button { Task { await model.calistir { try await $0.masaEylem(k.id, "geri") }; await yukle() } } label: {
                            Label("Masaya al", systemImage: "tray.and.arrow.up")
                        }
                        if !k.ghostun {
                            Button(role: .destructive) {
                                Task { await model.calistir { try await $0.masaEylem(k.id, "sil") }; await yukle() }
                            } label: { Label("Kalıcı sil", systemImage: "trash") }
                        }
                    }
                }
                if dolap.isEmpty { Text("Dolap boş").foregroundStyle(Renk.ikincil).frame(maxWidth: .infinity).padding(.top, 20) }
            }
            .padding()
        }
        .ghostZemin()
        .navigationTitle("Dolap")
        .satirBaslik()
        .task { await yukle() }
    }

    @MainActor private func yukle() async {
        do { dolap = try await oturum.api.masa(dolap: true).kartlar; hata = nil }
        catch { hata = error.localizedDescription }
    }
}

// MARK: Ekleme menüsü (+)

/// App B + menüsü: Not · Fotoğraf · Belge · Klasör. Yazma item ekranında olur (not editörü),
/// fotoğraf/belge seçilince doğrudan yüklenir.
struct MasaEkleMenusu: View {
    let ebeveyn: Int?
    @ObservedObject var model: MasaModel
    @State private var notAcik = false
    @State private var klasorAcik = false
    @State private var baglantiAcik = false
    @State private var galeriAcik = false
    @State private var belgeAcik = false
    @State private var kameraAcik = false
    @State private var secilenFotolar: [PhotosPickerItem] = []
    @State private var yeniAd = ""
    @State private var yeniURL = ""

    var body: some View {
        Menu {
            Button { notAcik = true } label: { Label("Not", systemImage: "note.text") }
            Button { galeriAcik = true } label: { Label("Fotoğraf", systemImage: "photo.on.rectangle") }
            #if os(iOS)
            Button { kameraAcik = true } label: { Label("Kamera", systemImage: "camera") }
            #endif
            Button { belgeAcik = true } label: { Label("Belge", systemImage: "doc") }
            Button { yeniURL = ""; yeniAd = ""; baglantiAcik = true } label: { Label("Bağlantı", systemImage: "link") }
            Divider()
            Button { yeniAd = ""; klasorAcik = true } label: { Label("Klasör", systemImage: "folder.badge.plus") }
        } label: {
            Image(systemName: "plus.circle").accessibilityLabel("Ekle")
        }
        .menuIndicator(.hidden)
        .disabled(model.mesgul)
        .sheet(isPresented: $notAcik) {
            NotEditoru(baslik: "Yeni not", ad: "", metin: "") { ad, metin in
                await model.calistir { try await $0.masaEkle(tur: "not", ad: ad, ebeveyn: ebeveyn, icerik: metin) }
            }
        }
        .alert("Yeni klasör", isPresented: $klasorAcik) {
            TextField("Ad", text: $yeniAd)
            Button("Oluştur") {
                let ad = yeniAd.trimmingCharacters(in: .whitespaces)
                guard !ad.isEmpty else { return }
                Task { await model.calistir { try await $0.masaEkle(tur: "klasor", ad: ad, ebeveyn: ebeveyn) } }
            }
            Button("Vazgeç", role: .cancel) {}
        }
        .alert("Bağlantı ekle", isPresented: $baglantiAcik) {
            TextField("https://…", text: $yeniURL)
            TextField("Ad (isteğe bağlı)", text: $yeniAd)
            Button("Ekle") {
                let u = yeniURL.trimmingCharacters(in: .whitespaces)
                guard !u.isEmpty else { return }
                let ad = yeniAd.trimmingCharacters(in: .whitespaces)
                Task { await model.calistir { try await $0.masaEkle(tur: "baglanti", ad: ad.isEmpty ? u : ad, ebeveyn: ebeveyn, icerik: u) } }
            }
            Button("Vazgeç", role: .cancel) {}
        }
        #if os(iOS)
        .sheet(isPresented: $kameraAcik) {
            Kamera { img in Task { await yukleGorsel(img, ad: "kamera.jpg") } }
                .ignoresSafeArea()
        }
        #endif
        .photosPicker(isPresented: $galeriAcik, selection: $secilenFotolar, maxSelectionCount: 6, matching: .images)
        .onChange(of: secilenFotolar) { _, yeni in
            guard !yeni.isEmpty else { return }
            Task {
                for (n, o) in yeni.enumerated() {
                    if let d = try? await o.loadTransferable(type: Data.self), let img = PlatformImage(data: d) {
                        await yukleGorsel(img, ad: "foto-\(n + 1).jpg")
                    }
                }
                secilenFotolar = []
            }
        }
        .fileImporter(isPresented: $belgeAcik, allowedContentTypes: [.item], allowsMultipleSelection: true) { sonuc in
            switch sonuc {
            case .success(let urller): Task { for u in urller { await yukleBelge(u) } }
            case .failure(let e): model.hata = "Belge seçilemedi: " + e.localizedDescription
            }
        }
    }

    @MainActor private func yukleGorsel(_ img: PlatformImage, ad: String) async {
        // Sunucu 1.8 MB alır; uzun kenar 1600 px JPEG her zaman sığar.
        let veri = GorselYardim.jpeg(GorselYardim.kucult(img, 1600))
        await model.dosyaYukle(veri, ad: ad, mime: "image/jpeg", ebeveyn: ebeveyn)
    }

    @MainActor private func yukleBelge(_ url: URL) async {
        let erisim = url.startAccessingSecurityScopedResource()
        defer { if erisim { url.stopAccessingSecurityScopedResource() } }
        do {
            let veri = try Data(contentsOf: url)
            let mime = UTType(filenameExtension: url.pathExtension)?.preferredMIMEType ?? "application/octet-stream"
            await model.dosyaYukle(veri, ad: url.lastPathComponent, mime: mime, ebeveyn: ebeveyn)
        } catch {
            model.hata = "Belge okunamadı: " + error.localizedDescription
        }
    }
}

// MARK: Kart menüsü (uzun bas / ⋯)

struct KartMenusu: View {
    let kart: MasaKart
    @ObservedObject var model: MasaModel

    var body: some View {
        Button { model.yeniAd = kart.ad; model.diyalog = .adlandir(kart) } label: { Label("Yeniden adlandır", systemImage: "pencil") }
        if kart.sabit != true {
            Button { model.diyalog = .tasi(kart) } label: { Label("Taşı", systemImage: "folder") }
            Button {
                Task { await model.calistir { try await $0.masaEylem(kart.id, "dolap") } }
            } label: { Label("Dolaba kaldır", systemImage: "archivebox") }
        }
        if !kart.klasor {
            Button {
                Task { await model.calistir { try await $0.masaEylem(kart.id, "ilet") } }
            } label: { Label("Ghost'a ilet", systemImage: "paperplane") }
        }
        if kart.sabit != true {
            Divider()
            Button(role: .destructive) { model.diyalog = .sil(kart) } label: {
                // Ghost'un yayını silinmez, dolaba kalkar — menü doğruyu söyler
                Label(kart.ghostun ? "Dolaba kaldır (Ghost yayını)" : "Sil", systemImage: "trash")
            }
        }
    }
}

/// Adlandır / taşı / sil diyalogları — MasaView kökünde tek yerde sunulur.
struct MasaDiyaloglari: ViewModifier {
    @ObservedObject var model: MasaModel

    private var adlandirKart: MasaKart? { if case .adlandir(let k) = model.diyalog { return k }; return nil }
    private var silKart: MasaKart? { if case .sil(let k) = model.diyalog { return k }; return nil }
    private var adlandirAcik: Binding<Bool> {
        Binding(get: { adlandirKart != nil }, set: { if !$0 { model.diyalog = nil } })
    }
    private var silAcik: Binding<Bool> {
        Binding(get: { silKart != nil }, set: { if !$0 { model.diyalog = nil } })
    }
    private var tasiKart: Binding<MasaKart?> {
        Binding(get: { if case .tasi(let k) = model.diyalog { return k }; return nil },
                set: { if $0 == nil { model.diyalog = nil } })
    }

    func body(content: Content) -> some View {
        content
            .alert("Yeniden adlandır", isPresented: adlandirAcik) {
                TextField("Ad", text: $model.yeniAd)
                Button("Kaydet") {
                    guard let k = adlandirKart else { return }
                    let ad = model.yeniAd.trimmingCharacters(in: .whitespaces)
                    guard !ad.isEmpty else { return }
                    Task { await model.calistir { try await $0.masaEylem(k.id, "adlandir", ["ad": ad]) } }
                }
                Button("Vazgeç", role: .cancel) {}
            }
            .confirmationDialog(silKart.map { $0.ghostun ? "Ghost'un yayını dolaba kalkar; içerik kaybolmaz." : "\"\($0.ad)\" kalıcı silinsin mi?" } ?? "",
                                isPresented: silAcik, titleVisibility: .visible) {
                if let k = silKart {
                    Button(k.ghostun ? "Dolaba kaldır" : "Sil", role: .destructive) {
                        Task { await model.calistir { try await $0.masaEylem(k.id, "sil") } }
                    }
                }
            }
            .sheet(item: tasiKart) { k in TasiSecici(kart: k, model: model) }
    }
}

/// Taşıma hedefi: kök ya da bir klasör (kendi alt ağacı hariç).
struct TasiSecici: View {
    let kart: MasaKart
    @ObservedObject var model: MasaModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Button { tasi(nil) } label: { Label("Masa (kök)", systemImage: "square.grid.2x2") }
                    .disabled(kart.ebeveyn == nil)
                ForEach(model.klasorler(haric: kart.id)) { k in
                    Button { tasi(k.id) } label: {
                        Label(model.kirinti(k.id), systemImage: "folder")
                    }
                    .disabled(k.id == kart.ebeveyn)
                }
            }
            .grupluListe()
            .navigationTitle("Nereye?")
            .toolbar {
                ToolbarItem(placement: .ghostSol) { Button("Vazgeç") { dismiss() } }
            }
        }
        .preferredColorScheme(.dark)
    }

    private func tasi(_ hedef: Int?) {
        Task {
            var ek: [String: Any] = [:]
            if let h = hedef { ek["ebeveyn"] = h }
            await model.calistir { try await $0.masaEylem(kart.id, "tasi", ek) }
            dismiss()
        }
    }
}

// MARK: Kayıt sayfası

/// Tek kayıt: not → markdown (+ Düzenle, kendi notuysa) · rapor → markdown + kaynak ·
/// sahne → bileşenler · görsel → tam · belge → indir/Quick Look · bağlantı → aç.
struct OgeSayfa: View {
    let id: Int
    @ObservedObject var model: MasaModel
    @EnvironmentObject var oturum: Oturum
    @State private var kart: MasaKart?
    @State private var hata: String?
    @State private var duzenleAcik = false
    @State private var onizleme: URL?
    @State private var indiriliyor = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if let k = kart {
                    HStack(spacing: 6) {
                        Rozet(metin: k.ghostun ? "ghost" : "sen", renk: k.ghostun ? Renk.ikincil : Renk.uyari)
                        Text(TurSimge.etiket(k.tur)).font(.caption).foregroundStyle(Renk.ikincil)
                        Text(Tarih.kisa(k.guncelleme)).font(.caption).foregroundStyle(Renk.ikincil)
                        Spacer()
                    }
                    Text(model.kirinti(id)).font(.caption).foregroundStyle(Renk.ikincil)
                    govde(k)
                    if let kaynak = k.kaynak, k.ghostun {
                        Text("Kaynak: \(kaynak) — bu bir anlık görüntü; Ghost yeniden yayınlayınca güncellenir.")
                            .font(.caption2).foregroundStyle(Renk.ikincil).padding(.top, 6)
                    }
                } else if let h = hata {
                    Text(h).font(.footnote).foregroundStyle(Renk.uyari).kart()
                } else {
                    ProgressView().tint(Renk.ikincil).frame(maxWidth: .infinity).padding(.top, 40)
                }
            }
            .padding()
        }
        .ghostZemin()
        .navigationTitle(kart?.ad ?? model.kart(id)?.ad ?? "")
        .satirBaslik()
        .toolbar {
            ToolbarItem(placement: .ghostSag) {
                if let k = kart ?? model.kart(id) {
                    HStack(spacing: 14) {
                        if k.tur == "not" && !k.ghostun {
                            Button { duzenleAcik = true } label: { Image(systemName: "pencil") }
                        }
                        Menu { KartMenusu(kart: k, model: model) } label: { Image(systemName: "ellipsis.circle") }
                            .menuIndicator(.hidden)
                    }
                }
            }
        }
        .task(id: id) { await yukle() }
        .onChange(of: model.seq) { _, _ in Task { await yukle() } }
        .sheet(isPresented: $duzenleAcik) {
            if let k = kart {
                NotEditoru(baslik: "Notu düzenle", ad: k.ad, metin: k.icerik ?? "") { ad, metin in
                    await model.calistir { try await $0.masaEylem(k.id, "duzenle", ["ad": ad, "icerik": metin]) }
                }
            }
        }
        .quickLookPreview($onizleme)
    }

    @ViewBuilder private func govde(_ k: MasaKart) -> some View {
        switch k.tur {
        case "not", "rapor":
            MarkdownMetin(metin: k.icerik ?? "").kart()
        case "sahne":
            ForEach(Array((k.bilesenler ?? []).enumerated()), id: \.offset) { _, b in BilesenView(b: b) }
        case "gorsel":
            ImzaliGorsel(yol: "/v1/masa/\(k.id)/dosya")
        case "dosya":
            Button { Task { await ac(k) } } label: {
                HStack(spacing: 10) {
                    Image(systemName: "doc").font(.title3).foregroundStyle(Renk.ikincil)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(k.ad).font(.subheadline).foregroundStyle(Renk.metin)
                        Text(boyut(k.boyut)).font(.caption2).foregroundStyle(Renk.ikincil)
                    }
                    Spacer()
                    if indiriliyor { ProgressView().tint(Renk.ikincil) }
                    else { Image(systemName: "arrow.down.circle").foregroundStyle(Renk.ikincil) }
                }
                .kart()
            }
            .buttonStyle(.plain).disabled(indiriliyor)
        case "baglanti":
            if let u = URL(string: k.icerik ?? "") {
                Link(destination: u) {
                    HStack { Image(systemName: "link"); Text(k.icerik ?? "").lineLimit(3) ; Spacer() }
                        .foregroundStyle(Renk.metin).kart()
                }
            } else {
                Text(k.icerik ?? "").foregroundStyle(Renk.metin).kart()
            }
        case "klasor":
            KlasorIcerik(ebeveyn: k.id, model: model)
        default:
            Text("Bilinmeyen tür: \(k.tur)").foregroundStyle(Renk.uyari).kart()
        }
    }

    private func boyut(_ b: Int?) -> String {
        guard let b else { return "" }
        return b < 1_000_000 ? "\(b / 1024) KB" : String(format: "%.1f MB", Double(b) / 1_048_576)
    }

    @MainActor private func yukle() async {
        do { kart = try await oturum.api.masaKart(id); hata = nil }
        catch { if kart == nil { hata = error.localizedDescription } }
    }

    @MainActor private func ac(_ k: MasaKart) async {
        indiriliyor = true
        defer { indiriliyor = false }
        do {
            let veri = try await oturum.api.masaDosya(k.id)
            let ad = k.ad.components(separatedBy: CharacterSet(charactersIn: "/\\")).last ?? "belge"
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("ghost-masa-\(k.id)-" + ad)
            try veri.write(to: url, options: .atomic)
            onizleme = url
        } catch { hata = error.localizedDescription }
    }
}

// MARK: Not editörü

struct NotEditoru: View {
    let baslik: String
    @State var ad: String
    @State var metin: String
    let kaydet: (String, String) async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var kaydediliyor = false
    @FocusState private var odak: Bool

    var body: some View {
        NavigationStack {
            VStack(spacing: 10) {
                TextField("Başlık (boşsa ilk satır)", text: $ad)
                    .foregroundStyle(Renk.metin)
                    .padding(.horizontal, 12).padding(.vertical, 9)
                    .background(Renk.kart, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                TextEditor(text: $metin)
                    .focused($odak)
                    .scrollContentBackground(.hidden)
                    .foregroundStyle(Renk.metin)
                    .padding(8)
                    .background(Renk.kart, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .frame(minHeight: 240)
                Text("Markdown geçerli: **kalın**, - madde, | tablo |").font(.caption2).foregroundStyle(Renk.ikincil)
            }
            .padding()
            .background(Renk.zemin)
            .navigationTitle(baslik)
            .satirBaslik()
            .toolbar {
                ToolbarItem(placement: .ghostSol) { Button("Vazgeç") { dismiss() } }
                ToolbarItem(placement: .ghostSag) {
                    Button(kaydediliyor ? "Kaydediliyor…" : "Kaydet") {
                        let m = metin.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !m.isEmpty else { return }
                        let a = ad.trimmingCharacters(in: .whitespaces)
                        kaydediliyor = true
                        Task {
                            await kaydet(a.isEmpty ? String(m.split(separator: "\n").first ?? "Not").prefix(60).description : a, m)
                            kaydediliyor = false
                            dismiss()
                        }
                    }
                    .disabled(kaydediliyor || metin.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            .task { odak = true }
        }
        .preferredColorScheme(.dark)
    }
}
