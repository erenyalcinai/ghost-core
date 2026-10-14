import SwiftUI

/// Ghost Kokpit — macOS (2026-09-26, Sahip kararı: "sen Mac'i kullanabiliyorsun, ben neden
/// seni Mac'te kullanamayayım"). iOS uygulamasıyla AYNI Model/Net/Views; yalnız giriş
/// noktası, pencere düzeni ve bildirim delegesi Mac'e özel. Telefonun duyuları (konum,
/// takvim, hatırlatıcı yazma, kamera, mikrofon, Live Activity) burada YOK — onlar cebin işi.
///
/// Mac'in telefonda olmayan kazancı: geniş Sahne (diff/tablo okunur), klavye + sürükle-bırak,
/// menü çubuğunda Nabız ve bekleyen onay, ⌘1–5 ile bölüm geçişi.
@main
struct GhostMacApp: App {
    @NSApplicationDelegateAdaptor(BildirimDelegesi.self) private var bildirim
    @StateObject private var oturum = Oturum()

    var body: some Scene {
        Window("Ghost", id: "ana") {
            MacRootView()
                .environmentObject(oturum)
                .frame(minWidth: 900, minHeight: 600)
                .onAppear {
                    bildirim.api = oturum.api
                    if oturum.esli { bildirim.kaydol() }
                }
                .onChange(of: oturum.esli) { _, esli in
                    bildirim.api = oturum.api
                    if esli { bildirim.kaydol() }
                }
        }
        .defaultSize(width: 1120, height: 780)
        .commands {
            CommandMenu("Bölüm") {
                Button("Nabız") { oturum.sayfa = nil; oturum.sekme = .nabiz }.keyboardShortcut("1")
                Button("Komutlar") { oturum.sayfa = nil; oturum.sekme = .komutlar }.keyboardShortcut("2")
                Button("Sohbet") { oturum.sayfa = nil; oturum.sekme = .sohbet }.keyboardShortcut("3")
                Button("Masa") { oturum.sayfa = nil; oturum.sekme = .sahne }.keyboardShortcut("4")
                Button("Ayarlar") { oturum.sayfa = nil; oturum.sekme = .ayarlar }.keyboardShortcut("5")
                Divider()
                Button("Kararlar") { oturum.sayfa = .kararlar }.keyboardShortcut("6")
                Button("Akış") { oturum.sayfa = .akis }.keyboardShortcut("7")
                Button("Siteler") { oturum.sayfa = .siteler }.keyboardShortcut("8")
            }
        }

        MenuBarExtra {
            MenuCubuguIcerik().environmentObject(oturum)
        } label: {
            // Bekleyen onay varsa simge dolu; nabız kritikse ünlem.
            Image(systemName: oturum.bekleyenOnay > 0 ? "waveform.path.ecg.rectangle.fill"
                  : (oturum.nabiz?.ozet == "kritik" ? "exclamationmark.triangle" : "waveform.path.ecg"))
        }
    }
}

/// Menü çubuğu: bir bakışta durum, tek tıkla pencereye ya da Kararlar'a.
struct MenuCubuguIcerik: View {
    @EnvironmentObject var oturum: Oturum
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        if !oturum.esli {
            Text("Ghost — eşlenmemiş")
        } else {
            Text(ozetMetni)
            if oturum.bekleyenOnay > 0 {
                Button(oturum.bekleyenOnay == 1 ? "1 karar bekliyor…" : "\(oturum.bekleyenOnay) karar bekliyor…") {
                    oturum.sayfa = .kararlar; pencere()
                }
            }
            if let a = oturum.nabiz?.isler?.aktif, a > 0 { Text("\(a) iş çalışıyor") }
            if oturum.nabiz?.freeze == true { Text("FREEZE aktif") }
        }
        Divider()
        Button("Kokpit'i aç") { pencere() }.keyboardShortcut("g", modifiers: [.command, .shift])
        Button("Sohbet") { oturum.sayfa = nil; oturum.sekme = .sohbet; pencere() }
        Divider()
        Button("Çık") { NSApplication.shared.terminate(nil) }.keyboardShortcut("q")
    }

    private var ozetMetni: String {
        switch (oturum.nabiz?.ozet ?? "").lowercased() {
        case "iyi": return "Ghost: her şey yolunda"
        case "uyari", "uyarı": return "Ghost: uyarı var"
        case "kritik": return "Ghost: KRİTİK"
        default: return "Ghost: durum bilinmiyor"
        }
    }

    private func pencere() {
        openWindow(id: "ana")
        NSApplication.shared.activate(ignoringOtherApps: true)
    }
}
