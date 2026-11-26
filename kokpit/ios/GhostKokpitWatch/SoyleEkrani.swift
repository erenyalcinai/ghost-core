import SwiftUI

/// "Söyledin": dikte edilen metnin önizlemesi → Gönder / Yeniden söyle.
///
/// Giden metin sunucuda "cep cihazı" mesajıdır: köprüye "[Cep cihazı: <ad>; onay sayılmaz]" önekiyle düşer ve
/// içinde "tamam / onayla" geçse de ONAY SAYILMAZ (kural onay servisinde; bu ekran yalnız metin gönderir,
/// onay/karar ucuna hiç istek atmaz).
struct SoyleEkrani: View {
    enum Asama: Equatable { case onizleme, gonderiliyor, gitti, hata(String) }

    @EnvironmentObject var oturum: SaatOturum
    @Environment(\.dismiss) private var kapat
    @AppStorage(SaatAyar.kendiligindenGonder) private var kendiliginden = false
    @State var metin: String
    @State private var asama: Asama = .onizleme
    @State private var ilkAcilis = true

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                Text(metin)
                    .font(.body)
                    .foregroundStyle(Renk.metin)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(8)
                    .background(Renk.kart, in: RoundedRectangle(cornerRadius: 10, style: .continuous))

                switch asama {
                case .onizleme:
                    dugmeler
                case .gonderiliyor:
                    HStack(spacing: 6) { ProgressView(); Text("Gönderiliyor…").font(.footnote) }
                        .frame(maxWidth: .infinity)
                case .gitti:
                    Label("Gönderildi", systemImage: "checkmark")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(Renk.metin)
                        .frame(maxWidth: .infinity)
                case .hata(let h):
                    Text(h).font(.footnote).foregroundStyle(Renk.kritik)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    dugmeler
                }
            }
            .padding(.horizontal, 2)
        }
        .background(Renk.zemin)
        .navigationTitle("Söyledin")
        .task {
            // "Kendiliğinden gönder" açıksa önizleme beklenmez (yalnız ekran ilk açıldığında).
            guard ilkAcilis else { return }
            ilkAcilis = false
            if kendiliginden { await gonder() }
        }
    }

    @ViewBuilder private var dugmeler: some View {
        Button {
            Task { await gonder() }
        } label: {
            Label("Gönder", systemImage: "arrow.up").frame(maxWidth: .infinity)
        }
        .handGestureShortcut(.primaryAction)       // çift dokunma gönderir (yalnız sohbet metni; onay değildir)

        TextFieldLink(prompt: Text("Ghost'a söyle")) {
            Label("Yeniden söyle", systemImage: "mic").frame(maxWidth: .infinity)
        } onSubmit: { yeni in
            let temiz = yeni.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !temiz.isEmpty else { return }
            metin = temiz
            asama = .onizleme
            if kendiliginden { Task { await gonder() } }
        }
    }

    private func gonder() async {
        let temiz = metin.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !temiz.isEmpty, asama != .gonderiliyor, asama != .gitti else { return }
        asama = .gonderiliyor
        do {
            try await oturum.api.mesajGonder(temiz)
            asama = .gitti
            Titre.basari()
            try? await Task.sleep(for: .milliseconds(900))
            kapat()
        } catch APIHata.yetkisiz {
            asama = .hata("Eşleşme düşmüş. Yeniden eşle.")
            Titre.hata()
        } catch {
            if SaatOturum.tekrarMi(error) {          // ilk deneme sunucuya ulaşmış: gitti say, çiftleme
                asama = .gitti
                Titre.basari()
                try? await Task.sleep(for: .milliseconds(900))
                kapat()
                return
            }
            asama = .hata(SaatOturum.kisaHata(error) + " — gitmedi.")
            Titre.hata()
        }
    }
}
