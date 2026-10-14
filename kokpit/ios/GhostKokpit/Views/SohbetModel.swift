import SwiftUI
import PhotosUI
import UniformTypeIdentifiers

/// Sohbet'in veri katmanı (2026-09-04 yapısal düzeltme): önceden 620 satırlık görünümün
/// içinde 20 @State birbirine bağlıydı — yoklama, gönderim, ek yükleme ve kaydırma aynı
/// gövdede yarışıyordu. Artık görünüm yalnız çizer; mesaj listesi, iyimser balonlar,
/// meşguliyet, ek tepsisi ve buton durumu burada. Telegram/ChatGPT'deki gibi: önce
/// tepsiye ekle (çoklu fotoğraf, belge, kamera, pano), sonra tek seferde gönder.
@MainActor
final class SohbetModel: ObservableObject {
    @Published var mesajlar: [Mesaj] = []
    @Published var bekleyenler: [BekleyenMesaj] = []
    @Published var mesgul: Mesgul?
    @Published var mesgulBasladi: Date?
    @Published var eskiYukleniyor = false
    @Published var eskiBitti = false
    @Published var gonderiliyor = false
    @Published var hata: String?
    @Published var okunmamis = 0
    @Published var altta = true
    @Published var komutlar: [Komut] = []
    @Published var ekler: [Ek] = []
    @Published var ekGonderiliyor = false
    /// Mesaj id → seçilen buton verisi: dokunulan karar/soru butonu ikinci kez basılmaz.
    @Published var kullanilanButonlar: [Int: String] = [:]

    private var api: GhostAPI?
    private var son = -1                       // -1: açılışta en son 50 satır
    private var hataSayaci: Task<Void, Never>?
    /// Yarım kalan çoklu gönderi (bir parça yüklenemedi): aynı grupla devam edilir.
    private var aktifGrup: (id: String, toplam: Int, gonderilen: Int)?

    static let ekTavan = 10
    static let belgeTavan = 1_800_000          // sunucu PAYLAS_MAX (nginx 2 MB)

    func bagla(_ api: GhostAPI) { self.api = api }

    // MARK: Teşhis günlüğü

    private var gunlukTampon: [String] = []
    private var gunlukGorevi: Task<Void, Never>?
    private static let saat: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "HH:mm:ss.SSS"; return f
    }()

    /// Kaydırma/klavye/mesaj olaylarını sunucuya taşır (3 sn'de bir toplu). Telefonda
    /// olan biteni Mac'ten göremiyorduk; teşhis tahmin oluyordu (2026-09-26).
    func gunluk(_ s: String) {
        gunlukTampon.append(Self.saat.string(from: Date()) + " " + s)
        if gunlukTampon.count > 400 { gunlukTampon.removeFirst(gunlukTampon.count - 400) }
        guard gunlukGorevi == nil else { return }
        gunlukGorevi = Task { [weak self] in
            try? await Task.sleep(for: .seconds(3))
            await self?.gunlukGonder()
        }
    }

    private func gunlukGonder() async {
        gunlukGorevi = nil
        guard let api, !gunlukTampon.isEmpty else { return }
        let satirlar = gunlukTampon
        gunlukTampon = []
        _ = try? await api.gunluk(satirlar)
    }

    // MARK: Hata

    func hataGoster(_ m: String) {
        hata = m
        hataSayaci?.cancel()
        hataSayaci = Task { [weak self] in
            try? await Task.sleep(for: .seconds(8))
            if !Task.isCancelled { self?.hata = nil }
        }
    }

    func hatayiKapat() { hata = nil; hataSayaci?.cancel() }

    // MARK: Yoklama

    func yoklamaDongusu() async {
        guard let api else { return }
        defer { mesgul = nil; mesgulBasladi = nil }   // sekmeden çıkınca bayat "çalışıyor" kalmasın
        while !Task.isCancelled {
            do {
                let baslangic = Date()
                let z = try await api.outbox(since: son, limit: 50, bekle: 25, durum: mesgul?.imza ?? "0|")
                ekle(z.mesajlar)
                if let s = z.son { son = max(son, s) }
                if let m = z.mesgul {
                    let yeniBaslangic = m.aktif ? baslangicTahmini(m) : nil
                    if yeniBaslangic != mesgulBasladi { mesgulBasladi = yeniBaslangic }
                    // Animasyon yalnız görünüp kaybolurken; her araç özetinde animasyonlu
                    // yeniden çizim satırı oynatıyordu.
                    if m.aktif != (mesgul?.aktif ?? false) {
                        withAnimation(.easeInOut(duration: 0.2)) { mesgul = m }
                    } else if m.imza != mesgul?.imza {
                        mesgul = m
                    }
                }
                bekleyenler.removeAll { Date().timeIntervalSince($0.ts) > 90 }
                if z.mesajlar.isEmpty, Date().timeIntervalSince(baslangic) < 1 {
                    try await Task.sleep(for: .seconds(1))
                }
            } catch is CancellationError {
                return
            } catch APIHata.yetkisiz {
                return
            } catch {
                if Task.isCancelled || (error as? URLError)?.code == .cancelled { return }
                hataGoster("Bağlantı: " + error.localizedDescription)
                try? await Task.sleep(for: .seconds(3))
            }
        }
    }

    /// "Ghost çalışıyor · N sn" sayacının sıfır noktası. Sunucu turun başından beri geçen
    /// süreyi (`sure_sn`) söyler; sayaç ondan kurulur ki sekme değişince, uygulama
    /// kapanıp açılınca ya da model yeniden doğunca sıfırlanmasın. Küçük sapmalar
    /// (yoklama gecikmesi) yerinde bırakılır — her turda 1 sn ileri-geri zıplamasın.
    private func baslangicTahmini(_ m: Mesgul) -> Date {
        guard let s = m.sureSn else { return mesgulBasladi ?? Date() }
        let tahmin = Date(timeIntervalSinceNow: -max(0, s))
        if let eski = mesgulBasladi, abs(eski.timeIntervalSince(tahmin)) < 3 { return eski }
        return tahmin
    }

    func dahaEski() async {
        guard let api, let ilk = mesajlar.first?.id, !eskiYukleniyor else { return }
        eskiYukleniyor = true
        do {
            let z = try await api.outboxEski(once: ilk, limit: 50)
            if z.mesajlar.isEmpty {
                eskiBitti = true
            } else {
                let varOlan = Set(mesajlar.map(\.id))
                mesajlar.append(contentsOf: z.mesajlar.filter { !varOlan.contains($0.id) })
                mesajlar.sort { $0.id < $1.id }
                // Üste eklenen satırlar yerleşene dek bayrak açık kalır: görünüm alt çapayı
                // bu bayrakla tutar, okuduğun yer kaymaz (defer ile aynı karede kapanıyordu).
                try? await Task.sleep(for: .milliseconds(300))
            }
        } catch {
            hataGoster(error.localizedDescription)
        }
        eskiYukleniyor = false
    }

    private func ekle(_ yeni: [Mesaj]) {
        let varOlan = Set(mesajlar.map(\.id))
        let ek = yeni.filter { !varOlan.contains($0.id) }
        guard !ek.isEmpty else { return }
        mesajlar.append(contentsOf: ek)
        mesajlar.sort { $0.id < $1.id }
        if let enSon = mesajlar.last?.id { son = max(son, enSon) }
        gunluk("ekle \(ek.map(\.id)) ghost=\(ek.filter { !$0.sahipten }.count) altta=\(altta)")
        // Sunucu yankısı → iyimser balonu düşür: metin aynıysa o, medyaysa sıradaki medya balonu.
        for m in ek where m.sahipten {
            if m.medya != nil {
                if let i = bekleyenler.firstIndex(where: { $0.gorsel != nil || $0.ekAdi != nil }) {
                    bekleyenler.remove(at: i)
                }
            } else {
                let t = m.metin.trimmingCharacters(in: .whitespacesAndNewlines)
                if let i = bekleyenler.firstIndex(where: { $0.metin == t && $0.gorsel == nil && $0.ekAdi == nil }) {
                    bekleyenler.remove(at: i)
                }
            }
        }
        // Okunmamış: yalnız Ghost'un mesajları (kendi yankım "yeni mesaj" değildir).
        if !altta { okunmamis += ek.filter { !$0.sahipten }.count }
    }

    func komutlariYukle() async {
        guard let api, komutlar.isEmpty else { return }
        komutlar = (try? await api.komutlar()) ?? []
    }

    // MARK: Gönderim

    /// Metin (ve varsa ekler). Başarısızsa geri yüklenecek metni döndürür.
    func gonder(_ ham: String, sesli: Bool) async -> String? {
        let m = ham.trimmingCharacters(in: .whitespacesAndNewlines)
        if !ekler.isEmpty {
            await ekleriGonder(not: m)
            return ekler.isEmpty ? nil : m           // ek kaldıysa metin de kalsın
        }
        guard let api, !m.isEmpty, !gonderiliyor else { return m.isEmpty ? nil : m }
        gonderiliyor = true
        defer { gonderiliyor = false }
        let bekleyen = BekleyenMesaj(metin: m)
        bekleyenler.append(bekleyen)
        gunluk("gonder bekleyen altta=\(altta)→true")
        altta = true
        okunmamis = 0
        Titre.hafif()
        do {
            try await api.mesajGonder(m, sesli: sesli)
            hatayiKapat()
            return nil
        } catch {
            bekleyenler.removeAll { $0.id == bekleyen.id }
            hataGoster("Gönderilemedi: " + error.localizedDescription)
            Titre.hata()
            return m
        }
    }

    /// Tepsideki ekleri sırayla, aynı grupla yükle; not son parçayla gider (köprü tek tur kurar).
    func ekleriGonder(not: String) async {
        guard let api, !ekler.isEmpty, !ekGonderiliyor else { return }
        ekGonderiliyor = true
        defer { ekGonderiliyor = false }
        var grup = aktifGrup ?? (id: UUID().uuidString.lowercased().replacingOccurrences(of: "-", with: "").prefix(16).description,
                                 toplam: ekler.count, gonderilen: 0)
        // Yeni ek eklendiyse toplam büyür (köprü eksik parçayı 20 sn bekler, fazlasını sayar).
        grup.toplam = max(grup.toplam, grup.gonderilen + ekler.count)
        altta = true
        okunmamis = 0
        while let ek = ekler.first {
            let sonParca = ekler.count == 1
            guard let i = ekler.firstIndex(where: { $0.id == ek.id }) else { break }
            ekler[i].durum = .yukleniyor
            do {
                try await api.paylasDosya(ek.veri, ad: ek.ad, mime: ek.mime, not: sonParca ? not : nil,
                                         grup: grup.toplam > 1 ? grup.id : nil,
                                         sira: grup.gonderilen + 1, toplam: grup.toplam)
                grup.gonderilen += 1
                bekleyenler.append(BekleyenMesaj(metin: sonParca ? not : "", gorsel: ek.kucuk,
                                                 ekAdi: ek.tur == "gorsel" ? nil : ek.ad))
                ekler.removeAll { $0.id == ek.id }
                Titre.hafif()
            } catch {
                if let j = ekler.firstIndex(where: { $0.id == ek.id }) {
                    ekler[j].durum = .hata(error.localizedDescription)
                }
                aktifGrup = grup
                hataGoster("Ek gönderilemedi — dokunup tekrar dene.")
                Titre.hata()
                return
            }
        }
        aktifGrup = nil
        hatayiKapat()
    }

    func ekiTekrarDene(_ ek: Ek, not: String) async {
        if let i = ekler.firstIndex(where: { $0.id == ek.id }) { ekler[i].durum = .hazir }
        await ekleriGonder(not: not)
    }

    // MARK: Ek tepsisi

    func ekCikar(_ ek: Ek) {
        ekler.removeAll { $0.id == ek.id }
        if ekler.isEmpty { aktifGrup = nil }
    }

    func ekleGorsel(_ img: PlatformImage, ad: String = "foto.jpg") {
        guard ekler.count < Self.ekTavan else { hataGoster("En fazla \(Self.ekTavan) ek."); return }
        let veri = GorselYardim.jpeg(img)
        ekler.append(Ek(ad: ad, mime: "image/jpeg", tur: "gorsel", veri: veri, kucuk: GorselYardim.kucult(img, 240)))
    }

    func ekleFotolar(_ ogeler: [PhotosPickerItem]) async {
        for (n, o) in ogeler.enumerated() {
            if let d = try? await o.loadTransferable(type: Data.self), let img = PlatformImage(data: d) {
                ekleGorsel(img, ad: "foto-\(n + 1).jpg")
            } else {
                hataGoster("Bir fotoğraf okunamadı.")
            }
        }
    }

    func ekleBelge(_ url: URL) {
        let erisim = url.startAccessingSecurityScopedResource()
        defer { if erisim { url.stopAccessingSecurityScopedResource() } }
        do {
            let veri = try Data(contentsOf: url)
            guard veri.count <= Self.belgeTavan else {
                hataGoster("\(url.lastPathComponent) çok büyük (\(veri.count / 1024) KB; sınır 1800 KB)."); return
            }
            guard ekler.count < Self.ekTavan else { hataGoster("En fazla \(Self.ekTavan) ek."); return }
            let mime = UTType(filenameExtension: url.pathExtension)?.preferredMIMEType ?? "application/octet-stream"
            let gorselMi = mime.hasPrefix("image/")
            if gorselMi, let img = PlatformImage(data: veri) {
                ekleGorsel(img, ad: url.lastPathComponent)
            } else {
                ekler.append(Ek(ad: url.lastPathComponent, mime: mime, tur: "belge", veri: veri, kucuk: nil))
            }
        } catch {
            hataGoster("Belge okunamadı: " + error.localizedDescription)
        }
    }

    func panodanYapistir() {
        if let img = Pano.gorsel {
            ekleGorsel(img, ad: "pano.jpg")
        } else {
            hataGoster("Panoda görsel yok.")
        }
    }

    // MARK: Butonlar

    func butonTikla(mesajID: Int, veri: String) async {
        guard let api, kullanilanButonlar[mesajID] == nil else { return }
        kullanilanButonlar[mesajID] = veri
        do {
            if veri.hasPrefix("ok:") {
                try await api.kararVer(id: String(veri.dropFirst(3)), onay: true)
                Titre.basari()
            } else if veri.hasPrefix("no:") {
                try await api.kararVer(id: String(veri.dropFirst(3)), onay: false)
                Titre.basari()
            } else {
                bekleyenler.append(BekleyenMesaj(metin: veri))
                try await api.mesajGonder(veri)
                Titre.hafif()
            }
            hatayiKapat()
        } catch {
            kullanilanButonlar[mesajID] = nil          // tekrar denenebilsin
            bekleyenler.removeAll { $0.metin == veri && $0.gorsel == nil }
            hataGoster(error.localizedDescription)
            Titre.hata()
        }
    }
}

/// Tepsideki ek: görsel (küçük önizleme) ya da belge.
struct Ek: Identifiable, Equatable {
    enum Durum: Equatable { case hazir, yukleniyor, hata(String) }
    let id = UUID()
    var ad: String
    var mime: String
    var tur: String                 // gorsel | belge
    var veri: Data
    var kucuk: PlatformImage?
    var durum: Durum = .hazir
    var boyutKB: Int { veri.count / 1024 }
    static func == (a: Ek, b: Ek) -> Bool { a.id == b.id && a.durum == b.durum }
}

/// Sunucuya gitti ama yankısı gelmedi: soluk balon (metin, görsel ya da belge).
struct BekleyenMesaj: Identifiable, Equatable {
    let id = UUID()
    var metin: String
    var gorsel: PlatformImage? = nil
    var ekAdi: String? = nil
    var ts = Date()
    static func == (a: BekleyenMesaj, b: BekleyenMesaj) -> Bool { a.id == b.id }
}

/// Bir kez inen görsel bir daha inmez (kaydırırken /v1/medya tekrar tekrar çekiliyordu).
enum GorselOnbellek {
    static let paylasilan: NSCache<NSString, PlatformImage> = {
        let c = NSCache<NSString, PlatformImage>()
        c.countLimit = 200
        c.totalCostLimit = 120 * 1024 * 1024
        return c
    }()
}
