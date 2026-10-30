import SwiftUI

/// Nabız: sahibin üç sorusu (2026-09-04 yeniden kuruldu — önceden altyapı sayıyordu):
///   1. Benden ne bekliyor? (kararlar + Ghost'un cevapsız soruları, tek dokunuşla onay)
///   2. Ghost bugün ne yaptı? (commit, iş, eylem, telefon — deterministik)
///   3. İş nasıl? (AppA vitrin, kullanıcı, ödeyen, hata — düne göre fark)
/// Sonra "Sen" (duyular) ve TEK SATIR altyapı; makineler/nöbetçiler/işler/bağlam katlanır
/// bölümün içinde durur (kaybolmadı, geri çekildi). 15 sn'de yenilenir.
struct NabizView: View {
    @EnvironmentObject var oturum: Oturum
    @State private var nabiz: Nabiz?
    @State private var hata: String?
    @State private var altyapiAcik = false
    @State private var mesgul: Set<String> = []
    /// Tazelik (Sahip 2026-09-06: "nabız güncel olsun, bağlantı durumunda geride kalıyor"):
    /// son BAŞARILI okuma zamanı; 45 sn'yi geçerse şerit "bağlantı yok · son HH:MM" der ve
    /// eski veri soluk kalır. Öne gelince ve hata sonrası 4 sn'de yeniden dener.
    @State private var sonOkuma: Date?
    @State private var simdi = Date()
    @Environment(\.scenePhase) private var scenePhase
    private var bayat: Bool { sonOkuma.map { simdi.timeIntervalSince($0) > 45 } ?? false }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    ozetSerit
                    if let u = oturum.apiUyari {        // 8c: sözleşme sürümü uyuşmazlığı
                        Text(u).font(.footnote).foregroundStyle(Renk.uyari).kart()
                    }
                    if let n = nabiz {
                        bekleyen(n)
                        bugun(n)
                        kota(n)
                        isNabzi(n)
                        siteler(n)
                        duyular(n)
                        altyapi(n)
                    } else if hata == nil {
                        ProgressView().tint(Renk.ikincil).frame(maxWidth: .infinity).padding(.top, 40)
                    }
                    if let h = hata {
                        Text(h).font(.footnote).foregroundStyle(Renk.kritik).kart()
                    }
                }
                .padding()
            }
            .ghostZemin()
            .navigationTitle("Nabız")
            .aracMenusu()
            .yenilenebilir { await yenile() }
            .task(id: scenePhase) {
                guard scenePhase == .active else { return }
                while !Task.isCancelled {
                    let ok = await yenile()
                    try? await Task.sleep(for: .seconds(ok ? 15 : 4))
                }
            }
            .task {
                while !Task.isCancelled {
                    try? await Task.sleep(for: .seconds(5))
                    simdi = Date()
                }
            }
        }
    }

    // MARK: Üç soru

    /// 1. Benden ne bekliyor? Karar kartları (onay/ret burada) + sorular (dokun → Sohbet).
    @ViewBuilder
    private func bekleyen(_ n: Nabiz) -> some View {
        let kararlar = n.bekleyen?.kararlar ?? []
        let sorular = n.bekleyen?.sorular ?? []
        VStack(alignment: .leading, spacing: 8) {
            BolumBaslik(metin: "Benden ne bekliyor?")
            if kararlar.isEmpty && sorular.isEmpty {
                HStack(spacing: 8) {
                    Image(systemName: "checkmark.circle").foregroundStyle(Renk.ikincil)
                    Text("Hiçbir şey — bekleyen karar ya da soru yok.").foregroundStyle(Renk.ikincil)
                }
                .font(.subheadline).kart()
            }
            ForEach(kararlar) { k in
                KararKarti(karar: k, mesgul: mesgul.contains(k.id.metin),
                           onay: { Task { await ver(k, onay: true) } },
                           ret: { Task { await ver(k, onay: false) } })
            }
            ForEach(sorular) { s in
                Button {
                    oturum.sekme = .sohbet
                } label: {
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: s.butonlu == true ? "hand.tap" : "questionmark.bubble")
                            .foregroundStyle(Renk.uyari).padding(.top, 2)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(s.metin).foregroundStyle(Renk.metin).multilineTextAlignment(.leading)
                            Text((s.ts.map { kisaSaat($0) + " · " } ?? "") + "cevaplamak için Sohbet'e dokun")
                                .font(.caption).foregroundStyle(Renk.ikincil)
                        }
                        Spacer()
                        Image(systemName: "chevron.right").font(.caption).foregroundStyle(Renk.ikincil)
                    }
                    .kart()
                }
                .buttonStyle(.plain)
            }
        }
    }

    /// 2. Ghost bugün ne yaptı? En fazla dört satır; sayılar altta küçük.
    @ViewBuilder
    private func bugun(_ n: Nabiz) -> some View {
        if let b = n.bugun {
            VStack(alignment: .leading, spacing: 8) {
                BolumBaslik(metin: "Ghost bugün ne yaptı?")
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(b.satirlar ?? []) { s in
                        HStack(alignment: .firstTextBaseline, spacing: 10) {
                            Image(systemName: bugunSimge(s.simge)).font(.caption)
                                .foregroundStyle(Renk.ikincil).frame(width: 16)
                            Text(s.metin).foregroundStyle(s.simge == "bos" ? Renk.ikincil : Renk.metin)
                                .font(.subheadline).lineLimit(3)
                        }
                    }
                    if let sy = b.sayilar, (sy.values.reduce(0, +)) > 0 {
                        Text(["commit", "is", "eylem", "gorev", "mesaj"].compactMap { k -> String? in
                            guard let v = sy[k], v > 0 else { return nil }
                            return "\(v) " + ["commit": "commit", "is": "iş", "eylem": "eylem", "gorev": "hatırlatıcı", "mesaj": "mesaj"][k]!
                        }.joined(separator: " · "))
                        .font(.caption).foregroundStyle(Renk.ikincil).padding(.top, 2)
                    }
                }
                .kart()
                .onTapGesture { oturum.sayfa = .akis }
            }
        }
    }

    private func bugunSimge(_ s: String?) -> String {
        switch s {
        case "commit": return "arrow.triangle.branch"
        case "is": return "checkmark.circle"
        case "eylem": return "bolt"
        case "telefon": return "iphone"
        default: return "moon.zzz"
        }
    }

    /// 3. İş nasıl? Vitrin satırı + sayı kutuları; fark varsa yanında işaretli.
    @ViewBuilder
    private func isNabzi(_ n: Nabiz) -> some View {
        if let i = n.is_, i.var_ == true {
            let m = i.metrikler ?? []
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    BolumBaslik(metin: "İş nasıl? · AppA")
                    if let t = i.tarih {
                        Text((i.yasGun ?? 0) > 1 ? "\(i.yasGun!) gün önce ölçüldü" : t.suffix(5).replacingOccurrences(of: "-", with: "."))
                            .font(.caption2).foregroundStyle((i.yasGun ?? 0) > 1 ? Renk.uyari : Renk.ikincil).padding(.top, 6)
                    }
                }
                ForEach(m.filter { $0.tur == "metin" }) { x in
                    HStack(spacing: 8) {
                        Text(x.ad).font(.caption).foregroundStyle(Renk.ikincil)
                        Text(x.goster?.metin ?? "—").foregroundStyle(Renk.metin)
                        if let o = x.onceki { Text("(dün: \(o.metin))").font(.caption).foregroundStyle(Renk.ikincil) }
                        Spacer()
                    }
                    .kart()
                }
                let sayilar = m.filter { $0.tur != "metin" }
                LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                    ForEach(sayilar) { x in metrikKutu(x) }
                }
                if i.oncekiTarih == nil {
                    Text("Düne göre fark yarından itibaren görünür (tarihçe bugün başladı).")
                        .font(.caption2).foregroundStyle(Renk.ikincil)
                }
            }
        }
    }

    private func metrikKutu(_ x: Metrik) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(x.goster?.metin ?? "—")
                    .font(.title3.weight(.semibold).monospacedDigit()).foregroundStyle(Renk.metin)
                if let f = x.fark, f != 0 {
                    Text(String(format: "%+.0f", f)).font(.caption.monospacedDigit())
                        .foregroundStyle(x.yon == "kotu" ? Renk.kritik : Color(hex: 0x7BC47F))
                }
            }
            Text(x.ad).font(.caption).foregroundStyle(Renk.ikincil).lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kart()
    }

    /// 4. Siteler (2026-09-06, Sahip: "site ziyaretlerini ve durumu görebileyim"): her site bir
    /// satır — erişim noktası, ad, 24 saat / 7 gün gerçek dış ziyaret (kendi telefonu ve botlar
    /// ayıklı, `ghost/trafik.py`). Ölçüm yoksa yalnız erişim durumu. Dokun → Siteler sayfası.
    @ViewBuilder
    private func siteler(_ n: Nabiz) -> some View {
        let sn = n.siteNabzi
        let olculdu = sn?.var_ == true
        let liste: [SiteSatir] = (sn?.siteler?.isEmpty == false)
            ? (sn?.siteler ?? [])
            : (n.siteler ?? []).map { SiteSatir(ad: $0.ad, url: $0.url, ok: $0.ok) }
        if !liste.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .firstTextBaseline) {
                    BolumBaslik(metin: "Siteler")
                    Spacer()
                    if olculdu, let t = sn?.toplam7g {
                        Text("\(t) dış ziyaret · 7 gün")
                            .font(.caption2).foregroundStyle(sn?.bayat == true ? Renk.uyari : Renk.ikincil)
                            .padding(.top, 6)
                    }
                }
                VStack(spacing: 0) {
                    ForEach(Array(liste.enumerated()), id: \.element.id) { i, st in
                        HStack(spacing: 10) {
                            Nokta(renk: Renk.durum(ok: st.ok))
                            VStack(alignment: .leading, spacing: 2) {
                                Text(st.ad).foregroundStyle(st.ok == false ? Renk.kritik : Renk.metin)
                                if st.ok == false {
                                    Text("erişilemiyor").font(.caption2).foregroundStyle(Renk.kritik)
                                } else if let k = st.kaynak, !k.isEmpty {
                                    Text(k).font(.caption2).foregroundStyle(Renk.ikincil).lineLimit(1)
                                }
                            }
                            Spacer()
                            if olculdu {
                                ziyaretSutun(st.ziyaret24s, "24 sa")
                                ziyaretSutun(st.ziyaret7g, "7 gün")
                            } else {
                                Text(st.ok == nil ? "—" : (st.ok == true ? "açık" : "kapalı"))
                                    .font(.caption).foregroundStyle(Renk.ikincil)
                            }
                        }
                        .padding(.vertical, 8)
                        if i < liste.count - 1 { Divider().overlay(Renk.ikincil.opacity(0.3)) }
                    }
                }
                .kart()
                .contentShape(Rectangle())
                .onTapGesture { oturum.sayfa = .siteler }
                if olculdu, let o = sn?.olculdu {
                    Text("Ölçüm " + kisaSaat(o) + " · kendi telefonun ve botlar ayıklı"
                         + (sn?.bayat == true ? " · ölçüm eski" : ""))
                        .font(.caption2).foregroundStyle(sn?.bayat == true ? Renk.uyari : Renk.ikincil)
                } else {
                    Text("Ziyaret ölçümü henüz yok — nöbetçi saatte bir ölçer.")
                        .font(.caption2).foregroundStyle(Renk.ikincil)
                }
            }
        }
    }

    /// Kota (2026-09-07, Sahip: "kullanımı dolar olarak değil, Claude uygulamasındaki gibi kota
    /// olarak göster"): abonelik limitleri — oturum (5 saat), haftalık (tüm modeller), model
    /// bazlı (Fable). Her satır: ad, dolu çubuk, yüzde, sıfırlanma. Renk önem seviyesinden.
    /// Veri `ghost/kota.py` → `ops/kota.json` (selfcheck 15 dk + her sohbet turundan sonra).
    @ViewBuilder
    private func kota(_ n: Nabiz) -> some View {
        if let k = n.kota, k.var_ == true, let limitler = k.limitler, !limitler.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .firstTextBaseline) {
                    BolumBaslik(metin: "Kota")
                    Spacer()
                    if let p = k.plan, !p.isEmpty {
                        Text(p).font(.caption2).foregroundStyle(Renk.ikincil).padding(.top, 6)
                    }
                }
                VStack(spacing: 10) {
                    ForEach(limitler) { l in
                        let y = min(max(l.yuzde ?? 0, 0), 100)
                        let renk = kotaRenk(l)
                        VStack(alignment: .leading, spacing: 4) {
                            HStack(alignment: .firstTextBaseline) {
                                Text(l.ad).font(.subheadline).foregroundStyle(Renk.metin)
                                Spacer()
                                Text("%\(y)")
                                    .font(.subheadline.weight(.semibold).monospacedDigit())
                                    .foregroundStyle(renk)
                            }
                            GeometryReader { g in
                                ZStack(alignment: .leading) {
                                    Capsule().fill(Renk.ikincil.opacity(0.25))
                                    Capsule().fill(renk)
                                        .frame(width: max(4, g.size.width * CGFloat(y) / 100))
                                }
                            }
                            .frame(height: 6)
                            if let s = l.sifirlanmaMetin, !s.isEmpty {
                                Text("sıfırlanma " + s).font(.caption2).foregroundStyle(Renk.ikincil)
                            }
                        }
                    }
                }
                .kart()
                if let o = k.olculdu {
                    Text("Ölçüm " + kisaSaat(o) + (k.bayat == true ? " · ölçüm eski" : ""))
                        .font(.caption2).foregroundStyle(k.bayat == true ? Renk.uyari : Renk.ikincil)
                }
            }
        } else if let k = n.kota, let h = k.hata, !h.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                BolumBaslik(metin: "Kota")
                Text(h).font(.caption).foregroundStyle(Renk.ikincil).kart()
            }
        }
    }

    private func kotaRenk(_ l: KotaLimit) -> Color {
        let y = l.yuzde ?? 0
        if l.onem == "critical" || y >= 95 { return Renk.kritik }
        if l.onem == "warning" || y >= 75 { return Renk.uyari }
        return Renk.iyi
    }

    private func ziyaretSutun(_ v: Int?, _ etiket: String) -> some View {
        VStack(alignment: .trailing, spacing: 1) {
            Text(v.map { String($0) } ?? "—")
                .font(.subheadline.weight(.semibold).monospacedDigit())
                .foregroundStyle((v ?? 0) > 0 ? Renk.metin : Renk.ikincil)
            Text(etiket).font(.caption2).foregroundStyle(Renk.ikincil)
        }
        .frame(minWidth: 44, alignment: .trailing)
    }

    /// Altyapı: tek satır; dokununca eski ayrıntı (makineler, nöbetçiler, işler, bağlam) açılır.
    @ViewBuilder
    private func altyapi(_ n: Nabiz) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                withAnimation(.easeInOut(duration: 0.2)) { altyapiAcik.toggle() }
            } label: {
                HStack(spacing: 10) {
                    Nokta(renk: Renk.durum(n.altyapi?.durum ?? n.ozet))
                    Text(n.altyapi?.satir ?? "Altyapı").foregroundStyle(Renk.metin).font(.subheadline)
                        .multilineTextAlignment(.leading)
                    Spacer()
                    Image(systemName: altyapiAcik ? "chevron.up" : "chevron.down")
                        .font(.caption).foregroundStyle(Renk.ikincil)
                }
                .kart()
            }
            .buttonStyle(.plain)
            if let sorunlar = n.altyapi?.sorunlar, !sorunlar.isEmpty, !altyapiAcik {
                ForEach(sorunlar, id: \.self) { s in
                    HStack(spacing: 8) {
                        Nokta(renk: Renk.kritik, boyut: 6)
                        Text(s).font(.footnote).foregroundStyle(Renk.metin)
                    }
                    .padding(.leading, 6)
                }
            }
            if altyapiAcik {
                makineler(n)
                nobetciler(n)
                isler(n)
                baglam(n)
            }
        }
    }

    private func ver(_ k: Karar, onay: Bool) async {
        let id = k.id.metin
        guard !mesgul.contains(id) else { return }
        mesgul.insert(id)
        defer { mesgul.remove(id) }
        do {
            try await oturum.api.kararVer(id: id, onay: onay)
            await yenile()
        } catch {
            hata = error.localizedDescription
        }
    }

    // MARK: Parçalar

    private var ozetSerit: some View {
        let renk = bayat ? Renk.ikincil : Renk.durum(nabiz?.ozet)
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 12) {
                Nokta(renk: renk, boyut: 12)
                VStack(alignment: .leading, spacing: 2) {
                    Text(bayat ? "Sunucuya ulaşılamıyor" : ozetMetni).font(.headline)
                        .foregroundStyle(bayat ? Renk.uyari : Renk.metin)
                    HStack(spacing: 8) {
                        if let m = nabiz?.model, !bayat { Text(m) }
                        Text(tazelikMetni)
                    }
                    .font(.caption).foregroundStyle(bayat ? Renk.uyari : Renk.ikincil)
                }
                Spacer()
                if nabiz?.freeze == true { Rozet(metin: "freeze", renk: Renk.kritik) }
                if nabiz?.guardAcik == true { Rozet(metin: "guard", renk: Renk.ikincil) }
            }
            if let b = nabiz?.baglanti {
                HStack(spacing: 14) {
                    uzuvPil("laptopcomputer", "Mac", b.mac)
                    uzuvPil("iphone", "iPhone", b.telefon)
                    if let t = b.tunel, !t.isEmpty, t != "saglikli" {
                        Text("tünel: " + tunelMetni(t)).font(.caption2).foregroundStyle(Renk.uyari)
                    }
                    Spacer()
                }
            }
        }
        .padding(14)
        .background(renk.opacity(0.10), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(renk.opacity(0.35), lineWidth: 1))
    }

    /// "12 sn önce" / "bağlantı yok · son 02:41" — Nabız'ın gerçekten ne kadar taze olduğu.
    private var tazelikMetni: String {
        guard let s = sonOkuma else { return nabiz == nil ? "" : "—" }
        let sn = Int(simdi.timeIntervalSince(s))
        if bayat {
            let f = DateFormatter(); f.dateFormat = "HH:mm"
            return "bağlantı yok · son " + f.string(from: s)
        }
        return sn < 5 ? "şimdi" : "\(sn) sn önce"
    }

    private func uzuvPil(_ simge: String, _ ad: String, _ acik: Bool?) -> some View {
        HStack(spacing: 4) {
            Image(systemName: simge).font(.caption2)
            Text(ad).font(.caption2)
            Nokta(renk: acik == true ? Color(hex: 0x7BC47F) : Renk.kritik, boyut: 6)
        }
        .foregroundStyle(bayat ? Renk.ikincil : Renk.metin)
    }

    private func tunelMetni(_ t: String) -> String {
        switch t {
        case "tunel-yok": return "kurulu değil"
        case "zombi-beklemede", "zombi-cozulemedi", "zombi-tunel-dusuruldu": return "kopuk, kuruluyor"
        default: return t
        }
    }

    private var ozetMetni: String {
        switch (nabiz?.ozet ?? "").lowercased() {
        case "iyi": return "Her şey yolunda"
        case "uyari", "uyarı": return "Uyarı var"
        case "kritik": return "Kritik durum"
        default: return nabiz == nil ? "Bağlanıyor…" : "Durum bilinmiyor"
        }
    }

    /// "Sen" bölümü (Faz 5): telefonun izinli olarak bildirdiği bağlam. Hiçbir duyu
    /// açık değilse bölüm hiç çizilmez — kapalı duyu boş kart göstermez.
    @ViewBuilder
    private func duyular(_ n: Nabiz) -> some View {
        if let d = n.duyular, d.var_ == true {
            VStack(alignment: .leading, spacing: 8) {
                BolumBaslik(metin: "Sen")
                VStack(alignment: .leading, spacing: 6) {
                    if let y = d.yer, !y.isEmpty {
                        duyuSatir("location.fill", y,
                                  alt: d.konumYas, soluk: d.konumTaze != true)
                    }
                    if let s = d.siradaki, !s.isEmpty {
                        duyuSatir("calendar", s,
                                  alt: (d.etkinlikSayisi ?? 0) > 1 ? "bugün \(d.etkinlikSayisi ?? 0) etkinlik" : nil,
                                  soluk: false)
                    } else if let c = d.etkinlikSayisi {
                        duyuSatir("calendar", c == 0 ? "Bugün takvim boş" : "Bugünün kalan etkinliği yok",
                                  alt: c > 0 ? "\(c) etkinlik geçti" : nil, soluk: false)
                    }
                    if let pil = d.pil {
                        duyuSatir(d.sarj == true ? "battery.100.bolt" : "battery.50",
                                  "%\(Int(pil * 100))" + (d.sarj == true ? " · şarjda" : ""),
                                  alt: d.ag, soluk: d.cihazTaze != true)
                    }
                }
                .kart()
            }
        }
    }

    private func duyuSatir(_ simge: String, _ metin: String, alt: String?, soluk: Bool) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: simge).font(.caption).foregroundStyle(Renk.ikincil).frame(width: 16)
            Text(metin).foregroundStyle(soluk ? Renk.ikincil : Renk.metin)
            Spacer()
            if let a = alt, !a.isEmpty {
                Text(a).font(.caption).foregroundStyle(Renk.ikincil)
            }
        }
    }

    private func makineler(_ n: Nabiz) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            BolumBaslik(metin: "Makineler")
            ForEach(n.makineler ?? []) { m in
                HStack(alignment: .top, spacing: 10) {
                    Nokta(renk: Renk.durum(m.durum)).padding(.top, 6)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(m.ad).foregroundStyle(Renk.metin)
                        if let d = m.detay, !d.isEmpty {
                            Text(d).font(.footnote).foregroundStyle(Renk.ikincil)
                        }
                    }
                    Spacer()
                }
                .kart()
            }
            if (n.makineler ?? []).isEmpty {
                Text("Makine bilgisi yok").font(.footnote).foregroundStyle(Renk.ikincil).kart()
            }
        }
    }

    private func nobetciler(_ n: Nabiz) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            BolumBaslik(metin: "Nöbetçiler")
            let liste = n.nobetciler ?? []
            if liste.isEmpty {
                Text("Nöbetçi bilgisi yok").font(.footnote).foregroundStyle(Renk.ikincil).kart()
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(liste.enumerated()), id: \.element.id) { i, nb in
                        HStack(spacing: 10) {
                            Nokta(renk: Renk.durum(ok: nb.ok))
                            Text(nb.ad).foregroundStyle(Renk.metin)
                            Spacer()
                            Text(yas(nb.yasSn)).font(.footnote.monospacedDigit()).foregroundStyle(Renk.ikincil)
                        }
                        .padding(.vertical, 8)
                        if i < liste.count - 1 { Divider().overlay(Renk.ikincil.opacity(0.3)) }
                    }
                }
                .kart()
            }
        }
    }

    private func isler(_ n: Nabiz) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            BolumBaslik(metin: "İşler")
            HStack(spacing: 12) {
                sayac("aktif", n.isler?.aktif)
                sayac("onay bekleyen", n.isler?.bekleyenOnay,
                      renk: (n.isler?.bekleyenOnay ?? 0) > 0 ? Renk.uyari : Renk.metin)
            }
            ForEach((n.isler?.ozet ?? []).prefix(5)) { i in
                HStack(alignment: .top, spacing: 10) {
                    Nokta(renk: Renk.durum(i.durum)).padding(.top, 6)
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text("#\(i.id.metin)").font(.footnote.monospaced())
                            if let t = i.tur { Text(t).font(.footnote) }
                            if let d = i.durum { Text("· " + d).font(.footnote) }
                        }
                        .foregroundStyle(Renk.ikincil)
                        if let p = i.prompt, !p.isEmpty {
                            Text(p).font(.subheadline).foregroundStyle(Renk.metin).lineLimit(2)
                        }
                    }
                    Spacer()
                    if ["kuyrukta", "çalışıyor", "denetimde"].contains(i.durum ?? "") {
                        Button {
                            Task { try? await oturum.api.mesajGonder("/cancel " + i.id.metin) }
                        } label: {
                            Image(systemName: "xmark.circle").foregroundStyle(Renk.ikincil)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("İşi iptal et")
                    }
                }
                .kart()
            }
        }
    }

    private func baglam(_ n: Nabiz) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            BolumBaslik(metin: "Bağlam ve kaynak")
            HStack(spacing: 12) {
                sayac("bağlam %", n.baglam?.yuzde.map { Int($0) },
                      renk: (n.baglam?.yuzde ?? 0) > 80 ? Renk.uyari : Renk.metin)
                sayac("tur", n.baglam?.tur)
                // Dolar YOK (Sahip 2026-09-07, "nabızda dolar gözüküyor halen"): abonelikte
                // gerçek kısıt kota; buraya oturum (5 sa) kotası, ayrıntı "Kota" bölümünde.
                let oturum = n.kota?.limitler?.first { $0.tur == "session" }
                sayac("oturum kota %", oturum?.yuzde,
                      renk: oturum.map { kotaRenk($0) } ?? Renk.metin)
            }
            HStack(spacing: 12) {
                sayac("disk boş GB", n.diskBosGb.map { String(format: "%.0f", $0) })
                sayac("bellek boş MB", n.bellekBosMb.map { String(format: "%.0f", $0) })
            }
        }
    }

    /// Küçük sayaç kartı; değer yoksa "—".
    private func sayac<T: CustomStringConvertible>(_ etiket: String, _ deger: T?, renk: Color = Renk.metin) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(deger.map { $0.description } ?? "—")
                .font(.title3.weight(.semibold).monospacedDigit())
                .foregroundStyle(renk)
            Text(etiket).font(.caption).foregroundStyle(Renk.ikincil)
        }
        .kart()
    }

    // MARK: Veri

    @discardableResult
    private func yenile() async -> Bool {
        do {
            nabiz = try await oturum.api.nabiz()
            oturum.nabiz = nabiz                      // Ayarlar sayfası kökten açılıyor
            if let b = nabiz?.isler?.bekleyenOnay { oturum.bekleyenOnay = b }
            hata = nil
            sonOkuma = Date(); simdi = sonOkuma!
            return true
        } catch is CancellationError {
            return true
        } catch {
            // Eski veri ekranda kalır; şerit "bağlantı yok · son HH:MM" der (bayat), kırmızı
            // metin yalnız hiç veri yokken.
            if nabiz == nil { hata = error.localizedDescription }
            return false
        }
    }

    private func yas(_ sn: Int?) -> String {
        guard let sn else { return "—" }
        if sn < 90 { return "\(sn) sn" }
        if sn < 5400 { return "\(sn / 60) dk" }
        return "\(sn / 3600) sa"
    }

    /// "2026-09-02 03:00:37" ya da ISO → "03:00"
    private func kisaSaat(_ s: String) -> String {
        let t = s.replacingOccurrences(of: "T", with: " ")
        guard let bosluk = t.firstIndex(of: " ") else { return s }
        let saat = t[t.index(after: bosluk)...]
        return String(saat.prefix(5))
    }
}
