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
                model.geriBildirim = { $0 ? Titre.hafif() : Titre.hata() }
                await model.dongu()
            }
        }
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
