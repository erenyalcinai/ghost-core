// Mac'te koşar (bu makinede Swift derleyicisi yok). Ağa ÇIKMAZ, kimlik istemez.
// Swift imza hesabının (GhostImza: kanonik dize, X-Ghost-Sig, X-Ghost-Sig0) sunucunun Python hesabıyla bire bir
// aynı olduğunu sınar: vektörler `tools/sig0_vektor.py --yaz` ile üretildi ve o betik aynı hesabı gerçek onay servisi
// koduna doğrulattı. Ayrıca: GhostAPI'nin kurduğu isteğin başlıkları tutarlı mı, telefon/Mac kimliğinde Sig0 YOK mu,
// saatin Markdown düzleştiricisi beklenen metni veriyor mu.
//
//   cd kokpit/ios && swiftc -parse-as-library -O \
//     GhostKokpit/Net/GhostAPI.swift GhostKokpit/Net/Keychain.swift GhostKokpit/Model/Modeller.swift \
//     GhostKokpitWatch/DuzMetin.swift tools/sig0_test.swift -o /tmp/sig0_test && /tmp/sig0_test
//
// Önkoşul: `ghostapi-sig0.patch` uygulanmış olmalı (GhostImza ve Kimlik.key0 o yamayla gelir).
// Beklenen son satır: "SONUÇ: hepsi geçti (N denetim)". Çıkış kodu 0 = geçti, 1 = en az bir fark.
import Foundation

struct Vektor: Decodable {
    var ad: String
    var key: String
    var key0: String
    var ts: String
    var nonce: String
    var method: String
    var yol: String
    var govde_hex: String
    var kanonik_hex: String
    var sig: String
    var sig0: String
}

struct VektorDosyasi: Decodable { var vektorler: [Vektor] }

@main
struct Sig0Test {
    static var gecen = 0
    static var kalan = 0

    static func bekle(_ ad: String, _ kosul: Bool, _ ayrinti: @autoclosure () -> String = "") {
        if kosul {
            gecen += 1
        } else {
            kalan += 1
            print("FARK  \(ad)  \(ayrinti())")
        }
    }

    static func main() {
        let yol = FileManager.default.currentDirectoryPath + "/tools/sig0_vektorler.json"
        guard let veri = FileManager.default.contents(atPath: yol),
              let dosya = try? JSONDecoder().decode(VektorDosyasi.self, from: veri) else {
            print("vektör dosyası okunamadı: \(yol) (kokpit/ios dizininden koş)")
            exit(2)
        }

        // 1. Sabit vektörler: kanonik dize, sig, sig0.
        for v in dosya.vektorler {
            guard let govde = Data(hex: v.govde_hex), let key = Data(hex: v.key), let key0 = Data(hex: v.key0) else {
                bekle(v.ad + " hex", false, "vektör hex'i çözülemedi")
                continue
            }
            let kan = GhostImza.kanonik(ts: v.ts, nonce: v.nonce, method: v.method, yolSorgu: v.yol, govde: govde)
            bekle(v.ad + " kanonik", Data(kan.utf8).hex == v.kanonik_hex, kan)
            let sig = GhostImza.sig(anahtar: key, kanonik: kan)
            bekle(v.ad + " sig", sig == v.sig, "\(sig) ≠ \(v.sig)")
            let sig0 = GhostImza.sig0(anahtar0: key0, kanonik: kan, sig: sig)
            bekle(v.ad + " sig0", sig0 == v.sig0, "\(sig0) ≠ \(v.sig0)")
        }

        // 2. GhostAPI'nin kurduğu istek: cep kimliğinde Sig0 var ve başlıklardan yeniden hesaplananla aynı.
        let key = String(repeating: "ab", count: 32), key0 = String(repeating: "cd", count: 32)
        let cep = Kimlik(deviceID: "saat-test", key: key, key0: key0)
        bekle("cep kimliği geçerli", cep.gecerli && cep.key0Bytes != nil)
        let api = GhostAPI(base: URL(string: "https://ghost-api.example.com")!, kimlik: cep)
        let sorgu = [URLQueryItem(name: "since", value: "-1"), URLQueryItem(name: "limit", value: "10"),
                     URLQueryItem(name: "bekle", value: "0"), URLQueryItem(name: "durum", value: "1|çalışıyor")]
        if let req = try? api.imzaliIstek("GET", "/v1/outbox", sorgu: sorgu),
           let url = req.url, let p = URLComponents(url: url, resolvingAgainstBaseURL: false) {
            let ts = req.value(forHTTPHeaderField: "X-Ghost-Ts") ?? ""
            let nonce = req.value(forHTTPHeaderField: "X-Ghost-Nonce") ?? ""
            let sig = req.value(forHTTPHeaderField: "X-Ghost-Sig") ?? ""
            let sig0 = req.value(forHTTPHeaderField: "X-Ghost-Sig0") ?? ""
            var hedef = p.percentEncodedPath
            if let q = p.percentEncodedQuery, !q.isEmpty { hedef += "?" + q }
            bekle("istek hedefi yüzde kodlu", hedef.contains("durum=1%7C%C3%A7al%C4%B1%C5%9F%C4%B1yor"), hedef)
            let kan = GhostImza.kanonik(ts: ts, nonce: nonce, method: "GET", yolSorgu: hedef, govde: Data())
            bekle("istek sig", sig == GhostImza.sig(anahtar: Data(hex: key)!, kanonik: kan))
            bekle("istek sig0", !sig0.isEmpty && sig0 == GhostImza.sig0(anahtar0: Data(hex: key0)!, kanonik: kan, sig: sig))
            bekle("istek sig0 küçük harf 64 hex", sig0.count == 64 && sig0 == sig0.lowercased())
            bekle("nonce 8..64", (8...64).contains(nonce.count))
            bekle("cihaz başlığı", req.value(forHTTPHeaderField: "X-Ghost-Device") == "saat-test")
        } else {
            bekle("cep isteği kurulamadı", false)
        }

        // 3. Telefon/Mac kimliği (key0 yok): davranış DEĞİŞMEDİ — Sig0 başlığı yok, kimlik geçerli.
        let tam = Kimlik(deviceID: "telefon-test", key: key)
        bekle("tam kimlik geçerli", tam.gecerli && tam.key0 == nil)
        let apiTam = GhostAPI(base: URL(string: "https://ghost-api.example.com")!, kimlik: tam)
        if let req = try? apiTam.imzaliIstek("GET", "/v1/nabiz") {
            bekle("tam kimlikte Sig0 yok", req.value(forHTTPHeaderField: "X-Ghost-Sig0") == nil)
            bekle("tam kimlikte Sig var", (req.value(forHTTPHeaderField: "X-Ghost-Sig") ?? "").count == 64)
        } else {
            bekle("tam isteği kurulamadı", false)
        }
        // Bozuk key0 (64 hex değil): kimlik geçersiz sayılır, istek HİÇ kurulmaz (Sig0'sız cep isteği gitmez).
        let bozuk = Kimlik(deviceID: "saat-test", key: key, key0: "kisa")
        bekle("bozuk key0 kimliği geçersiz", !bozuk.gecerli)
        let apiBozuk = GhostAPI(base: URL(string: "https://ghost-api.example.com")!, kimlik: bozuk)
        bekle("bozuk key0 ile istek kurulmaz", (try? apiBozuk.imzaliIstek("GET", "/v1/nabiz")) == nil)

        // 4. Eşleşme cevabı: tür ve key0 çözülür; telefon cevabında ikisi de nil.
        let cepYanit = try? JSONDecoder().decode(EslesmeYanit.self, from: Data(
            "{\"device_id\":\"abc\",\"key\":\"\(key)\",\"tur\":\"cep\",\"key0\":\"\(key0)\"}".utf8))
        bekle("eşleşme cevabı cep", cepYanit?.tur == "cep" && cepYanit?.key0 == key0)
        let tamYanit = try? JSONDecoder().decode(EslesmeYanit.self, from: Data(
            "{\"device_id\":\"abc\",\"key\":\"\(key)\"}".utf8))
        bekle("eşleşme cevabı tam", tamYanit != nil && tamYanit?.tur == nil && tamYanit?.key0 == nil)

        // 5. Saatin Markdown düzleştiricisi (sunucudaki bildirim gövdesiyle aynı kurallar).
        let c1 = DuzMetin.ilkParagraf("**Sonuç:** `main` dalına [commit](https://x.y/z) atıldı, *iki* test ~~kırık~~ geçti.")
        bekle("düz: işaretler", c1.metin == "Sonuç: main dalına commit atıldı, iki test kırık geçti." && !c1.devamVar, c1.metin)
        let c2 = DuzMetin.ilkParagraf("Deploy bitti, site açık.\n\nAyrıntı: üç dosya değişti.")
        bekle("düz: ilk paragraf + devam", c2.metin == "Deploy bitti, site açık." && c2.devamVar, c2.metin)
        let c3 = DuzMetin.parcala("Özet aşağıda.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```diff\n- eski\n+ yeni\n```\n\nSon.")
        bekle("düz: tablo ve diff telefonda",
              c3.map(\.metin) == ["Özet aşağıda.", DuzMetin.tabloIsareti, DuzMetin.diffIsareti, "Son."]
              && c3.map(\.telefonda) == [false, true, true, false], "\(c3.map(\.metin))")
        let c4 = DuzMetin.ilkParagraf("## Durum\n\nÜç organ sağlıklı.\n\nGerisi.")
        bekle("düz: başlık alttaki paragrafla", c4.metin == "Durum\nÜç organ sağlıklı." && c4.devamVar, c4.metin)
        let c5 = DuzMetin.ilkParagraf("- bir\n* iki\n+ üç")
        bekle("düz: maddeler", c5.metin == "• bir\n• iki\n• üç", c5.metin)
        bekle("düz: çarpım bozulmaz", DuzMetin.satirIci("Tutar 2 * 3 * 4 eder.") == "Tutar 2 * 3 * 4 eder.")
        bekle("düz: boş", DuzMetin.parcala("").isEmpty && DuzMetin.ilkParagraf("  \n ").metin.isEmpty)

        if kalan == 0 {
            print("SONUÇ: hepsi geçti (\(gecen) denetim)")
            exit(0)
        }
        print("SONUÇ: \(kalan) FARK, \(gecen) geçti")
        exit(1)
    }
}
