import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var settings: Settings
    @EnvironmentObject var updater: Updater
    @LocalState private var dropTargeted = false
    @Environment(\.colorScheme) private var systemScheme

    var accent: Color { settings.accent.color }

    /// The theme in force, made current (Theme.palette) before any view below
    /// reads its colours; the views are rebuilt (.id) when it or the accent
    /// changes, so the switch is live. (Match system: the colour scheme seen
    /// here is the computer's, as no scheme is asked for then.)
    private var themeKey: String {
        Theme.palette = settings.theme.palette(systemDark: systemScheme == .dark)
        return "\(Theme.palette.name)-\(settings.accent.rawValue)"
    }

    var body: some View {
        let key = themeKey
        return HStack(spacing: 0) {
            Sidebar(dropTargeted: $dropTargeted)
                .frame(width: 310)
            Divider().overlay(Theme.stroke)
            PreviewPanel()
                .frame(minWidth: 420, maxWidth: .infinity)
                .ignoresSafeArea(.container, edges: .top)
            Divider().overlay(Theme.stroke)
            SettingsPanel()
                .frame(width: 300)
        }
        .id(key)
        .background(Theme.bg)
        .foregroundStyle(Theme.text)
        .tint(accent)
        .preferredColorScheme(settings.theme.scheme)
        .onDrop(of: [UTType.fileURL], isTargeted: $dropTargeted) { providers in
            loadDropped(providers)
            return true
        }
        .onDeleteCommand {
            if let j = model.selectedJob { model.remove(j) }
        }
        .overlay {
            if model.engineMissing {
                EngineMissingBanner()
            }
        }
        .sheet(item: $model.exportRequest) { req in
            ExportSheet(request: req)
                .environmentObject(model)
                .environmentObject(settings)
        }
        .sheet(isPresented: $updater.showWindow) {
            UpdateView()
                .environmentObject(updater)
                .environmentObject(model)
                .environmentObject(settings)
        }
        .preferredColorScheme(.dark)
    }

    private func loadDropped(_ providers: [NSItemProvider]) {
        var urls: [URL] = []
        let group = DispatchGroup()
        let lock = NSLock()
        for p in providers {
            group.enter()
            _ = p.loadObject(ofClass: URL.self) { url, _ in
                if let url {
                    lock.lock(); urls.append(url); lock.unlock()
                }
                group.leave()
            }
        }
        group.notify(queue: .main) { model.add(urls: urls) }
    }
}

struct EngineMissingBanner: View {
    var body: some View {
        VStack {
            HStack(spacing: 10) {
                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(Theme.warn)
                Text(AppModel.engineMissingMessage)
                    .font(.system(size: 12))
            }
            .padding(12)
            .background(RoundedRectangle(cornerRadius: Theme.rMed).fill(Theme.card))
            .overlay(RoundedRectangle(cornerRadius: Theme.rMed).strokeBorder(Theme.warn.opacity(0.5)))
            .padding(.top, 12)
            Spacer()
        }
    }
}

// MARK: - Sidebar

struct Sidebar: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var settings: Settings
    @Binding var dropTargeted: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            BrandHeader()
            UpdateBanner()
            DropZone(targeted: dropTargeted)
            HStack {
                SectionLabel(text: "Queue")
                Spacer()
                if !model.jobs.isEmpty {
                    Text("\(model.jobs.count) file\(model.jobs.count == 1 ? "" : "s")")
                        .font(Theme.mono(10))
                        .foregroundStyle(Theme.textFaint)
                }
            }
            if model.jobs.isEmpty {
                VStack(spacing: 8) {
                    Image(systemName: "tray")
                        .font(.system(size: 22, weight: .light))
                        .foregroundStyle(Theme.textFaint)
                    Text("No files yet")
                        .font(.system(size: 12))
                        .foregroundStyle(Theme.textFaint)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 8) {
                        ForEach(model.jobs) { job in
                            JobRow(job: job, selected: model.selection == job.id)
                                .onTapGesture { model.selection = job.id }
                                .contextMenu { JobMenu(job: job) }
                        }
                    }
                    .padding(.bottom, 8)
                }
                .scrollIndicators(.never)
            }
            HStack(spacing: 8) {
                Button { model.openPanel() } label: {
                    Label("Add", systemImage: "plus")
                }
                .buttonStyle(GhostButtonStyle())
                Spacer()
                if model.jobs.contains(where: { $0.status == .done }) {
                    Button("Clear finished") { model.clearFinished() }
                        .buttonStyle(GhostButtonStyle())
                }
                if model.runningCount > 0 || model.jobs.contains(where: { $0.status == .queued }) {
                    Button("Stop all") { model.cancelAll() }
                        .buttonStyle(GhostButtonStyle(tint: Theme.bad))
                }
            }
        }
        .padding(16)
        .frame(maxHeight: .infinity, alignment: .top)
        .background(Theme.panel)
    }
}

struct BrandHeader: View {
    @EnvironmentObject var settings: Settings
    @EnvironmentObject var updater: Updater
    var body: some View {
        HStack(spacing: 10) {
            ZStack {
                RoundedRectangle(cornerRadius: Theme.rMed, style: .continuous)
                    .fill(LinearGradient(colors: [settings.accent.color.opacity(0.9), settings.accent.color.opacity(0.45)],
                                         startPoint: .topLeading, endPoint: .bottomTrailing))
                Image(systemName: "point.3.filled.connected.trianglepath.dotted")
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(Color.black.opacity(0.8))
            }
            .frame(width: 34, height: 34)
            .shadow(color: settings.accent.color.opacity(0.45), radius: 8)
            VStack(alignment: .leading, spacing: 1) {
                Text("Mesh Mix-S2C")
                    .font(.system(size: 15, weight: .bold))
                Text("STL → STEP converter")
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.textDim)
            }
            Spacer()
            Button { updater.checkFromMenu() } label: {
                Image(systemName: "arrow.down.circle")
                    .font(.system(size: 13, weight: .semibold))
                    .frame(width: 26, height: 26)
                    .background(Circle().fill(Theme.ink(0.07)))
                    .overlay(alignment: .topTrailing) {
                        if updater.availableVersion != nil {
                            Circle().fill(settings.accent.color).frame(width: 8, height: 8)
                                .overlay(Circle().stroke(Theme.panel, lineWidth: 2))
                                .offset(x: 2, y: -2)
                        }
                    }
                    .contentShape(Circle())
            }
            .buttonStyle(.plain)
            .foregroundStyle(updater.availableVersion != nil ? settings.accent.color : Theme.textDim)
            .help(updater.availableVersion.map { "Version \($0) is available — click to install" } ?? "Check for updates")
            .accessibilityLabel("Check for updates")
        }
        .padding(.bottom, 2)
    }
}

/// "Version X is available" note under the brand (after the quiet check at launch).
struct UpdateBanner: View {
    @EnvironmentObject var settings: Settings
    @EnvironmentObject var updater: Updater

    var body: some View {
        if let v = updater.availableVersion {
            Button { updater.checkFromMenu() } label: {
                HStack(spacing: 10) {
                    Image(systemName: "arrow.down.circle")
                        .font(.system(size: 18, weight: .light))
                        .foregroundStyle(settings.accent.color)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Version \(v) is available").font(.system(size: 12, weight: .semibold))
                        Text("Click to install. The app restarts by itself.")
                            .font(.system(size: 10.5)).foregroundStyle(Theme.textDim)
                    }
                    Spacer(minLength: 0)
                }
                .card(highlighted: true, accent: settings.accent.color, padding: 10)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
    }
}

struct DropZone: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var settings: Settings
    var targeted: Bool

    var body: some View {
        Button { model.openPanel() } label: {
            VStack(spacing: 8) {
                Image(systemName: targeted ? "arrow.down.circle.fill" : "square.and.arrow.down.on.square")
                    .font(.system(size: 24, weight: .light))
                    .foregroundStyle(targeted ? settings.accent.color : Theme.textDim)
                Text(targeted ? "Release to add" : "Drop files or folders here")
                    .font(.system(size: 13, weight: .semibold))
                Text("or click to browse · STL, 3MF, OBJ, PLY")
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.textFaint)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 18)
            .background(
                RoundedRectangle(cornerRadius: Theme.rBig, style: .continuous)
                    .fill(targeted ? settings.accent.color.opacity(0.08) : Theme.card)
            )
            .overlay(
                RoundedRectangle(cornerRadius: Theme.rBig, style: .continuous)
                    .strokeBorder(style: StrokeStyle(lineWidth: 1.3, dash: [6, 5]))
                    .foregroundStyle(targeted ? settings.accent.color : Theme.strokeStrong)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .animation(.easeOut(duration: 0.15), value: targeted)
    }
}

struct JobMenu: View {
    @EnvironmentObject var model: AppModel
    @ObservedObject var job: Job
    var body: some View {
        if job.status == .done {
            if !job.resultFiles.isEmpty {
                Button(job.isExported ? "Export Again…" : "Export…") { model.requestExport([job]) }
            }
            if job.isExported {
                Button("Show in Finder") { model.reveal(job) }
                Button("Open STEP File") { model.openOutput(job) }
            }
            Button("Convert Again") { model.reconvert(job) }
        }
        if job.canConvert {
            Button(job.status == .ready ? "Convert" : "Try Again") { model.enqueue(job) }
        }
        if job.canSplit && model.splitAvailable {
            Toggle("Split to Parts", isOn: Binding(get: { job.split }, set: { model.setSplit(job, $0) }))
                .disabled(job.status.isBusy || job.status == .queued)
        }
        if job.status == .converting || job.status == .queued {
            Button("Cancel") { model.cancel(job) }
        }
        Divider()
        Button("Remove from List") { model.remove(job) }
    }
}

struct JobRow: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var settings: Settings
    @ObservedObject var job: Job
    var selected: Bool
    @LocalState private var hover = false

    var statusColor: Color {
        switch job.status {
        case .done: return job.result?.hasErrors == true ? Theme.bad : (job.result?.hasIssues == true ? Theme.warn : Theme.good)
        case .failed: return Theme.bad
        case .converting, .inspecting: return settings.accent.color
        case .queued: return Theme.textDim
        case .cancelled: return Theme.textFaint
        case .ready: return Theme.textDim
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 8) {
                Image(systemName: icon)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(statusColor)
                    .frame(width: 16)
                VStack(alignment: .leading, spacing: 2) {
                    Text(job.fileName)
                        .font(.system(size: 12.5, weight: .semibold))
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .helpIfTruncated(job.fileName)
                    Text(subtitle)
                        .font(Theme.mono(10))
                        .foregroundStyle(job.needsExport ? settings.accent.color.opacity(0.85) : Theme.textFaint)
                        .lineLimit(1)
                        .helpIfTruncated(subtitle)
                }
                Spacer(minLength: 4)
                if hover || selected {
                    rowButtons
                }
            }
            if job.status == .converting {
                ThinProgress(value: job.progress, accent: settings.accent.color)
                Text(job.stage)
                    .font(.system(size: 10.5))
                    .foregroundStyle(Theme.textDim)
                    .lineLimit(1)
                    .helpIfTruncated(job.stage)
            } else if job.status == .inspecting {
                // the bar moves once the engine says how far the check is
                ThinProgress(value: job.progress, accent: settings.accent.color, indeterminate: job.progress <= 0)
                if !job.stage.isEmpty {
                    Text(job.stage)
                        .font(.system(size: 10.5))
                        .foregroundStyle(Theme.textDim)
                        .lineLimit(1)
                        .helpIfTruncated(job.stage)
                }
            } else if job.status == .failed, let e = job.error {
                Text(e)
                    .font(.system(size: 10.5))
                    .foregroundStyle(Theme.bad.opacity(0.9))
                    .lineLimit(2)
                    .help(e)
            }
        }
        .card(highlighted: selected, accent: settings.accent.color, padding: 10)
        .onHover { hover = $0 }
        .contentShape(Rectangle())
    }

    @ViewBuilder var rowButtons: some View {
        HStack(spacing: 4) {
            switch job.status {
            case .done:
                if job.needsExport {
                    iconButton("square.and.arrow.up", help: "Export the STEP file") { model.requestExport([job]) }
                } else if job.isExported {
                    iconButton("folder", help: "Show the exported STEP file in Finder") { model.reveal(job) }
                }
            case .converting, .queued:
                iconButton("xmark", help: "Cancel") { model.cancel(job) }
            case .ready, .failed, .cancelled:
                if job.readable {
                    iconButton(job.status == .ready ? "play.fill" : "arrow.clockwise", help: job.status == .ready ? "Convert this file" : "Try again") { model.enqueue(job) }
                }
                iconButton("trash", help: "Remove from the list") { model.remove(job) }
            default:
                EmptyView()
            }
        }
    }

    func iconButton(_ name: String, help: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: name)
                .font(.system(size: 10, weight: .bold))
                .frame(width: 22, height: 22)
                .background(Circle().fill(Theme.ink(0.07)))
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(Theme.textDim)
        .help(help)
        .accessibilityLabel(help)
    }

    var icon: String {
        switch job.status {
        case .done: return job.result?.hasIssues == true ? "exclamationmark.circle.fill" : "checkmark.circle.fill"
        case .failed: return "xmark.octagon.fill"
        case .converting: return "gearshape.2.fill"
        case .inspecting: return "doc.text.magnifyingglass"
        case .queued: return "clock"
        case .cancelled: return "stop.circle"
        case .ready: return "cube"
        }
    }

    var subtitle: String {
        if job.status == .done, let r = job.result {
            let preset = r.presetTitle
            let parts = job.isSplit ? " · \(job.partList.count) files" : ""
            let state = job.needsExport ? "Ready to export · " : (job.isExported ? "Exported · " : "")
            return "\(state)\(preset)\(parts) · \(r.faces.formatted()) faces · \(formatBytes(r.fileSize))"
        }
        if let t = job.triangles {
            var s = "\(t.formatted()) triangles"
            if job.canSplit {
                s += " · \(job.partList.count) parts → \(job.split ? "\(job.partList.count) STEP files" : "1 STEP file")"
            } else if let p = job.parts, p > 1 {
                s += " · \(p) parts"
            } else if let b = job.bodies, b > 1 {
                s += " · \(b) bodies"
            }
            if let o = job.openShells, o > 0 { s += " · has holes" }
            return s
        }
        if job.status == .failed && !job.readable { return "Can't be read" }
        return job.status.title
    }
}

func formatBytes(_ n: Int) -> String {
    let f = ByteCountFormatter()
    f.countStyle = .file
    return f.string(fromByteCount: Int64(n))
}
