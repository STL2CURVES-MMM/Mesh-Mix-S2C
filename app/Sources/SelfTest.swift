import AppKit
import SceneKit
import SwiftUI

/// Scripted UI walkthrough used during development.
///
///   MMM_SELFTEST=/path/to/output/dir
///   MMM_SELFTEST_FILES=/a.stl:/b.3mf          files to add and convert
///   MMM_SELFTEST_BIG=/big.stl                 optional file for the cancel test
///
/// The app drives itself through the main flows, saves a screenshot of its
/// window after every step and writes report.json, then quits.
@MainActor
enum SelfTest {
    nonisolated static var outputDir: URL? {
        ProcessInfo.processInfo.environment["MMM_SELFTEST"].map { URL(fileURLWithPath: $0) }
    }

    nonisolated static var isActive: Bool { outputDir != nil }

    /// Lets the walkthrough press buttons in the export window.
    nonisolated static let exportNow = Notification.Name("mmm.selftest.export")
    nonisolated static let renameNow = Notification.Name("mmm.selftest.rename")

    private static var log: [[String: Any]] = []
    private static var frame = NSRect(x: 80, y: 80, width: 1360, height: 860)
    private static var step = 0

    static func run(model: AppModel, settings: Settings, updater: Updater) {
        guard let dir = outputDir else { return }
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        Task { @MainActor in
            await walkthrough(model: model, settings: settings, dir: dir)
            // With MMM_UPDATE_URL: check for updates against it and show the window
            if ProcessInfo.processInfo.environment["MMM_UPDATE_URL"] != nil {
                await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in
                    updater.check { c.resume() }
                }
                updater.showWindow = true
                await sleep(1.0)
                shot("update-dialog", dir)
                var info: [String: Any] = ["state": updater.stateName, "error": updater.error]
                if let r = updater.release {
                    info["version"] = r.version
                    info["asset"] = r.assetName
                }
                info["can_install"] = [updater.installProblem == nil, updater.installProblem ?? ""] as [Any]
                note("update-check", info)
                updater.showWindow = false
                await sleep(0.5)
                // MMM_SELFTEST_INSTALL_UPDATE: download, check and install for real.
                // The app quits and the new version opens by itself, so the
                // report is written first.
                if ProcessInfo.processInfo.environment["MMM_SELFTEST_INSTALL_UPDATE"] != nil && updater.state == .available {
                    note("update-install-started", ["version": updater.release?.version ?? ""])
                    writeReport(dir)
                    updater.download()
                    _ = await wait(timeout: 600) { updater.state == .ready || updater.state == .failed }
                    if updater.state == .ready && updater.install() {
                        NSApp.terminate(nil)
                        return
                    }
                    note("update-install-failed", ["state": updater.stateName, "error": updater.error])
                }
            }
            writeReport(dir)
            NSApp.terminate(nil)
        }
    }

    private static func writeReport(_ dir: URL) {
        let data = try? JSONSerialization.data(withJSONObject: log, options: [.prettyPrinted])
        try? data?.write(to: dir.appendingPathComponent("report.json"))
    }

    private static func note(_ event: String, _ extra: [String: Any] = [:]) {
        var e = extra
        e["event"] = event
        e["t"] = Date().timeIntervalSince1970
        log.append(e)
    }

    private static func sleep(_ s: Double) async {
        try? await Task.sleep(nanoseconds: UInt64(s * 1_000_000_000))
    }

    private static func wait(timeout: Double, _ cond: () -> Bool) async -> Bool {
        let start = Date()
        while !cond() {
            if Date().timeIntervalSince(start) > timeout { return false }
            await sleep(0.25)
        }
        return true
    }

    private static func walkthrough(model: AppModel, settings: Settings, dir: URL) async {
        let env = ProcessInfo.processInfo.environment
        frame = NSRect(x: 80, y: 80, width: Double(env["MMM_SELFTEST_WIDTH"] ?? "") ?? 1360, height: 860)
        if let win = NSApp.windows.first(where: { $0.isVisible }) {
            win.setFrame(frame, display: true)
        }
        await sleep(1.0)
        // Start from known settings.
        settings.preset = .auto
        settings.quality = 0.7
        settings.outputMode = .subfolder
        settings.subfolderName = env["MMM_SELFTEST_SUBFOLDER"] ?? "SelfTest STEP"
        settings.replaceExisting = true
        if let r = env["MMM_SELFTEST_REPAIR"], let level = RepairLevel(rawValue: r) { settings.repair = level }
        let convertOnly = env["MMM_SELFTEST_CONVERT_ONLY"] != nil
        settings.parallelJobs = 2
        settings.convertOnDrop = false
        await sleep(0.5)
        shot("empty", dir)

        let files = (env["MMM_SELFTEST_FILES"] ?? "").split(separator: ":").map { URL(fileURLWithPath: String($0)) }
        model.add(urls: files)
        note("added", ["count": model.jobs.count])
        await sleep(0.3)
        shot("inspecting", dir)
        let inspected = await wait(timeout: 180) { !model.jobs.contains { $0.status == .inspecting } }
        note("inspected", ["ok": inspected, "jobs": jobSummary(model)])
        model.selection = model.jobs.first?.id
        await sleep(1.5)
        shot("ready", dir)

        if !convertOnly {
            for p in Preset.allCases {
                settings.preset = p
                await sleep(0.4)
                shot("preset-\(p.rawValue)", dir)
            }
            settings.preset = .auto
            settings.outputMode = .custom
            settings.outputFolder = ""
            await sleep(0.4)
            shot("custom-folder-missing", dir)
            settings.outputMode = .subfolder
            settings.quality = 0.15
            await sleep(0.3)
            shot("quality-fast", dir)
            settings.quality = 0.7
        }

        model.convertAll()
        note("convert-all")
        await sleep(2.0)
        shot("converting", dir)
        let done = await wait(timeout: 3600) {
            !model.jobs.contains { $0.status == .queued || $0.status == .converting || $0.status == .inspecting }
        }
        note("converted", ["ok": done, "jobs": jobSummary(model)])
        for (i, job) in model.jobs.enumerated() {
            model.selection = job.id
            await sleep(2.0)
            shot("result-\(i)-\(job.name)", dir)
            if job.isSplit {
                for k in job.partList.indices.dropFirst() {
                    job.selectedPart = k
                    await sleep(1.5)
                    shot("result-\(i)-\(job.name)-part\(k)", dir)
                }
                job.selectedPart = 0
            }
        }

        // Export window, then the export itself
        model.selection = model.jobs.first?.id
        model.requestExport(model.unexportedJobs)
        await sleep(1.2)
        shot("export-sheet", dir)
        NotificationCenter.default.post(name: exportNow, object: nil)
        let exported = await wait(timeout: 120) { model.exportRequest == nil }
        await sleep(1.0)
        note("exported", ["ok": exported, "jobs": jobSummary(model)])
        shot("exported", dir)

        if convertOnly {
            note("finished", ["jobs": jobSummary(model)])
            return
        }
        // Export again without "replace": the name clash question, then rename
        if let j = model.jobs.first(where: { $0.isExported }) {
            settings.replaceExisting = false
            model.requestExport([j])
            await sleep(1.0)
            shot("export-again-sheet", dir)
            NotificationCenter.default.post(name: exportNow, object: nil)
            await sleep(0.8)
            shot("export-name-clash", dir)
            NotificationCenter.default.post(name: renameNow, object: nil)
            _ = await wait(timeout: 60) { model.exportRequest == nil }
            await sleep(0.5)
            note("export-renamed", ["files": j.exportedFiles.map(\.path)])
            settings.replaceExisting = true
        }
        // Broken file
        let broken = FileManager.default.temporaryDirectory.appendingPathComponent("broken-selftest.stl")
        try? Data("this is not an stl file at all".utf8).write(to: broken)
        model.add(urls: [broken])
        _ = await wait(timeout: 60) { !model.jobs.contains { $0.status == .inspecting } }
        if let j = model.jobs.last { model.selection = j.id }
        await sleep(1.0)
        shot("broken-file", dir)
        note("broken", ["jobs": jobSummary(model)])

        // Cancel test
        if let big = env["MMM_SELFTEST_BIG"] {
            model.add(urls: [URL(fileURLWithPath: big)])
            _ = await wait(timeout: 300) { !model.jobs.contains { $0.status == .inspecting } }
            if let j = model.jobs.last {
                model.selection = j.id
                model.enqueue(j)
                await sleep(4.0)
                shot("big-converting", dir)
                model.cancel(j)
                _ = await wait(timeout: 30) { j.status != .converting }
                await sleep(0.8)
                shot("big-cancelled", dir)
                note("cancel", ["status": j.status.title])
            }
        }

        model.clearFinished()
        await sleep(0.6)
        shot("after-clear", dir)
        note("finished", ["jobs": jobSummary(model)])
    }

    private static func jobSummary(_ model: AppModel) -> [[String: Any]] {
        model.jobs.map { j in
            var d: [String: Any] = ["file": j.fileName, "status": j.status.title, "stage": j.stage]
            if let e = j.error { d["error"] = e }
            if let r = j.result {
                d["faces"] = r.faces
                d["valid"] = r.valid ?? NSNull()
                d["preset"] = r.preset
                d["seconds"] = r.seconds
                d["size"] = r.fileSize
            }
            if let t = j.triangles { d["triangles"] = t }
            if !j.exportedFiles.isEmpty { d["exported"] = j.exportedFiles.map(\.path) }
            if let r = j.result { d["checks"] = r.checks.map(\.message); d["checked"] = r.checked }
            if j.canSplit {
                d["split"] = j.split
                d["parts"] = j.partList.map { p -> [String: Any] in
                    var e: [String: Any] = ["name": p.name, "triangles": p.triangles]
                    if let o = p.exportedURL { e["exported"] = o.path }
                    if let r = p.result {
                        e["faces"] = r.faces
                        e["valid"] = r.valid ?? NSNull()
                        e["preset"] = r.preset
                        e["seconds"] = r.seconds
                    }
                    return e
                }
            }
            if !j.warnings.isEmpty { d["warnings"] = j.warnings }
            return d
        }
    }

    // MARK: screenshots

    static func shot(_ name: String, _ dir: URL) {
        step += 1
        guard let win = NSApp.windows.first(where: { $0.isVisible }), let view = win.contentView else { return }
        if win.frame.size != frame.size {
            // something else resized the window; screenshots should use the test size
            win.setFrame(frame, display: true)
        }
        view.layoutSubtreeIfNeeded()
        let bounds = view.bounds
        guard let rep = view.bitmapImageRepForCachingDisplay(in: bounds) else { return }
        view.cacheDisplay(in: bounds, to: rep)
        let base = String(format: "%02d-%@", step, name.replacingOccurrences(of: "/", with: "_"))
        if let png = rep.representation(using: .png, properties: [:]) {
            try? png.write(to: dir.appendingPathComponent(base + ".png"))
        }
        if let sheet = win.attachedSheet, let sv = sheet.contentView {
            sv.layoutSubtreeIfNeeded()
            if let srep = sv.bitmapImageRepForCachingDisplay(in: sv.bounds) {
                sv.cacheDisplay(in: sv.bounds, to: srep)
                if let png = srep.representation(using: .png, properties: [:]) {
                    try? png.write(to: dir.appendingPathComponent(base + "-sheet.png"))
                }
            }
        }
        // SceneKit renders through Metal and cacheDisplay leaves it blank;
        // save its own snapshot next to the UI capture.
        for (k, scn) in sceneViews(in: view).enumerated() where !scn.isHiddenOrHasHiddenAncestor {
            let img = scn.snapshot()
            if let tiff = img.tiffRepresentation, let bmp = NSBitmapImageRep(data: tiff),
               let png = bmp.representation(using: .png, properties: [:]) {
                try? png.write(to: dir.appendingPathComponent(base + "-3d\(k).png"))
            }
        }
    }

    private static func sceneViews(in v: NSView) -> [SCNView] {
        var out: [SCNView] = []
        if let s = v as? SCNView { out.append(s) }
        for sub in v.subviews { out.append(contentsOf: sceneViews(in: sub)) }
        return out
    }
}
