// İş kartı toparlayıcısının karar mantığı sınaması (2026-11-26). Mac'te:
//   cd ~/ghostbuild/ios && swiftc -parse-as-library -o /tmp/etkinlik_test GhostKokpit/Model/Modeller.swift \
//     GhostKokpit/Model/IsKartiKarar.swift tools/etkinlik_test.swift && /tmp/etkinlik_test
import Foundation

@main
struct EtkinlikTest {
    static func isler(_ json: String) -> Isler {
        try! JSONDecoder().decode(Isler.self, from: Data(json.utf8))
    }

    static func main() {
        var hata = 0
        func bak(_ ad: String, _ a: String?, _ b: String?) {
            if a == b { print("geçti  \(ad)") } else { hata += 1; print("KALDI  \(ad): \(a ?? "nil") != \(b ?? "nil")") }
        }
        // yeni sunucu: aktif_idler var
        let yeni = isler(#"{"aktif":1,"bekleyen_onay":2,"aktif_idler":[61],"ozet":[{"id":62,"durum":"hata"},{"id":61,"durum":"çalışıyor"}]}"#)
        bak("süren iş kalır", IsKartiKarar.bitisDurumu(jid: 61, isler: yeni), nil)
        bak("biten iş son durumuyla kapanır", IsKartiKarar.bitisDurumu(jid: 62, isler: yeni), "hata")
        bak("listede olmayan eski iş kapanır", IsKartiKarar.bitisDurumu(jid: 12, isler: yeni), "bitti")
        let bos = isler(#"{"aktif":0,"aktif_idler":[],"ozet":[]}"#)
        bak("hiç iş yoksa kapanır", IsKartiKarar.bitisDurumu(jid: 5, isler: bos), "bitti")
        // eski sunucu: aktif_idler yok → yalnız kesin bilgi
        let eski = isler(#"{"aktif":1,"ozet":[{"id":"70","durum":"bitti"},{"id":71,"durum":"denetimde"}]}"#)
        bak("eski: listede biten kapanır", IsKartiKarar.bitisDurumu(jid: 70, isler: eski), "bitti")
        bak("eski: denetimde kalır", IsKartiKarar.bitisDurumu(jid: 71, isler: eski), nil)
        bak("eski: bilinmeyen + aktif>0 kalır", IsKartiKarar.bitisDurumu(jid: 9, isler: eski), nil)
        let eskiBos = isler(#"{"aktif":0,"ozet":[]}"#)
        bak("eski: aktif=0 kapanır", IsKartiKarar.bitisDurumu(jid: 9, isler: eskiBos), "bitti")
        print(hata == 0 ? "HEPSİ GEÇTİ" : "\(hata) KALDI")
        exit(hata == 0 ? 0 : 1)
    }
}
