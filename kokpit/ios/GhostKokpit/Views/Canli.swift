import SwiftUI

/// MJPEG akışını (multipart/x-mixed-replace) kare kare çözen küçük oynatıcı.
/// Telefon canlı görüntüsü için; FFD8…FFD9 sınırlarını tarar, PlatformImage üretir.
final class MJPEGOynatici: NSObject, ObservableObject, URLSessionDataDelegate {
    @Published var kare: PlatformImage?
    @Published var hata: String?
    private var tampon = Data()
    private var oturum: URLSession?
    private var gorev: URLSessionDataTask?
    private let kilit = NSLock()

    func baslat(_ req: URLRequest) {
        durdur()
        let k = URLSessionConfiguration.default
        k.timeoutIntervalForRequest = 20
        k.waitsForConnectivity = false
        let o = URLSession(configuration: k, delegate: self, delegateQueue: nil)
        oturum = o
        gorev = o.dataTask(with: req)
        gorev?.resume()
    }

    func durdur() {
        gorev?.cancel()
        oturum?.invalidateAndCancel()
        gorev = nil
        oturum = nil
        kilit.lock(); tampon.removeAll(); kilit.unlock()
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                    didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        let kod = (response as? HTTPURLResponse)?.statusCode ?? 0
        if kod != 200 {
            DispatchQueue.main.async { self.hata = kod == 503 ? "uzuv uykuda" : "akış \(kod)" }
            completionHandler(.cancel); return
        }
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        kilit.lock()
        tampon.append(data)
        var son: PlatformImage?
        while let bas = tampon.range(of: Data([0xFF, 0xD8, 0xFF])),
              let bit = tampon.range(of: Data([0xFF, 0xD9]), in: bas.upperBound..<tampon.endIndex) {
            let jpeg = tampon.subdata(in: bas.lowerBound..<bit.upperBound)
            tampon.removeSubrange(0..<bit.upperBound)
            if let img = PlatformImage(data: jpeg) { son = img }
        }
        if tampon.count > 4_000_000 { tampon.removeAll() }   // bozuk akış — sıfırla
        kilit.unlock()
        if let img = son {
            DispatchQueue.main.async { self.kare = img; self.hata = nil }
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let e = error, (e as? URLError)?.code != .cancelled {
            DispatchQueue.main.async { self.hata = "akış koptu" }
        }
    }
}

/// Canlı görüntü: telefon → MJPEG (düşerse jpg yoklama), mac → 1 sn'de bir jpg.
/// `kucuk` kipi (Sahne'deki Uzuvlar şeridi): Mac'ten 640 px kare ister, telefonda akış yerine
/// jpg yoklar — iki kart yan yana sürekli açık dururken bant genişliği tam ekranın onda biri.
struct CanliGorunum: View {
    @EnvironmentObject var oturum: Oturum
    let kaynak: String
    var yukseklik: CGFloat = 360
    var kucuk: Bool = false
    var aralik: Double = 1.0
    /// Telefon için ham MJPEG akışı istensin mi. 2026-09-06: tam ekranda akış (tam çözünürlük,
    /// ~10 kare/sn, 1–3 MB/sn) Mac'in zayıf Wi-Fi uplink'ini anında doldurup tüneli öldürüyordu
    /// (Sahip: "büyütmek istediğimde kopuyorlar"). Varsayılan KAPALI: tam ekran da jpg yoklar.
    var akis: Bool = false
    /// Kare geldi → true, uzuv uykuda/hata → false (şerit kartındaki nokta için).
    var onDurum: ((Bool) -> Void)? = nil
    @StateObject private var oynatici = MJPEGOynatici()
    @State private var kare: PlatformImage?
    @State private var hata: String?
    @State private var mjpegDustu = false

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Renk.kart)
            if let img = kare ?? oynatici.kare {
                Image(platform: img).resizable().scaledToFit()
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            } else {
                VStack(spacing: 6) {
                    Image(systemName: kaynak == "mac" ? "appbopcomputer" : "iphone")
                        .font(.title2).foregroundStyle(Renk.ikincil)
                    Text(hata ?? oynatici.hata ?? "bağlanıyor…")
                        .font(.footnote).foregroundStyle(Renk.ikincil)
                }
            }
        }
        .frame(maxWidth: .infinity)
        .frame(height: yukseklik)
        .task(id: kaynak) { await dongu() }
        .onDisappear { oynatici.durdur() }
    }

    private func dongu() async {
        if kaynak == "telefon", akis, !kucuk, !mjpegDustu, let req = try? oturum.api.imzaliIstek("GET", "/v1/sahne/telefon.mjpeg") {
            oynatici.baslat(req)
            // 6 sn içinde kare gelmezse jpg yoklamasına düş
            try? await Task.sleep(for: .seconds(6))
            if oynatici.kare != nil { onDurum?(true); return }
            oynatici.durdur()
            mjpegDustu = true
        }
        let yol = kaynak == "mac" ? "/v1/sahne/mac.jpg" : "/v1/sahne/telefon.jpg"
        // Mac: şerit 640 px, tam ekran 1000 px (1512 tam boy ~250 KB/kare, uplink'e ağır).
        let sorgu = kaynak == "mac" ? [URLQueryItem(name: "boy", value: kucuk ? "640" : "1000")] : []
        while !Task.isCancelled {
            do {
                let veri = try await oturum.api.kare(yol, sorgu: sorgu)
                if let img = PlatformImage(data: veri) { kare = img; hata = nil; onDurum?(true) }
            } catch is CancellationError {
                return
            } catch APIHata.sunucu(let kod, _) where kod == 503 {
                hata = kucuk ? "uzuv uykuda" : "uzuv uykuda ya da tünel kapalı"
                onDurum?(false)
            } catch {
                if Task.isCancelled { return }
                hata = error.localizedDescription
                onDurum?(false)
            }
            try? await Task.sleep(for: .seconds(hata == nil ? aralik : max(4.0, aralik)))
        }
    }
}

/// Tam ekran canlı görüntü (Uzuvlar şeridindeki karta dokununca). Telefon MJPEG, Mac 1 sn jpg.
struct CanliTamEkran: View {
    let kaynak: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            GeometryReader { g in
                // Tam ekran: Mac 2 sn'de bir 1000 px, telefon 1.5 sn'de bir hafif kare (WDA
                // ölçek/kalite sunucuda düşürülür, `canli.telefon_hafiflet`). Akış yok.
                CanliGorunum(kaynak: kaynak, yukseklik: g.size.height, aralik: kaynak == "mac" ? 2.0 : 1.5)
                    .padding(.horizontal, 8)
            }
            .ghostZemin()
            .navigationTitle(kaynak == "mac" ? "Mac ekranı" : "iPhone ekranı")
            .satirBaslik()
            .toolbar {
                ToolbarItem(placement: .ghostSol) { Button("Kapat") { dismiss() } }
            }
        }
    }
}
