import SwiftUI
import PhotosUI
import UniformTypeIdentifiers

/// Sohbet: outbox'ın uzun yoklamayla akan dökümü. Sahip sağda, Ghost solda; butonlu
/// mesajlar karar kartı. Arka planda yoklama durur, öne gelince sürer (.task(id: scenePhase)).
///
/// 2026-09-03 deneyim turu: geriye kaydırırken "n yeni mesaj" pili, "/" komut paleti,
/// iyimser balon, gün ayracı, "Ghost çalışıyor" şeridi (giriş üstünde), kopyala, bekleyen karar şeridi.
/// 2026-09-04 yapısal tur (Sahip: "buglar, yapısal sorunlar var"): veri/mantık `SohbetModel`e
/// çıktı; ek TEPSİSİ (çoklu fotoğraf, çoklu belge, kamera, pano → önizle → tek gönderi,
/// hata olursa dokunup tekrar dene); görsel önbelleği; karar/soru butonu bir kez basılır ve
/// seçim görünür; markdown tablo/alıntı çizimi; yankı gelince ekran artık zıplamıyor;
/// okunmamış sayacı yalnız Ghost mesajlarını sayar; hata şeridi dokununca kapanır.
struct SohbetView: View {
    @EnvironmentObject var oturum: Oturum
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var model = SohbetModel()
    @StateObject private var konusma = Konusma()
    @State private var metin = ""
    @FocusState private var odak: Bool
    @State private var kameraAcik = false
    @State private var galeriAcik = false
    @State private var belgeAcik = false
    @State private var secilenFotolar: [PhotosPickerItem] = []
    /// Kullanıcı şu an listeyi kaydırıyor mu — "altta" yalnız kullanıcı hareketiyle değişir.
    @State private var kaydiriyor = false
    /// Listenin altındaki blok (palet, hata, ekler, çalışıyor şeridi, giriş) yüksekliği;
    /// büyüdüğünde alttaysan liste itilir, yoksa son mesaj bloğun arkasında kalır.
    @State private var altYukseklik: CGFloat = 0
    /// Kaydırma konumu — açılışta alt kenar; enAlta `scrollTo(edge: .bottom)` ile iner.
    @State private var konum = ScrollPosition(edge: .bottom)

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if oturum.bekleyenOnay > 0 { kararSeridi }
                ScrollView {
                    // VStack, LazyVStack DEĞİL (2026-09-26, kök neden): tembel yığın görünmeyen
                    // satırları TAHMİNİ yükseklikle sayıyor; telefon günlüğünde aynı liste için
                    // içerik 7 925 ↔ 17 165 pt arasında zıpladı. Alt kenara inince üstteki
                    // satırlar tahminde kalıyor, ilk parmak hareketinde gerçek boya geçince
                    // ofset yerinde kalıp görünüm listenin ortasına "ışınlanıyor"; tersinde
                    // içerik küçülünce ofset sondan taşıp simsiyah alan görünüyordu. Sayfa 50
                    // mesaj + Equatable balon (değişmeyen satır yeniden çizilmez) ile eager
                    // yığın ucuz; içerik boyu her an KESİN, scrollTo(edge:) tam yere iner.
                    VStack(spacing: 10) {
                        if model.mesajlar.isEmpty && model.bekleyenler.isEmpty {
                            Text("Henüz mesaj yok").font(.footnote).foregroundStyle(Renk.ikincil).padding(.top, 40)
                        } else if !model.eskiBitti && !model.mesajlar.isEmpty {
                            Button { Task { await model.dahaEski() } } label: {
                                if model.eskiYukleniyor { ProgressView().tint(Renk.ikincil) }
                                else { Text("Daha eski").font(.footnote).foregroundStyle(Renk.ikincil) }
                            }
                            .disabled(model.eskiYukleniyor)
                            .padding(.vertical, 4)
                        }
                        ForEach(Array(model.mesajlar.enumerated()), id: \.element.id) { i, m in
                            if let g = gunAyraci(i) { GunAyraci(metin: g) }
                            MesajBalonu(mesaj: m, secili: model.kullanilanButonlar[m.id]) { veri in
                                Task { await model.butonTikla(mesajID: m.id, veri: veri) }
                            }
                            .equatable()
                            .id(m.id)
                        }
                        ForEach(model.bekleyenler) { b in
                            BekleyenBalonu(mesaj: b).id(b.id)
                        }
                    }
                    .padding(.horizontal, 12).padding(.vertical, 10)
                    .kaydirmaUsteKapali()
                }
                .kaydirincaKlavyeyiIndir()
                // Kaydırma motoru (2026-09-26, üçüncü yapı): ScrollPosition + scrollTo(edge: .bottom).
                // Eski ScrollViewProxy.scrollTo(id) tembel yığında yeni balonun yüksekliğini
                // tahminle alıyor, uzun mesajda yarım kalıyordu. Sözleşme:
                //  • açılışta en alt (initialOffset);
                //  • ALTTAYSAN içerik büyüyünce alt sabit (sizeChanges çapası) + edge scrollTo;
                //  • "altta" yalnız KULLANICI hareketiyle değişir (faz), kap değişimi bozamaz;
                //  • yukarıdaysan hiçbir şey oynatmaz, "N yeni mesaj" pili çıkar.
                .scrollPosition($konum, anchor: .bottom)
                .defaultScrollAnchor(.bottom, for: .initialOffset)
                .defaultScrollAnchor(model.altta || model.eskiYukleniyor ? .bottom : nil, for: .sizeChanges)
                .onScrollPhaseChange { eski, yeni, ctx in
                    kaydiriyor = yeni != .idle
                    let a = altta(ctx.geometry)
                    model.gunluk("faz \(eski)→\(yeni) altta=\(a) \(ozet(ctx.geometry))")
                    alttaGuncelle(a)
                }
                .onScrollGeometryChange(for: Bool.self) { g in altta(g) } action: { _, yeni in
                    model.gunluk("geo altta=\(yeni) kaydiriyor=\(kaydiriyor)")
                    if kaydiriyor { alttaGuncelle(yeni) }
                }
                .onChange(of: model.mesajlar.last?.id) { _, id in
                    model.gunluk("son mesaj \(id ?? -1) altta=\(model.altta)")
                    if model.altta { enAlta(neden: "mesaj") }
                }
                // Kendi mesajım EKLENİNCE in; yankı gelip balon düşünce yerinde kal.
                .onChange(of: model.bekleyenler.count) { eski, yeni in if yeni > eski { enAlta(neden: "bekleyen") } }
                .onChange(of: model.ekler.count) { eski, yeni in if yeni > eski { enAlta(animasyonlu: false, neden: "ek") } }
                .onChange(of: odak) { _, acik in if acik { enAlta(animasyonlu: false, neden: "odak") } }
                // Alt blok büyüdü (klavye, şerit, ek tepsisi, çok satırlı yazı): alttaysan itil.
                .onChange(of: altYukseklik) { eski, yeni in
                    model.gunluk("altBlok \(Int(eski))→\(Int(yeni)) altta=\(model.altta)")
                    guard yeni > eski, model.altta else { return }
                    enAlta(animasyonlu: false, neden: "altBlok")
                }
                .klavyeAcilinca {
                    model.gunluk("klavye açıldı altta=\(model.altta)")
                    if model.altta { enAlta(animasyonlu: false, neden: "klavye") }
                }
                .onReceive(NotificationCenter.default.publisher(for: RootView.sohbetEnAlta)) { _ in
                    alttaGuncelle(true)
                    enAlta(animasyonlu: false, neden: "sekme")
                }
                .onAppear { enAlta(animasyonlu: false, neden: "acilis") }
                .overlay(alignment: .bottomTrailing) {
                    if model.okunmamis > 0 { yeniPili }
                }
                VStack(spacing: 0) {
                    if paletAcik { palet }
                    if let h = model.hata { hataSeridi(h) }
                    if !model.ekler.isEmpty { ekTepsisi }
                    if let d = model.mesgul, d.aktif {
                        MesgulSeridi(ozet: d.ozet ?? "", baslangic: model.mesgulBasladi ?? Date())
                    }
                    giris
                }
                .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { altYukseklik = $0 }
            }
            .background(Renk.zemin)
            .navigationTitle("Sohbet")
            .satirBaslik()
            .aracMenusu()
            .task(id: scenePhase) {
                guard scenePhase == .active else { return }
                model.bagla(oturum.api)
                await model.yoklamaDongusu()
            }
        }
    }

    // MARK: Parçalar

    private var kararSeridi: some View {
        Button { oturum.sayfa = .kararlar } label: {
            HStack(spacing: 8) {
                Image(systemName: "checkmark.seal.fill").foregroundStyle(Renk.uyari)
                Text(oturum.bekleyenOnay == 1 ? "1 karar bekliyor" : "\(oturum.bekleyenOnay) karar bekliyor")
                    .font(.subheadline).foregroundStyle(Renk.metin)
                Spacer()
                Image(systemName: "chevron.right").font(.caption).foregroundStyle(Renk.ikincil)
            }
            .padding(.horizontal, 14).padding(.vertical, 10)
            .background(Renk.uyari.opacity(0.12))
            .overlay(alignment: .bottom) { Rectangle().fill(Renk.uyari.opacity(0.3)).frame(height: 1) }
        }
        .buttonStyle(.plain)
    }

    private func hataSeridi(_ h: String) -> some View {
        Button { model.hatayiKapat() } label: {
            HStack(spacing: 8) {
                Image(systemName: "exclamationmark.triangle").font(.caption)
                Text(h).font(.caption).lineLimit(2).multilineTextAlignment(.leading)
                Spacer()
                Image(systemName: "xmark").font(.caption2)
            }
            .foregroundStyle(Renk.uyari)
            .padding(.horizontal, 12).padding(.vertical, 6)
        }
        .buttonStyle(.plain)
    }

    private var yeniPili: some View {
        Button {
            alttaGuncelle(true)
            enAlta(neden: "pil")
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "arrow.down")
                Text(model.okunmamis == 1 ? "1 yeni mesaj" : "\(model.okunmamis) yeni mesaj")
            }
            .font(.footnote.weight(.medium))
            .foregroundStyle(Renk.metin)
            .padding(.horizontal, 12).padding(.vertical, 8)
            .background(Renk.kartUst, in: Capsule())
            .overlay(Capsule().stroke(Renk.ikincil.opacity(0.4), lineWidth: 1))
        }
        .buttonStyle(.plain)
        .padding(.trailing, 14).padding(.bottom, 10)
        .transition(.opacity)
    }

    /// "/" yazınca açılan komut paleti — Komutlar sayfasına gitmeden, klavyeden çıkmadan.
    private var paletAcik: Bool {
        metin.hasPrefix("/") && !metin.contains(" ") && !paletListesi.isEmpty
    }

    private var paletListesi: [Komut] {
        let q = metin.lowercased()
        return Array(model.komutlar.filter { $0.komut.lowercased().hasPrefix(q) }.prefix(6))
    }

    private var palet: some View {
        VStack(spacing: 0) {
            Divider().overlay(Renk.ikincil.opacity(0.3))
            ForEach(paletListesi) { k in
                Button { paletSec(k) } label: {
                    HStack(spacing: 10) {
                        Text(k.komut).font(.subheadline.monospaced()).foregroundStyle(Renk.metin)
                        if let a = k.aciklama, !a.isEmpty {
                            Text(a).font(.caption).foregroundStyle(Renk.ikincil).lineLimit(1)
                        }
                        Spacer(minLength: 0)
                    }
                    .padding(.horizontal, 14).padding(.vertical, 9)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
        .background(Renk.kart)
    }

    private func paletSec(_ k: Komut) {
        let a = k.aciklama ?? ""
        if a.contains("<") || a.contains("[") || k.komut.contains(" ") {
            metin = k.komut + " "
            odak = true
        } else {
            metin = k.komut
            Task { await gonder() }
        }
    }

    /// Ek tepsisi: küçük önizlemeler, ✕ ile çıkar, hata olursa dokunup tekrar dene.
    private var ekTepsisi: some View {
        VStack(spacing: 4) {
            Divider().overlay(Renk.ikincil.opacity(0.3))
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 10) {
                    ForEach(model.ekler) { ek in
                        EkKarti(ek: ek) {
                            model.ekCikar(ek)
                        } tekrar: {
                            Task { await model.ekiTekrarDene(ek, not: metin.trimmingCharacters(in: .whitespacesAndNewlines)) }
                        }
                    }
                }
                .padding(.horizontal, 12).padding(.vertical, 8)
            }
            HStack {
                Text(model.ekler.count == 1 ? "1 ek" : "\(model.ekler.count) ek")
                    .font(.caption2).foregroundStyle(Renk.ikincil)
                Text("· açıklama yazıp gönder").font(.caption2).foregroundStyle(Renk.ikincil)
                Spacer()
            }
            .padding(.horizontal, 14)
        }
        .background(Renk.zemin)
    }

    private var giris: some View {
        HStack(alignment: .bottom, spacing: 8) {
            Menu {
                #if os(iOS)
                Button { kameraAcik = true } label: { Label("Fotoğraf çek", systemImage: "camera") }
                #endif
                Button { galeriAcik = true } label: { Label("Galeriden seç (çoklu)", systemImage: "photo.on.rectangle") }
                Button { belgeAcik = true } label: { Label("Belge ekle", systemImage: "doc") }
                if Pano.gorselVar {
                    Button { model.panodanYapistir() } label: { Label("Panodaki görseli ekle", systemImage: "doc.on.clipboard") }
                }
            } label: {
                Image(systemName: model.ekGonderiliyor ? "paperclip.badge.ellipsis" : "paperclip")
                    .font(.system(size: 22)).foregroundStyle(Renk.ikincil).padding(.bottom, 6)
                    .overlay(alignment: .topTrailing) {
                        if !model.ekler.isEmpty {
                            Text("\(model.ekler.count)").font(.caption2.bold()).foregroundStyle(Renk.zemin)
                                .padding(.horizontal, 5).padding(.vertical, 1)
                                .background(Renk.metin, in: Capsule()).offset(x: 8, y: -6)
                        }
                    }
            }
            .menuIndicator(.hidden)
            .disabled(model.ekGonderiliyor)
            #if os(iOS)
            .sheet(isPresented: $kameraAcik) {
                Kamera { img in model.ekleGorsel(img) }
                    .ignoresSafeArea()
            }
            #endif
            .photosPicker(isPresented: $galeriAcik, selection: $secilenFotolar,
                          maxSelectionCount: SohbetModel.ekTavan, matching: .images)
            .onChange(of: secilenFotolar) { _, yeni in
                guard !yeni.isEmpty else { return }
                Task {
                    await model.ekleFotolar(yeni)
                    secilenFotolar = []
                }
            }
            .fileImporter(isPresented: $belgeAcik, allowedContentTypes: [.item], allowsMultipleSelection: true) { sonuc in
                switch sonuc {
                case .success(let urller): urller.forEach { model.ekleBelge($0) }
                case .failure(let e): model.hataGoster("Belge seçilemedi: " + e.localizedDescription)
                }
            }
            TextField(konusma.dinliyor ? "Dinliyorum…" : (model.ekler.isEmpty ? "Ghost'a yaz…" : "Ekler için açıklama…"),
                      text: $metin, axis: .vertical)
                .lineLimit(1...5)
                .focused($odak)
                .foregroundStyle(Renk.metin)
                .padding(.horizontal, 12).padding(.vertical, 9)
                .background(Renk.kart, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                .onChange(of: metin) { _, yeni in
                    guard yeni.hasPrefix("/") else { return }
                    Task { await model.komutlariYukle() }
                }
                #if os(macOS)
                .onSubmit { Task { await gonder() } }
                #endif
            #if os(iOS)
            Button { konusma.degistir() } label: {
                Image(systemName: konusma.dinliyor ? "mic.circle.fill" : "mic.circle")
                    .font(.system(size: 30))
                    .foregroundStyle(konusma.dinliyor ? Renk.kritik : Renk.ikincil)
                    .symbolEffect(.pulse, isActive: konusma.dinliyor)
            }
            .onChange(of: konusma.metin) { _, yeni in if !yeni.isEmpty { metin = yeni } }
            .onChange(of: konusma.hata) { _, h in if let h { model.hataGoster(h) } }
            #endif
            Button { Task { await gonder() } } label: {
                Image(systemName: "arrow.up.circle.fill").font(.system(size: 30))
                    .foregroundStyle(gonderilebilir ? Renk.metin : Renk.ikincil)
            }
            .disabled(!gonderilebilir)
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .background(Renk.zemin)
    }

    private var gonderilebilir: Bool {
        guard !model.gonderiliyor, !model.ekGonderiliyor else { return false }
        return !metin.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !model.ekler.isEmpty
    }

    private func gonder() async {
        let m = metin
        // Mikrofondan geldiyse "sesli": Ghost cevabı sesli de verir (Telegram sesli mesaj davranışı).
        let sesli = !konusma.metin.isEmpty
            && m.trimmingCharacters(in: .whitespacesAndNewlines) == konusma.metin.trimmingCharacters(in: .whitespacesAndNewlines)
        metin = ""
        konusma.metin = ""
        if let geri = await model.gonder(m, sesli: sesli) { metin = geri }   // başarısız: yazdığın kaybolmasın
    }

    /// Gün değiştiyse ayraç metni ("Bugün" · "Dün" · "1 Eylül"), değişmediyse nil.
    private func gunAyraci(_ i: Int) -> String? {
        let g = String((model.mesajlar[i].ts ?? "").prefix(10))
        guard g.count == 10 else { return nil }
        if i > 0, String((model.mesajlar[i - 1].ts ?? "").prefix(10)) == g { return nil }
        return gunEtiketi(g)
    }

    /// Son 80 pt içinde miyiz (kısa listede içerik kaptan küçükse de "altta").
    /// iOS'ta containerSize üst güvenli alanı DIŞLAR, contentOffset ise onu içerir (UIKit
    /// ofseti): alt kenarda off + kap + ins.top == icerik (telefon günlüğü 2026-09-26:
    /// 6907 + 577 + 116 = 7600). ins.top'suz formül telefonda alt kenarı hiç görmüyor,
    /// yeni mesajda iniş yerine pil çıkıyordu. macOS'ta kap inseti içerir; ins.top eklemek
    /// eşiği 52 pt gevşetir, zararsız.
    private func altta(_ g: ScrollGeometry) -> Bool {
        g.contentOffset.y + g.containerSize.height + g.contentInsets.top >= g.contentSize.height - 80
    }

    private func alttaGuncelle(_ yeni: Bool) {
        if model.altta != yeni { model.altta = yeni }
        if yeni, model.okunmamis != 0 { model.okunmamis = 0 }
    }

    private func ozet(_ g: ScrollGeometry) -> String {
        "off=\(Int(g.contentOffset.y)) kap=\(Int(g.containerSize.height)) icerik=\(Int(g.contentSize.height)) ins=\(Int(g.contentInsets.top))/\(Int(g.contentInsets.bottom)) gor=\(Int(g.visibleRect.minY))-\(Int(g.visibleRect.maxY))"
    }

    /// En alta in. İkinci dokunuş: uzun markdown/görsel geç yerleşirse ilk iniş kısa kalır.
    private func enAlta(animasyonlu: Bool = true, neden: String) {
        model.gunluk("enAlta(\(neden)) anim=\(animasyonlu)")
        if animasyonlu {
            withAnimation(.easeOut(duration: 0.25)) { konum.scrollTo(edge: .bottom) }
        } else {
            konum.scrollTo(edge: .bottom)
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
            if model.altta { konum.scrollTo(edge: .bottom) }
        }
    }
}

/// Tepsideki tek ek: görsel küçük resim ya da belge simgesi; ✕ çıkarır; hata → tekrar.
struct EkKarti: View {
    let ek: Ek
    var cikar: () -> Void
    var tekrar: () -> Void

    var body: some View {
        ZStack(alignment: .topTrailing) {
            Group {
                if let k = ek.kucuk {
                    Image(platform: k).resizable().scaledToFill()
                } else {
                    VStack(spacing: 4) {
                        Image(systemName: "doc.fill").font(.title2).foregroundStyle(Renk.ikincil)
                        Text(ek.ad).font(.caption2).foregroundStyle(Renk.metin).lineLimit(2).multilineTextAlignment(.center)
                        Text("\(ek.boyutKB) KB").font(.caption2).foregroundStyle(Renk.ikincil)
                    }
                    .padding(6)
                }
            }
            .frame(width: 84, height: 84)
            .background(Renk.kart)
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay {
                switch ek.durum {
                case .yukleniyor:
                    ZStack { Color.black.opacity(0.45); ProgressView().tint(.white) }
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                case .hata:
                    Button(action: tekrar) {
                        ZStack {
                            Color.black.opacity(0.55)
                            VStack(spacing: 2) {
                                Image(systemName: "arrow.clockwise").foregroundStyle(Renk.kritik)
                                Text("tekrar").font(.caption2).foregroundStyle(.white)
                            }
                        }
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    }
                    .buttonStyle(.plain)
                case .hazir:
                    EmptyView()
                }
            }
            if ek.durum != .yukleniyor {
                Button(action: cikar) {
                    Image(systemName: "xmark.circle.fill").font(.system(size: 18))
                        .foregroundStyle(Renk.metin, Renk.zemin)
                }
                .buttonStyle(.plain)
                .offset(x: 6, y: -6)
            }
        }
    }
}

struct BekleyenBalonu: View {
    var mesaj: BekleyenMesaj

    var body: some View {
        HStack {
            Spacer(minLength: 48)
            VStack(alignment: .leading, spacing: 8) {
                if let g = mesaj.gorsel {
                    Image(platform: g).resizable().scaledToFit().frame(maxHeight: 220)
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                }
                if let ad = mesaj.ekAdi {
                    HStack(spacing: 8) {
                        Image(systemName: "doc").foregroundStyle(Renk.ikincil)
                        Text(ad).font(.subheadline).foregroundStyle(Renk.metin).lineLimit(1)
                    }
                }
                if !mesaj.metin.isEmpty { Text(mesaj.metin).foregroundStyle(Renk.metin) }
                HStack(spacing: 4) {
                    Image(systemName: "clock").font(.caption2)
                    Text("gönderiliyor").font(.caption2)
                }
                .foregroundStyle(Renk.ikincil)
            }
            .padding(12)
            .background(Renk.kartUst, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .opacity(0.55)
        }
    }
}

struct GunAyraci: View {
    var metin: String
    var body: some View {
        HStack(spacing: 10) {
            Rectangle().fill(Renk.ikincil.opacity(0.25)).frame(height: 1)
            Text(metin).font(.caption2).foregroundStyle(Renk.ikincil)
            Rectangle().fill(Renk.ikincil.opacity(0.25)).frame(height: 1)
        }
        .padding(.vertical, 6)
    }
}

struct MesajBalonu: View, Equatable {
    var mesaj: Mesaj
    /// Bu mesajda daha önce basılan buton (varsa): butonlar kilitlenir, seçilen vurgulanır.
    var secili: String?
    var butonTikla: (String) -> Void

    /// Kapanış (closure) karşılaştırılamaz; SwiftUI o yüzden her üst güncellemede tüm
    /// balonları yeniden çiziyordu. Eager VStack'te bu 50+ markdown çözümlemesi demek —
    /// mesaj ve seçim aynıysa satır aynıdır.
    static func == (a: MesajBalonu, b: MesajBalonu) -> Bool { a.mesaj == b.mesaj && a.secili == b.secili }

    var body: some View {
        HStack {
            if mesaj.sahipten { Spacer(minLength: 48) }
            VStack(alignment: .leading, spacing: 8) {
                if let md = mesaj.medya {
                    MedyaBalonu(mesajID: mesaj.id, medya: md)
                }
                if !mesaj.metin.isEmpty {
                    if mesaj.sahipten {
                        Text(mesaj.metin).foregroundStyle(Renk.metin)
                    } else if mesaj.gecici == true {
                        // Faz 2.7: ilk satır — Ghost'un niyet cümlesi; sonuç gelince aynı balon dolar.
                        HStack(alignment: .top, spacing: 8) {
                            MarkdownMetin(metin: mesaj.metin).opacity(0.75)
                            ProgressView().controlSize(.small)
                        }
                    } else {
                        MarkdownMetin(metin: mesaj.metin)
                    }
                }
                if let satirlar = mesaj.butonlar, !satirlar.isEmpty {
                    VStack(spacing: 6) {
                        ForEach(Array(satirlar.enumerated()), id: \.offset) { _, satir in
                            HStack(spacing: 6) {
                                ForEach(satir, id: \.self) { b in
                                    let bu = secili == b.veri
                                    Button { butonTikla(b.veri) } label: {
                                        HStack(spacing: 4) {
                                            if bu { Image(systemName: "checkmark").font(.caption) }
                                            Text(b.etiket).font(.subheadline)
                                        }
                                        .frame(maxWidth: .infinity)
                                    }
                                    .buttonStyle(.bordered)
                                    .tint(b.veri.hasPrefix("no:") ? Renk.kritik : Renk.metin)
                                    .disabled(secili != nil)
                                    .opacity(secili == nil || bu ? 1 : 0.35)
                                }
                            }
                        }
                    }
                }
                if let ts = mesaj.ts, !sohbetSaati(ts).isEmpty {
                    Text(sohbetSaati(ts)).font(.caption2).foregroundStyle(Renk.ikincil)
                }
            }
            .padding(12)
            .background(mesaj.sahipten ? Renk.kartUst : Renk.kart,
                        in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .frame(maxWidth: Duzen.balonTavan, alignment: mesaj.sahipten ? .trailing : .leading)
            .contextMenu {
                Button { Pano.yaz(mesaj.metin) } label: {
                    Label("Kopyala", systemImage: "doc.on.doc")
                }
                ShareLink(item: mesaj.metin) { Label("Paylaş", systemImage: "square.and.arrow.up") }
            }
            if !mesaj.sahipten { Spacer(minLength: 48) }
        }
    }
}

/// "Ghost çalışıyor" — Telegram'daki typing/⏳ karşılığı. Liste balonu DEĞİL, giriş
/// çubuğunun üstünde sabit şerit (2026-09-26 yeniden yapı): listenin içindeyken her
/// araç çağrısında yüksekliği değişip kaydırmayı oynatıyor, kaybolunca alta inişi
/// bozuyordu. Şerit listeye dokunmaz, yukarıda okurken de görünür. Süre sunucudan
/// (mesgul.sure_sn) kurulur; son araç çağrısı tek satır.
struct MesgulSeridi: View {
    var ozet: String
    var baslangic: Date

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { ctx in
            HStack(spacing: 10) {
                ProgressView().controlSize(.small).tint(Renk.ikincil)
                VStack(alignment: .leading, spacing: 1) {
                    Text("Ghost çalışıyor · " + sure(ctx.date))
                        .font(.footnote.monospacedDigit()).foregroundStyle(Renk.metin)
                    Text(ozet.isEmpty ? " " : ozet)
                        .font(.caption2.monospaced()).foregroundStyle(Renk.ikincil)
                        .lineLimit(1, reservesSpace: true).truncationMode(.tail)
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 14).padding(.vertical, 6)
            .background(Renk.kart)
            .overlay(alignment: .top) { Rectangle().fill(Renk.ikincil.opacity(0.25)).frame(height: 1) }
        }
        .transition(.move(edge: .bottom).combined(with: .opacity))
    }

    private func sure(_ simdi: Date) -> String {
        let sn = max(0, Int(simdi.timeIntervalSince(baslangic)))
        return sn < 60 ? "\(sn) sn" : String(format: "%d:%02d", sn / 60, sn % 60)
    }
}

/// "2026-09-03 06:22:38" → "06:22" (gün ayracı zaten tarihi söylüyor).
private func sohbetSaati(_ ts: String) -> String {
    let t = ts.replacingOccurrences(of: "T", with: " ")
    guard let bosluk = t.firstIndex(of: " ") else { return "" }
    return String(t[t.index(after: bosluk)...].prefix(5))
}

/// "2026-09-03" → "Bugün" · "Dün" · "1 Eylül"
private func gunEtiketi(_ gun: String) -> String {
    let f = DateFormatter()
    f.dateFormat = "yyyy-MM-dd"
    f.locale = Locale(identifier: "tr_TR")
    guard let d = f.date(from: gun) else { return gun }
    if Calendar.current.isDateInToday(d) { return "Bugün" }
    if Calendar.current.isDateInYesterday(d) { return "Dün" }
    let g = DateFormatter()
    g.locale = Locale(identifier: "tr_TR")
    g.dateFormat = Calendar.current.isDate(d, equalTo: Date(), toGranularity: .year) ? "d MMMM" : "d MMMM yyyy"
    return g.string(from: d)
}

#if os(iOS)
/// Sohbette sekmeye ikinci kez dokunmak (ve durum çubuğuna dokunmak) listeyi en üste
/// fırlatıyordu; UIKit'in `scrollsToTop` refleksini kaynağında kapatıyoruz — Mesajlar
/// uygulamasının davranışı da budur. En alta inmeyi RootView bildirimi yapar.
private struct ScrollToTopKapali: UIViewRepresentable {
    func makeUIView(context: Context) -> Kanca { Kanca(frame: .zero) }
    func updateUIView(_ view: Kanca, context: Context) { view.kapat() }

    final class Kanca: UIView {
        override func didMoveToWindow() {
            super.didMoveToWindow()
            kapat()
        }

        func kapat() {
            var ust = superview
            while let g = ust {
                if let kaydirma = g as? UIScrollView { kaydirma.scrollsToTop = false; return }
                ust = g.superview
            }
        }
    }
}
#endif

extension View {
    /// iOS: durum çubuğuna dokununca en üste zıplamayı kapat. macOS'ta böyle refleks yok.
    @ViewBuilder func kaydirmaUsteKapali() -> some View {
        #if os(iOS)
        self.background(ScrollToTopKapali())
        #else
        self
        #endif
    }
}
