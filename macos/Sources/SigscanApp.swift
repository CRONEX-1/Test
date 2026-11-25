import AppKit
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

@main
struct SigscanApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store = ScanStore()

    var body: some Scene {
        Window("Sigscan", id: "main") {
            ContentView()
                .environmentObject(store)
                .frame(minWidth: 980, minHeight: 560)
                .onAppear { store.start() }
        }
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandMenu("Scan") {
                Button(store.paused ? "Resume Scanning" : "Pause Scanning") { store.togglePause() }
                    .keyboardShortcut("p")
                Button("Clear List") { store.clear() }
                    .keyboardShortcut("k")
                Divider()
                Button("Export CSV…") { store.exportCSV() }
                    .keyboardShortcut("e")
            }
        }
    }
}
