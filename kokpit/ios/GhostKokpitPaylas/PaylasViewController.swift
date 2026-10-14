import UIKit
import UniformTypeIdentifiers

/// "Ghost'a gönder" — Share Extension. Metin/URL JSON ile, görsel küçültülmüş JPEG olarak
/// /v1/paylas'a gider; köprü "[Paylaşım: …]" mesajı olarak işler. Kimlik: uygulamayla
/// paylaşılan Keychain grubu (Keychain.grup). Uygulama hiç eşlenmediyse uyarır.
final class PaylasViewController: UIViewController {
    private let kart = UIView()
    private let etiket = UILabel()
    private let donen = UIActivityIndicatorView(style: .medium)
    private let kapat = UIButton(type: .system)

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = UIColor.black.withAlphaComponent(0.45)
        kart.backgroundColor = UIColor(red: 0.082, green: 0.090, blue: 0.102, alpha: 1)
        kart.layer.cornerRadius = 16
        kart.translatesAutoresizingMaskIntoConstraints = false
        etiket.text = "Ghost'a gönderiliyor…"
        etiket.textColor = UIColor(red: 0.93, green: 0.918, blue: 0.894, alpha: 1)
        etiket.font = .preferredFont(forTextStyle: .body)
        etiket.numberOfLines = 0
        etiket.textAlignment = .center
        etiket.translatesAutoresizingMaskIntoConstraints = false
        donen.color = .lightGray
        donen.translatesAutoresizingMaskIntoConstraints = false
        donen.startAnimating()
        kapat.setTitle("Kapat", for: .normal)
        kapat.tintColor = .lightGray
        kapat.isHidden = true
        kapat.translatesAutoresizingMaskIntoConstraints = false
        kapat.addAction(UIAction { [weak self] _ in self?.bitir() }, for: .touchUpInside)
        view.addSubview(kart)
        kart.addSubview(donen); kart.addSubview(etiket); kart.addSubview(kapat)
        NSLayoutConstraint.activate([
            kart.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            kart.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            kart.widthAnchor.constraint(equalTo: view.widthAnchor, multiplier: 0.8),
            donen.topAnchor.constraint(equalTo: kart.topAnchor, constant: 22),
            donen.centerXAnchor.constraint(equalTo: kart.centerXAnchor),
            etiket.topAnchor.constraint(equalTo: donen.bottomAnchor, constant: 12),
            etiket.leadingAnchor.constraint(equalTo: kart.leadingAnchor, constant: 18),
            etiket.trailingAnchor.constraint(equalTo: kart.trailingAnchor, constant: -18),
            kapat.topAnchor.constraint(equalTo: etiket.bottomAnchor, constant: 10),
            kapat.centerXAnchor.constraint(equalTo: kart.centerXAnchor),
            kapat.bottomAnchor.constraint(equalTo: kart.bottomAnchor, constant: -14),
        ])
        Task { await gonder() }
    }

    private func bitir() {
        extensionContext?.completeRequest(returningItems: nil)
    }

    private func hata(_ m: String) {
        donen.stopAnimating()
        etiket.text = m
        kapat.isHidden = false
    }

    private func kimlik() -> GhostAPI? {
        guard let id = Keychain.oku("device_id"), let key = Keychain.oku("device_key") else { return nil }
        let k = Kimlik(deviceID: id, key: key)
        guard k.gecerli else { return nil }
        var taban = "https://ghost-api.example.com"
        if let s = Bundle.main.object(forInfoDictionaryKey: "GhostAPIBase") as? String,
           !s.isEmpty, !s.hasPrefix("$(") { taban = s }
        guard let url = URL(string: taban) else { return nil }
        return GhostAPI(base: url, kimlik: k)
    }

    @MainActor
    private func gonder() async {
        guard let api = kimlik() else {
            hata("Cihaz eşlenmemiş. Önce Ghost uygulamasını açıp eşle."); return
        }
        let items = (extensionContext?.inputItems as? [NSExtensionItem]) ?? []
        var not = ""
        var gonderilen = 0
        for item in items {
            if not.isEmpty, let t = item.attributedContentText?.string.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty {
                not = t
            }
            for p in item.attachments ?? [] {
                do {
                    if p.hasItemConformingToTypeIdentifier(UTType.url.identifier) {
                        if let u = await p.yukle(UTType.url.identifier) as? URL {
                            if u.isFileURL, let img = UIImage(contentsOfFile: u.path) {
                                try await api.paylasGorsel(PaylasViewController.jpeg(img), not: not)
                            } else {
                                try await api.paylas(tur: "url", icerik: u.absoluteString, not: not)
                            }
                            gonderilen += 1
                        }
                    } else if p.hasItemConformingToTypeIdentifier(UTType.image.identifier) {
                        let v = await p.yukle(UTType.image.identifier)
                        var img: UIImage?
                        if let i = v as? UIImage { img = i }
                        else if let d = v as? Data { img = UIImage(data: d) }
                        else if let u = v as? URL { img = UIImage(contentsOfFile: u.path) }
                        if let i = img {
                            try await api.paylasGorsel(PaylasViewController.jpeg(i), not: not)
                            gonderilen += 1
                        }
                    } else if p.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) || p.hasItemConformingToTypeIdentifier(UTType.data.identifier),
                              !p.hasItemConformingToTypeIdentifier(UTType.plainText.identifier) {
                        let v = await p.yukle(p.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) ? UTType.fileURL.identifier : UTType.data.identifier)
                        var veri: Data?; var ad = "belge"
                        if let u = v as? URL { veri = try? Data(contentsOf: u); ad = u.lastPathComponent }
                        else if let d = v as? Data { veri = d; ad = p.suggestedName ?? "belge" }
                        if let d = veri {
                            guard d.count <= 1_800_000 else { hata("Belge çok büyük (sınır 1800 KB)."); return }
                            let mime = UTType(filenameExtension: (ad as NSString).pathExtension)?.preferredMIMEType ?? "application/octet-stream"
                            try await api.paylasDosya(d, ad: ad, mime: mime, not: not)
                            gonderilen += 1
                        }
                    } else if p.hasItemConformingToTypeIdentifier(UTType.plainText.identifier) {
                        let v = await p.yukle(UTType.plainText.identifier)
                        let t = (v as? String) ?? (v as? NSAttributedString)?.string ?? ""
                        if !t.isEmpty {
                            try await api.paylas(tur: "metin", icerik: t, not: "")
                            gonderilen += 1
                        }
                    }
                } catch {
                    hata("Gönderilemedi: " + error.localizedDescription); return
                }
            }
        }
        if gonderilen == 0, !not.isEmpty {
            do { try await api.paylas(tur: "metin", icerik: not, not: ""); gonderilen = 1 }
            catch { hata("Gönderilemedi: " + error.localizedDescription); return }
        }
        if gonderilen == 0 { hata("Paylaşılacak içerik bulunamadı."); return }
        donen.stopAnimating()
        etiket.text = "Ghost'a gitti ✓"
        try? await Task.sleep(for: .seconds(0.7))
        bitir()
    }

    /// En uzun kenar ≤ 1280 px, JPEG 0.6 — nginx 2 MB sınırının rahat altında.
    static func jpeg(_ img: UIImage) -> Data {
        let enBuyuk: CGFloat = 1280
        let oran = min(1, enBuyuk / max(img.size.width, img.size.height))
        let boy = CGSize(width: img.size.width * oran, height: img.size.height * oran)
        let f = UIGraphicsImageRendererFormat.default(); f.scale = 1
        let k = UIGraphicsImageRenderer(size: boy, format: f).image { _ in img.draw(in: CGRect(origin: .zero, size: boy)) }
        return k.jpegData(compressionQuality: 0.6) ?? Data()
    }
}

extension NSItemProvider {
    func yukle(_ tip: String) async -> NSSecureCoding? {
        await withCheckedContinuation { c in
            loadItem(forTypeIdentifier: tip, options: nil) { veri, _ in c.resume(returning: veri) }
        }
    }
}
