import AppKit
import SwiftUI

/// The Check for Updates window.
struct UpdateView: View {
    @EnvironmentObject var updater: Updater
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var settings: Settings

    private var state: Updater.State { updater.state }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: state == .failed ? "exclamationmark.triangle" : "arrow.down.circle")
                    .font(.system(size: 28, weight: .light))
                    .foregroundStyle(state == .failed ? Theme.warn : settings.accent.color)
                VStack(alignment: .leading, spacing: 4) {
                    Text(title)
                        .font(.system(size: 15, weight: .bold))
                        .fixedSize(horizontal: false, vertical: true)
                    Text(text)
                        .font(.system(size: 12))
                        .foregroundStyle(Theme.textDim)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            if let r = updater.release, !r.notes.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
               [.available, .downloading, .ready, .failed].contains(state) {
                ScrollView {
                    Text(r.notes)
                        .font(.system(size: 11.5))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(height: 140)
                .card(padding: 10)
            }
            if state == .downloading {
                ProgressView(value: updater.progress)
                    .tint(settings.accent.color)
            }
            HStack(spacing: 8) {
                if updater.release != nil || state == .failed {
                    Button("Open Download Page") {
                        NSWorkspace.shared.open(updater.release?.pageURL ?? Updater.releasesPage)
                    }
                    .buttonStyle(GhostButtonStyle())
                }
                Spacer()
                Button("Close") { updater.showWindow = false }
                    .buttonStyle(GhostButtonStyle())
                    .keyboardShortcut(.cancelAction)
                if updater.release == nil && (state == .current || state == .failed) {
                    Button("Check Again") { updater.check() }
                        .buttonStyle(GhostButtonStyle())
                }
                if state == .downloading {
                    Button("Stop Download") { updater.cancelDownload() }
                        .buttonStyle(GhostButtonStyle())
                }
                if updater.release != nil && [.available, .failed, .ready].contains(state) && updater.installProblem == nil {
                    Button {
                        if state == .ready { installNow() } else { updater.download() }
                    } label: {
                        Text(state == .failed ? "Try Again" : "Install and Restart").padding(.horizontal, 8)
                    }
                    .buttonStyle(AccentButtonStyle(accent: settings.accent.color))
                    .fixedSize()
                    .keyboardShortcut(.defaultAction)
                }
            }
        }
        .padding(20)
        .frame(width: 480)
        .background(Theme.panel)
        .foregroundStyle(Theme.text)
        .tint(settings.accent.color)
        .preferredColorScheme(.dark)
        .onChange(of: updater.state) { _, new in
            if new == .ready { installNow() }
        }
    }

    private var title: String {
        switch state {
        case .idle, .checking: return "Checking for updates…"
        case .current: return "You're up to date"
        case .available: return "Version \(updater.release?.version ?? "") is available"
        case .downloading: return "Downloading version \(updater.release?.version ?? "")…"
        case .ready: return "Version \(updater.release?.version ?? "") is ready to install"
        case .installing: return "Installing version \(updater.release?.version ?? "")…"
        case .failed: return updater.release == nil ? "Couldn't check for updates" : "The update didn't work"
        }
    }

    private var text: String {
        let current = Updater.currentVersion
        switch state {
        case .idle, .checking: return "You have version \(current)."
        case .current: return "Version \(current) is the newest version of Manifold Mesh Mix for macOS."
        case .available:
            if let p = updater.installProblem { return "You have version \(current). \(p)" }
            return "You have version \(current). Install and Restart downloads it, checks it, installs it and opens the app again."
        case .downloading:
            let size = (updater.release?.size ?? 0) > 0 ? String(format: " (%.0f MB)", Double(updater.release!.size) / 1e6) : ""
            return "\(Int(updater.progress * 100))%\(size). The app restarts by itself when it's installed."
        case .ready: return "The app will close, install the update and open again."
        case .installing: return "The app closes now and opens again when the update is installed."
        case .failed: return updater.error
        }
    }

    /// Asks about converted files that weren't exported, then installs and quits.
    private func installNow() {
        let n = model.unexportedJobs.count
        if n > 0 && !SelfTest.isActive {
            let a = NSAlert()
            a.messageText = n == 1 ? "Install the update without exporting?" : "Install the update without exporting \(n) files?"
            a.informativeText = "\(n == 1 ? "A converted file hasn't" : "\(n) converted files haven't") been exported yet. The app restarts to install the update, so you'd have to convert \(n == 1 ? "it" : "them") again."
            a.addButton(withTitle: "Not Now")
            a.addButton(withTitle: "Install Anyway")
            if a.runModal() != .alertSecondButtonReturn { return }
        }
        if updater.install() {
            updater.showWindow = false
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { NSApp.terminate(nil) }
        }
    }
}
