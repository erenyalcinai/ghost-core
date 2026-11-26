import SwiftUI

/// Maskotun hâli (marka/kimlik.md §4.1). Hâl GÖSTERGEDİR: yalnız Ghost gerçekten o durumdaysa kullanılır.
enum MaskotHal: Equatable {
    case notr           // 01 ayakta, boşta
    case dikkat         // 04 seni bekliyor (karar var) / dinliyor
    case dusunuyor      // 05 arıyor, tarıyor (tur sürüyor, araç özeti yok)
    case calisiyor      // 06 iş yürüyor
    case hata           // 07 gerçek hata (nabız kritik)
    case yokluk         // 10 ulaşılamıyor / eşli değil: gövde gider, iki göz kalır
}

/// Onaylı siluet (`marka/video/hayalet.js` ile aynı yol ve ölçüler; viewBox -14 0 94 100).
/// İki renk + ikincil gri; gradyan, gölge, yumuşak geçiş YOK. Hareket 12 kare/sn'ye kilitli tam piksel adımlar.
struct Maskot: View {
    var hal: MaskotHal
    var yukseklik: CGFloat = 64

    private static let oran: CGFloat = 0.94      // genişlik / yükseklik
    private static let acik = Color(hex: 0xEDEAE4)
    private static let gri = Color(hex: 0x5C5A57)
    private static let zemin = Color(hex: 0x0B0C0E)

    var body: some View {
        // Kırpma ve durum çubuğu için saat: 12 kare/sn. Bilek inince sistem TimelineView'i kendisi yavaşlatır.
        TimelineView(.periodic(from: .now, by: 1.0 / 12.0)) { zaman in
            let ms = Int(zaman.date.timeIntervalSinceReferenceDate * 1000)
            Canvas { baglam, boyut in
                let olcek = boyut.height / 100
                baglam.translateBy(x: 14 * olcek, y: 0)
                baglam.scaleBy(x: olcek, y: olcek)
                Maskot.ciz(&baglam, hal: hal, ms: ms)
            }
        }
        .frame(width: yukseklik * Maskot.oran, height: yukseklik)
        .accessibilityLabel(Text(Maskot.aciklama(hal)))
    }

    static func aciklama(_ hal: MaskotHal) -> String {
        switch hal {
        case .notr: return "Ghost boşta"
        case .dikkat: return "Ghost seni bekliyor"
        case .dusunuyor: return "Ghost düşünüyor"
        case .calisiyor: return "Ghost çalışıyor"
        case .hata: return "Ghost'ta sorun var"
        case .yokluk: return "Ghost'a ulaşılamıyor"
        }
    }

    /// Gövde: yuvarlak tepe + dört uçlu etek. SVG yolu:
    /// M40 6 C22 6 12 20 12 38 v50 l9-9 9 9 10-9 10 9 9-9 9 9 V38 C68 20 58 6 40 6 z
    private static var govde: Path {
        var p = Path()
        p.move(to: CGPoint(x: 40, y: 6))
        p.addCurve(to: CGPoint(x: 12, y: 38), control1: CGPoint(x: 22, y: 6), control2: CGPoint(x: 12, y: 20))
        p.addLine(to: CGPoint(x: 12, y: 88))
        p.addLine(to: CGPoint(x: 21, y: 79))
        p.addLine(to: CGPoint(x: 30, y: 88))
        p.addLine(to: CGPoint(x: 40, y: 79))
        p.addLine(to: CGPoint(x: 50, y: 88))
        p.addLine(to: CGPoint(x: 59, y: 79))
        p.addLine(to: CGPoint(x: 68, y: 88))
        p.addLine(to: CGPoint(x: 68, y: 38))
        p.addCurve(to: CGPoint(x: 40, y: 6), control1: CGPoint(x: 68, y: 20), control2: CGPoint(x: 58, y: 6))
        p.closeSubpath()
        return p
    }

    private static func gozler(_ x1: CGFloat, _ x2: CGFloat, _ y: CGFloat, _ h: CGFloat) -> Path {
        var p = Path()
        p.addRect(CGRect(x: x1, y: y, width: 9, height: h))
        p.addRect(CGRect(x: x2, y: y, width: 9, height: h))
        return p
    }

    private static func ciz(_ b: inout GraphicsContext, hal: MaskotHal, ms: Int) {
        let kirp = (ms % 2400) < 167                    // her 2400 ms'de iki kare göz kapanır (canlılık)
        let acikGoz = gozler(26, 45, 36, 13)
        let kapaliGoz = gozler(26, 45, 41, 3)
        switch hal {
        case .notr:
            b.fill(govde, with: .color(acik))
            b.fill(kirp ? kapaliGoz : acikGoz, with: .color(zemin))
        case .dikkat:                                   // gözler 9×19
            b.fill(govde, with: .color(acik))
            b.fill(gozler(26, 45, 32, 19), with: .color(zemin))
        case .dusunuyor:                                // gözler yana kayar (+4)
            b.fill(govde, with: .color(acik))
            b.fill(gozler(30, 49, 36, 13), with: .color(zemin))
        case .calisiyor:                                // gövde altında durum çubuğu tam piksel adımlarla dolar
            b.fill(govde, with: .color(acik))
            b.fill(kirp ? kapaliGoz : acikGoz, with: .color(zemin))
            let w = CGFloat(6 + ((ms / 110) * 4) % 42)
            b.fill(Path(CGRect(x: 16, y: 93, width: 48, height: 5)), with: .color(gri))
            b.fill(Path(CGRect(x: 16, y: 93, width: w, height: 5)), with: .color(acik))
        case .hata:                                     // gövde dört dilime ayrılır, dilimler kayar
            let dilimler: [(y: CGFloat, h: CGFloat, dx: CGFloat)] = [(0, 26, 0), (26, 20, 6), (46, 22, -4), (68, 32, 3)]
            for d in dilimler {
                var k = b
                k.clip(to: Path(CGRect(x: -14 + d.dx, y: d.y, width: 94, height: d.h)))
                k.translateBy(x: d.dx, y: 0)
                k.fill(govde, with: .color(acik))
                k.fill(acikGoz, with: .color(zemin))
            }
        case .yokluk:                                   // gövde gider, iki göz kalır
            b.fill(acikGoz, with: .color(acik))
        }
    }
}
