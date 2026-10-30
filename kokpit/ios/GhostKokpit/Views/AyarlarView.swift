import SwiftUI

/// Ayarlar: Telegram'da komutla değişen kipler burada tek dokunuş — birincil kanal
/// (/kanal), sesli yanıt (/sesli), onay kapısı (/guard), motor (/model). Hepsi köprüye
/// aynı komut olarak gider; sunucu tek yazar kalır. Altta cihaz bilgisi ve eşlemeyi sıfırlama.
struct AyarlarView: View {
    @EnvironmentObject var oturum: Oturum
    @Environment(\.dismiss) private var dismiss
    /// Açan ekranın anlık görüntüsü; sekme olarak açıldığında yok — kendi çeker.
    var nabiz: Nabiz?
    /// Ayarlar'ın kendi çektiği taze nabız — nereden açılırsa açılsın değerler dolu olsun.
    @State private var canli: Nabiz?
    @State private var bilgi: String?
    @State private var sifirlaSor = false
    @State private var freezeSor = false
    #if os(iOS)
    @StateObject private var duyu = DuyuYoneticisi.paylasilan
    @StateObject private var eller = EllerYoneticisi.paylasilan
    #endif

    static var surum: String {
        let v = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"
        let b = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "?"
        return "\(v) (\(b))"
    }

    private static let modeller = ["fable", "opus", "sonnet", "haiku"]

    /// Gösterilecek nabız: taze olan varsa o, yoksa açılışta verilen.
    private var n: Nabiz? { canli ?? nabiz }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    satir("Birincil kanal", deger: n?.kanal ?? "—") {
                        Button("Kokpit") { Task { await gonder("/kanal kokpit") } }
                        Button("Telegram") { Task { await gonder("/kanal telegram") } }
                    }
                    Text("Kokpit birincilken her Ghost cevabı telefona push olur; Telegram birincilken yalnız onay, biten iş ve alarm.")
                        .font(.caption).foregroundStyle(Renk.ikincil).listRowBackground(Renk.zemin)
                } header: { Text("Kanal").foregroundStyle(Renk.ikincil) }

                Section {
                    satir("Sesli yanıt", deger: (n?.sesli ?? false) ? "açık" : "kapalı") {
                        Button("Aç") { Task { await gonder("/sesli on") } }
                        Button("Kapat") { Task { await gonder("/sesli off") } }
                    }
                    satir("Onay kapısı (guard)", deger: (n?.guardAcik ?? true) ? "açık" : "kapalı") {
                        Button("Aç") { Task { await gonder("/guard on") } }
                        Button("Kapat") { Task { await gonder("/guard off") } }
                    }
                    satir("Motor", deger: n?.model ?? "—") {
                        ForEach(AyarlarView.modeller, id: \.self) { m in
                            Button(m) { Task { await gonder("/model " + m) } }
                        }
                    }
                } header: { Text("Kipler").foregroundStyle(Renk.ikincil) }

                Section {
                    Button { Task { await gonder("/tazele") } } label: {
                        Label("Sohbeti tazele (bağlamı devret)", systemImage: "arrow.triangle.2.circlepath")
                    }
                    Button { Task { await gonder("/new") } } label: {
                        Label("Yeni sohbet (temiz sayfa)", systemImage: "square.and.pencil")
                    }
                    Button { Task { await gonder("/durum") } } label: {
                        Label("Durum metni iste", systemImage: "text.alignleft")
                    }
                } header: { Text("Sohbet").foregroundStyle(Renk.ikincil) }
                .listRowBackground(Renk.kart)
                .foregroundStyle(Renk.metin)

                #if os(iOS)
                Section {
                    ForEach(DuyuYoneticisi.Ayar.allCases, id: \.rawValue) { ayar in
                        VStack(alignment: .leading, spacing: 3) {
                            Toggle(isOn: Binding(
                                get: { duyuAcik(ayar) },
                                set: { duyu.degistir(ayar, $0) })) {
                                Label(ayar.baslik, systemImage: ayar.simge)
                                    .foregroundStyle(Renk.metin)
                            }
                            Text(ayar.aciklama).font(.caption).foregroundStyle(Renk.ikincil)
                        }
                        .listRowBackground(Renk.kart)
                    }
                    if let d = n?.duyular, d.var_ == true {
                        bilgiSatiri("Ghost'un gördüğü", duyuOzetMetni(d))
                    }
                    if !duyu.sunucuAcik {
                        Text("Ghost tarafında duyular kapalı — Telegram'dan /duyular ac.")
                            .font(.caption).foregroundStyle(.orange).listRowBackground(Renk.zemin)
                    }
                    if !duyu.sonDurum.isEmpty {
                        Text(duyu.sonDurum).font(.caption).foregroundStyle(Renk.ikincil)
                            .listRowBackground(Renk.zemin)
                    }
                    Text("Konum semt hassasiyetindedir (sürekli GPS yok) ve yer adına çevrilerek gönderilir. Takvimden yalnız başlık, saat ve yer okunur — not, katılımcı, ek okunmaz. Ghost bu veriyi cevaplarında bağlam olarak kullanır; /nerede ve /bugun ile ne gördüğünü sorabilirsin.")
                        .font(.caption).foregroundStyle(Renk.ikincil).listRowBackground(Renk.zemin)
                } header: { Text("Duyular").foregroundStyle(Renk.ikincil) }

                Section {
                    Toggle(isOn: Binding(get: { eller.acik }, set: { eller.degistir($0) })) {
                        Label("Ghost telefona yazsın", systemImage: "hand.point.up.left")
                            .foregroundStyle(Renk.metin)
                    }
                    .listRowBackground(Renk.kart)
                    if !eller.sonDurum.isEmpty {
                        Text(eller.sonDurum).font(.caption).foregroundStyle(Renk.ikincil)
                            .listRowBackground(Renk.zemin)
                    }
                    Text("Açıkken Ghost gerçek iOS hatırlatıcısı ve takvim etkinliği kurabilir — alarmı Ghost'a değil telefonun kendisine bağlı olur, Ghost kapalıyken de çalar. Ne yazdığı /gorevler ile görünür; Ghost her yazımı sana bildirir.")
                        .font(.caption).foregroundStyle(Renk.ikincil).listRowBackground(Renk.zemin)
                } header: { Text("Eller").foregroundStyle(Renk.ikincil) }

                #endif
                Section {
                    if n?.freeze == true {
                        Button { Task { await gonder("/unfreeze") } } label: {
                            Label("Freeze'i kaldır (otonom eylemler yeniden açılır)", systemImage: "play.circle")
                        }
                        .listRowBackground(Renk.kart).foregroundStyle(Renk.metin)
                    } else {
                        Button(role: .destructive) { freezeSor = true } label: {
                            Label("Acil dur — freeze", systemImage: "stop.circle")
                        }
                        .listRowBackground(Renk.kart)
                    }
                    Text("Freeze tüm otonom eylemleri durdurur; yalnız /durum, /saglik, /help ve /unfreeze çalışır.")
                        .font(.caption).foregroundStyle(Renk.ikincil).listRowBackground(Renk.zemin)
                } header: { Text("Acil").foregroundStyle(Renk.ikincil) }

                Section {
                    bilgiSatiri("Sunucu", oturum.taban.host ?? oturum.taban.absoluteString)
                    bilgiSatiri("Cihaz", oturum.api.kimlik?.deviceID ?? "—")
                    bilgiSatiri("Sürüm", AyarlarView.surum)
                    bilgiSatiri("API", "\(GhostAPI.apiSurum)" + (oturum.apiUyari == nil ? "" : " · uyumsuz"))
                    Button(role: .destructive) { sifirlaSor = true } label: {
                        Label("Eşlemeyi sıfırla", systemImage: "xmark.shield")
                    }
                    .listRowBackground(Renk.kart)
                } header: { Text("Cihaz").foregroundStyle(Renk.ikincil) }
            }
            .grupluListe()
            .ghostZemin()
            .navigationTitle("Ayarlar")
            .satirBaslik()
            .task { await nabizCek() }
            .aracMenusu()
            .overlay(alignment: .bottom) {
                if let b = bilgi {
                    Text(b).font(.footnote).foregroundStyle(Renk.zemin)
                        .padding(.horizontal, 14).padding(.vertical, 8)
                        .background(Renk.metin, in: Capsule())
                        .padding(.bottom, 12)
                        .transition(.opacity)
                }
            }
            .confirmationDialog("Tüm otonom eylemler durdurulsun mu?", isPresented: $freezeSor, titleVisibility: .visible) {
                Button("Freeze", role: .destructive) { Task { await gonder("/freeze uygulamadan") } }
                Button("Vazgeç", role: .cancel) {}
            }
            .confirmationDialog("Eşleme silinsin mi?", isPresented: $sifirlaSor, titleVisibility: .visible) {
                Button("Sıfırla", role: .destructive) { oturum.sifirla(); dismiss() }
                Button("Vazgeç", role: .cancel) {}
            } message: {
                Text("Anahtar bu cihazdan silinir; yeniden eşlemek için Telegram'dan /cihaz kodu gerekir.")
            }
        }
    }

    @ViewBuilder
    private func satir<M: View>(_ etiket: String, deger: String, @ViewBuilder menu: () -> M) -> some View {
        HStack {
            Text(etiket).foregroundStyle(Renk.metin)
            Spacer()
            Menu {
                menu()
            } label: {
                HStack(spacing: 4) {
                    Text(deger).foregroundStyle(Renk.ikincil)
                    Image(systemName: "chevron.up.chevron.down").font(.caption2).foregroundStyle(Renk.ikincil)
                }
            }
            .menuIndicator(.hidden)
        }
        .listRowBackground(Renk.kart)
    }

    #if os(iOS)
    /// Anahtarın anlık hali — izin reddedilince yönetici kendini kapatır, UI izlesin.
    private func duyuAcik(_ ayar: DuyuYoneticisi.Ayar) -> Bool {
        switch ayar {
        case .konum: return duyu.konumAcik
        case .takvim: return duyu.takvimAcik
        case .cihaz: return duyu.cihazAcik
        }
    }

    /// Sunucunun o an gördüğü özet — "açık ama veri gitmiyor" hâli burada yakalanır.
    private func duyuOzetMetni(_ d: DuyuOzet) -> String {
        var p: [String] = []
        if let y = d.yer, !y.isEmpty { p.append(y + (d.konumTaze == true ? "" : " (bayat)")) }
        if let n = d.etkinlikSayisi, n > 0 { p.append("\(n) etkinlik") }
        if let pil = d.pil { p.append("%\(Int(pil * 100))") }
        return p.isEmpty ? "—" : p.joined(separator: " · ")
    }
    #endif

    private func bilgiSatiri(_ etiket: String, _ deger: String) -> some View {
        HStack {
            Text(etiket).foregroundStyle(Renk.metin)
            Spacer()
            Text(deger).font(.footnote.monospaced()).foregroundStyle(Renk.ikincil).lineLimit(1)
        }
        .listRowBackground(Renk.kart)
    }

    private func gonder(_ komut: String) async {
        do {
            try await oturum.api.mesajGonder(komut)
            withAnimation { bilgi = "Gönderildi: " + komut }
        } catch {
            withAnimation { bilgi = "Gönderilemedi: " + error.localizedDescription }
        }
        try? await Task.sleep(for: .seconds(1.4))
        withAnimation { bilgi = nil }
        await nabizCek()          // kip değiştiyse satırdaki değer de değişsin
    }

    private func nabizCek() async {
        if let taze = try? await oturum.api.nabiz() { canli = taze }
    }
}
