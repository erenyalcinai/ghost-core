import Foundation
import SwiftUI

/// Son mesajlar + Ghost'un meşguliyeti. Yalnız uygulama ÖNDEYKEN uzun yoklama yapar (HTTP; soket yok).
/// Bilek inince sistem uygulamayı uyutur: döngü iptal edilir, hiçbir şey arka plana güvenmez — cevap o zaman
/// bildirimle gelir, uygulama yeniden öne gelince döküm baştan (son 10) çekilir.
@MainActor
final class SaatSohbet: ObservableObject {
    static let tavan = 10

    @Published var mesajlar: [Mesaj] = []
    @Published var mesgul: Mesgul?
    @Published var hata: String?
    @Published var yuklendi = false

    private var son = -1
    private var gts: Double = 0
    private var gorev: Task<Void, Never>?

    /// Öne gelince çağrılır. Zaten koşuyorsa dokunmaz.
    func basla(api: GhostAPI) {
        guard gorev == nil else { return }
        son = -1
        gts = 0
        gorev = Task { [weak self] in
            guard let self else { return }
            await self.dongu(api)
        }
    }

    /// Arkaya düşünce / eşleşme kalkınca çağrılır.
    func dur() {
        gorev?.cancel()
        gorev = nil
        mesgul = nil          // bayat "çalışıyor" kalmasın
    }

    func temizle() {
        dur()
        mesajlar = []
        yuklendi = false
        hata = nil
    }

    private func dongu(_ api: GhostAPI) async {
        while !Task.isCancelled {
            do {
                let ilk = son < 0
                let baslangic = Date()
                // İlk çağrı beklemez (son 10 satır); sonrakiler 20 sn tutulur, yeni satır ya da meşguliyet
                // değişince erken döner.
                let z = try await api.outbox(since: ilk ? -1 : son,
                                             limit: ilk ? SaatSohbet.tavan : 20,
                                             bekle: ilk ? 0 : 20,
                                             durum: mesgul?.imza ?? "0|",
                                             gts: gts)
                if Task.isCancelled { return }
                birlestir(z.mesajlar)
                if let s = z.son { son = max(son, s) }
                if son < 0 { son = 0 }                    // boş döküm: bir daha "ilk" sayma
                if let g = z.gts { gts = max(gts, g) }
                if let m = z.mesgul, m.imza != mesgul?.imza { mesgul = m }
                yuklendi = true
                hata = nil
                if !ilk, z.mesajlar.isEmpty, Date().timeIntervalSince(baslangic) < 1 {
                    try await Task.sleep(for: .seconds(1))   // sunucu hemen döndüyse dönüp durma
                }
            } catch is CancellationError {
                return
            } catch APIHata.yetkisiz {
                return
            } catch {
                if Task.isCancelled { return }
                hata = SaatOturum.kisaHata(error)
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }

    /// Yeni satırları ekle, güncellenen balonu (aynı id) yerinde değiştir, son `tavan` satırı tut.
    private func birlestir(_ gelen: [Mesaj]) {
        guard !gelen.isEmpty else { return }
        var sozluk = Dictionary(uniqueKeysWithValues: mesajlar.map { ($0.id, $0) })
        for m in gelen { sozluk[m.id] = m }
        let sirali = sozluk.values.sorted { $0.id < $1.id }
        let yeni = Array(sirali.suffix(SaatSohbet.tavan))
        if yeni != mesajlar { mesajlar = yeni }
    }
}
