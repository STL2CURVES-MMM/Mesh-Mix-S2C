import AppKit
import SwiftUI

/// The export window: where the STEP files go, their names and what happens
/// when a file with the same name is already there.
struct ExportSheet: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var settings: Settings
    let request: ExportRequest

    @LocalState private var items: [ExportItem] = []
    @LocalState private var exporting = false
    /// Files still to check for name clashes, and those that are settled
    @LocalState private var pending: [ExportItem] = []
    @LocalState private var settled: [ExportItem] = []
    /// The name clash being asked about
    @LocalState private var clash: ExportItem?
    @LocalState private var newName = ""
    @LocalState private var failures: [ExportFailure] = []
    /// Redraws the "already exists" notes after the folder changes
    @LocalState private var tick = 0

    var accent: Color { settings.accent.color }
    var missingFolder: Bool { settings.outputMode == .custom && settings.outputFolder.isEmpty }

    var body: some View {
        ZStack {
            VStack(alignment: .leading, spacing: 16) {
                header
                fileList
                destination
                options
                if !failures.isEmpty { failureCard }
                footer
            }
            .padding(20)
            .disabled(clash != nil)
            if let c = clash { clashPrompt(c) }
        }
        .frame(width: 580)
        .background(Theme.panel)
        .foregroundStyle(Theme.text)
        .tint(accent)
        .preferredColorScheme(.dark)
        .onAppear { items = model.exportItems(for: request.jobs) }
        .onReceive(NotificationCenter.default.publisher(for: SelfTest.exportNow)) { _ in startExport() }
        .onReceive(NotificationCenter.default.publisher(for: SelfTest.renameNow)) { _ in renameClash() }
    }

    // MARK: parts

    var header: some View {
        HStack(spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: 9, style: .continuous).fill(accent.opacity(0.16))
                Image(systemName: "square.and.arrow.up").font(.system(size: 16, weight: .semibold)).foregroundStyle(accent)
            }
            .frame(width: 36, height: 36)
            VStack(alignment: .leading, spacing: 2) {
                Text(items.count == 1 ? "Export STEP File" : "Export \(items.count) STEP Files")
                    .font(.system(size: 16, weight: .bold))
                Text("Check the names and where the files go, then press Export.")
                    .font(.system(size: 11.5)).foregroundStyle(Theme.textDim)
            }
            Spacer()
        }
    }

    var fileList: some View {
        VStack(alignment: .leading, spacing: 6) {
            SectionLabel(text: items.count == 1 ? "File" : "Files")
            ScrollView {
                VStack(spacing: 6) {
                    ForEach($items) { $item in
                        fileRow($item)
                    }
                }
            }
            .frame(maxHeight: items.count > 3 ? 230 : nil)
            .fixedSize(horizontal: false, vertical: items.count <= 3)
        }
        .id(tick)
    }

    func fileRow(_ item: Binding<ExportItem>) -> some View {
        let dest = model.exportURL(for: item.wrappedValue)
        let exists = FileManager.default.fileExists(atPath: dest.path)
        let folder = (dest.deletingLastPathComponent().path as NSString).abbreviatingWithTildeInPath
        return VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: "doc").font(.system(size: 11)).foregroundStyle(Theme.textDim)
                TextField("File name", text: item.name)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 12))
                Text(".step").font(Theme.mono(11)).foregroundStyle(Theme.textDim)
                if exists {
                    Pill(text: settings.replaceExisting ? "Will be replaced" : "Name taken",
                         color: Theme.warn, icon: "exclamationmark")
                        .fixedSize()
                        .help(settings.replaceExisting
                              ? "A file with this name is already in the folder. It will be replaced."
                              : "A file with this name is already in the folder. You'll be asked whether to replace it or use another name.")
                }
            }
            Text("→ " + folder)
                .font(Theme.mono(10)).foregroundStyle(Theme.textFaint)
                .lineLimit(1).truncationMode(.head)
                .helpIfTruncated(dest.deletingLastPathComponent().path)
                .padding(.leading, 18)
        }
        .card(padding: 8)
    }

    var destination: some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionLabel(text: "Save to")
            Picker("", selection: $settings.outputMode) {
                ForEach(OutputMode.allCases) { m in Text(m.title).tag(m) }
            }
            .pickerStyle(.radioGroup)
            .labelsHidden()
            .font(.system(size: 12))
            .onChange(of: settings.outputMode) { _, _ in tick += 1 }
            if settings.outputMode == .subfolder {
                HStack(spacing: 6) {
                    Image(systemName: "folder").foregroundStyle(Theme.textDim)
                    TextField("Folder name", text: $settings.subfolderName)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(size: 12))
                        .frame(width: 200)
                        .onChange(of: settings.subfolderName) { _, _ in tick += 1 }
                    Text("next to each original file").font(.system(size: 11)).foregroundStyle(Theme.textFaint)
                }
            } else if settings.outputMode == .custom {
                HStack {
                    Text(missingFolder ? "No folder chosen yet" : (settings.outputFolder as NSString).abbreviatingWithTildeInPath)
                        .font(Theme.mono(10.5))
                        .foregroundStyle(missingFolder ? Theme.warn : Theme.textDim)
                        .lineLimit(1).truncationMode(.head)
                        .helpIfTruncated(settings.outputFolder)
                    Spacer()
                    Button("Choose…") { if model.chooseOutputFolder() { tick += 1; failures = [] } }
                        .buttonStyle(GhostButtonStyle())
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }

    var options: some View {
        VStack(alignment: .leading, spacing: 8) {
            Toggle("Replace files that already exist", isOn: $settings.replaceExisting)
                .font(.system(size: 12))
            Text(settings.replaceExisting
                 ? "A file with the same name in the folder is overwritten."
                 : "If a file with the same name is already there, you'll be asked whether to replace it or give the new file another name.")
                .font(.system(size: 10.5)).foregroundStyle(Theme.textFaint)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.leading, 20)
            Toggle("Show in Finder after exporting", isOn: $settings.revealWhenDone)
                .font(.system(size: 12))
        }
    }

    var failureCard: some View {
        let permission = failures.contains { $0.isPermission }
        return VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(failures.enumerated()), id: \.offset) { _, f in
                HStack(alignment: .top, spacing: 7) {
                    Image(systemName: "xmark.octagon.fill").font(.system(size: 11)).foregroundStyle(Theme.bad).padding(.top, 1)
                    Text(f.message).font(.system(size: 11.5)).fixedSize(horizontal: false, vertical: true)
                }
            }
            if permission {
                Text("To allow it, open Privacy Settings, choose Files & Folders, and turn on the folder under Manifold Mesh Mix. Or choose another folder to export to.")
                    .font(.system(size: 11)).foregroundStyle(Theme.textDim)
                    .fixedSize(horizontal: false, vertical: true)
                HStack {
                    Button("Choose Another Folder…") {
                        if model.chooseOutputFolder() { failures = []; tick += 1 }
                    }
                    .buttonStyle(GhostButtonStyle(tint: accent))
                    Button("Open Privacy Settings") {
                        if let u = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_FilesAndFolders") {
                            NSWorkspace.shared.open(u)
                        }
                    }
                    .buttonStyle(GhostButtonStyle())
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 10).fill(Theme.bad.opacity(0.08)))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Theme.bad.opacity(0.4)))
    }

    var footer: some View {
        HStack {
            if exporting {
                ProgressView().controlSize(.small)
                Text("Exporting…").font(.system(size: 11.5)).foregroundStyle(Theme.textDim)
            } else if missingFolder {
                Text("Choose a folder first").font(.system(size: 11.5)).foregroundStyle(Theme.warn)
            }
            Spacer()
            Button("Cancel") { model.exportRequest = nil }
                .keyboardShortcut(.cancelAction)
                .buttonStyle(GhostButtonStyle())
                .disabled(exporting)
            Button { startExport() } label: {
                Text(items.count == 1 ? "Export" : "Export \(items.count) Files").padding(.horizontal, 8)
            }
            .buttonStyle(AccentButtonStyle(accent: accent))
            .fixedSize()
            .keyboardShortcut(.defaultAction)
            .disabled(exporting || missingFolder || items.isEmpty || items.contains { $0.name.trimmingCharacters(in: .whitespaces).isEmpty })
        }
    }

    // MARK: name clashes

    /// "A file with this name exists" prompt: replace it, or rename the new one.
    func clashPrompt(_ item: ExportItem) -> some View {
        let dest = model.exportURL(for: item)
        let candidate = ExportItem(job: item.job, part: item.part, source: item.source, name: newName)
        let stillTaken = isTaken(candidate)
        let emptyName = newName.trimmingCharacters(in: .whitespaces).isEmpty
        return ZStack {
            Color.black.opacity(0.45)
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "doc.on.doc.fill").font(.system(size: 20)).foregroundStyle(Theme.warn)
                    VStack(alignment: .leading, spacing: 4) {
                        Text("“\(dest.lastPathComponent)” already exists")
                            .font(.system(size: 14, weight: .bold))
                            .lineLimit(2)
                            .fixedSize(horizontal: false, vertical: true)
                        Text("There is already a file with this name in “\(dest.deletingLastPathComponent().lastPathComponent)”. Replace it, or save the new file under another name?")
                            .font(.system(size: 11.5)).foregroundStyle(Theme.textDim)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text("New name").font(.system(size: 11, weight: .semibold)).foregroundStyle(Theme.textDim)
                    HStack(spacing: 6) {
                        TextField("New name", text: $newName)
                            .textFieldStyle(.roundedBorder)
                            .font(.system(size: 12))
                            .onSubmit { if !stillTaken && !emptyName { renameClash() } }
                        Text(".step").font(Theme.mono(11)).foregroundStyle(Theme.textDim)
                    }
                    if stillTaken && !emptyName {
                        Text("That name is taken too.").font(.system(size: 10.5)).foregroundStyle(Theme.warn)
                    }
                }
                HStack {
                    Button("Cancel Export") { cancelClash() }
                        .buttonStyle(GhostButtonStyle())
                    Spacer()
                    Button("Replace") { replaceClash() }
                        .buttonStyle(GhostButtonStyle(tint: Theme.bad))
                        .help("Overwrite the file that is already there")
                    Button { renameClash() } label: { Text("Save as New Name").padding(.horizontal, 6) }
                        .buttonStyle(AccentButtonStyle(accent: accent))
                        .fixedSize()
                        .disabled(stillTaken || emptyName)
                }
            }
            .padding(18)
            .frame(width: 440)
            .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Theme.card))
            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(Theme.strokeStrong))
            .shadow(color: .black.opacity(0.5), radius: 20)
        }
    }

    /// The file's destination is already used: by a file in the folder, or
    /// by a file earlier in this export.
    private func isTaken(_ item: ExportItem) -> Bool {
        let dest = model.exportURL(for: item)
        let key = dest.path.lowercased()
        if settled.contains(where: { model.exportURL(for: $0).path.lowercased() == key }) { return true }
        return FileManager.default.fileExists(atPath: dest.path)
    }

    /// "Part 2", "Part 3", … — the first name that's free.
    private func freeName(for item: ExportItem) -> String {
        let base = item.name
        var n = 2
        while true {
            let c = ExportItem(job: item.job, part: item.part, source: item.source, name: "\(base) \(n)")
            if !isTaken(c) { return c.name }
            n += 1
        }
    }

    // MARK: export flow

    private func startExport() {
        guard !exporting, !missingFolder else { return }
        failures = []
        settled = []
        pending = items
        nextClash()
    }

    /// Goes through the files; stops to ask when a name is taken.
    private func nextClash() {
        while let item = pending.first {
            let inFolder = FileManager.default.fileExists(atPath: model.exportURL(for: item).path)
            let inExport = settled.contains { model.exportURL(for: $0).path.lowercased() == model.exportURL(for: item).path.lowercased() }
            if inExport || (inFolder && !settings.replaceExisting) {
                newName = freeName(for: item)
                clash = item
                return
            }
            settled.append(item)
            pending.removeFirst()
        }
        runExport(settled)
    }

    private func replaceClash() {
        guard let item = clash else { return }
        // two files of this export with the same name: the later one wins
        settled.removeAll { model.exportURL(for: $0).path.lowercased() == model.exportURL(for: item).path.lowercased() }
        settled.append(item)
        pending.removeFirst()
        clash = nil
        nextClash()
    }

    private func renameClash() {
        guard var item = clash else { return }
        item.name = AppModel.safeFileName(newName)
        if let i = items.firstIndex(where: { $0.id == item.id }) { items[i].name = item.name }
        pending[0] = item
        clash = nil
        nextClash()
    }

    private func cancelClash() {
        clash = nil
        pending = []
        settled = []
    }

    private func runExport(_ list: [ExportItem]) {
        guard !list.isEmpty else { return }
        exporting = true
        model.export(list) { failed in
            exporting = false
            failures = failed
            tick += 1
            if failed.isEmpty {
                model.exportRequest = nil
            } else {
                // the others are saved; only the failed files are left to export
                let ids = Set(failed.map { $0.item.id })
                items = items.filter { ids.contains($0.id) }
            }
        }
    }
}
