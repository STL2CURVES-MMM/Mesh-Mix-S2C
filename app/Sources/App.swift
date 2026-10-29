import AppKit
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    var model: AppModel?
    var updater: Updater?
    var pendingURLs: [URL] = []

    func application(_ application: NSApplication, open urls: [URL]) {
        if let model {
            model.add(urls: urls)
        } else {
            pendingURLs.append(contentsOf: urls)
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        let installing = updater?.state == .installing
        if let model, !SelfTest.isActive, !installing {
            let n = model.unexportedJobs.count
            if n > 0 {
                let a = NSAlert()
                a.messageText = n == 1 ? "Quit without exporting?" : "Quit without exporting \(n) files?"
                a.informativeText = "\(n == 1 ? "A converted file hasn't" : "\(n) converted files haven't") been exported yet. If you quit now, you'll have to convert \(n == 1 ? "it" : "them") again."
                a.addButton(withTitle: "Cancel")
                a.addButton(withTitle: "Quit Anyway")
                if a.runModal() != .alertSecondButtonReturn { return .terminateCancel }
            }
        }
        model?.cancelAll()
        model?.shutdown()
        model?.cleanUpPreviews()
        return .terminateNow
    }
}

@main
struct ManifoldMeshMixApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var settings: Settings
    @StateObject private var model: AppModel
    @StateObject private var updater: Updater

    init() {
        let s = Settings()
        _settings = StateObject(wrappedValue: s)
        _model = StateObject(wrappedValue: AppModel(settings: s))
        _updater = StateObject(wrappedValue: Updater())
    }

    var body: some Scene {
        WindowGroup("Mesh Mix-S2C") {
            ContentView()
                .environmentObject(model)
                .environmentObject(settings)
                .environmentObject(updater)
                .frame(minWidth: 1080, minHeight: 640)
                .onAppear {
                    delegate.model = model
                    delegate.updater = updater
                    if !delegate.pendingURLs.isEmpty {
                        model.add(urls: delegate.pendingURLs)
                        delegate.pendingURLs.removeAll()
                    }
                    let args = CommandLine.arguments.dropFirst().filter { !$0.hasPrefix("-") }
                    let urls = args.map { URL(fileURLWithPath: $0) }.filter { FileManager.default.fileExists(atPath: $0.path) }
                    if !urls.isEmpty { model.add(urls: urls) }
                    if SelfTest.isActive {
                        SelfTest.run(model: model, settings: settings, updater: updater)
                    } else {
                        Updater.reportPreviousFailure()
                        if settings.checkUpdates {
                            DispatchQueue.main.asyncAfter(deadline: .now() + 4) {
                                if settings.checkUpdates { updater.check() }
                            }
                        }
                    }
                }
        }
        .windowStyle(.hiddenTitleBar)
        .defaultSize(width: 1280, height: 780)
        .commands {
            CommandGroup(replacing: .appInfo) {
                Button("About Mesh Mix-S2C") { Support.showAbout() }
            }
            CommandGroup(after: .appInfo) {
                Button("Check for Updates…") { updater.checkFromMenu() }
            }
            CommandGroup(after: .help) {
                Button("Support the Project ☕") { NSWorkspace.shared.open(Support.url) }
            }
            CommandGroup(replacing: .newItem) {
                Button("Add Files…") { model.openPanel() }
                    .keyboardShortcut("o", modifiers: .command)
            }
            CommandMenu("Convert") {
                Button("Convert All") { model.convertAll() }
                    .keyboardShortcut("r", modifiers: .command)
                Button("Export Selected File…") {
                    if let j = model.selectedJob { model.requestExport([j]) }
                }
                .keyboardShortcut("e", modifiers: .command)
                .disabled(!(model.selectedJob.map { $0.status == .done && !$0.resultFiles.isEmpty } ?? false))
                Button("Export All…") { model.requestExport(model.unexportedJobs.isEmpty ? model.exportableJobs : model.unexportedJobs) }
                    .keyboardShortcut("e", modifiers: [.command, .shift])
                    .disabled(model.exportableJobs.isEmpty)
                Button("Stop All") { model.cancelAll() }
                    .keyboardShortcut(".", modifiers: .command)
                Divider()
                Button("Clear Finished") { model.clearFinished() }
                Divider()
                Picker("Preset", selection: $settings.preset) {
                    ForEach(Preset.allCases) { p in Text(p.title).tag(p) }
                }
            }
        }

        SwiftUI.Settings {
            PreferencesView()
                .environmentObject(settings)
                .environmentObject(updater)
                .frame(width: 420)
                .preferredColorScheme(settings.theme.scheme)
        }
    }
}

struct PreferencesView: View {
    @EnvironmentObject var settings: Settings
    @EnvironmentObject var updater: Updater

    var body: some View {
        Form {
            Section("Appearance") {
                Picker("Theme", selection: $settings.theme) {
                    ForEach(ThemeChoice.allCases) { t in Text(t.title).tag(t) }
                }
                Picker("Accent color", selection: $settings.accent) {
                    ForEach(AccentChoice.allCases) { a in
                        HStack {
                            Circle().fill(a.baseColor).frame(width: 10, height: 10)
                            Text(a.title)
                        }
                        .tag(a)
                    }
                }
            }
            Section("Conversion") {
                Picker("Default preset", selection: $settings.preset) {
                    ForEach(Preset.allCases) { p in Text(p.title).tag(p) }
                }
                Stepper("Files at once: \(settings.parallelJobs)", value: $settings.parallelJobs, in: 1...6)
                Toggle("Convert as soon as files are added", isOn: $settings.convertOnDrop)
            }
            Section("Export") {
                Toggle("Replace existing files without asking", isOn: $settings.replaceExisting)
                Toggle("Show in Finder after exporting", isOn: $settings.revealWhenDone)
                Toggle("Split 3MF projects into parts", isOn: $settings.splitParts)
            }
            Section("Updates") {
                Toggle("Check for updates when the app opens", isOn: $settings.checkUpdates)
                    .help("Only looks: an update is installed when you click Install.")
                HStack {
                    Text("Version \(Updater.currentVersion)").foregroundStyle(.secondary)
                    Spacer()
                    Button("Check for Updates…") { updater.checkFromMenu() }
                }
            }
        }
        .formStyle(.grouped)
        .padding(.vertical, 8)
    }
}

/// Buy Me a Coffee: Help → Support the Project, and a link in the About box.
enum Support {
    static let url = URL(string: "https://buymeacoffee.com/tunathefirst")!

    /// The standard About box, with links to the source code and the coffee page.
    static func showAbout() {
        let center = NSMutableParagraphStyle()
        center.alignment = .center
        let base: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 11), .paragraphStyle: center,
                                                   .foregroundColor: NSColor.labelColor]
        func link(_ text: String, _ target: URL) -> NSAttributedString {
            var attrs = base
            attrs[.link] = target
            return NSAttributedString(string: text, attributes: attrs)
        }
        let credits = NSMutableAttributedString(string: "STL / 3MF / OBJ / PLY → editable STEP\n\n"
            + "Free software under the GNU Affero General Public License v3, with no warranty.\n\n", attributes: base)
        credits.append(link("Source code", URL(string: "https://github.com/\(Updater.repo)")!))
        credits.append(NSAttributedString(string: "\n\nFree to use. If it saves you time, you can ", attributes: base))
        credits.append(link("buy me a coffee ☕", url))
        credits.append(NSAttributedString(string: ": it helps pay for the AI credits that go into building and improving it.",
                                          attributes: base))
        NSApp.orderFrontStandardAboutPanel(options: [.credits: credits])
        NSApp.activate(ignoringOtherApps: true)
    }
}
