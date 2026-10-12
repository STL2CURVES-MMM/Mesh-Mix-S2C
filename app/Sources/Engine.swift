import Foundation

/// Locates and runs the bundled conversion engine (a frozen Python program).
/// The engine speaks JSON lines on stdout: stage / progress / log / warning /
/// result / error events.
enum EngineLocator {
    struct Command {
        let executable: URL
        let prefixArgs: [String]
    }

    static func command() -> Command? {
        let fm = FileManager.default
        // 1. Bundled engine inside the app.
        if let res = Bundle.main.resourceURL {
            let exe = res.appendingPathComponent("engine/mmm-engine")
            if fm.isExecutableFile(atPath: exe.path) {
                return Command(executable: exe, prefixArgs: [])
            }
        }
        // 2. Development: explicit environment variables.
        let env = ProcessInfo.processInfo.environment
        if let py = env["MMM_ENGINE_PYTHON"], let script = env["MMM_ENGINE_SCRIPT"] {
            return Command(executable: URL(fileURLWithPath: py), prefixArgs: [script])
        }
        // 3. Development: the project checkout.
        let home = fm.homeDirectoryForCurrentUser
        let root = home.appendingPathComponent("Documents/Claude Projects/STL to STEP")
        let py = root.appendingPathComponent(".venv/bin/python")
        let script = root.appendingPathComponent("engine/mmm_engine.py")
        if fm.isExecutableFile(atPath: py.path) && fm.fileExists(atPath: script.path) {
            return Command(executable: py, prefixArgs: [script.path])
        }
        return nil
    }
}

final class EngineTask {
    let process = Process()
    private var buffer = Data()
    private var stderrTail = Data()
    private let queue = DispatchQueue(label: "engine.io")
    var onEvent: (([String: Any]) -> Void)?
    var onExit: ((Int32, String) -> Void)?
    private(set) var cancelled = false
    /// true when the engine was killed by a signal (crash, out of memory)
    private(set) var signaled = false

    init?(arguments: [String]) {
        guard let cmd = EngineLocator.command() else { return nil }
        process.executableURL = cmd.executable
        process.arguments = cmd.prefixArgs + arguments
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        env["OMP_NUM_THREADS"] = env["OMP_NUM_THREADS"] ?? "4"
        process.environment = env
        process.qualityOfService = .userInitiated
    }

    func start() throws {
        let out = Pipe()
        let err = Pipe()
        process.standardOutput = out
        process.standardError = err
        out.fileHandleForReading.readabilityHandler = { [weak self] h in
            let d = h.availableData
            guard let self else { return }
            if d.isEmpty { return }
            self.queue.async { self.consume(d) }
        }
        err.fileHandleForReading.readabilityHandler = { [weak self] h in
            let d = h.availableData
            guard let self, !d.isEmpty else { return }
            self.queue.async {
                self.stderrTail.append(d)
                if self.stderrTail.count > 64_000 {
                    self.stderrTail = self.stderrTail.suffix(32_000)
                }
            }
        }
        process.terminationHandler = { [weak self] p in
            out.fileHandleForReading.readabilityHandler = nil
            err.fileHandleForReading.readabilityHandler = nil
            guard let self else { return }
            // drain whatever is left
            let rest = out.fileHandleForReading.readDataToEndOfFile()
            self.queue.async {
                if !rest.isEmpty { self.consume(rest) }
                self.flushLine()
                let tail = String(decoding: self.stderrTail, as: UTF8.self)
                let status = p.terminationStatus
                self.signaled = p.terminationReason == .uncaughtSignal
                DispatchQueue.main.async { self.onExit?(status, tail) }
            }
        }
        try process.run()
    }

    func cancel() {
        cancelled = true
        if process.isRunning {
            process.terminate()
            let pid = process.processIdentifier
            DispatchQueue.global().asyncAfter(deadline: .now() + 2) {
                if self.process.isRunning { kill(pid, SIGKILL) }
            }
        }
    }

    private func consume(_ d: Data) {
        buffer.append(d)
        while let nl = buffer.firstIndex(of: 0x0A) {
            let line = buffer.subdata(in: buffer.startIndex..<nl)
            buffer.removeSubrange(buffer.startIndex...nl)
            handle(line)
        }
    }

    private func flushLine() {
        if !buffer.isEmpty {
            handle(buffer)
            buffer.removeAll()
        }
    }

    private func handle(_ line: Data) {
        guard !line.isEmpty,
              let obj = try? JSONSerialization.jsonObject(with: line) as? [String: Any] else { return }
        DispatchQueue.main.async { self.onEvent?(obj) }
    }
}
