import SwiftUI

/// Uygulama içi gezinti hedefleri.
enum Rota: Hashable {
    case soyle(String)      // dikte edilen metin → önizleme
    case mesajlar
    case mesaj(Int)         // tek cevabın tamamı (mesaj id)
    case ayarlar
}

/// Ana ekran: maskot (gerçek hâl), durum satırı, tek büyük düğme: konuş.
/// Düğme sistemin metin girişini (dikte) açar ve çift dokunmanın birincil eylemidir: bileği kaldırıp iki parmağı
/// birbirine vurmak konuşmayı başlatır. Bu ekranda onaya bağlı hiçbir düğme YOKTUR.
struct AnaEkran: View {
    @EnvironmentObject var oturum: SaatOturum
    @EnvironmentObject var sohbet: SaatSohbet
    @Binding var yol: [Rota]

    var body: some View {
        VStack(spacing: 4) {
            Maskot(hal: hal, yukseklik: 52)
            Text(durumSatiri)
                .font(.footnote)
                .foregroundStyle(Renk.ikincil)
                .lineLimit(2)
                .multilineTextAlignment(.center)
                .minimumScaleFactor(0.8)
            if oturum.bekleyenKarar > 0 {
                // Yalnız sayı: kart ekranı sonraki aşama; karar telefonda verilir.
                Text("\(oturum.bekleyenKarar) karar bekliyor · telefonda")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(Renk.uyari)
            }
            Spacer(minLength: 2)
            TextFieldLink(prompt: Text("Ghost'a söyle")) {
                Label("Konuş", systemImage: "mic.fill")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
            } onSubmit: { metin in
                let temiz = metin.trimmingCharacters(in: .whitespacesAndNewlines)
                if !temiz.isEmpty { yol.append(.soyle(temiz)) }
            }
            .handGestureShortcut(.primaryAction)
            .accessibilityLabel(Text("Konuş"))
        }
        .padding(.horizontal, 4)
        .background(Renk.zemin)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                NavigationLink(value: Rota.mesajlar) { Image(systemName: "text.bubble") }
                    .accessibilityLabel(Text("Son mesajlar"))
            }
            ToolbarItem(placement: .topBarTrailing) {
                NavigationLink(value: Rota.ayarlar) { Image(systemName: "gearshape") }
                    .accessibilityLabel(Text("Ayarlar"))
            }
        }
    }

    /// Hâl gerçek durumdan türetilir, elle seçilmez (kimlik.md §4.1 ilke 3). Öncelik: ulaşılamıyor → gerçek sorun →
    /// seni bekliyor → tur sürüyor → boşta.
    private var hal: MaskotHal {
        if oturum.agHatasi != nil || sohbet.hata != nil { return .yokluk }
        if (oturum.nabizOzet ?? "").lowercased() == "kritik" { return .hata }
        if oturum.bekleyenKarar > 0 { return .dikkat }
        if let m = sohbet.mesgul, m.aktif {
            return (m.ozet ?? "").isEmpty ? .dusunuyor : .calisiyor
        }
        return .notr
    }

    private var durumSatiri: String {
        if let h = oturum.agHatasi ?? sohbet.hata { return h }
        if oturum.freeze { return "Dondurulmuş" }
        if (oturum.nabizOzet ?? "").lowercased() == "kritik" { return "Sorun var · ayrıntı telefonda" }
        if let m = sohbet.mesgul, m.aktif {
            let o = (m.ozet ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            return o.isEmpty ? "Düşünüyor…" : String(o.prefix(60))
        }
        if oturum.bekleyenKarar > 0 { return "Seni bekliyor" }
        return "Hazır"
    }
}
