import Foundation
import CryptoKit

/// Cihaz kimliği: device_id + 64 hex anahtar.
struct Kimlik: Equatable {
    var deviceID: String
    var key: String
    var keyBytes: Data? { Data(hex: key) }
    var gecerli: Bool { !deviceID.isEmpty && key.count == 64 && keyBytes != nil }
}

enum APIHata: LocalizedError {
    case kimlikYok
    case yetkisiz                 // 401 → eşleme ekranı
    case sunucu(Int, String)
    case cozumleme(String)

    var errorDescription: String? {
        switch self {
        case .kimlikYok: return "Cihaz eşlenmemiş."
        case .yetkisiz: return "Kimlik reddedildi (401)."
        case .sunucu(let kod, let govde):
            let g = govde.trimmingCharacters(in: .whitespacesAndNewlines)
            return "Sunucu \(kod)" + (g.isEmpty ? "" : ": " + String(g.prefix(160)))
        case .cozumleme(let m): return "Yanıt çözülemedi: " + m
        }
    }
}

/// Ghost Kokpit sunucusuyla konuşan tek sınıf: HMAC imzalama + tüm v1 uçları.
///
/// İmza: X-Ghost-Sig = hex(HMAC-SHA256(key, "{ts}\n{nonce}\n{METHOD}\n{path?query}\n{sha256hex(body)}"))
final class GhostAPI {
    let base: URL
    var kimlik: Kimlik?
    /// 401 gelince çağrılır; Oturum bunu eşleme ekranına düşmek için kullanır.
    var yetkisizOldu: (() -> Void)?

    static let kullaniciAjani = "GhostKokpit/1 (iOS)"

    private let oturum: URLSession
    private let uzunOturum: URLSession   // outbox uzun yoklaması (25 sn bekler)

    init(base: URL, kimlik: Kimlik?) {
        self.base = base
        self.kimlik = kimlik
        let k = URLSessionConfiguration.default
        k.timeoutIntervalForRequest = 20
        k.waitsForConnectivity = false
        oturum = URLSession(configuration: k)
        let u = URLSessionConfiguration.default
        u.timeoutIntervalForRequest = 45
        u.waitsForConnectivity = false
        uzunOturum = URLSession(configuration: u)
    }

    // MARK: Kimliksiz uçlar

    func ping() async throws -> PingYanit {
        try await istek("GET", "/v1/ping", imzali: false)
    }

    func eslesme(kod: String, ad: String) async throws -> EslesmeYanit {
        try await istek("POST", "/v1/eslesme", govde: ["kod": kod, "ad": ad], imzali: false)
    }

    // MARK: Kimlikli uçlar

    func nabiz() async throws -> Nabiz { try await istek("GET", "/v1/nabiz") }

    /// Ses ajanı konuşma jetonu (kısa ömürlü). Anahtar telefonda YOK; sunucu verir.
    func sesToken() async throws -> String {
        let y: SesTokenYanit = try await istek("GET", "/v1/ses/token")
        return y.token
    }

    func kararlar() async throws -> [Karar] {
        let z: KararlarZarf = try await istek("GET", "/v1/kararlar")
        return z.kararlar
    }

    func kararVer(id: String, onay: Bool) async throws {
        let _: OkYanit = try await istek("POST", "/v1/kararlar/\(id)",
                                         govde: ["karar": onay ? "onay" : "ret"])
    }

    /// sesli: mikrofonla söylendi → Ghost cevabı sesli de verir (Telegram sesli mesajıyla aynı).
    @discardableResult
    func mesajGonder(_ metin: String, sesli: Bool = false) async throws -> Int? {
        var g: [String: Any] = ["metin": metin]
        if sesli { g["sesli"] = true }
        let y: OkYanit = try await istek("POST", "/v1/mesaj", govde: g)
        return y.id
    }

    /// since = -1 → en son `limit` satır. durum: bilinen meşguliyet imzası; değişince erken döner.
    func outbox(since: Int, limit: Int = 50, bekle: Int = 25, durum: String? = nil) async throws -> OutboxZarf {
        var q = [URLQueryItem(name: "since", value: String(since)),
                 URLQueryItem(name: "limit", value: String(limit)),
                 URLQueryItem(name: "bekle", value: String(bekle))]
        if let d = durum { q.append(URLQueryItem(name: "durum", value: d)) }
        return try await istek("GET", "/v1/outbox", sorgu: q, uzun: true)
    }

    /// "Daha eski": id < once olan son `limit` satır (beklemez).
    func outboxEski(once: Int, limit: Int = 50) async throws -> OutboxZarf {
        try await istek("GET", "/v1/outbox",
                        sorgu: [URLQueryItem(name: "once", value: String(once)),
                                URLQueryItem(name: "limit", value: String(limit))])
    }

    /// Döküm medyası (görsel/video/ses/belge) — ham bayt.
    func medya(_ mesajID: Int) async throws -> Data { try await kare("/v1/medya/\(mesajID)") }

    // MARK: Faz 1 — akış, olaylar, sahne, eylem, paylaşım

    func akis(once: String? = nil, limit: Int = 80, kaynak: String? = nil) async throws -> AkisZarf {
        var q = [URLQueryItem(name: "limit", value: String(limit))]
        if let o = once { q.append(URLQueryItem(name: "once", value: o)) }
        if let k = kaynak, !k.isEmpty { q.append(URLQueryItem(name: "kaynak", value: k)) }
        return try await istek("GET", "/v1/akis", sorgu: q)
    }

    func olaylar(since: Int, limit: Int = 100, bekle: Int = 25) async throws -> OlaylarZarf {
        try await istek("GET", "/v1/olaylar",
                        sorgu: [URLQueryItem(name: "since", value: String(since)),
                                URLQueryItem(name: "limit", value: String(limit)),
                                URLQueryItem(name: "bekle", value: String(bekle))],
                        uzun: bekle > 0)
    }

    /// Tarihçedeki sahne (seq ile) — 404 → sunucu hatası olarak gelir.
    func sahneSeq(_ seq: Int) async throws -> SahneZarf {
        try await istek("GET", "/v1/sahne", sorgu: [URLQueryItem(name: "seq", value: String(seq))])
    }

    func sahneGecmis() async throws -> SahneGecmisZarf {
        try await istek("GET", "/v1/sahne/gecmis")
    }

    func sahne(since: Int = -1, bekle: Int = 0) async throws -> SahneZarf {
        try await istek("GET", "/v1/sahne",
                        sorgu: [URLQueryItem(name: "since", value: String(since)),
                                URLQueryItem(name: "bekle", value: String(bekle))],
                        uzun: bekle > 0)
    }

    func eylem(id: String, veri: String? = nil, alanlar: [String: String]? = nil) async throws {
        var g: [String: Any] = ["id": id]
        if let v = veri { g["veri"] = v }
        if let a = alanlar { g["alanlar"] = a }
        let _: OkYanit = try await istek("POST", "/v1/eylem", govde: g)
    }

    /// Uygulama dışı süreçler (widget, intent, paylaşım) için: Keychain kimliğiyle API.
    static func keychaindan() -> GhostAPI? {
        guard let id = Keychain.oku("device_id"), let key = Keychain.oku("device_key") else { return nil }
        let k = Kimlik(deviceID: id, key: key)
        guard k.gecerli else { return nil }
        var taban = "https://ghost-api.example.com"
        if let s = Bundle.main.object(forInfoDictionaryKey: "GhostAPIBase") as? String,
           !s.isEmpty, !s.hasPrefix("$(") { taban = s }
        guard let url = URL(string: taban) else { return nil }
        return GhostAPI(base: url, kimlik: k)
    }

    func etkinlikToken(tur: String, token: String, jid: Int?) async throws -> OkYanit {
        var g: [String: Any] = ["tur": tur, "token": token]
        if let j = jid { g["jid"] = String(j) }
        return try await istek("POST", "/v1/etkinlik", govde: g)
    }

    /// Faz 5 duyular: konum/takvim/cihaz anlık görüntüsü. Kısmi gönderi normaldir —
    /// sunucu yalnız gelen duyuyu günceller. Ghost tarafı kapalıysa 403 döner.
    func duyularGonder(_ govde: [String: Any]) async throws -> DuyuYanit {
        try await istek("POST", "/v1/duyular", govde: govde)
    }

    func duyuDurum() async throws -> DuyuDurum { try await istek("GET", "/v1/duyular") }

    /// Teşhis günlüğü (kaydırma/klavye olayları) → sunucuda ops/kokpit_gunluk.log.
    func gunluk(_ satirlar: [String]) async throws -> OkYanit {
        try await istek("POST", "/v1/gunluk", govde: ["satirlar": satirlar])
    }

    /// Faz 6: Ghost'un telefona yazdırmak istediği bekleyen işler.
    func gorevler() async throws -> GorevlerZarf { try await istek("GET", "/v1/gorevler") }

    /// Sonuç GERİ BİLDİRİLİR — "gönderdim" ile "telefona yazıldı" aynı şey değil.
    func gorevSonuc(id: Int, ok: Bool, mesaj: String, yerelID: String) async throws {
        let _: OkYanit = try await istek("POST", "/v1/gorevler/\(id)",
                                         govde: ["ok": ok, "mesaj": mesaj, "yerel_id": yerelID])
    }

    func bolgeler() async throws -> BolgelerZarf { try await istek("GET", "/v1/bolgeler") }

    func bolgeOlay(ad: String, olay: String) async throws {
        let _: OkYanit = try await istek("POST", "/v1/bolge", govde: ["ad": ad, "olay": olay])
    }

    func apnsKaydet(token: String, env: String) async throws -> OkYanit {
        try await istek("POST", "/v1/apns", govde: ["token": token, "env": env])
    }

    func paylas(tur: String, icerik: String, not: String? = nil) async throws {
        var g: [String: Any] = ["tur": tur, (tur == "url" ? "url" : "metin"): icerik]
        if let n = not, !n.isEmpty { g["not"] = n }
        let _: OkYanit = try await istek("POST", "/v1/paylas", govde: g)
    }

    func paylasGorsel(_ jpeg: Data, not: String? = nil) async throws {
        try await paylasDosya(jpeg, ad: "foto.jpg", mime: "image/jpeg", not: not)
    }

    /// Her tür belge (PDF, metin, görsel…) ham gövdeyle; sunucu 1.8 MB'a kadar alır.
    /// grup/sira/toplam: aynı gönderideki çoklu dosya — köprü grubu TEK sohbet turu yapar.
    func paylasDosya(_ veri: Data, ad: String, mime: String, not: String? = nil,
                     grup: String? = nil, sira: Int? = nil, toplam: Int? = nil) async throws {
        var q = [URLQueryItem(name: "ad", value: ad)]
        if let n = not, !n.isEmpty { q.append(URLQueryItem(name: "not", value: n)) }
        if let g = grup {
            q.append(URLQueryItem(name: "grup", value: g))
            q.append(URLQueryItem(name: "sira", value: String(sira ?? 1)))
            q.append(URLQueryItem(name: "toplam", value: String(toplam ?? 1)))
        }
        let _: OkYanit = try await istek("POST", "/v1/paylas", sorgu: q,
                                         hamGovde: veri, icerikTuru: mime, uzun: true)
    }

    /// İmzalı ham GET (jpg kare vb.) — gövde bayt olarak döner.
    func kare(_ yol: String, sorgu: [URLQueryItem] = []) async throws -> Data {
        let req = try imzaliIstek("GET", yol, sorgu: sorgu)
        let (veri, yanit) = try await oturum.data(for: req)
        let kod = (yanit as? HTTPURLResponse)?.statusCode ?? 0
        if kod == 401 { yetkisizOldu?(); throw APIHata.yetkisiz }
        guard (200..<300).contains(kod) else {
            throw APIHata.sunucu(kod, String(data: veri, encoding: .utf8) ?? "")
        }
        return veri
    }

    /// SSE: `/v1/sse` — sunucu ~75 sn sonra kapatır, çağıran son id'lerle yeniden bağlanır.
    func sse(olay: Int, outbox: Int, sahne: Int) -> AsyncThrowingStream<SseOlay, Error> {
        AsyncThrowingStream { devam in
            let gorev = Task {
                do {
                    let req = try imzaliIstek("GET", "/v1/sse", sorgu: [
                        URLQueryItem(name: "olay", value: String(olay)),
                        URLQueryItem(name: "outbox", value: String(outbox)),
                        URLQueryItem(name: "sahne", value: String(sahne))])
                    let (bytes, yanit) = try await uzunOturum.bytes(for: req)
                    let kod = (yanit as? HTTPURLResponse)?.statusCode ?? 0
                    if kod == 401 { yetkisizOldu?(); throw APIHata.yetkisiz }
                    guard kod == 200 else { throw APIHata.sunucu(kod, "") }
                    var ad = "", veri = ""
                    for try await satir in bytes.lines {
                        if satir.hasPrefix("event: ") { ad = String(satir.dropFirst(7)) }
                        else if satir.hasPrefix("data: ") { veri += String(satir.dropFirst(6)) }
                        else if satir.isEmpty {
                            if !ad.isEmpty { devam.yield(SseOlay(ad: ad, veri: Data(veri.utf8))) }
                            ad = ""; veri = ""
                        }
                    }
                    devam.finish()
                } catch {
                    devam.finish(throwing: error)
                }
            }
            devam.onTermination = { _ in gorev.cancel() }
        }
    }

    // MARK: Masa (2026-10-04) — ortak kalıcı içerik alanı

    /// Düz ağaç (içerik yerine önizleme). since ≥ 0 ve bekle > 0 → seq değişince döner (uzun yoklama).
    func masa(since: Int = -1, bekle: Int = 0, dolap: Bool = false) async throws -> MasaZarf {
        var q = [URLQueryItem(name: "since", value: String(since)),
                 URLQueryItem(name: "bekle", value: String(bekle))]
        if dolap { q.append(URLQueryItem(name: "dolap", value: "1")) }
        return try await istek("GET", "/v1/masa", sorgu: q, uzun: bekle > 0)
    }

    func masaKart(_ id: Int) async throws -> MasaKart { try await istek("GET", "/v1/masa/\(id)") }

    func masaDosya(_ id: Int) async throws -> Data { try await kare("/v1/masa/\(id)/dosya") }
    /// Faz 1.13b (2026-10-29): kartı PDF olarak indir (sunucu Chromium yoksa HTML döner; Paylaş sayfası yine açılır).
    func masaPdf(_ id: Int) async throws -> Data { try await kare("/v1/masa/\(id)/pdf") }

    /// Uygulamadan eklenen her şey yazar=sahip. tur: klasor | not | baglanti.
    func masaEkle(tur: String, ad: String, ebeveyn: Int?, icerik: String? = nil) async throws -> MasaYazYanit {
        var g: [String: Any] = ["tur": tur, "ad": ad]
        if let e = ebeveyn { g["ebeveyn"] = e }
        if let i = icerik { g["icerik"] = i }
        return try await istek("POST", "/v1/masa", govde: g)
    }

    /// Görsel/belge ham gövdeyle (sunucu 1.8 MB'a kadar alır).
    func masaDosyaYukle(_ veri: Data, ad: String, mime: String, ebeveyn: Int?) async throws -> MasaYazYanit {
        var q = [URLQueryItem(name: "ad", value: ad)]
        if let e = ebeveyn { q.append(URLQueryItem(name: "ebeveyn", value: String(e))) }
        return try await istek("POST", "/v1/masa/dosya", sorgu: q, hamGovde: veri, icerikTuru: mime, uzun: true)
    }

    /// eylem: duzenle (icerik/ad) · adlandir (ad) · tasi (ebeveyn) · dolap · geri · sil · ilet.
    /// İzin hatası 403 olarak gelir (ghost/masa.py kuralları).
    func masaEylem(_ id: Int, _ eylem: String, _ ek: [String: Any] = [:]) async throws -> MasaYazYanit {
        var g = ek
        g["eylem"] = eylem
        return try await istek("POST", "/v1/masa/\(id)", govde: g)
    }

    func komutlar() async throws -> [Komut] {
        let z: KomutlarZarf = try await istek("GET", "/v1/komutlar")
        return z.komutlar
    }

    func siteler() async throws -> [Site] {
        let z: SitelerZarf = try await istek("GET", "/v1/siteler")
        return z.siteler
    }

    // MARK: Çekirdek istek

    /// İmzalı istek kur (gövdesiz GET): SSE, MJPEG, jpg kare gibi ham akışlar için.
    func imzaliIstek(_ method: String, _ yol: String, sorgu: [URLQueryItem] = []) throws -> URLRequest {
        try kur(method, yol, sorgu: sorgu, govde: nil, hamGovde: nil, icerikTuru: nil, imzali: true)
    }

    private func kur(_ method: String, _ yol: String, sorgu: [URLQueryItem],
                     govde: [String: Any]?, hamGovde: Data?, icerikTuru: String?,
                     imzali: Bool) throws -> URLRequest {
        guard var parcalar = URLComponents(url: base, resolvingAgainstBaseURL: false) else {
            throw APIHata.cozumleme("taban URL bozuk")
        }
        var kok = parcalar.percentEncodedPath
        if kok.hasSuffix("/") { kok.removeLast() }
        parcalar.percentEncodedPath = kok + yol
        if !sorgu.isEmpty { parcalar.queryItems = sorgu }
        guard let url = parcalar.url else { throw APIHata.cozumleme("URL kurulamadı") }

        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        // Cloudflare Browser Integrity Check bazı varsayılan UA'ları 1010 ile kesiyor;
        // kendimizi açıkça tanıt, URLSession varsayılanına güvenme.
        req.setValue(GhostAPI.kullaniciAjani, forHTTPHeaderField: "User-Agent")
        var govdeVeri = Data()
        if let h = hamGovde {
            govdeVeri = h
            req.httpBody = h
            req.setValue(icerikTuru ?? "application/octet-stream", forHTTPHeaderField: "Content-Type")
        } else if let g = govde {
            govdeVeri = try JSONSerialization.data(withJSONObject: g, options: [.sortedKeys])
            req.httpBody = govdeVeri
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }

        if imzali {
            guard let k = kimlik, k.gecerli, let anahtar = k.keyBytes else { throw APIHata.kimlikYok }
            // İmzalanan yol = path + "?" + query (sunucu ham istek hedefini imzalar).
            var yolSorgu = parcalar.percentEncodedPath
            if let q = parcalar.percentEncodedQuery, !q.isEmpty { yolSorgu += "?" + q }
            let ts = String(Int(Date().timeIntervalSince1970))
            let nonce = Data((0..<16).map { _ in UInt8.random(in: 0...255) }).hex
            let govdeOzeti = Data(SHA256.hash(data: govdeVeri)).hex
            let mesaj = [ts, nonce, method, yolSorgu, govdeOzeti].joined(separator: "\n")
            let imza = HMAC<SHA256>.authenticationCode(for: Data(mesaj.utf8),
                                                       using: SymmetricKey(data: anahtar))
            req.setValue(k.deviceID, forHTTPHeaderField: "X-Ghost-Device")
            req.setValue(ts, forHTTPHeaderField: "X-Ghost-Ts")
            req.setValue(nonce, forHTTPHeaderField: "X-Ghost-Nonce")
            req.setValue(Data(imza).hex, forHTTPHeaderField: "X-Ghost-Sig")
        }
        return req
    }

    private func istek<T: Decodable>(_ method: String, _ yol: String,
                                     sorgu: [URLQueryItem] = [],
                                     govde: [String: Any]? = nil,
                                     hamGovde: Data? = nil,
                                     icerikTuru: String? = nil,
                                     imzali: Bool = true,
                                     uzun: Bool = false) async throws -> T {
        let req = try kur(method, yol, sorgu: sorgu, govde: govde, hamGovde: hamGovde,
                          icerikTuru: icerikTuru, imzali: imzali)
        let (veri, yanit) = try await (uzun ? uzunOturum : oturum).data(for: req)
        let kod = (yanit as? HTTPURLResponse)?.statusCode ?? 0
        if kod == 401 {
            yetkisizOldu?()
            throw APIHata.yetkisiz
        }
        guard (200..<300).contains(kod) else {
            throw APIHata.sunucu(kod, String(data: veri, encoding: .utf8) ?? "")
        }
        do {
            return try JSONDecoder().decode(T.self, from: veri)
        } catch {
            throw APIHata.cozumleme(String(describing: error).prefix(200).description)
        }
    }
}

// MARK: Hex yardımcıları

extension Data {
    /// "a1b2..." → bayt dizisi; tek sayıda ya da geçersiz karakterde nil.
    init?(hex: String) {
        let s = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        guard s.count % 2 == 0 else { return nil }
        var d = Data(capacity: s.count / 2)
        var i = s.startIndex
        while i < s.endIndex {
            let j = s.index(i, offsetBy: 2)
            guard let b = UInt8(s[i..<j], radix: 16) else { return nil }
            d.append(b)
            i = j
        }
        self = d
    }
    var hex: String { map { String(format: "%02x", $0) }.joined() }
}
