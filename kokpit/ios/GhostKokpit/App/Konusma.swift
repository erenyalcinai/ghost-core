import Foundation
import Speech
import AVFoundation

/// Mikrofon → metin (Türkçe, cihaz üstü tercih). Sohbet çekmecesindeki mikrofon düğmesi.
@MainActor
final class Konusma: ObservableObject {
    @Published var dinliyor = false
    @Published var metin = ""
    @Published var hata: String?

    private let taniyici = SFSpeechRecognizer(locale: Locale(identifier: "tr-TR"))
    private let motor = AVAudioEngine()
    private var istek: SFSpeechAudioBufferRecognitionRequest?
    private var gorev: SFSpeechRecognitionTask?

    func degistir() {
        if dinliyor { durdur() } else { Task { await baslat() } }
    }

    func baslat() async {
        hata = nil
        let izin = await withCheckedContinuation { c in
            SFSpeechRecognizer.requestAuthorization { c.resume(returning: $0) }
        }
        guard izin == .authorized else { hata = "Konuşma tanıma izni yok."; return }
        let mik = await AVAudioApplication.requestRecordPermission()
        guard mik else { hata = "Mikrofon izni yok."; return }
        guard let t = taniyici, t.isAvailable else { hata = "Türkçe tanıma kullanılamıyor."; return }
        do {
            let oturum = AVAudioSession.sharedInstance()
            try oturum.setCategory(.record, mode: .measurement, options: .duckOthers)
            try oturum.setActive(true, options: .notifyOthersOnDeactivation)
            let r = SFSpeechAudioBufferRecognitionRequest()
            r.shouldReportPartialResults = true
            if t.supportsOnDeviceRecognition { r.requiresOnDeviceRecognition = false }
            istek = r
            let giris = motor.inputNode
            let bicim = giris.outputFormat(forBus: 0)
            giris.removeTap(onBus: 0)
            giris.installTap(onBus: 0, bufferSize: 1024, format: bicim) { buf, _ in r.append(buf) }
            motor.prepare()
            try motor.start()
            dinliyor = true
            gorev = t.recognitionTask(with: r) { [weak self] sonuc, hata in
                Task { @MainActor in
                    guard let self else { return }
                    if let s = sonuc { self.metin = s.bestTranscription.formattedString }
                    if hata != nil || (sonuc?.isFinal ?? false) { self.durdur() }
                }
            }
        } catch {
            hata = "Mikrofon başlatılamadı: " + error.localizedDescription
            durdur()
        }
    }

    func durdur() {
        if motor.isRunning {
            motor.stop()
            motor.inputNode.removeTap(onBus: 0)
        }
        istek?.endAudio()
        gorev?.cancel()
        istek = nil
        gorev = nil
        dinliyor = false
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}
