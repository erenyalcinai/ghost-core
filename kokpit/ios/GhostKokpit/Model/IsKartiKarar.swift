import Foundation

/// İş kartı (Live Activity) toparlayıcısının kararı (2026-11-26, "iş bitince kart kapanmıyor").
/// ActivityKit'e bağlı DEĞİL (Mac'te ve komut satırı sınamasında derlensin: tools/etkinlik_test.swift);
/// kartı kapatan kod App/Bildirim.swift'te.
enum IsKartiKarar {
    /// Sunucunun süren iş saydığı durumlar (ghost/health.py AKTIF_IS ile aynı).
    static let aktifDurumlar: Set<String> = ["kuyrukta", "çalışıyor", "denetimde"]

    /// `nil` = iş sürüyor ya da bilinmiyor, kart kalsın; değer = kartı bu son durumla kapat.
    /// Yeni sunucu `aktif_idler` verir (listede olmayan iş bitmiştir); eskisinde yalnız kesin bilgiyle kapatılır.
    static func bitisDurumu(jid: Int, isler: Isler) -> String? {
        let son = isler.ozet?.first { $0.id.metin == String(jid) }?.durum
        if let ids = isler.aktifIdler {
            if ids.contains(jid) { return nil }
            if let d = son, !aktifDurumlar.contains(d) { return d }
            return "bitti"
        }
        if let d = son { return aktifDurumlar.contains(d) ? nil : d }
        return isler.aktif == 0 ? "bitti" : nil
    }
}
