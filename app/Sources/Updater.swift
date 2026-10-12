import AppKit
import CryptoKit
import Foundation

/// Finds, downloads and installs a newer version from the project's GitHub
/// releases. Every release carries ManifoldMeshMix-<version>-macOS-arm64.zip
/// and a SHA256SUMS.txt; the zip is checked against it before installing.
/// Installing hands over to install-update.sh, which waits for the app to
/// quit, swaps the app bundle and opens the new version.
///
/// Like AppModel, the published state is only changed on the main thread.
final class Updater: ObservableObject {
    enum State: Equatable {
        case idle, checking, current, available, downloading, ready, installing, failed
    }

    struct Release {
        let version: String
        let notes: String
        let assetName: String
        let assetURL: URL
        let size: Int
        let sumsURL: URL
        let pageURL: URL
    }

    static let repo = "TUNA-THE-FIRST/Manifold-Mesh-Mix"
    static let assetSuffix = "-macOS-arm64.zip"
    static let sumsName = "SHA256SUMS.txt"
    static let releasesPage = URL(string: "https://github.com/\(repo)/releases/latest")!

    @Published var state: State = .idle
    @Published var release: Release?
    @Published var error = ""
    @Published var progress: Double = 0
    /// The update window is open
    @Published var showWindow = false
    /// The new app, unpacked and checked, waiting to be installed
    private(set) var downloadedApp: URL?
    private var downloader: Downloader?

    static var currentVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
    }

    var availableVersion: String? {
        [.available, .downloading, .ready].contains(state) ? release?.version : nil
    }

    /// Where a failed install leaves its reason for the next launch.
    static var failureMarker: URL {
        FileManager.default.temporaryDirectory.appendingPathComponent("ManifoldMeshMix-update-failed.txt")
    }

    // MARK: versions

    /// "v1.2.3" / "1.2" -> [1, 2, 3]; nil when it isn't a version.
    static func parse(_ s: String) -> [Int]? {
        var t = s.trimmingCharacters(in: .whitespaces)
        if t.hasPrefix("v") || t.hasPrefix("V") { t.removeFirst() }
        let digits = t.prefix { $0.isNumber || $0 == "." }
        let parts = digits.split(separator: ".").compactMap { Int($0) }
        guard !parts.isEmpty else { return nil }
        return parts + Array(repeating: 0, count: max(0, 3 - parts.count))
    }

    static func isNewer(_ latest: String, than current: String) -> Bool {
        guard let a = parse(latest), let b = parse(current) else { return false }
        for (x, y) in zip(a, b) where x != y { return x > y }
        return a.count > b.count && a[b.count...].contains { $0 > 0 }
    }

    // MARK: checking

    private var apiURL: URL {
        // MMM_UPDATE_URL is for tests against a server on this computer only
        if let s = ProcessInfo.processInfo.environment["MMM_UPDATE_URL"], let u = URL(string: s), Self.isLocal(u) { return u }
        return URL(string: "https://api.github.com/repos/\(Self.repo)/releases/latest")!
    }

    static func isLocal(_ u: URL) -> Bool { ["127.0.0.1", "localhost", "::1"].contains(u.host ?? "") }

    /// Updates only come over HTTPS (plain HTTP only from a test server on this computer).
    static func allowed(_ u: URL) -> Bool { u.scheme == "https" || (u.scheme == "http" && isLocal(u)) }

    /// Looks for a newer release; `completion` runs on the main thread when done.
    func check(completion: (() -> Void)? = nil) {
        guard ![.checking, .downloading, .installing, .ready].contains(state) else {
            completion?()
            return
        }
        state = .checking
        error = ""
        var req = URLRequest(url: apiURL, timeoutInterval: 30)
        req.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        req.setValue("ManifoldMeshMix/\(Self.currentVersion) (macOS)", forHTTPHeaderField: "User-Agent")
        URLSession.shared.dataTask(with: req) { data, response, err in
            let outcome: Result<Release?, Error>
            do {
                if let err { throw err }
                if let response { try Self.checkHTTP(response) }
                outcome = .success(try Self.release(from: data ?? Data()))
            } catch {
                outcome = .failure(error)
            }
            DispatchQueue.main.async {
                switch outcome {
                case .success(let rel):
                    self.release = rel
                    self.state = rel == nil ? .current : .available
                case .failure(let e):
                    self.error = Self.message(for: e)
                    self.state = .failed
                }
                completion?()
            }
        }.resume()
    }

    /// The newer release in GitHub's answer, or nil when this version is the newest.
    static func release(from data: Data) throws -> Release? {
        guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let tag = obj["tag_name"] as? String, parse(tag) != nil else {
            throw UpdateError("The newest release has no version number the app understands.")
        }
        guard isNewer(tag, than: currentVersion) else { return nil }
        let version = tag.hasPrefix("v") || tag.hasPrefix("V") ? String(tag.dropFirst()) : tag
        let assets = obj["assets"] as? [[String: Any]] ?? []
        let asset = assets.first { ($0["name"] as? String ?? "").lowercased().hasSuffix(assetSuffix.lowercased()) }
        let sums = assets.first { $0["name"] as? String == sumsName }
        guard let a = asset, let name = a["name"] as? String,
              let aurl = (a["browser_download_url"] as? String).flatMap({ URL(string: $0) }) else {
            throw UpdateError("Version \(version) is out, but it has no download for macOS yet.")
        }
        guard let s = sums, let surl = (s["browser_download_url"] as? String).flatMap({ URL(string: $0) }) else {
            throw UpdateError("Version \(version) is out, but its checksum file is missing, so it can't be installed safely.")
        }
        guard allowed(aurl), allowed(surl) else {
            throw UpdateError("The update's download address isn't secure, so it wasn't used.")
        }
        return Release(version: version, notes: obj["body"] as? String ?? "", assetName: name, assetURL: aurl,
                       size: a["size"] as? Int ?? 0, sumsURL: surl,
                       pageURL: (obj["html_url"] as? String).flatMap({ URL(string: $0) }) ?? releasesPage)
    }

    // MARK: downloading

    func download() {
        guard let rel = release, [.available, .failed].contains(state) else { return }
        state = .downloading
        progress = 0
        error = ""
        URLSession.shared.dataTask(with: rel.sumsURL) { data, response, err in
            do {
                if let err { throw err }
                if let response { try Self.checkHTTP(response) }
                guard let expected = Self.expectedHash(in: String(decoding: data ?? Data(), as: UTF8.self), for: rel.assetName) else {
                    throw UpdateError("The update's checksum is missing, so it can't be installed safely.")
                }
                let folder = FileManager.default.temporaryDirectory
                    .appendingPathComponent("ManifoldMeshMix-update-\(UUID().uuidString.prefix(8))", isDirectory: true)
                try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                let zip = folder.appendingPathComponent(rel.assetName)
                DispatchQueue.main.async { self.startDownload(rel, to: zip, in: folder, expected: expected) }
            } catch {
                self.fail(error)
            }
        }.resume()
    }

    private func startDownload(_ rel: Release, to zip: URL, in folder: URL, expected: String) {
        let d = Downloader(progress: { p in
            DispatchQueue.main.async { self.progress = p }
        }, completion: { err in
            if let err {
                self.fail(err)
                return
            }
            // hashing and unzipping a few hundred MB: off the main thread
            do {
                guard try Self.sha256(of: zip) == expected else {
                    try? FileManager.default.removeItem(at: folder)
                    throw UpdateError("The downloaded file doesn't match its checksum, so it wasn't installed. Try again.")
                }
                let app = try Self.unpack(zip, in: folder)
                DispatchQueue.main.async {
                    self.downloader = nil
                    self.downloadedApp = app
                    self.state = .ready
                }
            } catch {
                self.fail(error)
            }
        })
        downloader = d
        d.start(rel.assetURL, to: zip)
    }

    private func fail(_ e: Error) {
        DispatchQueue.main.async {
            self.downloader = nil
            self.error = Self.message(for: e)
            self.state = .failed
        }
    }

    func cancelDownload() {
        downloader?.cancel()
    }

    // MARK: installing

    /// Why this copy can't replace itself, or nil when it can.
    var installProblem: String? {
        let dest = Bundle.main.bundleURL
        if dest.path.contains("/AppTranslocation/") {
            return "macOS is running the app from a temporary place. Move Manifold Mesh Mix into your Applications folder, open it from there, then update."
        }
        let fm = FileManager.default
        if !fm.isWritableFile(atPath: dest.deletingLastPathComponent().path) || !fm.isWritableFile(atPath: dest.path) {
            return "The app's folder (\(dest.deletingLastPathComponent().path)) can't be written to, so the app can't update itself."
        }
        return nil
    }

    /// Starts the installer script and returns true; the caller quits the app.
    func install() -> Bool {
        guard let app = downloadedApp else { return false }
        if let p = installProblem {
            error = p
            state = .failed
            return false
        }
        guard let bundled = Bundle.main.url(forResource: "install-update", withExtension: "sh") else {
            error = "Part of the app is missing (the update installer). Download the new version from the release page."
            state = .failed
            return false
        }
        do {
            // run a copy: the original goes away with the old app
            let script = app.deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("install-update.sh")
            try? FileManager.default.removeItem(at: script)
            try FileManager.default.copyItem(at: bundled, to: script)
            try? FileManager.default.removeItem(at: Self.failureMarker)
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/bin/sh")
            p.arguments = [script.path, String(ProcessInfo.processInfo.processIdentifier), app.path,
                           Bundle.main.bundleURL.path, Self.failureMarker.path]
            p.standardInput = FileHandle.nullDevice
            p.standardOutput = FileHandle.nullDevice
            p.standardError = FileHandle.nullDevice
            try p.run()
        } catch {
            self.error = "The update couldn't be started (\(error.localizedDescription))."
            state = .failed
            return false
        }
        state = .installing
        return true
    }

    /// Opens the window and looks for a newer version.
    func checkFromMenu() {
        showWindow = true
        if ![.available, .downloading, .ready].contains(state) { check() }
    }

    /// Shows why the last update failed (left behind by install-update.sh).
    static func reportPreviousFailure() {
        guard let text = try? String(contentsOf: failureMarker, encoding: .utf8) else { return }
        try? FileManager.default.removeItem(at: failureMarker)
        let a = NSAlert()
        a.messageText = "The update wasn't installed"
        a.informativeText = text.trimmingCharacters(in: .whitespacesAndNewlines)
        a.addButton(withTitle: "OK")
        a.runModal()
    }

    /// State name for the self-test report (same words as the Windows/Linux app).
    var stateName: String {
        switch state {
        case .idle: return "idle"
        case .checking: return "checking"
        case .current: return "current"
        case .available: return "available"
        case .downloading: return "downloading"
        case .ready: return "ready"
        case .installing: return "installing"
        case .failed: return "error"
        }
    }

    // MARK: helpers

    struct UpdateError: LocalizedError {
        let message: String
        init(_ m: String) { message = m }
        var errorDescription: String? { message }
    }

    static func checkHTTP(_ r: URLResponse) throws {
        guard let h = r as? HTTPURLResponse else { return }
        switch h.statusCode {
        case 200..<300: return
        case 404: throw UpdateError("No release has been published yet.")
        case 403, 429: throw UpdateError("GitHub is limiting update checks right now. Try again in an hour.")
        default: throw UpdateError("GitHub answered with an error (\(h.statusCode)). Try again later.")
        }
    }

    static func message(for error: Error) -> String {
        if let e = error as? UpdateError { return e.message }
        if let u = error as? URLError {
            if u.code == .cancelled { return "The download was cancelled." }
            return "Couldn't reach GitHub. Check your internet connection and try again."
        }
        return error.localizedDescription
    }

    static func expectedHash(in sums: String, for name: String) -> String? {
        for line in sums.split(whereSeparator: { $0.isNewline }) {
            let parts = line.split(whereSeparator: { $0.isWhitespace })
            guard parts.count >= 2 else { continue }
            var file = String(parts[parts.count - 1])
            if file.hasPrefix("*") { file.removeFirst() }
            let hash = parts[0].lowercased()
            if file == name, hash.count == 64, hash.allSatisfy({ $0.isHexDigit }) { return hash }
        }
        return nil
    }

    static func sha256(of url: URL) throws -> String {
        let fh = try FileHandle(forReadingFrom: url)
        defer { try? fh.close() }
        var h = SHA256()
        while let d = try fh.read(upToCount: 1 << 20), !d.isEmpty {
            h.update(data: d)
        }
        return h.finalize().map { String(format: "%02x", $0) }.joined()
    }

    /// Unzips the release and returns the app inside it (checked to be this app).
    static func unpack(_ zip: URL, in folder: URL) throws -> URL {
        let out = folder.appendingPathComponent("extracted", isDirectory: true)
        try? FileManager.default.removeItem(at: out)
        try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/ditto")
        p.arguments = ["-x", "-k", zip.path, out.path]
        try p.run()
        p.waitUntilExit()
        guard p.terminationStatus == 0 else { throw UpdateError("The downloaded update couldn't be unpacked. Try again.") }
        let items = (try? FileManager.default.contentsOfDirectory(at: out, includingPropertiesForKeys: nil)) ?? []
        guard let app = items.first(where: { $0.pathExtension == "app" }),
              Bundle(url: app)?.bundleIdentifier == Bundle.main.bundleIdentifier else {
            throw UpdateError("The downloaded update doesn't contain Manifold Mesh Mix.")
        }
        try? FileManager.default.removeItem(at: zip)
        return app
    }
}

/// A file download with progress reports. Both callbacks run on a
/// background queue.
final class Downloader: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {
    private let onProgress: (Double) -> Void
    private let onDone: (Error?) -> Void
    private var destination: URL?
    private var task: URLSessionDownloadTask?
    private var moveError: Error?

    init(progress: @escaping (Double) -> Void, completion: @escaping (Error?) -> Void) {
        onProgress = progress
        onDone = completion
    }

    func start(_ url: URL, to dest: URL) {
        destination = dest
        let s = URLSession(configuration: .ephemeral, delegate: self, delegateQueue: nil)
        let t = s.downloadTask(with: url)
        task = t
        t.resume()
    }

    func cancel() {
        task?.cancel()
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64,
                    totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        if totalBytesExpectedToWrite > 0 {
            onProgress(min(1, Double(totalBytesWritten) / Double(totalBytesExpectedToWrite)))
        }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        // the file at `location` is deleted when this returns
        guard let dest = destination else { return }
        if let h = downloadTask.response as? HTTPURLResponse, !(200..<300).contains(h.statusCode) {
            moveError = Updater.UpdateError("The download failed (GitHub answered \(h.statusCode)). Try again later.")
            return
        }
        do {
            try? FileManager.default.removeItem(at: dest)
            try FileManager.default.moveItem(at: location, to: dest)
        } catch {
            moveError = error
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        session.finishTasksAndInvalidate()
        onDone(error ?? moveError)
    }
}
