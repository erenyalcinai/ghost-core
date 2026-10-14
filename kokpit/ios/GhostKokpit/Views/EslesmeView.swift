import SwiftUI

/// Eşleme: kimlik yoksa ya da 401'de. 6 haneli kod → POST /v1/eslesme → Keychain.
struct EslesmeView: View {
    @EnvironmentObject var oturum: Oturum
    @State private var kod = ""
    @State private var mesgul = false
    @State private var hata: String?
    @State private var sunucu: String?   // "erişilebilir" / hata metni
    @FocusState private var odak: Bool

    var body: some View {
        VStack(spacing: 28) {
            Spacer()
            VStack(spacing: 6) {
                Text("Ghost").font(.system(size: 34, weight: .semibold)).foregroundStyle(Renk.metin)
                Text("Kokpit eşleme").font(.subheadline).foregroundStyle(Renk.ikincil)
            }
            Text("Telegram'da /cihaz yaz; gelen 6 haneli kodu buraya gir (10 dk geçerli).")
                .font(.footnote).foregroundStyle(Renk.ikincil)
                .multilineTextAlignment(.center).padding(.horizontal, 32)

            TextField("000000", text: $kod)
                .sayiKlavyesi()
                .textContentType(.oneTimeCode)
                .font(.system(size: 34, weight: .medium, design: .monospaced))
                .multilineTextAlignment(.center)
                .foregroundStyle(Renk.metin)
                .focused($odak)
                .padding(.vertical, 14)
                .background(Renk.kart, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .padding(.horizontal, 48)
                .onChange(of: kod) { _, yeni in
                    let temiz = String(yeni.filter(\.isNumber).prefix(6))
                    if temiz != yeni { kod = temiz }
                    if temiz.count == 6 { Task { await esle() } }
                }

            Button { Task { await esle() } } label: {
                Group {
                    if mesgul { ProgressView().tint(Renk.zemin) } else { Text("Eşle") }
                }
                .frame(maxWidth: .infinity).padding(.vertical, 12)
            }
            .background(kod.count == 6 && !mesgul ? Renk.metin : Renk.ikincil,
                        in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .foregroundStyle(Renk.zemin)
            .disabled(kod.count != 6 || mesgul)
            .padding(.horizontal, 48)

            if let h = hata {
                Text(h).font(.footnote).foregroundStyle(Renk.kritik)
                    .multilineTextAlignment(.center).padding(.horizontal, 32)
            }
            Spacer()
            VStack(spacing: 4) {
                Text(oturum.taban.absoluteString).font(.caption2.monospaced())
                HStack(spacing: 6) {
                    Nokta(renk: sunucu == "erişilebilir" ? Renk.iyi : (sunucu == nil ? Renk.ikincil : Renk.kritik), boyut: 6)
                    Text("Sunucu: " + (sunucu ?? "kontrol ediliyor…")).font(.caption2)
                }
            }
            .foregroundStyle(Renk.ikincil)
            .padding(.bottom, 16)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Renk.zemin)
        .onTapGesture { odak = false }
        .task { await pingle() }
        .onAppear { odak = true }
    }

    private func esle() async {
        guard kod.count == 6, !mesgul else { return }
        mesgul = true
        defer { mesgul = false }
        do {
            try await oturum.esle(kod: kod, ad: Cihaz.ad)
            hata = nil
        } catch APIHata.sunucu(let k, _) where k == 403 {
            hata = "Kod kabul edilmedi (403). Yeni kod al."
            kod = ""
        } catch {
            hata = error.localizedDescription
        }
    }

    private func pingle() async {
        do {
            let p = try await oturum.api.ping()
            sunucu = p.ok ? "erişilebilir" : "yanıt ok değil"
        } catch {
            sunucu = "erişilemiyor (" + String(error.localizedDescription.prefix(60)) + ")"
        }
    }
}
