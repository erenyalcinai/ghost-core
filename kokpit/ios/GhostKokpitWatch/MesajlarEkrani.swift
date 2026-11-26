import SwiftUI

/// Son mesajlar: dökümün son 10 balonu. Ghost'un cevabında İLK PARAGRAF görünür; devamı varsa balona dokununca
/// cevabın tamamı açılır ve Digital Crown ile kaydırılır. Tablo/diff bilekte gösterilmez, "telefonda" diye işaretlenir.
struct MesajlarEkrani: View {
    @EnvironmentObject var sohbet: SaatSohbet

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 6) {
                if !sohbet.yuklendi {
                    if let h = sohbet.hata {
                        Text(h).font(.footnote).foregroundStyle(Renk.kritik)
                    } else {
                        ProgressView().frame(maxWidth: .infinity)
                    }
                } else if sohbet.mesajlar.isEmpty {
                    Text("Henüz mesaj yok.").font(.footnote).foregroundStyle(Renk.ikincil)
                }
                ForEach(sohbet.mesajlar) { m in
                    Balon(mesaj: m)
                }
                if let mg = sohbet.mesgul, mg.aktif {
                    HStack(spacing: 6) {
                        Maskot(hal: (mg.ozet ?? "").isEmpty ? .dusunuyor : .calisiyor, yukseklik: 18)
                        Text((mg.ozet ?? "").isEmpty ? "Düşünüyor…" : String((mg.ozet ?? "").prefix(50)))
                            .font(.caption2).foregroundStyle(Renk.ikincil).lineLimit(2)
                    }
                }
            }
            .padding(.horizontal, 2)
        }
        .defaultScrollAnchor(.bottom)          // en yeni altta; ekran altta açılır
        .background(Renk.zemin)
        .navigationTitle("Mesajlar")
    }
}

/// Tek balon. sahibin mesajı sağda düz; Ghost'un cevabı solda, ilk paragrafıyla.
private struct Balon: View {
    let mesaj: Mesaj

    var body: some View {
        if mesaj.sahipten {
            Text(DuzMetin.satirIci(SaatMetin.oneksiz(mesaj.metin)))
                .font(.footnote)
                .foregroundStyle(Renk.metin)
                .lineLimit(4)
                .padding(6)
                .background(Renk.kartUst, in: RoundedRectangle(cornerRadius: 9, style: .continuous))
                .frame(maxWidth: .infinity, alignment: .trailing)
        } else {
            let ozet = SaatMetin.ozet(mesaj)
            if ozet.devamVar {
                NavigationLink(value: Rota.mesaj(mesaj.id)) { govde(ozet.metin, devam: true) }
                    .buttonStyle(.plain)
            } else {
                govde(ozet.metin, devam: false)
            }
        }
    }

    private func govde(_ metin: String, devam: Bool) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(metin)
                .font(.footnote)
                .foregroundStyle(mesaj.gecici == true ? Renk.ikincil : Renk.metin)
                .frame(maxWidth: .infinity, alignment: .leading)
            if devam {
                Text("devamı ›").font(.caption2).foregroundStyle(Renk.ikincil)
            }
        }
        .padding(6)
        .background(Renk.kart, in: RoundedRectangle(cornerRadius: 9, style: .continuous))
    }
}

/// Tek cevabın tamamı: paragraf paragraf, Digital Crown ile kaydırılır.
struct MesajEkrani: View {
    @EnvironmentObject var sohbet: SaatSohbet
    let mesajID: Int

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                if let m = sohbet.mesajlar.first(where: { $0.id == mesajID }) {
                    ForEach(DuzMetin.parcala(m.metin)) { p in
                        Text(p.metin)
                            .font(p.telefonda ? .caption2 : .footnote)
                            .foregroundStyle(p.telefonda ? Renk.ikincil : Renk.metin)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    if m.medya != nil {
                        Text("▣ ek — telefonda").font(.caption2).foregroundStyle(Renk.ikincil)
                    }
                    if m.butonlar?.isEmpty == false {
                        Text("Seçenekli soru — telefonda cevapla").font(.caption2).foregroundStyle(Renk.ikincil)
                    }
                } else {
                    Text("Mesaj artık listede değil.").font(.footnote).foregroundStyle(Renk.ikincil)
                }
            }
            .padding(.horizontal, 2)
        }
        .background(Renk.zemin)
        .navigationTitle("Cevap")
    }
}

/// Balon metni yardımcıları (görünümden bağımsız).
enum SaatMetin {
    /// Saatten giden mesaj dökümde "[Cep cihazı: <ad>; onay sayılmaz] " önekiyle durur; balonda önek gösterilmez.
    static func oneksiz(_ metin: String) -> String {
        guard metin.hasPrefix("[Cep cihazı:"), let kapanis = metin.firstIndex(of: "]") else { return metin }
        return String(metin[metin.index(after: kapanis)...]).trimmingCharacters(in: .whitespaces)
    }

    /// Ghost balonunun listede görünen hâli: ilk paragraf; ek/seçenek/başka paragraf varsa "devamı".
    static func ozet(_ m: Mesaj) -> (metin: String, devamVar: Bool) {
        let o = DuzMetin.ilkParagraf(m.metin)
        let ekVar = m.medya != nil || (m.butonlar?.isEmpty == false)
        if o.metin.isEmpty {
            return (m.medya != nil ? "▣ ek — telefonda" : "…", false)
        }
        return (o.metin, o.devamVar || ekVar)
    }
}
