import ActivityKit
import Foundation

/// Live Activity: uzun iş kartı (kilit ekranı + Dynamic Island). Sunucu `ghost/apns.py`
/// la_durum() ile BİRE BİR: durum · ozet · basladi (epoch) · bitti (epoch?).
struct IsEtkinligi: ActivityAttributes {
    struct ContentState: Codable, Hashable {
        var durum: String          // çalışıyor | denetimde | bitti | hata | iptal
        var ozet: String
        var basladi: Int
        var bitti: Int?
    }
    var jid: Int
    var tur: String
}
