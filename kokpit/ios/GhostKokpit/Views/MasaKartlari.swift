import SwiftUI
import PhotosUI
import QuickLook
import UniformTypeIdentifiers

/// Masa kart sayfaları ve menüleri (MasaView'dan ayrıldı, Faz 3.12 8b): + menüsü, kart menüsü,
/// adlandır/taşı/sil diyalogları, kayıt sayfası, not editörü. Kök ağaç ve klasör sayfaları MasaView.swift'te.

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
            if ["not", "rapor", "sahne"].contains(kart.tur) {
                // Faz 1.13b (Sahip 2026-10-29 "masadaki yazıları telefona kaydedemiyorum"): kart → PDF → iOS paylaşım sayfası
                ShareLink(item: MasaPdf(id: kart.id, ad: kart.ad, api: model.apiErisim),
                          preview: SharePreview(kart.ad, image: Image(systemName: "doc.richtext"))) {
                    Label("PDF olarak paylaş", systemImage: "square.and.arrow.up")
                }
            }
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
