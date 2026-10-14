import SwiftUI
import UniformTypeIdentifiers
#if canImport(UIKit)
import UIKit
/// iOS: UIImage · macOS: NSImage. Görsel taşıyan her yer bu adı kullanır.
typealias PlatformImage = UIImage
#else
import AppKit
typealias PlatformImage = NSImage
#endif

/// Tek kaynaktan iki platform (2026-09-26, Sahip kararı: "Mac'te de app olsun").
/// iOS'a özel API'ler (UIKit, pano, titreşim, klavye türü, gezinti çubuğu) burada
/// köprülenir; görünümler yalnız bu adları kullanır, `#if os(...)` görünümlere sızmaz.
/// Widget hedefi bu dosyayı ALMAZ (yalnız Tema.swift alır) — buraya Tema bağımlılığı koyma.

extension Image {
    init(platform img: PlatformImage) {
        #if canImport(UIKit)
        self.init(uiImage: img)
        #else
        self.init(nsImage: img)
        #endif
    }
}

/// Sistem panosu: görsel var mı / oku · metin yaz.
enum Pano {
    static var gorselVar: Bool {
        #if canImport(UIKit)
        return UIPasteboard.general.hasImages
        #else
        return NSPasteboard.general.canReadObject(forClasses: [NSImage.self], options: nil)
        #endif
    }

    static var gorsel: PlatformImage? {
        #if canImport(UIKit)
        return UIPasteboard.general.image
        #else
        return NSPasteboard.general.readObjects(forClasses: [NSImage.self], options: nil)?.first as? NSImage
        #endif
    }

    static func yaz(_ metin: String) {
        #if canImport(UIKit)
        UIPasteboard.general.string = metin
        #else
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(metin, forType: .string)
        #endif
    }
}

/// Bu cihazın adı — eşleme kaydında görünür ("sahibin iPhone'u", "MacBook Pro").
enum Cihaz {
    static var ad: String {
        #if canImport(UIKit)
        return UIDevice.current.name
        #else
        return Host.current().localizedName ?? "Mac"
        #endif
    }
}

/// Dokunsal geri bildirim — gönderim, onay, hata. Mac'te izleme dörtgeni (Force Touch) titrer.
enum Titre {
    #if canImport(UIKit)
    static func hafif() { UIImpactFeedbackGenerator(style: .light).impactOccurred() }
    static func basari() { UINotificationFeedbackGenerator().notificationOccurred(.success) }
    static func hata() { UINotificationFeedbackGenerator().notificationOccurred(.error) }
    #else
    static func hafif() { NSHapticFeedbackManager.defaultPerformer.perform(.generic, performanceTime: .now) }
    static func basari() { NSHapticFeedbackManager.defaultPerformer.perform(.levelChange, performanceTime: .now) }
    static func hata() { NSHapticFeedbackManager.defaultPerformer.perform(.alignment, performanceTime: .now) }
    #endif
}

/// Görsel ölçekleme/JPEG — her iki platformda aynı sınırlar (nginx 2 MB, tepsi 240 px).
enum GorselYardim {
    /// En uzun kenar ≤ 1280 px, JPEG 0.6 — nginx 2 MB sınırının altında (Share Extension ile aynı).
    static func jpeg(_ img: PlatformImage) -> Data {
        let k = kucult(img, 1280)
        #if canImport(UIKit)
        return k.jpegData(compressionQuality: 0.6) ?? Data()
        #else
        guard let tiff = k.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff) else { return Data() }
        return rep.representation(using: .jpeg, properties: [.compressionFactor: 0.6]) ?? Data()
        #endif
    }

    /// Tepsi/iyimser balon için küçük kopya (uzun kenar ≤ `boy` px).
    static func kucult(_ img: PlatformImage, _ boy: CGFloat) -> PlatformImage {
        let oran = min(1, boy / max(img.size.width, img.size.height))
        let hedef = CGSize(width: img.size.width * oran, height: img.size.height * oran)
        #if canImport(UIKit)
        let f = UIGraphicsImageRendererFormat.default(); f.scale = 1
        return UIGraphicsImageRenderer(size: hedef, format: f).image { _ in img.draw(in: CGRect(origin: .zero, size: hedef)) }
        #else
        if oran >= 1 { return img }
        let yeni = NSImage(size: hedef)
        yeni.lockFocus()
        img.draw(in: CGRect(origin: .zero, size: hedef), from: .zero, operation: .copy, fraction: 1)
        yeni.unlockFocus()
        return yeni
        #endif
    }
}

/// Geniş pencere düzeni: telefonda tam genişlik doğal, Mac'te balon ve görsel tavanlanır.
enum Duzen {
    #if os(macOS)
    static let balonTavan: CGFloat = 760
    static let gorselTavan: CGFloat = 480
    /// Mac uygulaması Mac'in kendi ekranını göstermesin (ayna içinde ayna).
    static let macKarti = false
    #else
    static let balonTavan: CGFloat = .infinity
    static let gorselTavan: CGFloat = .infinity
    static let macKarti = true
    #endif
}

extension ToolbarItemPlacement {
    /// iOS: gezinti çubuğu sol · macOS: iptal/kapat konumu.
    static var ghostSol: ToolbarItemPlacement {
        #if os(iOS)
        return .topBarLeading
        #else
        return .cancellationAction
        #endif
    }
    /// iOS: gezinti çubuğu sağ · macOS: birincil eylem.
    static var ghostSag: ToolbarItemPlacement {
        #if os(iOS)
        return .topBarTrailing
        #else
        return .primaryAction
        #endif
    }
}

extension View {
    /// iOS: satır içi (küçük) gezinti başlığı. macOS'ta böyle bir kip yok.
    @ViewBuilder func satirBaslik() -> some View {
        #if os(iOS)
        self.navigationBarTitleDisplayMode(.inline)
        #else
        self
        #endif
    }

    /// iOS: gruplu iç liste · macOS: inset liste.
    @ViewBuilder func grupluListe() -> some View {
        #if os(iOS)
        self.listStyle(.insetGrouped)
        #else
        self.listStyle(.inset)
        #endif
    }

    /// Sayı klavyesi (iOS); Mac'te donanım klavyesi, kip yok.
    @ViewBuilder func sayiKlavyesi(ondalik: Bool = false) -> some View {
        #if os(iOS)
        self.keyboardType(ondalik ? .decimalPad : .numberPad)
        #else
        self
        #endif
    }

    /// Kaydırınca klavyeyi indir (iOS).
    /// Listeyi kaydırmaya başlayınca klavye iner. `.interactively` yalnız parmak klavyeye
    /// kadar sürüklenince indiriyordu; Sahip listenin herhangi bir yerinden kaydırınca
    /// kapanmasını istedi (2026-09-26).
    @ViewBuilder func kaydirincaKlavyeyiIndir() -> some View {
        #if os(iOS)
        self.scrollDismissesKeyboard(.immediately)
        #else
        self
        #endif
    }

    /// iOS: klavye animasyonu BİTİNCE çalışır (odak onChange'i erken düşer, 0.3 sn tahmini
    /// yarışıyordu). macOS'ta klavye yok.
    @ViewBuilder func klavyeAcilinca(_ eylem: @escaping () -> Void) -> some View {
        #if os(iOS)
        self.onReceive(NotificationCenter.default.publisher(for: UIResponder.keyboardDidShowNotification)) { _ in eylem() }
        #else
        self
        #endif
    }

    /// iOS: aşağı çekince yenile · macOS: aynısı + araç çubuğunda Yenile düğmesi (⌘R),
    /// çünkü izleme dörtgeninde "aşağı çek" refleksi yok.
    @ViewBuilder func yenilenebilir(_ eylem: @escaping @Sendable () async -> Void) -> some View {
        #if os(iOS)
        self.refreshable { await eylem() }
        #else
        self.refreshable { await eylem() }
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button { Task { await eylem() } } label: { Image(systemName: "arrow.clockwise") }
                        .keyboardShortcut("r", modifiers: .command)
                        .help("Yenile (⌘R)")
                }
            }
        #endif
    }

    /// iOS: tam ekran kapak · macOS: pencere içi sheet (tam ekran kapak yok).
    @ViewBuilder func tamEkranKapak<Item: Identifiable, Content: View>(
        item: Binding<Item?>, @ViewBuilder content: @escaping (Item) -> Content) -> some View {
        #if os(iOS)
        self.fullScreenCover(item: item, content: content)
        #else
        // macOS sheet'i içeriğin söylediği boyutu alır; GeometryReader'lı canlı görüntü
        // hiçbir şey söylemediği için pencere sıfıra çöküyordu (Sahip, 2026-09-26).
        // Telefon oranında (375×812) başlayan, büyütülebilir bir pencere.
        self.sheet(item: item) { i in
            content(i).frame(minWidth: 440, idealWidth: 480, maxWidth: 1200,
                             minHeight: 720, idealHeight: 900, maxHeight: 1600)
        }
        #endif
    }
}
