import AppKit
import Combine
import Foundation
import SwiftUI
import UniformTypeIdentifiers

enum Preset: String, CaseIterable, Identifiable, Codable {
    case auto, cad, smooth, precise

    var id: String { rawValue }

    var title: String {
        switch self {
        case .auto: return "Auto"
        case .cad: return "CAD"
        case .smooth: return "Smooth"
        case .precise: return "Precise"
        }
    }

    var subtitle: String {
        switch self {
        case .auto: return "Chooses CAD or Smooth for each part automatically"
        case .cad: return "For mechanical parts: flat faces, holes, cylinders, cones and fillets you can select and edit"
        case .smooth: return "For organic shapes, sculpts and scans: smooth surfaces that follow the shape"
        case .precise: return "Follows the mesh most closely: tighter CAD faces for mechanical parts, more detailed smooth surfaces for organic ones (slower)"
        }
    }

    var icon: String {
        switch self {
        case .auto: return "wand.and.stars"
        case .cad: return "cube.transparent"
        case .smooth: return "drop.fill"
        case .precise: return "scope"
        }
    }

    /// What the speed/quality slider changes for this preset, in plain words.
    var qualityHint: String {
        switch self {
        case .auto: return "Higher quality fits surfaces more tightly and adds detail, but takes longer."
        case .cad: return "Higher quality makes the faces follow the mesh more closely and adds detail to curved freeform areas, but takes longer."
        case .smooth: return "Higher quality uses more surfaces, so finer details are kept, but takes longer and makes bigger files."
        case .precise: return "Higher quality makes the fit even tighter, but takes longer."
        }
    }
}

enum OutputMode: String, CaseIterable, Identifiable {
    case subfolder, sameFolder, custom
    var id: String { rawValue }
    var title: String {
        switch self {
        case .subfolder: return "Subfolder next to the original"
        case .sameFolder: return "Same folder as the original"
        case .custom: return "A folder I choose"
        }
    }
}

enum PlateLayout: String, CaseIterable, Identifiable {
    case parts, plate
    var id: String { rawValue }
    var title: String {
        switch self {
        case .parts: return "One of each part"
        case .plate: return "Whole build plate"
        }
    }
    var help: String {
        switch self {
        case .parts: return "Each different part once, in its original orientation (copies on the plate are skipped)."
        case .plate: return "Every object exactly where it sits on the slicer's build plate."
        }
    }
}

enum RepairLevel: String, CaseIterable, Identifiable {
    case off, auto, aggressive
    var id: String { rawValue }
    var title: String {
        switch self {
        case .off: return "Off"
        case .auto: return "Automatic"
        case .aggressive: return "Thorough"
        }
    }
}

enum OutputUnit: String, CaseIterable, Identifiable {
    case mm, cm, m, `in`
    var id: String { rawValue }
    var title: String {
        switch self {
        case .mm: return "Millimeters"
        case .cm: return "Centimeters"
        case .m: return "Meters"
        case .in: return "Inches"
        }
    }
}

/// User settings, persisted in UserDefaults.
final class Settings: ObservableObject {
    private let d = Settings.store()

    /// The scripted self-test gets its own throwaway store so it never
    /// changes the user's settings.
    private static func store() -> UserDefaults {
        let suite = "com.manifold.meshmix.selftest"
        if ProcessInfo.processInfo.environment["MMM_SELFTEST"] != nil, let s = UserDefaults(suiteName: suite) {
            s.removePersistentDomain(forName: suite)
            return s
        }
        return .standard
    }

    @Published var preset: Preset { didSet { d.set(preset.rawValue, forKey: "preset") } }
    @Published var quality: Double { didSet { d.set(quality, forKey: "quality") } }
    /// With the Auto preset, pick the quality for each file (moving the
    /// slider turns it off: then every file uses the slider's quality)
    @Published var autoQuality: Bool { didSet { d.set(autoQuality, forKey: "autoQuality") } }
    @Published var repair: RepairLevel { didSet { d.set(repair.rawValue, forKey: "repair") } }
    @Published var removeDebris: Bool { didSet { d.set(removeDebris, forKey: "removeDebris") } }
    @Published var unit: OutputUnit { didSet { d.set(unit.rawValue, forKey: "unit") } }
    @Published var useCustomTolerance: Bool { didSet { d.set(useCustomTolerance, forKey: "useCustomTolerance") } }
    @Published var customTolerance: Double { didSet { d.set(customTolerance, forKey: "customTolerance") } }
    @Published var maxFaces: Int { didSet { d.set(maxFaces, forKey: "maxFaces") } }
    @Published var outputMode: OutputMode { didSet { d.set(outputMode.rawValue, forKey: "outputMode") } }
    @Published var subfolderName: String { didSet { d.set(subfolderName, forKey: "subfolderName") } }
    @Published var outputFolder: String { didSet { d.set(outputFolder, forKey: "outputFolder") } }
    @Published var plateLayout: PlateLayout { didSet { d.set(plateLayout.rawValue, forKey: "plateLayout") } }
    @Published var replaceExisting: Bool { didSet { d.set(replaceExisting, forKey: "replaceExisting") } }
    @Published var parallelJobs: Int { didSet { d.set(parallelJobs, forKey: "parallelJobs") } }
    @Published var accent: AccentChoice { didSet { d.set(accent.rawValue, forKey: "accent") } }
    @Published var convertOnDrop: Bool { didSet { d.set(convertOnDrop, forKey: "convertOnDrop") } }
    @Published var revealWhenDone: Bool { didSet { d.set(revealWhenDone, forKey: "revealWhenDone") } }
    /// 3MF projects: one STEP file per part (like "Split to parts" in a slicer)
    @Published var splitParts: Bool { didSet { d.set(splitParts, forKey: "splitParts") } }
    /// STEP view: give every CAD face its own color (off = one color, like a CAD program)
    @Published var colorFaces: Bool { didSet { d.set(colorFaces, forKey: "colorFaces") } }
    /// Look for a newer version once when the app opens (installing always needs a click)
    @Published var checkUpdates: Bool { didSet { d.set(checkUpdates, forKey: "checkUpdates") } }

    init() {
        preset = Preset(rawValue: d.string(forKey: "preset") ?? "") ?? .auto
        quality = d.object(forKey: "quality") as? Double ?? 0.7
        autoQuality = d.object(forKey: "autoQuality") as? Bool ?? true
        repair = RepairLevel(rawValue: d.string(forKey: "repair") ?? "") ?? .auto
        removeDebris = d.object(forKey: "removeDebris") as? Bool ?? true
        unit = OutputUnit(rawValue: d.string(forKey: "unit") ?? "") ?? .mm
        useCustomTolerance = d.object(forKey: "useCustomTolerance") as? Bool ?? false
        customTolerance = d.object(forKey: "customTolerance") as? Double ?? 0.05
        maxFaces = d.object(forKey: "maxFaces") as? Int ?? 0
        outputMode = OutputMode(rawValue: d.string(forKey: "outputMode") ?? "") ?? .subfolder
        subfolderName = d.string(forKey: "subfolderName") ?? "STEP Files"
        outputFolder = d.string(forKey: "outputFolder") ?? ""
        plateLayout = PlateLayout(rawValue: d.string(forKey: "plateLayout") ?? "") ?? .parts
        replaceExisting = d.object(forKey: "replaceExisting") as? Bool ?? false
        parallelJobs = d.object(forKey: "parallelJobs") as? Int ?? 2
        accent = AccentChoice(rawValue: d.string(forKey: "accent") ?? "") ?? .cyan
        convertOnDrop = d.object(forKey: "convertOnDrop") as? Bool ?? false
        revealWhenDone = d.object(forKey: "revealWhenDone") as? Bool ?? false
        splitParts = d.object(forKey: "splitParts") as? Bool ?? true
        colorFaces = d.object(forKey: "colorFaces") as? Bool ?? false
        checkUpdates = d.object(forKey: "checkUpdates") as? Bool ?? true
    }

    /// Auto quality (with the Auto preset): each file gets a quality from its
    /// triangle count. Small meshes are quick even near the top of the
    /// slider; very big ones get a faster setting, so they don't take hours
    /// or make huge STEP files. (triangles below, quality)
    static let autoQualitySteps: [(Int, Double)] = [(50_000, 0.9), (300_000, 0.8), (1_000_000, 0.7), (3_000_000, 0.6)]
    static let autoQualityBiggest = 0.5

    /// The quality Auto picks for a mesh with this many triangles.
    static func pickedQuality(triangles: Int) -> Double {
        for (limit, q) in autoQualitySteps where triangles < limit { return q }
        return autoQualityBiggest
    }

    var autoQualityOn: Bool { preset == .auto && autoQuality }

    /// The quality a file with this many triangles is converted at.
    func quality(for triangles: Int?) -> Double {
        if autoQualityOn, let t = triangles, t > 0 { return Settings.pickedQuality(triangles: t) }
        return quality
    }
}

/// The settings a conversion run used, kept with the job (for the "Auto
/// used" mark and bug reports).
struct RunSettings {
    let preset: Preset
    /// The quality the file was converted at (Auto quality picks one per file)
    let quality: Double
    let autoQuality: Bool
    let repair: RepairLevel
    let unit: OutputUnit
    let plateLayout: PlateLayout
    let removeDebris: Bool
    let useCustomTolerance: Bool
    let customTolerance: Double
    let maxFaces: Int

    /// The settings now, for a file with this many triangles.
    init(_ s: Settings, triangles: Int?) {
        preset = s.preset
        quality = s.quality(for: triangles)
        autoQuality = s.autoQuality
        repair = s.repair
        unit = s.unit
        plateLayout = s.plateLayout
        removeDebris = s.removeDebris
        useCustomTolerance = s.useCustomTolerance
        customTolerance = s.customTolerance
        maxFaces = s.maxFaces
    }
}

enum JobStatus: Equatable {
    case inspecting, ready, queued, converting, done, failed, cancelled

    var title: String {
        switch self {
        case .inspecting: return "Reading"
        case .ready: return "Ready"
        case .queued: return "Queued"
        case .converting: return "Converting"
        case .done: return "Done"
        case .failed: return "Failed"
        case .cancelled: return "Cancelled"
        }
    }

    var isBusy: Bool { self == .inspecting || self == .converting }
}

struct ConversionResult {
    var preset: String

    /// Display name of the preset the engine actually used ("mixed" when
    /// some parts of a file used CAD and others Smooth).
    var presetTitle: String {
        if preset == "mixed" { return "CAD + Smooth" }
        return Preset(rawValue: preset)?.title ?? preset.capitalized
    }
    var valid: Bool?
    var solids: Int
    var shells: Int
    var faces: Int
    var fileSize: Int
    var seconds: Double
    var volume: Double
    var meshVolume: Double
    var tolerance: Double
    /// The saved STEP file was opened again and checked
    var checked = false
    var checks: [CheckIssue] = []
    /// Repair steps the engine took because the first check found problems
    var repaired: [String] = []

    /// How closely the shape follows the mesh (nil: not measured)
    var fidelity: Fidelity?

    var hasErrors: Bool { checks.contains { $0.isError } }
    /// The shape doesn't follow the mesh everywhere
    var offMesh: Bool { fidelity.map { !$0.ok } ?? false }
    var hasIssues: Bool { !checks.isEmpty || valid == false || offMesh }
}

/// How closely the STEP shape follows the mesh (engine/mmm/fidelity.py).
struct Fidelity {
    var ok = true
    var tolerance = 0.0
    /// Share of the surface within twice the tolerance
    var within2 = 1.0
    var p99 = 0.0
    var largestGap = 0.0
    /// Share of the surface facing another way than the mesh
    var wrongWay = 0.0
    /// Judged as a noisy scan (its noise is allowed for)
    var scan = false
    /// The first check was close, so it was checked again
    var rechecked = false

    /// The engine's limit for wrongWay (engine/mmm/fidelity.py, Verdict)
    static let wrongWayMax = 0.005

    init?(event value: Any?) {
        guard let d = value as? [String: Any] else { return nil }
        ok = (d["ok"] as? Bool) != false
        tolerance = d["tolerance"] as? Double ?? 0
        within2 = d["within2"] as? Double ?? 1
        p99 = d["p99"] as? Double ?? 0
        largestGap = d["max"] as? Double ?? 0
        wrongWay = d["wrong_way"] as? Double ?? 0
        scan = (d["mesh_kind"] as? String) == "scan"
        rechecked = (d["rechecked"] as? Bool) == true
    }

    func summary(unit: String) -> String {
        guard tolerance > 0 else { return "the details weren't reported" }
        // rounded down, so a result that fails never shows 100.0%
        let share = (1000 * Swift.min(Swift.max(within2, 0), 1) + 1e-6).rounded(.down) / 10
        var text = "\(String(format: "%.1f", share))% of the surface is within \(String(format: "%.2g", 2 * tolerance)) \(unit) of the mesh; "
            + "the largest gap is \(String(format: "%.2g", largestGap)) \(unit)"
        if wrongWay > Self.wrongWayMax {
            text += "; \(String(format: "%.1f", 100 * wrongWay))% of it faces another way than the mesh (wrinkles or dents)"
        }
        if scan { text += " (the mesh is a noisy scan, so its noise is allowed for)" }
        if rechecked { text += " (checked twice, as the first check was close)" }
        return text
    }
}

extension Job {
    /// The unit this job's result is in: 3MF files carry their own (the
    /// engine saves them in mm), others the unit the last conversion used.
    func resultUnit(_ settings: Settings) -> String {
        if url.pathExtension.lowercased() == "3mf" { return "mm" }
        return (runSettings?.unit ?? settings.unit).rawValue
    }
}

/// One problem found when the saved STEP file was checked.
struct CheckIssue: Hashable {
    let code: String
    let level: String
    let message: String
    var isError: Bool { level == "error" }
}

/// One thing the mesh check found, and what it did about it.
struct MeshIssue: Hashable {
    let code: String
    let count: Int
    /// fixed / not_fixed / action / note
    let status: String
    let problem: String
    let result: String
    /// The 3MF part it is in ("" for the whole file)
    let part: String

    /// The problem with a capital first letter, to start a sentence.
    var problemCapitalized: String { problem.prefix(1).uppercased() + String(problem.dropFirst()) }
}

/// The engine's mesh check: what it found and fixed, and whether a second
/// check of the fixed mesh found it ready for conversion.
struct MeshCheck {
    /// ok / fixed / action / problems
    var status = "ok"
    var issues: [MeshIssue] = []
    /// nil when the engine didn't report it
    var ready: Bool?
    var problems: [String] = []

    static let titles = ["ok": "Mesh is clean", "fixed": "Mesh fixed",
                         "action": "Your choice needed", "problems": "Mesh has problems"]

    var title: String { Self.titles[status] ?? "Mesh checked" }

    /// Overlapping pieces the user can choose to merge.
    var hasOverlaps: Bool { issues.contains { $0.code == "overlaps" && $0.status == "action" } }

    var fixedAnything: Bool { issues.contains { $0.status == "fixed" } }

    /// The user chose to keep touching parts separate: the engine reports
    /// that as a note (the same words as engine/mmm/meshcheck.py).
    mutating func keptPartsSeparate() {
        issues = issues.map { i in
            guard i.code == "touching_parts" else { return i }
            return MeshIssue(code: i.code, count: i.count, status: "note", problem: i.problem,
                             result: "Kept as separate parts, as you chose. Use “Merge Touching Parts” to join them into one solid.",
                             part: i.part)
        }
        let st = Set(issues.map(\.status))
        status = st.contains("not_fixed") ? "problems" : st.contains("action") ? "action" : st.contains("fixed") ? "fixed" : "ok"
    }

    /// The engine's "mesh_check" JSON (nil when it isn't there).
    init?(event value: Any?) {
        guard let d = value as? [String: Any] else { return nil }
        if let s = d["status"] as? String, !s.isEmpty { status = s }
        for case let i as [String: Any] in (d["issues"] as? [Any] ?? []) {
            issues.append(MeshIssue(code: i["code"] as? String ?? "",
                                    count: i["count"] as? Int ?? 0,
                                    status: i["status"] as? String ?? "note",
                                    problem: i["problem"] as? String ?? "",
                                    result: i["result"] as? String ?? "",
                                    part: i["part"] as? String ?? ""))
        }
        if let v = d["verified"] as? [String: Any] {
            ready = v["ready"] as? Bool
            problems = (v["problems"] as? [Any] ?? []).compactMap { $0 as? String }
        }
    }
}

extension ConversionResult {
    init(event ev: [String: Any]) {
        self.init(
            preset: ev["preset"] as? String ?? "",
            valid: ev["valid"] as? Bool,
            solids: ev["solids"] as? Int ?? 0,
            shells: ev["shells"] as? Int ?? 0,
            faces: ev["faces"] as? Int ?? 0,
            fileSize: ev["file_size"] as? Int ?? 0,
            seconds: ev["seconds"] as? Double ?? 0,
            volume: ev["volume"] as? Double ?? 0,
            meshVolume: ev["mesh_volume"] as? Double ?? 0,
            tolerance: ev["tolerance"] as? Double ?? 0
        )
        checked = ev["checked"] as? Bool ?? false
        repaired = ev["repaired"] as? [String] ?? []
        fidelity = Fidelity(event: ev["fidelity"])
        checks = (ev["checks"] as? [[String: Any]] ?? []).map {
            CheckIssue(code: $0["code"] as? String ?? "", level: $0["level"] as? String ?? "warning",
                       message: $0["message"] as? String ?? "")
        }
    }
}

/// One part of a 3MF project (a separate STL in the slicer).
struct JobPart: Identifiable {
    let index: Int
    let name: String
    let triangles: Int
    let bboxSize: [Double]?
    var inputPreview: URL?
    var outputPreview: URL?
    /// The converted STEP file, kept in the app's temporary folder until it is exported
    var outputURL: URL?
    /// Where the user exported it
    var exportedURL: URL?
    var result: ConversionResult?
    var id: Int { index }
}

/// The heads-up for very detailed conversions. At the top of the quality
/// slider the engine keeps the fine detail of dense meshes
/// (engine/mmm/smooth.py, fine_cage_budget): the number of surfaces grows
/// with the mesh, which makes much bigger STEP files and longer conversions.
/// The numbers mirror the engine's (and desktop/mmm_app/detail.py).
enum FineDetail {
    static let start = 0.85
    static let trianglesPerCageVertex = 43.0
    static let maxCage = 60000.0
    static let kbPerPatch = 0.65        // STEP file size per surface patch (measured)
    static let normalPatches = 16000    // about the most one piece gets without fine detail
    static let warnRatio = 3

    /// Surface patches for one piece of this many triangles (0 = fine detail off).
    static func patches(triangles: Int, quality: Double) -> Int {
        let q = min(max(quality, 0), 1)
        guard q > start, triangles > 0 else { return 0 }
        let c = pow(10.0, (q - 1) / (1 - start)) / trianglesPerCageVertex
        return 4 * Int(min(maxCage, Double(triangles) * c))
    }

    /// Megabytes when this file will be far bigger and slower than usual (an
    /// upper bound: CAD-like parts and small pieces don't use fine detail).
    static func estimateMB(triangles: Int?, quality: Double, preset: Preset, maxFaces: Int) -> Double? {
        guard let t = triangles, preset != .cad, maxFaces == 0 else { return nil }
        let n = patches(triangles: t, quality: quality)
        guard n >= warnRatio * normalPatches else { return nil }
        return Double(n) * kbPerPatch / 1000
    }

    /// quality: each job's quality (Auto quality picks one per file).
    static func warning(jobs: [Job], quality: (Job) -> Double, preset: Preset, maxFaces: Int) -> String? {
        var hits: [(mb: Double, job: Job)] = []
        for j in jobs {
            if let mb = estimateMB(triangles: j.triangles, quality: quality(j), preset: preset, maxFaces: maxFaces) {
                hits.append((mb: mb, job: j))
            }
        }
        hits.sort { $0.mb > $1.mb }
        guard let top = hits.first, let t = top.job.triangles else { return nil }
        let mb = top.mb, job = top.job
        let millions = Double(t) / 1e6
        let tris = millions >= 1 ? "\(millions.formatted(.number.precision(.fractionLength(1)))) million" : t.formatted()
        let size = Int(mb.rounded()).formatted()
        var text = "Best quality keeps the fine surface detail of dense meshes. “\(job.fileName)” has \(tris) triangles, "
        text += "so its STEP file can grow to about \(size) MB, several times the usual size, and converting it takes a lot longer."
        let others = hits.count - 1
        if others > 0 { text += " \(others) more file\(others == 1 ? " is" : "s are") very dense too." }
        return text + " Move the slider a little to the left for a smaller, faster file."
    }
}

final class Job: ObservableObject, Identifiable {
    let id = UUID()
    let url: URL
    @Published var status: JobStatus = .inspecting
    @Published var stage: String = ""
    @Published var progress: Double = 0
    @Published var triangles: Int?
    @Published var bodies: Int?
    @Published var openShells: Int?
    @Published var parts: Int?
    /// false when the file could not even be read (no point retrying)
    @Published var readable = true
    @Published var bboxSize: [Double]?
    @Published var result: ConversionResult?
    @Published var error: String?
    /// Why it went wrong, in plain words, and the technical details for a
    /// bug report (from the engine's error event)
    @Published var errorReason: String?
    @Published var errorDetails: String?
    /// What the mesh check found, and whether overlapping pieces are merged
    /// into one solid (the user's choice, per file)
    @Published var meshCheck: MeshCheck?
    @Published var mergeOverlaps = false
    @Published var mergeError: String?
    /// 3MF parts that touch each other (the part names of each group), and
    /// what to do with them: nil (not asked yet), "split" or "merge"
    @Published var touching: [[String]] = []
    @Published var touchingChoice: String?
    /// Convert as soon as it has been read again (the choice came first)
    var convertAfterRead = false
    /// The settings of the last conversion run
    var runSettings: RunSettings?
    /// Where "Save Fixed Mesh" last saved this file's fixed mesh
    @Published var savedFixedMesh: URL?
    /// The fixed mesh is being saved right now
    @Published var savingFixedMesh = false
    /// Read again after a change (no automatic conversion then)
    var reinspecting = false
    /// Size of the file in bytes (for bug reports)
    var fileSize: Int?
    @Published var warnings: [String] = []
    @Published var log: [String] = []
    @Published var inputPreview: URL?
    @Published var outputPreview: URL?
    /// The converted STEP file, kept in the app's temporary folder until it is exported
    @Published var outputURL: URL?
    /// Where the user exported it
    @Published var exportedURL: URL?
    @Published var usedPreset: Preset?
    /// Parts of a 3MF project (empty for plain mesh files)
    @Published var partList: [JobPart] = []
    /// Save each part as its own STEP file
    @Published var split = true
    /// Selected tab when the parts are split
    @Published var selectedPart = 0
    var task: EngineTask?
    var startedAt: Date?

    init(url: URL) { self.url = url }

    var canSplit: Bool { partList.count > 1 }
    var isSplit: Bool { split && partList.count > 1 }
    var currentPart: JobPart? {
        isSplit && partList.indices.contains(selectedPart) ? partList[selectedPart] : nil
    }
    /// The converted STEP files waiting in the temporary folder (part index for split 3MFs)
    var resultFiles: [(source: URL, part: Int?)] {
        if isSplit {
            return partList.indices.compactMap { i in partList[i].outputURL.map { ($0, i) } }
        }
        return outputURL.map { [($0, nil)] } ?? []
    }
    /// Every STEP file the user exported from this job
    var exportedFiles: [URL] {
        isSplit ? partList.compactMap { $0.exportedURL } : (exportedURL.map { [$0] } ?? [])
    }
    var isExported: Bool { !exportedFiles.isEmpty }
    /// Converted, but not saved anywhere yet
    var needsExport: Bool { status == .done && !resultFiles.isEmpty && !isExported }

    /// Drops the results of a previous conversion and returns the temporary
    /// files to delete (exported files stay where they are).
    func clearResults() -> [URL] {
        var old: [URL] = []
        if let o = outputPreview { old.append(o) }
        if let o = outputURL { old.append(o) }
        for i in partList.indices {
            if let o = partList[i].outputPreview { old.append(o) }
            if let o = partList[i].outputURL { old.append(o) }
            partList[i].outputPreview = nil
            partList[i].outputURL = nil
            partList[i].exportedURL = nil
            partList[i].result = nil
        }
        result = nil
        outputPreview = nil
        outputURL = nil
        exportedURL = nil
        return old
    }

    var name: String { url.deletingPathExtension().lastPathComponent }

    var canConvert: Bool {
        readable && (status == .ready || status == .failed || status == .cancelled)
    }
    var fileName: String { url.lastPathComponent }
}

final class AppModel: ObservableObject {
    @Published var jobs: [Job] = []
    @Published var selection: UUID?
    @Published var engineMissing = false
    /// Open export window
    @Published var exportRequest: ExportRequest?
    let settings: Settings
    /// Views that depend on job states (Stop all, Convert N files, …) observe
    /// the model, so job status changes are forwarded to it.
    private var jobWatchers: [UUID: AnyCancellable] = [:]

    private func watch(_ job: Job) {
        jobWatchers[job.id] = job.$status
            .merge(with: job.$readable.map { _ in job.status })
            .merge(with: job.$exportedURL.map { _ in job.status })
            .dropFirst()
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.objectWillChange.send() }
    }
    private let previewDir: URL
    /// Engines saving a fixed mesh ("Save Fixed Mesh")
    private var fixTasks: [EngineTask] = []
    /// Files waiting to be asked about their touching parts (one at a time)
    private var touchWaiting: [Job] = []
    private var stampCount = 0

    /// A new part for temporary file names every time (the preview cache
    /// goes by file name).
    private func nextStamp() -> String {
        stampCount += 1
        return "\(Int(Date().timeIntervalSince1970))-\(stampCount)"
    }

    static let acceptedExtensions: Set<String> = ["stl", "obj", "ply", "off", "3mf", "glb", "gltf"]

    init(settings: Settings) {
        self.settings = settings
        previewDir = FileManager.default.temporaryDirectory.appendingPathComponent("ManifoldMeshMix-\(ProcessInfo.processInfo.processIdentifier)")
        try? FileManager.default.createDirectory(at: previewDir, withIntermediateDirectories: true)
        engineMissing = EngineLocator.command() == nil
        DispatchQueue.global(qos: .utility).async { Self.removeStalePreviewDirs() }
    }

    // MARK: preview files (3D previews live in a temporary folder)

    /// Deletes this session's preview folder (called when the app quits).
    func cleanUpPreviews() {
        try? FileManager.default.removeItem(at: previewDir)
    }

    private func discardPreviews(_ urls: [URL?]) {
        for case let u? in urls { try? FileManager.default.removeItem(at: u) }
    }

    /// Preview folders of earlier sessions that didn't quit normally.
    private static func removeStalePreviewDirs() {
        let fm = FileManager.default
        guard let items = try? fm.contentsOfDirectory(at: fm.temporaryDirectory, includingPropertiesForKeys: nil) else { return }
        let prefix = "ManifoldMeshMix-"
        for u in items where u.lastPathComponent.hasPrefix(prefix) {
            guard let pid = Int32(u.lastPathComponent.dropFirst(prefix.count)) else { continue }
            if pid != ProcessInfo.processInfo.processIdentifier && kill(pid, 0) != 0 {
                try? fm.removeItem(at: u)
            }
        }
    }

    var selectedJob: Job? { jobs.first { $0.id == selection } }

    var convertibleCount: Int {
        jobs.filter { [.ready, .failed, .cancelled, .done].contains($0.status) }.count
    }

    var pendingCount: Int { jobs.filter { $0.canConvert }.count }

    var runningCount: Int { jobs.filter { $0.status == .converting }.count }

    // MARK: adding files

    func add(urls: [URL]) {
        var files: [URL] = []
        let fm = FileManager.default
        for u in urls {
            var isDir: ObjCBool = false
            if fm.fileExists(atPath: u.path, isDirectory: &isDir), isDir.boolValue {
                if let en = fm.enumerator(at: u, includingPropertiesForKeys: nil, options: [.skipsHiddenFiles]) {
                    for case let f as URL in en where Self.acceptedExtensions.contains(f.pathExtension.lowercased()) {
                        files.append(f)
                    }
                }
            } else if Self.acceptedExtensions.contains(u.pathExtension.lowercased()) {
                files.append(u)
            }
        }
        var firstNew: Job?
        for f in files {
            if jobs.contains(where: { $0.url.standardizedFileURL == f.standardizedFileURL && $0.status != .done }) { continue }
            let job = Job(url: f)
            job.fileSize = (try? f.resourceValues(forKeys: [.fileSizeKey]))?.fileSize
            watch(job)
            jobs.append(job)
            if firstNew == nil { firstNew = job }
            inspect(job)
        }
        if let j = firstNew { selection = j.id }
    }

    func openPanel() {
        let p = NSOpenPanel()
        p.allowsMultipleSelection = true
        p.canChooseDirectories = true
        p.canChooseFiles = true
        p.allowedContentTypes = Self.acceptedExtensions.compactMap { UTType(filenameExtension: $0) }
        p.message = "Choose mesh files (STL, 3MF, OBJ, PLY) or folders to convert"
        if p.runModal() == .OK { add(urls: p.urls) }
    }

    @discardableResult
    func chooseOutputFolder() -> Bool {
        let p = NSOpenPanel()
        p.canChooseDirectories = true
        p.canChooseFiles = false
        p.canCreateDirectories = true
        p.prompt = "Use Folder"
        p.message = "Choose the folder to export STEP files to"
        if p.runModal() == .OK, let u = p.url {
            settings.outputFolder = u.path
            settings.outputMode = .custom
            return true
        }
        return false
    }

    // MARK: inspect

    private func inspect(_ job: Job) {
        // a new name every time: the preview cache goes by file name, and
        // merging overlapping pieces reads the file again
        let stamp = nextStamp()
        let prev = previewDir.appendingPathComponent("\(job.id.uuidString)-in-\(stamp).mmmp")
        let partDir = previewDir.appendingPathComponent("\(job.id.uuidString)-parts-\(stamp)", isDirectory: true)
        let s = settings
        var args = ["inspect", job.url.path, "--json", "--preview", prev.path, "--part-previews", partDir.path,
                    "--repair", s.repair.rawValue, "--layout", s.plateLayout.rawValue,
                    "--touching-parts", job.touchingChoice ?? "ask"]
        if !s.removeDebris { args.append("--keep-debris") }
        if job.mergeOverlaps { args.append("--merge-overlaps") }
        guard let t = EngineTask(arguments: args) else {
            job.status = .failed
            job.error = AppModel.engineMissingMessage
            engineMissing = true
            return
        }
        job.task = t
        job.stage = ""
        job.progress = 0
        t.onEvent = { [weak self, weak job] ev in
            guard let self, let job else { return }
            if ev["event"] as? String == "stage" {
                // what the mesh check is doing right now
                job.stage = ev["stage"] as? String ?? ""
                if let p = ev["progress"] as? Double { job.progress = Swift.max(job.progress, p) }
            } else if ev["event"] as? String == "progress" {
                if let p = ev["progress"] as? Double { job.progress = Swift.max(job.progress, p) }
            } else if ev["event"] as? String == "result" {
                job.triangles = ev["triangles"] as? Int ?? ev["input_triangles"] as? Int
                job.bodies = ev["bodies"] as? Int
                job.openShells = ev["open_shells"] as? Int
                job.parts = ev["parts"] as? Int
                if let bb = ev["bbox"] as? [[Double]], bb.count == 2 {
                    job.bboxSize = zip(bb[1], bb[0]).map { $0 - $1 }
                }
                job.meshCheck = MeshCheck(event: ev["mesh_check"])
                // groups of 3MF parts that touch each other (their names)
                job.touching = (ev["touching"] as? [[String: Any]] ?? []).compactMap { g in
                    (g["names"] as? [Any]).map { $0.map { "\($0)" } }
                }
                if let list = ev["part_list"] as? [[String: Any]], list.count > 1 {
                    job.partList = list.enumerated().map { k, e in
                        var size: [Double]?
                        if let bb = e["bbox"] as? [[Double]], bb.count == 2 { size = zip(bb[1], bb[0]).map { $0 - $1 } }
                        return JobPart(index: e["index"] as? Int ?? k,
                                       name: e["name"] as? String ?? "Part \(k + 1)",
                                       triangles: e["triangles"] as? Int ?? 0,
                                       bboxSize: size,
                                       inputPreview: (e["preview"] as? String).map { URL(fileURLWithPath: $0) })
                    }
                    job.split = self.settings.splitParts && self.settings.plateLayout == .parts
                    job.selectedPart = 0
                }
            } else if ev["event"] as? String == "error" {
                AppModel.takeError(job, ev)
            }
        }
        t.onExit = { [weak self, weak job] status, _ in
            guard let self else { return }
            guard let job, self.jobs.contains(where: { $0 === job }) else {
                // removed from the list while it was being read
                self.discardPreviews([prev, partDir])
                return
            }
            job.task = nil
            job.stage = ""
            job.progress = 0
            if status == 0 && !t.cancelled {
                job.inputPreview = prev
                job.status = .ready
                if !job.touching.isEmpty && job.touchingChoice == nil {
                    // ask whether to keep the touching parts separate (then convert)
                    self.askTouching(job)
                } else if job.convertAfterRead
                            || (self.settings.convertOnDrop && job.mergeError == nil && !job.reinspecting) {
                    // not converted on its own after a merge failed or when read again
                    self.enqueue(job)
                }
                job.convertAfterRead = false
                job.reinspecting = false
            } else if job.mergeOverlaps && !t.cancelled {
                // merging went wrong: read the file again the usual way
                job.mergeOverlaps = false
                job.mergeError = job.error ?? "The overlapping pieces couldn't be merged."
                job.error = nil
                job.errorReason = nil
                job.errorDetails = nil
                self.discardPreviews([prev, partDir])
                job.status = .inspecting
                self.inspect(job)
            } else {
                job.reinspecting = false
                job.convertAfterRead = false
                job.status = .failed
                job.readable = false
                if job.error == nil {
                    job.error = t.signaled && status == SIGKILL
                        ? "macOS stopped reading this file, most likely because it is too big for the free memory. Close other apps and try again."
                        : "This file couldn't be read. It may be damaged, or not a 3D mesh."
                }
            }
        }
        do { try t.start() } catch {
            job.task = nil
            job.reinspecting = false
            job.convertAfterRead = false
            job.status = .failed
            job.error = "The converter couldn't start (\(error.localizedDescription)). Try reinstalling Manifold Mesh Mix."
        }
    }

    /// Joins the pieces of the file that overlap each other into one solid
    /// (or keeps them separate again). The file is read and checked again,
    /// and a finished file goes back to Ready.
    func setMergeOverlaps(_ job: Job, _ on: Bool) {
        guard job.mergeOverlaps != on, job.readable, !job.status.isBusy, job.status != .queued else { return }
        if job.needsExport && !confirmDiscard(count: 1, action: "Change") { return }
        job.mergeOverlaps = on
        job.mergeError = nil
        reread(job)
    }

    /// Asks (one file at a time) whether parts that touch each other stay
    /// separate or are merged into one solid.
    private func askTouching(_ job: Job) {
        touchWaiting.append(job)
        if touchWaiting.count == 1 {
            DispatchQueue.main.async { [weak self] in self?.askNextTouching() }
        }
    }

    private func askNextTouching() {
        // files read while the question is open join the queue
        while let job = touchWaiting.first {
            if jobs.contains(where: { $0 === job }) && job.touchingChoice == nil && job.status == .ready {
                let choice = SelfTest.isActive ? "split" : askTouchingAlert(job)
                setTouchingChoice(job, choice, convert: settings.convertOnDrop)
            }
            touchWaiting.removeFirst()
        }
    }

    /// "split" or "merge"
    private func askTouchingAlert(_ job: Job) -> String {
        let groups = job.touching.map { $0.joined(separator: ", ") }.joined(separator: "; ")
        let separate = splitAvailable ? "one STEP file each" : "separate solids in one STEP file"
        let a = NSAlert()
        a.messageText = "Parts that touch"
        a.informativeText = "In “\(job.fileName)”, these parts touch each other: \(groups).\n\n"
            + "Keep them as separate parts (\(separate)), or merge them into one solid? "
            + "You can change this later in the Mesh view."
        // the first button is the default (Return)
        a.addButton(withTitle: "Split to Parts")
        a.addButton(withTitle: "Merge Into One")
        return a.runModal() == .alertSecondButtonReturn ? "merge" : "split"
    }

    /// Keeps parts that touch each other separate ("split") or merges them
    /// into one solid ("merge", the file is read again). A finished file
    /// goes back to Ready.
    func setTouchingChoice(_ job: Job, _ choice: String, convert: Bool = false) {
        guard choice == "split" || choice == "merge", !job.status.isBusy, job.status != .queued else { return }
        let before = job.touchingChoice
        if before != nil && before != choice && job.needsExport && !confirmDiscard(count: 1, action: "Change") { return }
        job.touchingChoice = choice
        if choice == "split" {
            if before == "merge" {
                reread(job, convert: convert)
                return
            }
            if splitAvailable && job.partList.count > 1 { job.split = true }
            job.meshCheck?.keptPartsSeparate()
            if convert { enqueue(job) }
            return
        }
        if before != "merge" { reread(job, convert: convert) }
    }

    /// Reads the file again with the current choices (results are dropped).
    private func reread(_ job: Job, convert: Bool = false) {
        discardPreviews(job.clearResults())
        // the old previews of the file and its parts (and their folder)
        var old: [URL?] = [job.inputPreview] + job.partList.map { $0.inputPreview }
        let partsPrefix = "\(job.id.uuidString)-parts-"
        for p in job.partList {
            if let dir = p.inputPreview?.deletingLastPathComponent(), dir.lastPathComponent.hasPrefix(partsPrefix),
               !old.contains(dir) {
                old.append(dir)
            }
        }
        discardPreviews(old)
        job.inputPreview = nil
        job.partList = []
        job.selectedPart = 0
        job.error = nil
        job.errorReason = nil
        job.errorDetails = nil
        job.stage = ""
        job.reinspecting = true
        job.convertAfterRead = convert
        job.status = .inspecting
        inspect(job)
    }

    /// Mesh repair, Remove debris or the 3MF layout changed: files that
    /// aren't converted yet are checked again, so the Mesh view shows what
    /// will happen.
    func recheck() {
        for j in jobs where j.readable && j.status == .ready && j.task == nil { reread(j) }
    }

    /// Checks and fixes the mesh the same way the conversion does and saves
    /// the fixed copy (.stl or .3mf). completion(error message or nil) is
    /// called on the main thread.
    func saveFixedMesh(_ job: Job, to dest: URL, completion: @escaping (String?) -> Void) {
        let s = settings
        var args = ["fix", job.url.path, "-o", dest.path, "--json",
                    "--repair", s.repair.rawValue,
                    "--layout", s.plateLayout.rawValue]
        if !s.removeDebris { args.append("--keep-debris") }
        if job.mergeOverlaps { args.append("--merge-overlaps") }
        if !job.touching.isEmpty { args += ["--touching-parts", job.touchingChoice ?? "split"] }
        guard let t = EngineTask(arguments: args) else {
            completion(AppModel.engineMissingMessage)
            return
        }
        var failure: String?
        var gotResult = false
        t.onEvent = { ev in
            switch ev["event"] as? String {
            case "error": failure = AppModel.friendly(ev["message"] as? String)
            case "result": gotResult = true
            default: break
            }
        }
        t.onExit = { [weak self] status, _ in
            self?.fixTasks.removeAll { $0 === t }
            if t.cancelled { return }  // the app is quitting
            if status == 0 && gotResult {
                completion(nil)
            } else {
                completion(failure ?? AppModel.crashMessage(status: status, signaled: t.signaled))
            }
        }
        fixTasks.append(t)
        do { try t.start() } catch {
            fixTasks.removeAll { $0 === t }
            completion("The converter couldn't start (\(error.localizedDescription)). Try reinstalling Manifold Mesh Mix.")
        }
    }

    /// Stops every engine: conversions, files being read and fixed meshes
    /// being saved (the app is quitting).
    func shutdown() {
        for j in jobs { j.task?.cancel() }
        for t in fixTasks { t.cancel() }
    }

    /// Asks where to save the checked and fixed copy of the mesh, then saves
    /// it (.stl, or .3mf with one object per part).
    func askToSaveFixedMesh(_ job: Job) {
        guard !job.savingFixedMesh else { return }
        let multi = job.partList.count > 1
        let ext = multi ? "3mf" : "stl"
        let p = NSSavePanel()
        p.message = "Save the fixed mesh"
        p.allowedContentTypes = (multi ? ["3mf", "stl"] : ["stl", "3mf"]).compactMap { UTType(filenameExtension: $0) }
        p.directoryURL = job.url.deletingLastPathComponent()
        p.nameFieldStringValue = "\(job.name) (fixed).\(ext)"
        p.canCreateDirectories = true
        guard p.runModal() == .OK, let chosen = p.url else { return }
        let dest = ["stl", "3mf"].contains(chosen.pathExtension.lowercased()) ? chosen : chosen.appendingPathExtension(ext)
        job.savingFixedMesh = true
        saveFixedMesh(job, to: dest) { [weak job] err in
            job?.savingFixedMesh = false
            if let err {
                let a = NSAlert()
                a.messageText = "The fixed mesh couldn't be saved"
                a.informativeText = err
                a.addButton(withTitle: "OK")
                a.runModal()
            } else {
                job?.savedFixedMesh = dest
            }
        }
    }

    // MARK: conversion queue

    func convertAll() {
        for j in jobs where j.canConvert { enqueue(j) }
    }

    func enqueue(_ job: Job) {
        guard !job.status.isBusy, job.status != .queued, job.readable else { return }
        if job.status != .done { discardPreviews(job.clearResults()) }
        job.status = .queued
        job.progress = 0
        job.stage = "Waiting"
        job.error = nil
        job.errorReason = nil
        job.errorDetails = nil
        job.warnings = []
        job.log = []
        pump()
    }

    func reconvert(_ job: Job) {
        discardPreviews(job.clearResults())
        enqueue(job)
    }

    /// Turns "Split to parts" on or off for one file. A finished file goes
    /// back to Ready, so it can be converted the new way.
    func setSplit(_ job: Job, _ on: Bool) {
        guard job.canSplit, job.split != on, !job.status.isBusy, job.status != .queued, !on || splitAvailable else { return }
        job.split = on
        job.selectedPart = 0
        if job.status == .done {
            discardPreviews(job.clearResults())
            job.status = .ready
            job.stage = ""
        }
    }

    /// Splitting only makes sense for "one of each part" (a whole build
    /// plate is kept together).
    var splitAvailable: Bool { settings.plateLayout == .parts }

    /// The Settings default changed: files that aren't converted yet follow it.
    func applySplitDefault() {
        for j in jobs where j.canSplit && [.ready, .failed, .cancelled].contains(j.status) {
            j.split = settings.splitParts && splitAvailable
            j.selectedPart = 0
        }
    }

    private func pump() {
        let limit = max(1, settings.parallelJobs)
        while runningCount < limit, let next = jobs.first(where: { $0.status == .queued }) {
            start(next)
        }
    }

    func outputDirectory(for job: Job) -> URL {
        let source = job.url.deletingLastPathComponent()
        switch settings.outputMode {
        case .sameFolder:
            return source
        case .subfolder:
            let name = settings.subfolderName.trimmingCharacters(in: .whitespacesAndNewlines)
            return source.appendingPathComponent(name.isEmpty ? "STEP Files" : name, isDirectory: true)
        case .custom:
            return settings.outputFolder.isEmpty ? source : URL(fileURLWithPath: settings.outputFolder, isDirectory: true)
        }
    }

    /// File name (without .step) a job's STEP file gets when exported.
    func defaultExportName(for job: Job, part: Int?) -> String {
        if let p = part, job.partList.indices.contains(p) {
            // "Project - Part" for every part of a split 3MF; parts with the same name get a number
            let base = "\(job.name) - \(Self.safeFileName(job.partList[p].name))"
            let same = job.partList.prefix(p).filter { Self.safeFileName($0.name).lowercased() == Self.safeFileName(job.partList[p].name).lowercased() }.count
            return same > 0 ? "\(base) \(same + 1)" : base
        }
        // a project file and a mesh with the same name (Part.3mf / Part.stl)
        // must not overwrite each other
        let ext = job.url.pathExtension.lowercased()
        let sameNameSibling = jobs.contains { $0.id != job.id && $0.name == job.name && $0.url.deletingLastPathComponent() == job.url.deletingLastPathComponent() }
        return (sameNameSibling && ext != "stl") ? "\(job.name) (\(ext.uppercased()))" : job.name
    }

    static func safeFileName(_ s: String) -> String {
        let bad = CharacterSet(charactersIn: "/\\:*?\"<>|").union(.controlCharacters)
        let cleaned = s.unicodeScalars.map { bad.contains($0) ? "-" : String($0) }.joined()
            .trimmingCharacters(in: CharacterSet.whitespaces.union(CharacterSet(charactersIn: ".")))
        return cleaned.isEmpty ? "Part" : String(cleaned.prefix(100))
    }

    private func start(_ job: Job) {
        let s = settings
        let stamp = nextStamp()
        // the settings this run uses (for the "Auto used" mark and reports)
        job.runSettings = RunSettings(s, triangles: job.triangles)
        // The STEP files go into the app's temporary folder first; nothing is
        // saved next to the originals until the user exports.
        let resultDir = previewDir.appendingPathComponent("results/\(job.id.uuidString)-\(stamp)", isDirectory: true)
        try? FileManager.default.createDirectory(at: resultDir, withIntermediateDirectories: true)
        var partOuts: [URL] = []
        if job.isSplit {
            partOuts = job.partList.indices.map { resultDir.appendingPathComponent("part\($0).step") }
        }
        let out = partOuts.first ?? resultDir.appendingPathComponent("result.step")
        let prev = previewDir.appendingPathComponent("\(job.id.uuidString)-out-\(stamp).mmmp")
        let partPreviews = job.isSplit
            ? job.partList.map { previewDir.appendingPathComponent("\(job.id.uuidString)-part\($0.index)-out-\(stamp).mmmp") }
            : []
        var args = ["convert", job.url.path, "-o", out.path, "--json",
                    "--preset", s.preset.rawValue,
                    "--repair", s.repair.rawValue,
                    "--unit", s.unit.rawValue,
                    "--quality", String(format: "%.3f", s.quality(for: job.triangles)),
                    "--layout", s.plateLayout.rawValue,
                    "--preview", prev.path]
        if job.isSplit {
            args.append("--split")
            for u in partOuts { args += ["--part-output", u.path] }
            for u in partPreviews { args += ["--part-preview", u.path] }
        }
        if !s.removeDebris { args.append("--keep-debris") }
        if s.useCustomTolerance && s.customTolerance > 0 { args += ["--tolerance", String(s.customTolerance)] }
        if s.maxFaces > 0 { args += ["--max-faces", String(s.maxFaces)] }
        if job.mergeOverlaps { args.append("--merge-overlaps") }
        if !job.touching.isEmpty { args += ["--touching-parts", job.touchingChoice ?? "split"] }
        guard let t = EngineTask(arguments: args) else {
            job.status = .failed
            job.error = AppModel.engineMissingMessage
            return
        }
        job.status = .converting
        job.stage = "Starting"
        job.progress = 0.01
        job.startedAt = Date()
        job.task = t
        var gotResult = false
        t.onEvent = { [weak job] ev in
            guard let job else { return }
            switch ev["event"] as? String {
            case "stage":
                job.stage = ev["stage"] as? String ?? ""
                if let p = ev["progress"] as? Double { job.progress = max(job.progress, p) }
            case "progress":
                if let p = ev["progress"] as? Double { job.progress = max(job.progress, p) }
            case "log":
                if let m = ev["message"] as? String { job.log.append(m) }
            case "warning":
                if let m = ev["message"] as? String { job.warnings.append(m) }
            case "error":
                AppModel.takeError(job, ev)
            case "part":
                // one part of a split 3MF is finished: show it right away
                if let p = ev["part"] as? Int, let i = job.partList.firstIndex(where: { $0.index == p }) {
                    job.partList[i].result = ConversionResult(event: ev)
                    if let o = ev["output"] as? String { job.partList[i].outputURL = URL(fileURLWithPath: o) }
                    if let pv = ev["preview"] as? String, FileManager.default.fileExists(atPath: pv) {
                        job.partList[i].outputPreview = URL(fileURLWithPath: pv)
                    }
                }
            case "result":
                gotResult = true
                job.result = ConversionResult(event: ev)
                job.usedPreset = Preset(rawValue: ev["preset"] as? String ?? "")
                if let mc = MeshCheck(event: ev["mesh_check"]) { job.meshCheck = mc }
            default: break
            }
        }
        t.onExit = { [weak self, weak job] status, stderr in
            guard let self else { return }
            guard let job, self.jobs.contains(where: { $0 === job }) else {
                // removed while converting: nothing will show these files
                self.discardPreviews([resultDir, prev] + partPreviews)
                self.pump()
                return
            }
            job.task = nil
            if t.cancelled {
                job.status = .cancelled
                job.stage = "Cancelled"
            } else if status == 0 && gotResult {
                job.status = .done
                job.progress = 1
                job.stage = "Done"
                job.outputURL = out
                if FileManager.default.fileExists(atPath: prev.path) { job.outputPreview = prev }
            } else {
                job.status = .failed
                if job.error == nil {
                    job.error = AppModel.crashMessage(status: status, signaled: t.signaled)
                    let lastLine = stderr.split(separator: "\n").last.map(String.init) ?? ""
                    if !lastLine.isEmpty { job.log.append("Converter output: \(lastLine)") }
                }
            }
            self.pump()
            if self.runningCount == 0 && !self.jobs.contains(where: { $0.status == .queued }) {
                NSApp.requestUserAttention(.informationalRequest)
            }
        }
        do { try t.start() } catch {
            job.status = .failed
            job.error = "The converter couldn't start (\(error.localizedDescription)). Try reinstalling Manifold Mesh Mix."
            pump()
        }
    }

    static let engineMissingMessage = "Part of the app is missing (the converter). Please reinstall Manifold Mesh Mix."

    /// What to tell the user when the converter ends without a result.
    static func crashMessage(status: Int32, signaled: Bool) -> String {
        if signaled {
            switch status {
            case SIGKILL:
                return "macOS stopped the conversion, most likely because the Mac ran low on memory. Move the slider toward Faster, or convert one file at a time (Advanced → Files at once)."
            case SIGSEGV, SIGBUS, SIGILL, SIGABRT, SIGFPE:
                return "The converter crashed on this file. Try another preset, or set Mesh repair to Thorough under Advanced."
            default:
                return "The conversion was stopped unexpectedly (signal \(status))."
            }
        }
        if status == 0 { return "The converter finished without saving a STEP file. Convert the file again." }
        return "The converter stopped unexpectedly (code \(status)). Convert the file again; if it keeps failing, try another preset."
    }

    /// The engine's error event: what went wrong, why, and the details.
    static func takeError(_ job: Job, _ ev: [String: Any]) {
        job.error = friendly(ev["message"] as? String)
        job.errorReason = ev["reason"] as? String
        job.errorDetails = ev["details"] as? String
    }

    /// Strip Python exception class prefixes ("MeshError: ...").
    static func friendly(_ msg: String?) -> String? {
        guard var m = msg?.trimmingCharacters(in: .whitespacesAndNewlines), !m.isEmpty else { return msg }
        if let r = m.range(of: #"^[A-Za-z_][A-Za-z0-9_]*(Error|Exception): "#, options: .regularExpression) {
            m.removeSubrange(r)
        }
        return m
    }

    func cancel(_ job: Job) {
        if job.status == .queued {
            job.status = .cancelled
            job.stage = "Cancelled"
            return
        }
        job.task?.cancel()
    }

    func cancelAll() {
        for j in jobs where j.status == .queued || j.status == .converting { cancel(j) }
    }

    func remove(_ job: Job) {
        if job.needsExport && !confirmDiscard(count: 1, action: "Remove") { return }
        job.task?.cancel()
        jobWatchers[job.id] = nil
        jobs.removeAll { $0.id == job.id }
        if selection == job.id { selection = jobs.first?.id }
        discardPreviews(Self.previewFiles(of: job))
    }

    private static func previewFiles(of job: Job) -> [URL?] {
        [job.inputPreview, job.outputPreview, job.outputURL]
            + job.partList.flatMap { [$0.inputPreview, $0.outputPreview, $0.outputURL] }
    }

    func clearFinished() {
        let done = jobs.filter { $0.status == .done }
        let unexported = done.filter { $0.needsExport }.count
        if unexported > 0 && !confirmDiscard(count: unexported, action: "Remove") { return }
        for j in done { jobWatchers[j.id] = nil }
        // only the files asked about (others may have finished while asking)
        jobs.removeAll { j in done.contains { $0 === j } }
        if selectedJob == nil { selection = jobs.first?.id }
        discardPreviews(done.flatMap { Self.previewFiles(of: $0) })
    }

    /// Asks before throwing away converted files that weren't exported.
    func confirmDiscard(count: Int, action: String) -> Bool {
        if SelfTest.isActive { return true }
        let a = NSAlert()
        a.messageText = count == 1 ? "This file hasn't been exported yet" : "\(count) files haven't been exported yet"
        a.informativeText = "If you continue, the converted STEP \(count == 1 ? "file is" : "files are") deleted and you would have to convert again."
        a.addButton(withTitle: "Cancel")
        a.addButton(withTitle: "\(action) Anyway")
        return a.runModal() == .alertSecondButtonReturn
    }

    func reveal(_ job: Job) {
        let files = job.exportedFiles
        if !files.isEmpty { NSWorkspace.shared.activateFileViewerSelecting(files) }
    }

    /// Shows one part's exported STEP file (or the file's only STEP).
    func revealCurrent(_ job: Job) {
        if let u = job.isSplit ? job.currentPart?.exportedURL : job.exportedURL {
            NSWorkspace.shared.activateFileViewerSelecting([u])
        } else {
            reveal(job)
        }
    }

    func openOutput(_ job: Job) {
        if let u = (job.isSplit ? job.currentPart?.exportedURL : job.exportedURL) ?? job.exportedFiles.first {
            NSWorkspace.shared.open(u)
        }
    }

    // MARK: export

    /// Converted files that can be exported, and those that haven't been yet.
    var exportableJobs: [Job] { jobs.filter { $0.status == .done && !$0.resultFiles.isEmpty } }
    var unexportedJobs: [Job] { exportableJobs.filter { !$0.isExported } }

    /// Opens the export window for these files.
    func requestExport(_ list: [Job]) {
        let js = list.filter { $0.status == .done && !$0.resultFiles.isEmpty }
        guard !js.isEmpty else { return }
        exportRequest = ExportRequest(jobs: js)
    }

    func exportItems(for list: [Job]) -> [ExportItem] {
        list.flatMap { job in
            job.resultFiles.map { ExportItem(job: job, part: $0.part, source: $0.source, name: defaultExportName(for: job, part: $0.part)) }
        }
    }

    func exportURL(for item: ExportItem) -> URL {
        outputDirectory(for: item.job).appendingPathComponent(Self.safeFileName(item.name) + ".step")
    }

    /// Copies the converted files to their destinations. Runs the file work
    /// in the background (macOS may ask for permission first) and reports
    /// the files that couldn't be saved.
    func export(_ items: [ExportItem], completion: @escaping ([ExportFailure]) -> Void) {
        let plan = items.map { ($0, exportURL(for: $0)) }
        let reveal = settings.revealWhenDone && !SelfTest.isActive
        DispatchQueue.global(qos: .userInitiated).async {
            var done: [(ExportItem, URL)] = []
            var failures: [ExportFailure] = []
            for (item, dest) in plan {
                do {
                    try Self.copyResult(item.source, to: dest)
                    done.append((item, dest))
                } catch {
                    failures.append(ExportFailure(item: item, destination: dest, error: error))
                }
            }
            DispatchQueue.main.async {
                for (item, dest) in done {
                    if let p = item.part, item.job.partList.indices.contains(p) {
                        item.job.partList[p].exportedURL = dest
                    } else {
                        item.job.exportedURL = dest
                    }
                    item.job.objectWillChange.send()
                }
                if reveal && !done.isEmpty { NSWorkspace.shared.activateFileViewerSelecting(done.map { $0.1 }) }
                completion(failures)
            }
        }
    }

    /// Writes a hidden temporary copy next to the destination, then renames
    /// it into place, so a half-written STEP file never appears.
    private static func copyResult(_ source: URL, to dest: URL) throws {
        let fm = FileManager.default
        let dir = dest.deletingLastPathComponent()
        try fm.createDirectory(at: dir, withIntermediateDirectories: true)
        let tmp = dir.appendingPathComponent(".\(dest.lastPathComponent).\(UUID().uuidString.prefix(8)).tmp")
        do {
            try fm.copyItem(at: source, to: tmp)
            if rename(tmp.path, dest.path) != 0 {
                throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno))
            }
        } catch {
            try? fm.removeItem(at: tmp)
            throw error
        }
    }
}

/// The export window's list of files.
struct ExportRequest: Identifiable {
    let id = UUID()
    let jobs: [Job]
}

/// One STEP file to export.
struct ExportItem: Identifiable {
    let id = UUID()
    let job: Job
    /// part index of a split 3MF
    let part: Int?
    let source: URL
    /// file name without .step
    var name: String
}

/// A file that couldn't be exported, with a message in plain words.
struct ExportFailure {
    let item: ExportItem
    let destination: URL
    let error: Error

    private var posixCode: Int? {
        let ns = error as NSError
        if ns.domain == NSPOSIXErrorDomain { return ns.code }
        if let u = ns.userInfo[NSUnderlyingErrorKey] as? NSError, u.domain == NSPOSIXErrorDomain { return u.code }
        return nil
    }

    /// macOS refused access to the folder (privacy settings or file permissions).
    var isPermission: Bool {
        let ns = error as NSError
        if ns.domain == NSCocoaErrorDomain && [NSFileWriteNoPermissionError, NSFileReadNoPermissionError].contains(ns.code) { return true }
        return posixCode == Int(EPERM) || posixCode == Int(EACCES)
    }

    var folderName: String { destination.deletingLastPathComponent().lastPathComponent }

    var message: String {
        let ns = error as NSError
        let file = destination.lastPathComponent
        if isPermission {
            return "macOS didn't allow saving into the folder “\(folderName)”."
        }
        if ns.code == NSFileWriteOutOfSpaceError || posixCode == Int(ENOSPC) {
            return "The disk is full, so “\(file)” couldn't be saved. Free up some space and export again."
        }
        if ns.code == NSFileWriteVolumeReadOnlyError || posixCode == Int(EROFS) {
            return "The folder “\(folderName)” is on a disk that can't be written to. Choose another folder."
        }
        return "“\(file)” couldn't be saved (\(ns.localizedDescription))."
    }
}
