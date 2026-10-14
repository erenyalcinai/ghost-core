import SwiftUI
import WidgetKit

@main
struct GhostKokpitApp: App {
    @UIApplicationDelegateAdaptor(BildirimDelegesi.self) private var bildirim
    @StateObject private var oturum = Oturum()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(oturum)
                .onAppear {
                    bildirim.api = oturum.api
                    if oturum.esli { bildirim.kaydol() }
                }
                .onChange(of: oturum.esli) { _, esli in
                    bildirim.api = oturum.api
                    if esli { bildirim.kaydol() }
                }
                .onChange(of: scenePhase) { _, p in
                    if p == .background {
                        WidgetCenter.shared.reloadAllTimelines()
                        DuyuYoneticisi.paylasilan.arkaPlanPlanla()
                    }
                    // Öne gelince duyuları tazele (kısıt: 10 dk'da bir, bkz. asgariAra).
                    if p == .active {
                        Task {
                            await DuyuYoneticisi.paylasilan.bildir()
                            await DuyuYoneticisi.paylasilan.bolgeleriTazele()
                            await EllerYoneticisi.paylasilan.kuyrugaBak()
                        }
                    }
                }
        }
    }
}
