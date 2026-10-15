import AppKit
import Foundation
import UniformTypeIdentifiers

/// A plain-text report about one file, for sending to the developer when
/// something went wrong: the app and system, the settings, what the mesh
/// check found, and the error with its technical details.
///
/// Nothing personal goes in: the file's folder, the home folder (which holds
/// the user name) and the temporary folder are replaced everywhere, so only
/// the file's own name is left.
enum BugReport {
    static let statusWords = ["fixed": "Fixed", "not_fixed": "Not fixed", "action": "Your choice", "note": "Note"]

    static let issuesURL = "https://github.com/\(Updater.repo)/issues"

    /// A folder path as it may appear in text: as is, standardized, with
    /// symlinks resolved, and with or without /private in front.
    private static func variants(_ path: String) -> Set<String> {
        var out = Set<String>()
        guard !path.isEmpty else { return out }
        let forms = [path, (path as NSString).standardizingPath, URL(fileURLWithPath: path).resolvingSymlinksInPath().path]
        for f in forms {
            var p = f
            while p.hasSuffix("/") { p.removeLast() }
            if p.count < 3 { continue }  // never replace "/" alone
            out.insert(p)
            if p.hasPrefix("/private/") {
                out.insert(String(p.dropFirst("/private".count)))
            } else if p.hasPrefix("/var/") || p.hasPrefix("/tmp/") || p == "/tmp" {
                out.insert("/private" + p)
            }
        }
        return out
    }

    /// Removes folder names that could identify the user.
    static func scrub(_ text: String, fileURL: URL) -> String {
        guard !text.isEmpty else { return text }
        let places: [(String, String)] = [(fileURL.deletingLastPathComponent().path, "<file folder>"),
                                          (NSTemporaryDirectory(), "<temp>"),
                                          (NSHomeDirectory(), "<home>")]
        var pairs: [(String, String)] = []
        for (path, label) in places {
            for v in variants(path) { pairs.append((v, label)) }
        }
        // longest first, so a folder inside the home folder wins over the home folder
        pairs.sort { $0.0.count > $1.0.count }
        var out = text
        for (v, label) in pairs {
            out = out.replacingOccurrences(of: v, with: label)
        }
        // a user name left in some other path (/Users/name/..., /home/name/...)
        // (names can have spaces: up to the next separator or quote)
        return out.replacingOccurrences(of: #"(/(?:home|Users)/)[^/"'\r\n]+"#, with: "$1<user>", options: .regularExpression)
    }

    /// "macOS 14.5, arm64"
    static var systemLine: String {
        let v = ProcessInfo.processInfo.operatingSystemVersion
        let version = v.patchVersion > 0 ? "\(v.majorVersion).\(v.minorVersion).\(v.patchVersion)"
                                         : "\(v.majorVersion).\(v.minorVersion)"
        return "macOS \(version), \(machine)"
    }

    /// The processor architecture (arm64, or x86_64 under Rosetta).
    private static var machine: String {
        var u = utsname()
        guard uname(&u) == 0 else { return "unknown processor" }
        let m = withUnsafeBytes(of: &u.machine) { raw -> String in
            String(decoding: raw.prefix(while: { $0 != 0 }), as: UTF8.self)
        }
        return m.isEmpty ? "unknown processor" : m
    }

    private static let numbers: NumberFormatter = {
        let f = NumberFormatter()
        f.locale = Locale(identifier: "en_US")
        f.numberStyle = .decimal
        f.usesGroupingSeparator = true
        f.groupingSeparator = ","
        f.groupingSize = 3
        f.maximumFractionDigits = 0
        return f
    }()

    /// 1234567 -> "1,234,567"
    private static func grouped(_ n: Int) -> String {
        numbers.string(from: NSNumber(value: n)) ?? String(n)
    }

    static func size(_ bytes: Int?) -> String {
        guard let bytes else { return "unknown size" }
        var n = Double(bytes)
        for unit in ["bytes", "KB", "MB", "GB"] {
            if n < 1024 || unit == "GB" {
                return unit == "bytes" ? "\(grouped(bytes)) bytes" : String(format: "%.1f", n) + " " + unit
            }
            n /= 1024
        }
        return ""
    }

    private static func maxFacesTitle(_ n: Int) -> String {
        n > 0 ? grouped(n) : "None"
    }

    /// The report for one job, as text.
    static func build(job: Job, settings s: Settings) -> String {
        var lines: [String] = []
        func add(_ line: String) { lines.append(line) }
        let df = DateFormatter()
        df.locale = Locale(identifier: "en_US_POSIX")
        df.dateFormat = "yyyy-MM-dd HH:mm"
        let installed = Bundle.main.bundleURL.pathExtension == "app"
        add("Manifold Mesh Mix - problem report")
        add(String(repeating: "=", count: 34))
        add("Created:   \(df.string(from: Date()))")
        add("App:       Manifold Mesh Mix \(Updater.currentVersion) (\(installed ? "installed app" : "development build"))")
        add("System:    \(systemLine)")
        add("Toolkit:   SwiftUI (native macOS app)")
        add("")
        add("File")
        add("  Name:      \(job.fileName)   (the folder isn't included)")
        add("  Size:      \(size(job.fileSize))")
        if let t = job.triangles {
            add("  Triangles: \(grouped(t))")
        }
        if let b = job.bodies {
            let open = job.openShells ?? 0
            add("  Pieces:    \(grouped(b))" + (open > 0 ? " (\(grouped(open)) not closed)" : ""))
        }
        if let p = job.parts, p != 0 {
            add("  Parts:     \(p)")
        }
        if let bb = job.bboxSize, !bb.isEmpty {
            add("  Dimensions: " + bb.map { String(format: "%.4g", $0) }.joined(separator: " x ") + " \(s.unit.rawValue)")
        }
        add("")
        // the settings the last conversion used (else the current ones)
        let run = job.runSettings ?? RunSettings(s, triangles: job.triangles)
        add("Settings" + (job.runSettings != nil ? " (of the last conversion)" : " (current)"))
        add("  Preset:        \(run.preset.title)")
        let percent = Int((run.quality * 100).rounded())
        add("  Quality:       \(run.preset == .auto && run.autoQuality ? "Auto, " : "")\(percent)%")
        add("  Mesh repair:   \(run.repair.title)")
        add("  Units:         \(run.unit.title)")
        add("  3MF projects:  \(run.plateLayout.title)")
        add("  Remove debris: \(run.removeDebris ? "yes" : "no")")
        add("  Merge overlapping pieces: \(job.mergeOverlaps ? "yes" : "no")")
        if !job.touching.isEmpty {
            add("  Touching parts: \(job.touchingChoice ?? "not asked yet")")
        }
        if run.useCustomTolerance && run.customTolerance > 0 {
            add("  Tolerance:     \(String(format: "%g", run.customTolerance)) \(run.unit.rawValue)")
        } else {
            add("  Tolerance:     automatic")
        }
        add("  Max faces:     \(maxFacesTitle(run.maxFaces))")
        add("  Split to parts: \(job.isSplit ? "yes" : "no")")
        add("")
        add("Mesh check")
        if let mc = job.meshCheck {
            let ready: String
            switch mc.ready {
            case .some(true): ready = "yes"
            case .some(false): ready = "no"
            case .none: ready = "not reported"
            }
            add("  Result: \(mc.title); ready for conversion: \(ready)")
            for i in mc.issues {
                let place = i.part.isEmpty ? "" : " [\(i.part)]"
                add("  - \(statusWords[i.status] ?? i.status)\(place): \(i.problem) -> \(i.result)")
            }
            for p in mc.problems {
                add("  ! Still wrong after fixing: \(p)")
            }
            if let e = job.mergeError {
                add("  ! Merging the overlapping pieces failed: \(e)")
            }
        } else {
            add("  (not reported)")
        }
        add("")
        add("Conversion")
        let status = job.status.title
        add("  Status: \(status)" + (!job.stage.isEmpty && job.stage != status ? " (\(job.stage))" : ""))
        if let r = job.result {
            add("  Preset used: \(r.presetTitle); faces \(grouped(r.faces)); solids \(r.solids); shells \(r.shells); "
                + "STEP \(size(r.fileSize)); \(String(format: "%.1f", r.seconds)) s")
            if let f = r.fidelity {
                add("  Compared with the mesh: \(f.summary(unit: job.resultUnit(s)))"
                    + (f.ok ? "" : " (doesn't follow it closely everywhere)"))
            }
            if r.checked {
                let repaired = r.repaired.isEmpty ? "" : " (repaired automatically: " + r.repaired.joined(separator: ", ") + ")"
                add("  STEP file read back and checked" + repaired)
            }
            for c in r.checks {
                add("  - \(c.level): \(c.message)")
            }
        }
        if let e = job.error {
            add("  What went wrong: \(e)")
            if let why = job.errorReason {
                add("  Why: \(why)")
            }
        }
        if !job.warnings.isEmpty {
            add("")
            add("Warnings")
            for w in job.warnings {
                add("  - \(w)")
            }
        }
        if var details = job.errorDetails, !details.isEmpty {
            while let last = details.last, last.isWhitespace { details.removeLast() }
            add("")
            add("Technical details")
            for ln in details.split(omittingEmptySubsequences: false, whereSeparator: { $0.isNewline }) {
                add("  \(ln)")
            }
        }
        if !job.log.isEmpty {
            add("")
            add("Log")
            for ln in job.log.suffix(400) {
                add("  \(ln)")
            }
        }
        add("")
        return scrub(lines.joined(separator: "\n"), fileURL: job.url)
    }

    static func defaultName(for job: Job) -> String {
        AppModel.safeFileName("Manifold Mesh Mix report - \(job.name)") + ".txt"
    }

    static func save(job: Job, settings: Settings, to url: URL) throws {
        try build(job: job, settings: settings).write(to: url, atomically: true, encoding: .utf8)
    }
}

extension AppModel {
    /// Saves a text report about this file to send with a bug report.
    func saveReport(for job: Job) {
        let p = NSSavePanel()
        p.message = "Save a report to send"
        p.allowedContentTypes = [.plainText]
        p.directoryURL = FileManager.default.homeDirectoryForCurrentUser
        p.nameFieldStringValue = BugReport.defaultName(for: job)
        p.canCreateDirectories = true
        guard p.runModal() == .OK, let chosen = p.url else { return }
        let url = chosen.pathExtension.lowercased() == "txt" ? chosen : chosen.appendingPathExtension("txt")
        do {
            try BugReport.save(job: job, settings: settings, to: url)
        } catch {
            let a = NSAlert()
            a.messageText = "The report couldn't be saved"
            a.informativeText = error.localizedDescription
            a.addButton(withTitle: "OK")
            a.runModal()
            return
        }
        let a = NSAlert()
        a.messageText = "The report was saved as “\(url.lastPathComponent)”"
        a.informativeText = "Send it with a short description of what happened, by opening an issue at \(BugReport.issuesURL) "
            + "(attach the file), or to whoever gave you the app. It has the file's name and its check results, but not "
            + "its folder or your user name, and not the mesh itself."
        a.addButton(withTitle: "OK")
        a.runModal()
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }
}
