import Foundation

/// Mac'te mikrofon→metin YOK (AVAudioSession iOS'a özgü; klavye zaten var). SohbetView
/// aynı kaynaktan derlensin diye sessiz eş: hiç dinlemez, metin boş kalır.
@MainActor
final class Konusma: ObservableObject {
    @Published var dinliyor = false
    @Published var metin = ""
    @Published var hata: String?
    func degistir() {}
}
