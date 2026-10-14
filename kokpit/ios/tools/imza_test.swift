// Mac'te GhostAPI.swift'in İMZALAMA kodunu gerçek sunucuya karşı dener (UIKit yok).
//   cd kokpit/ios && swiftc -parse-as-library -O \
//     GhostKokpit/Net/GhostAPI.swift GhostKokpit/Model/Modeller.swift tools/imza_test.swift \
//     -o /tmp/imza_test && /tmp/imza_test
// Kimliği Secrets.xcconfig'ten okur; ping (kimliksiz) + nabiz (imzalı) çağırır.
import Foundation

@main
struct ImzaTest {
    static func main() async {
        let yol = FileManager.default.currentDirectoryPath + "/Secrets.xcconfig"
        var ayar: [String: String] = [:]
        if let icerik = try? String(contentsOfFile: yol, encoding: .utf8) {
            for satir in icerik.split(separator: "\n") {
                let p = satir.split(separator: "=", maxSplits: 1).map { $0.trimmingCharacters(in: .whitespaces) }
                if p.count == 2, !p[0].hasPrefix("//") { ayar[p[0]] = p[1] }
            }
        }
        let taban = (ayar["GHOST_API_BASE"] ?? "https://ghost-api.example.com").replacingOccurrences(of: "$()", with: "")
        let kimlik = Kimlik(deviceID: ayar["GHOST_DEVICE_ID"] ?? "", key: ayar["GHOST_DEVICE_KEY"] ?? "")
        print("taban:", taban, "| kimlik geçerli:", kimlik.gecerli, "| device:", kimlik.deviceID.prefix(8) + "…")
        let api = GhostAPI(base: URL(string: taban)!, kimlik: kimlik.gecerli ? kimlik : nil)
        do {
            let p = try await api.ping()
            print("ping:", p.ok, p.at ?? "")
        } catch { print("ping HATA:", error.localizedDescription) }
        do {
            let n = try await api.nabiz()
            print("nabiz: ozet=\(n.ozet ?? "-") model=\(n.model ?? "-") makineler=\(n.makineler?.count ?? 0) nobetciler=\(n.nobetciler?.count ?? 0) baglam=\(n.baglam?.yuzde ?? -1)%")
        } catch { print("nabiz HATA:", error.localizedDescription) }
        do {
            let k = try await api.kararlar()
            print("kararlar:", k.count)
            let s = try await api.siteler()
            print("siteler:", s.map { "\($0.ad)=\($0.ok == true ? "ok" : "x")" }.joined(separator: " "))
            let km = try await api.komutlar()
            print("komutlar:", km.count)
            let o = try await api.outbox(since: 0, limit: 5, bekle: 1)
            print("outbox: \(o.mesajlar.count) mesaj, son=\(o.son ?? -1)")
        } catch { print("diğer uçlar HATA:", error.localizedDescription) }
    }
}
