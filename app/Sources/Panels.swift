import SwiftUI

// MARK: - Preview panel

struct PreviewPanel: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var settings: Settings

    var body: some View {
        if let job = model.selectedJob {
            JobPreview(job: job)
                .id(job.id)
        } else {
            EmptyPreview()
        }
    }
}

struct EmptyPreview: View {
    @EnvironmentObject var settings: Settings
    @EnvironmentObject var model: AppModel
    var body: some View {
        ZStack {
            LinearGradient(colors: [Color(nsColor: Theme.viewportTop), Color(nsColor: Theme.viewportBottom)],
                           startPoint: .top, endPoint: .bottom)
            GridPattern().stroke(Color.white.opacity(0.035), lineWidth: 1)
            VStack(spacing: 14) {
                ZStack {
                    Circle().fill(settings.accent.color.opacity(0.10)).frame(width: 110, height: 110)
                    Circle().strokeBorder(settings.accent.color.opacity(0.35), lineWidth: 1).frame(width: 110, height: 110)
                    Image(systemName: "cube.transparent")
                        .font(.system(size: 44, weight: .ultraLight))
                        .foregroundStyle(settings.accent.color)
                }
                Text("Convert 3D-print meshes into editable STEP files")
                    .font(.system(size: 20, weight: .bold))
                Text("Drop STL or 3MF files (or a whole folder) anywhere in this window.\nChoose a preset on the right, then press Convert.")
                    .multilineTextAlignment(.center)
                    .font(.system(size: 13))
                    .foregroundStyle(Theme.textDim)
                HStack(spacing: 10) {
                    FeatureChip(icon: "cube.transparent", text: "Editable CAD faces")
                    FeatureChip(icon: "drop.fill", text: "Smooth surfaces")
                    FeatureChip(icon: "checkmark.seal", text: "Every file checked")
                }
                .padding(.top, 6)
            }
        }
    }
}

struct FeatureChip: View {
    @EnvironmentObject var settings: Settings
    let icon: String
    let text: String
    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: icon).font(.system(size: 10)).foregroundStyle(settings.accent.color)
            Text(text).font(.system(size: 11, weight: .medium))
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(Capsule().fill(Theme.card))
        .overlay(Capsule().strokeBorder(Theme.stroke))
    }
}

struct GridPattern: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        let step: CGFloat = 32
        var x: CGFloat = 0
        while x < rect.width { p.move(to: CGPoint(x: x, y: 0)); p.addLine(to: CGPoint(x: x, y: rect.height)); x += step }
        var y: CGFloat = 0
        while y < rect.height { p.move(to: CGPoint(x: 0, y: y)); p.addLine(to: CGPoint(x: rect.width, y: y)); y += step }
        return p
    }
}

enum PreviewMode: String, CaseIterable, Identifiable {
    case mesh = "Mesh"
    case result = "STEP"
    var id: String { rawValue }
}

struct JobPreview: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var settings: Settings
    @ObservedObject var job: Job
    @LocalState private var mode: PreviewMode = .mesh
    @LocalState private var showEdges = true
    @LocalState private var resetToken = 0
    @LocalState private var inMesh: PreviewMesh?
    @LocalState private var outMesh: PreviewMesh?
    @LocalState private var meshCache: [String: PreviewMesh] = [:]
    @LocalState private var showLog = false

    /// The selected tab's part when a 3MF is split into parts.
    private var part: JobPart? { job.currentPart }
    private var inURL: URL? { part?.inputPreview ?? job.inputPreview }
    private var outURL: URL? { job.isSplit ? part?.outputPreview : job.outputPreview }
    private var shownResult: ConversionResult? { job.isSplit ? part?.result : job.result }
    private var previewKey: String { "\(inURL?.path ?? "")|\(outURL?.path ?? "")" }

    var body: some View {
        ZStack(alignment: .top) {
            Viewport(mesh: mode == .result ? outMesh : inMesh,
                     faceColors: mode == .result && settings.colorFaces,
                     showEdges: mode == .result && showEdges,
                     tint: mode == .result ? NSColor(white: 0.78, alpha: 1) : NSColor(red: 0.62, green: 0.66, blue: 0.72, alpha: 1),
                     resetToken: resetToken,
                     meshID: "\(mode.rawValue)-\(mode == .result ? outURL?.path ?? "" : inURL?.path ?? "")")
            topBar
            VStack {
                Spacer()
                bottomBar
            }
            if (mode == .mesh && inMesh == nil) || (mode == .result && outMesh == nil) {
                placeholder
            }
        }
        .onAppear {
            if job.status == .done { mode = .result }
            loadMeshes()
        }
        .onChange(of: previewKey) { _, _ in loadMeshes() }
        .onChange(of: job.status) { _, new in
            if new == .done { mode = .result }
        }
        .onChange(of: part?.outputPreview) { _, new in
            // a part finished while the rest of the file is still converting
            if new != nil && job.status == .converting { mode = .result }
        }
    }

    private func loadMeshes() {
        let a = inURL
        let b = outURL
        let cached = meshCache
        DispatchQueue.global(qos: .userInitiated).async {
            let ma = a.flatMap { cached[$0.path] ?? PreviewMesh.load($0) }
            let mb = b.flatMap { cached[$0.path] ?? PreviewMesh.load($0) }
            DispatchQueue.main.async {
                guard a == inURL, b == outURL else { return }  // another tab was picked meanwhile
                inMesh = ma
                outMesh = mb
                if meshCache.count > 8 { meshCache.removeAll() }
                if let a, let ma { meshCache[a.path] = ma }
                if let b, let mb { meshCache[b.path] = mb }
            }
        }
    }

    @ViewBuilder var placeholder: some View {
        VStack(spacing: 10) {
            Spacer()
            if mode == .result {
                if job.status == .converting || job.status == .queued {
                    ProgressView().controlSize(.large).tint(settings.accent.color)
                    Text(job.stage.isEmpty ? "Waiting…" : job.stage).foregroundStyle(Theme.textDim).font(.system(size: 12))
                        .multilineTextAlignment(.center).frame(maxWidth: 380)
                } else if job.status == .failed {
                    Image(systemName: "xmark.octagon").font(.system(size: 30, weight: .light)).foregroundStyle(Theme.bad)
                    Text(job.error ?? "Conversion failed").foregroundStyle(Theme.textDim).font(.system(size: 12))
                        .multilineTextAlignment(.center).frame(maxWidth: 380)
                } else if job.status == .done && job.isSplit {
                    Image(systemName: "exclamationmark.triangle").font(.system(size: 30, weight: .light)).foregroundStyle(Theme.warn)
                    Text("This part couldn't be converted").foregroundStyle(Theme.textDim).font(.system(size: 12))
                } else {
                    Image(systemName: "cube.transparent").font(.system(size: 30, weight: .ultraLight)).foregroundStyle(Theme.textFaint)
                    Text("Convert to see the STEP result").foregroundStyle(Theme.textDim).font(.system(size: 12))
                }
            } else if job.status == .inspecting {
                ProgressView().tint(settings.accent.color)
                Text("Reading the file…").foregroundStyle(Theme.textDim).font(.system(size: 12))
            } else if !job.readable {
                Image(systemName: "exclamationmark.triangle").font(.system(size: 30, weight: .light)).foregroundStyle(Theme.bad)
                Text("This file can't be read").foregroundStyle(Theme.text).font(.system(size: 13, weight: .semibold))
                if let e = job.error {
                    Text(e).foregroundStyle(Theme.textDim).font(.system(size: 12))
                        .multilineTextAlignment(.center).frame(maxWidth: 380)
                }
            }
            Spacer()
        }
        .allowsHitTesting(false)
    }

    /// Two rows so it fits any window width: the tabs with the view controls,
    /// then where the file lives, its check result and "Split to parts".
    var topBar: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                tabStrip
                    .frame(maxWidth: .infinity, alignment: .leading)
                viewControls
                    .fixedSize()
            }
            HStack(spacing: 8) {
                Text(job.url.path)
                    .font(Theme.mono(10)).foregroundStyle(Theme.textFaint).lineLimit(1).truncationMode(.head)
                    .helpIfTruncated(job.url.path)
                    .padding(.leading, 2)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if let r = shownResult {
                    checkBadge(r)
                }
                if job.status == .done && !job.resultFiles.isEmpty {
                    if job.isExported {
                        Pill(text: "Exported", color: Theme.textDim, icon: "square.and.arrow.up")
                            .fixedSize()
                            .help("Saved to:\n" + job.exportedFiles.map(\.path).joined(separator: "\n"))
                    } else {
                        Pill(text: "Not exported yet", color: settings.accent.color, icon: "clock")
                            .fixedSize()
                            .help("The STEP \(job.isSplit ? "files are" : "file is") ready. Look it over, then press Export to save it.")
                    }
                }
                if job.canSplit && model.splitAvailable {
                    Toggle(isOn: Binding(get: { job.split }, set: { model.setSplit(job, $0) })) {
                        Label("Split to parts", systemImage: "square.split.2x1")
                            .font(.system(size: 11.5, weight: .medium))
                    }
                    .toggleStyle(.button)
                    .controlSize(.small)
                    .fixedSize()
                    .disabled(job.status.isBusy || job.status == .queued)
                    .help(job.split
                          ? "Each part of this 3MF becomes its own STEP file. Click to put all parts in one STEP file instead."
                          : "Click to save each part of this 3MF as its own STEP file, like “Split to parts” in the slicer.")
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity)
        .background(LinearGradient(colors: [Theme.bg.opacity(0.9), Theme.bg.opacity(0)], startPoint: .top, endPoint: .bottom))
    }

    var viewControls: some View {
        HStack(spacing: 6) {
            Picker("", selection: $mode) {
                ForEach(PreviewMode.allCases) { m in Text(m.rawValue).tag(m) }
            }
            .pickerStyle(.segmented)
            .frame(width: 120)
            .labelsHidden()
            if mode == .result {
                Toggle(isOn: $settings.colorFaces) { Image(systemName: "paintpalette") }
                    .toggleStyle(.button).help("Give every face its own color, to see how the surfaces were split up")
                Toggle(isOn: $showEdges) { Image(systemName: "square.dashed") }
                    .toggleStyle(.button).help("Show edges")
            }
            Button { resetToken += 1 } label: { Image(systemName: "scope") }
                .buttonStyle(GhostButtonStyle()).help("Reset view")
        }
    }

    /// One tab per part (each STL in the 3MF and its STEP), or a single tab
    /// for the file itself.
    @ViewBuilder var tabStrip: some View {
        if job.isSplit {
            ScrollViewReader { proxy in
                ScrollView(.horizontal) {
                    HStack(spacing: 4) {
                        ForEach(Array(job.partList.enumerated()), id: \.element.id) { i, p in
                            FileTab(title: p.name,
                                    icon: tabIcon(for: p),
                                    iconColor: tabColor(for: p),
                                    selected: i == job.selectedPart,
                                    maxWidth: 170) {
                                job.selectedPart = i
                            }
                            .id(p.id)
                            .help("\(p.name) · \(p.triangles.formatted()) triangles" + (p.exportedURL.map { " → \($0.lastPathComponent)" } ?? ""))
                        }
                    }
                }
                .scrollIndicators(.never)
                .onAppear { scrollToSelected(proxy) }
                .onChange(of: job.selectedPart) { _, _ in
                    withAnimation(.easeOut(duration: 0.2)) { scrollToSelected(proxy) }
                }
            }
        } else {
            FileTab(title: job.fileName,
                    icon: job.canSplit ? "square.stack.3d.up" : "doc",
                    iconColor: Theme.textDim,
                    selected: true,
                    badge: job.canSplit ? "\(job.partList.count) parts" : nil) {}
                .help(job.canSplit ? "This 3MF has \(job.partList.count) parts, saved together in one STEP file." : job.fileName)
        }
    }

    private func scrollToSelected(_ proxy: ScrollViewProxy) {
        if job.partList.indices.contains(job.selectedPart) {
            proxy.scrollTo(job.partList[job.selectedPart].id)
        }
    }

    private func tabIcon(for p: JobPart) -> String {
        if let r = p.result { return r.hasIssues ? "exclamationmark.circle.fill" : "checkmark.circle.fill" }
        if job.status == .converting || job.status == .queued { return "clock" }
        return "cube"
    }

    private func tabColor(for p: JobPart) -> Color {
        if let r = p.result { return r.hasErrors ? Theme.bad : (r.hasIssues ? Theme.warn : Theme.good) }
        return Theme.textDim
    }

    @ViewBuilder func checkBadge(_ r: ConversionResult) -> some View {
        if r.hasErrors {
            Pill(text: "Problem found", color: Theme.bad, icon: "xmark")
                .fixedSize()
                .help(r.checks.map(\.message).joined(separator: "\n"))
        } else if !r.checks.isEmpty {
            Pill(text: r.checks.count == 1 ? "1 thing to check" : "\(r.checks.count) things to check", color: Theme.warn, icon: "exclamationmark")
                .fixedSize()
                .help(r.checks.map(\.message).joined(separator: "\n"))
        } else if r.checked {
            Pill(text: "File checked", color: Theme.good, icon: "checkmark")
                .fixedSize()
                .help("The STEP file was read back and checked: it is a valid solid and matches the mesh.")
        } else if let v = r.valid {
            Pill(text: v ? "Valid solid" : "Has small errors", color: v ? Theme.good : Theme.warn, icon: v ? "checkmark" : "exclamationmark")
                .fixedSize()
        }
    }

    /// What the check of the saved STEP file found, in plain words.
    @ViewBuilder var issuesCard: some View {
        if let r = shownResult, !r.checks.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                ForEach(r.checks, id: \.self) { issue in
                    HStack(alignment: .top, spacing: 7) {
                        Image(systemName: issue.isError ? "xmark.octagon.fill" : "exclamationmark.triangle.fill")
                            .font(.system(size: 11))
                            .foregroundStyle(issue.isError ? Theme.bad : Theme.warn)
                            .padding(.top, 1)
                        Text(issue.message)
                            .font(.system(size: 11.5))
                            .foregroundStyle(Theme.text)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .card(padding: 10)
        }
    }

    var bottomBar: some View {
        VStack(alignment: .leading, spacing: 8) {
            issuesCard
            if showLog && (!job.log.isEmpty || !job.warnings.isEmpty) {
                ScrollView {
                    VStack(alignment: .leading, spacing: 3) {
                        ForEach(Array(job.log.enumerated()), id: \.offset) { _, l in
                            Text(l).font(Theme.mono(10.5)).foregroundStyle(Theme.textDim)
                        }
                        ForEach(Array(job.warnings.enumerated()), id: \.offset) { _, w in
                            Text("⚠ " + w).font(Theme.mono(10.5)).foregroundStyle(Theme.warn)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 140)
                .card(padding: 10)
            }
            HStack(spacing: 8) {
                ScrollView(.horizontal) {
                    HStack(spacing: 8) { statChips }
                }
                .scrollIndicators(.never)
                .frame(maxWidth: .infinity, alignment: .leading)
                if !job.log.isEmpty || !job.warnings.isEmpty {
                    Button { showLog.toggle() } label: {
                        HStack(spacing: 4) {
                            Image(systemName: "text.alignleft")
                            if !job.warnings.isEmpty { Text("\(job.warnings.count)").foregroundStyle(Theme.warn) }
                        }
                    }
                    .buttonStyle(GhostButtonStyle()).help("Show the conversion log")
                }
                if job.status == .done && job.isExported {
                    Button { model.revealCurrent(job) } label: {
                        Label("Show in Finder", systemImage: "folder").lineLimit(1).fixedSize()
                    }
                    .buttonStyle(GhostButtonStyle())
                    .fixedSize()
                    .help(job.isSplit ? "Show this part's exported STEP file in Finder" : "Show the exported STEP file in Finder")
                }
                if job.status == .done && !job.resultFiles.isEmpty {
                    if job.isExported {
                        Button { model.requestExport([job]) } label: {
                            Label("Export Again…", systemImage: "square.and.arrow.up").lineLimit(1).fixedSize()
                        }
                        .buttonStyle(GhostButtonStyle(tint: settings.accent.color))
                        .fixedSize()
                        .help("Save the STEP \(job.isSplit ? "files" : "file") somewhere else, or under another name")
                    } else {
                        Button { model.requestExport([job]) } label: {
                            Label(job.isSplit ? "Export \(job.resultFiles.count) Files…" : "Export…", systemImage: "square.and.arrow.up")
                                .lineLimit(1).padding(.horizontal, 4)
                        }
                        .buttonStyle(AccentButtonStyle(accent: settings.accent.color))
                        .fixedSize()
                        .help("Choose where to save the STEP \(job.isSplit ? "files" : "file") (⌘E)")
                    }
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity)
        .background(LinearGradient(colors: [Theme.bg.opacity(0), Theme.bg.opacity(0.85)], startPoint: .top, endPoint: .bottom))
    }

    @ViewBuilder var statChips: some View {
                if let t = part?.triangles ?? job.triangles { StatChip(label: "Triangles", value: t.formatted()) }
                if let bb = part?.bboxSize ?? job.bboxSize, bb.count == 3 {
                    StatChip(label: "Size", value: bb.map { String(format: $0 >= 100 ? "%.0f" : "%.1f", $0) }.joined(separator: "×") + " " + settings.unit.rawValue)
                }
                if let r = shownResult {
                    StatChip(label: "Preset", value: r.presetTitle, accent: true)
                    StatChip(label: "Faces", value: r.faces.formatted(), accent: true)
                    StatChip(label: r.solids > 0 ? "Solids" : "Shells", value: "\(r.solids > 0 ? r.solids : r.shells)")
                    StatChip(label: "STEP", value: formatBytes(r.fileSize))
                    StatChip(label: "Time", value: String(format: "%.1fs", r.seconds))
                }
    }
}

/// A tab in the preview header: a file, or one part of a 3MF project.
struct FileTab: View {
    @EnvironmentObject var settings: Settings
    let title: String
    let icon: String
    var iconColor: Color = Theme.textDim
    let selected: Bool
    var badge: String? = nil
    var maxWidth: CGFloat = 260
    let action: () -> Void
    @LocalState private var hover = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Image(systemName: icon)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(iconColor)
                Text(title)
                    .font(.system(size: 13, weight: selected ? .semibold : .medium))
                    .foregroundStyle(selected ? Theme.text : Theme.textDim)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .helpIfTruncated(title)
                if let badge {
                    Text(badge)
                        .font(Theme.mono(9.5, .semibold))
                        .foregroundStyle(Theme.textDim)
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .background(Capsule().fill(Color.white.opacity(0.07)))
                }
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .frame(maxWidth: maxWidth)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(selected ? settings.accent.color.opacity(0.14) : (hover ? Color.white.opacity(0.06) : Theme.card.opacity(0.7)))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .strokeBorder(selected ? settings.accent.color.opacity(0.7) : Theme.stroke, lineWidth: 1)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
    }
}

struct StatChip: View {
    @EnvironmentObject var settings: Settings
    let label: String
    let value: String
    var accent: Bool = false
    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label.uppercased()).font(.system(size: 8.5, weight: .semibold)).tracking(0.8).foregroundStyle(Theme.textFaint)
            Text(value).font(Theme.mono(11.5, .semibold)).foregroundStyle(accent ? settings.accent.color : Theme.text)
                .lineLimit(1).helpIfTruncated(value)
        }
        .padding(.horizontal, 9).padding(.vertical, 5)
        .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(Theme.card.opacity(0.92)))
        .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(Theme.stroke))
    }
}

// MARK: - Settings panel

struct SettingsPanel: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var settings: Settings
    @LocalState private var showAdvanced = false

    var accent: Color { settings.accent.color }

    var qualityWord: String {
        switch settings.quality {
        case ..<0.25: return "Fastest"
        case ..<0.5: return "Fast"
        case ..<0.62: return "Balanced"
        case ..<0.85: return "High quality"
        default: return "Best quality"
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    SectionLabel(text: "Preset")
                    VStack(spacing: 8) {
                        ForEach(Preset.allCases) { p in
                            PresetCard(preset: p, selected: settings.preset == p)
                                .onTapGesture { settings.preset = p }
                                .accessibilityAddTraits(.isButton)
                                .accessibilityLabel("\(p.title) preset")
                        }
                    }

                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            SectionLabel(text: "Speed ↔ Quality")
                            Spacer()
                            Text(qualityWord).font(.system(size: 10.5, weight: .semibold)).foregroundStyle(accent)
                        }
                        Slider(value: $settings.quality, in: 0...1)
                            .controlSize(.small)
                            .accessibilityLabel("Speed versus quality")
                        HStack {
                            Label("Faster", systemImage: "hare").labelStyle(.titleAndIcon)
                            Spacer()
                            Label("Better", systemImage: "sparkles").labelStyle(.titleAndIcon)
                        }
                        .font(.system(size: 10))
                        .foregroundStyle(Theme.textFaint)
                        Text(settings.preset.qualityHint)
                            .font(.system(size: 10.5))
                            .foregroundStyle(Theme.textDim)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .card()

                    VStack(alignment: .leading, spacing: 10) {
                        SectionLabel(text: "Export to")
                        Picker("", selection: $settings.outputMode) {
                            ForEach(OutputMode.allCases) { m in Text(m.title).tag(m) }
                        }
                        .pickerStyle(.radioGroup)
                        .labelsHidden()
                        .font(.system(size: 12))
                        if settings.outputMode == .subfolder {
                            HStack(spacing: 6) {
                                Image(systemName: "folder").foregroundStyle(Theme.textDim)
                                TextField("Folder name", text: $settings.subfolderName)
                                    .textFieldStyle(.roundedBorder)
                                    .font(.system(size: 12))
                            }
                            Text("When you export, a folder with this name is made next to each original file.")
                                .font(.system(size: 10.5)).foregroundStyle(Theme.textFaint)
                                .fixedSize(horizontal: false, vertical: true)
                        } else if settings.outputMode == .custom {
                            HStack {
                                Text(settings.outputFolder.isEmpty ? "No folder chosen yet" : (settings.outputFolder as NSString).abbreviatingWithTildeInPath)
                                    .font(Theme.mono(10.5))
                                    .foregroundStyle(settings.outputFolder.isEmpty ? Theme.warn : Theme.textDim)
                                    .lineLimit(1).truncationMode(.head)
                                    .helpIfTruncated(settings.outputFolder)
                                Spacer()
                                Button("Choose…") { model.chooseOutputFolder() }.buttonStyle(GhostButtonStyle())
                            }
                        }
                        Divider().overlay(Theme.stroke)
                        HStack {
                            Text("Units").font(.system(size: 12))
                            Spacer()
                            Picker("", selection: $settings.unit) {
                                ForEach(OutputUnit.allCases) { u in Text(u.title).tag(u) }
                            }
                            .labelsHidden().frame(width: 130)
                        }
                        .help("The unit your STL files were made in. 3D-printing files are almost always millimeters.")
                        Toggle("Replace existing files", isOn: $settings.replaceExisting)
                            .font(.system(size: 12))
                            .help("When off, the app asks before replacing a file that has the same name, and lets you rename the new one.")
                        Toggle("Split 3MF projects into parts", isOn: $settings.splitParts)
                            .font(.system(size: 12))
                            .disabled(!model.splitAvailable)
                            .help("Each part of a slicer project becomes its own STEP file (Project - Part.step), like “Split to parts” in the slicer. When off, all parts go into one STEP file.")
                            .onChange(of: settings.splitParts) { _, _ in model.applySplitDefault() }
                    }
                    .card()

                    DisclosureGroup(isExpanded: $showAdvanced) {
                        VStack(alignment: .leading, spacing: 12) {
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text("3MF projects").font(.system(size: 12))
                                    Spacer()
                                    Picker("", selection: $settings.plateLayout) {
                                        ForEach(PlateLayout.allCases) { l in Text(l.title).tag(l) }
                                    }
                                    .labelsHidden().frame(width: 150)
                                    .onChange(of: settings.plateLayout) { _, _ in model.applySplitDefault() }
                                }
                                Text(settings.plateLayout.help)
                                    .font(.system(size: 10.5)).foregroundStyle(Theme.textFaint)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            HStack {
                                Text("Mesh repair").font(.system(size: 12))
                                Spacer()
                                Picker("", selection: $settings.repair) {
                                    ForEach(RepairLevel.allCases) { r in Text(r.title).tag(r) }
                                }
                                .labelsHidden().frame(width: 120)
                            }
                            .help("Closes holes and fixes broken triangles before converting.")
                            Toggle("Remove tiny floating pieces", isOn: $settings.removeDebris).font(.system(size: 12))
                            Toggle("Custom fit tolerance", isOn: $settings.useCustomTolerance).font(.system(size: 12))
                                .help("How far the CAD surfaces may deviate from the mesh. Normally chosen automatically from the part size and the quality slider.")
                            if settings.useCustomTolerance {
                                HStack {
                                    TextField("", value: $settings.customTolerance, format: .number)
                                        .textFieldStyle(.roundedBorder).frame(width: 90)
                                    Text(settings.unit == .in ? "inches" : settings.unit.rawValue).font(.system(size: 11)).foregroundStyle(Theme.textDim)
                                }
                            }
                            HStack {
                                Text("Smooth surface limit").font(.system(size: 12))
                                Spacer()
                                Picker("", selection: $settings.maxFaces) {
                                    Text("None").tag(0)
                                    Text("1,000").tag(1000)
                                    Text("4,000").tag(4000)
                                    Text("10,000").tag(10000)
                                    Text("25,000").tag(25000)
                                }
                                .labelsHidden().frame(width: 120)
                            }
                            .help("The most surfaces the Smooth preset may create. Fewer means smaller files but less detail.")
                            HStack {
                                Text("Files at once").font(.system(size: 12))
                                Spacer()
                                Stepper("\(settings.parallelJobs)", value: $settings.parallelJobs, in: 1...6)
                                    .font(Theme.mono(11))
                            }
                            .help("How many files convert at the same time. Big meshes need a lot of memory; 1–2 is safest.")
                            Toggle("Convert as soon as files are added", isOn: $settings.convertOnDrop).font(.system(size: 12))
                            Toggle("Show in Finder after exporting", isOn: $settings.revealWhenDone).font(.system(size: 12))
                        }
                        .padding(.top, 8)
                    } label: {
                        SectionLabel(text: "Advanced")
                    }
                    .card()
                }
                .padding(16)
            }
            .scrollIndicators(.never)

            VStack(spacing: 8) {
                Divider().overlay(Theme.stroke)
                let n = model.pendingCount
                let toExport = model.unexportedJobs
                let exportFiles = toExport.reduce(0) { $0 + $1.resultFiles.count }
                Button {
                    model.convertAll()
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "bolt.fill")
                        Text(n > 0 ? "Convert \(n) file\(n == 1 ? "" : "s")" : "Convert")
                    }
                }
                .buttonStyle(AccentButtonStyle(accent: accent, prominent: n > 0 || exportFiles == 0))
                .disabled(n == 0)
                .keyboardShortcut(.return, modifiers: .command)
                .padding(.horizontal, 16)
                .help(n == 0 ? "Add files to convert" : "Convert all files that are ready (⌘↩)")
                if exportFiles > 0 {
                    Button {
                        model.requestExport(toExport)
                    } label: {
                        HStack(spacing: 8) {
                            Image(systemName: "square.and.arrow.up")
                            Text("Export \(exportFiles) STEP file\(exportFiles == 1 ? "" : "s")…")
                        }
                    }
                    .buttonStyle(AccentButtonStyle(accent: accent, prominent: n == 0))
                    .padding(.horizontal, 16)
                    .help("Choose where to save the converted files that haven't been exported yet (⇧⌘E)")
                }
                if n == 0 && !model.jobs.isEmpty && model.runningCount == 0 && !model.jobs.contains(where: { $0.status == .inspecting || $0.status == .queued }) {
                    let unreadable = model.jobs.filter { !$0.readable }.count
                    Text(unreadable > 0 ? "Done · \(unreadable) file\(unreadable == 1 ? "" : "s") couldn't be read"
                         : (exportFiles > 0 ? "Converted · look the results over, then export" : "Everything is converted"))
                        .font(.system(size: 10.5)).foregroundStyle(unreadable > 0 ? Theme.warn : Theme.textFaint)
                }
                if let j = model.selectedJob, j.status == .done {
                    Button { model.reconvert(j) } label: {
                        HStack(spacing: 6) { Image(systemName: "arrow.clockwise"); Text("Convert selected file again") }
                    }
                    .buttonStyle(AccentButtonStyle(accent: accent, prominent: false))
                    .padding(.horizontal, 16)
                    .help("Convert the selected file again with the current preset and settings")
                }
            }
            .padding(.bottom, 16)
        }
        .background(Theme.panel)
    }
}

struct PresetCard: View {
    @EnvironmentObject var settings: Settings
    let preset: Preset
    let selected: Bool
    @LocalState private var hover = false

    var body: some View {
        HStack(spacing: 10) {
            ZStack {
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(selected ? settings.accent.color.opacity(0.18) : Color.white.opacity(0.05))
                Image(systemName: preset.icon)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(selected ? settings.accent.color : Theme.textDim)
            }
            .frame(width: 32, height: 32)
            VStack(alignment: .leading, spacing: 2) {
                Text(preset.title).font(.system(size: 13, weight: .semibold))
                Text(preset.subtitle).font(.system(size: 10.5)).foregroundStyle(Theme.textDim)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
            if selected {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(settings.accent.color)
            }
        }
        .card(highlighted: selected || hover, accent: selected ? settings.accent.color : Theme.strokeStrong, padding: 10)
        .onHover { hover = $0 }
        .contentShape(Rectangle())
    }
}
