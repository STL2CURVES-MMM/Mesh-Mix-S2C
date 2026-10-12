import SceneKit
import SwiftUI

/// Geometry decoded from the engine's MMMP preview format.
struct PreviewMesh {
    var positions: Data
    var normals: Data
    var colors: Data
    var indices: Data
    var lines: Data
    var vertexCount: Int
    var triangleCount: Int
    var lineCount: Int
    var bboxMin: SIMD3<Float>
    var bboxMax: SIMD3<Float>

    static func load(_ url: URL) -> PreviewMesh? {
        guard let d = try? Data(contentsOf: url, options: .mappedIfSafe), d.count >= 20 else { return nil }
        guard d.prefix(4) == Data("MMMP".utf8) else { return nil }
        func u32(_ o: Int) -> Int {
            Int(d.subdata(in: o..<o + 4).withUnsafeBytes { $0.loadUnaligned(as: UInt32.self) })
        }
        let nv = u32(8), nt = u32(12), nl = u32(16)
        var o = 20
        func take(_ n: Int) -> Data? {
            guard o + n <= d.count else { return nil }
            let s = d.subdata(in: o..<o + n)
            o += n
            return s
        }
        guard let P = take(nv * 12), let N = take(nv * 12), let C = take(nv * 4),
              let I = take(nt * 12), let L = take(nl * 12) else { return nil }
        var mn = SIMD3<Float>(repeating: .greatestFiniteMagnitude)
        var mx = SIMD3<Float>(repeating: -.greatestFiniteMagnitude)
        P.withUnsafeBytes { raw in
            let f = raw.bindMemory(to: Float.self)
            var i = 0
            let step = max(1, nv / 20000)
            while i < nv {
                let p = SIMD3<Float>(f[i * 3], f[i * 3 + 1], f[i * 3 + 2])
                mn = simd_min(mn, p)
                mx = simd_max(mx, p)
                i += step
            }
        }
        if nv == 0 { mn = .zero; mx = .zero }
        return PreviewMesh(positions: P, normals: N, colors: C, indices: I, lines: L,
                           vertexCount: nv, triangleCount: nt, lineCount: nl, bboxMin: mn, bboxMax: mx)
    }

    func node(faceColors: Bool, tint: NSColor, showEdges: Bool, edgeColor: NSColor) -> SCNNode {
        let root = SCNNode()
        guard vertexCount > 0 else { return root }
        var sources = [
            SCNGeometrySource(data: positions, semantic: .vertex, vectorCount: vertexCount,
                              usesFloatComponents: true, componentsPerVector: 3, bytesPerComponent: 4,
                              dataOffset: 0, dataStride: 12),
            SCNGeometrySource(data: normals, semantic: .normal, vectorCount: vertexCount,
                              usesFloatComponents: true, componentsPerVector: 3, bytesPerComponent: 4,
                              dataOffset: 0, dataStride: 12),
        ]
        if faceColors {
            // SceneKit wants float colors for reliable results.
            var fc = Data(count: vertexCount * 16)
            colors.withUnsafeBytes { src in
                fc.withUnsafeMutableBytes { dst in
                    let s = src.bindMemory(to: UInt8.self)
                    let o = dst.bindMemory(to: Float.self)
                    for i in 0..<vertexCount {
                        o[i * 4] = Float(s[i * 4]) / 255
                        o[i * 4 + 1] = Float(s[i * 4 + 1]) / 255
                        o[i * 4 + 2] = Float(s[i * 4 + 2]) / 255
                        o[i * 4 + 3] = 1
                    }
                }
            }
            sources.append(SCNGeometrySource(data: fc, semantic: .color, vectorCount: vertexCount,
                                             usesFloatComponents: true, componentsPerVector: 4, bytesPerComponent: 4,
                                             dataOffset: 0, dataStride: 16))
        }
        let el = SCNGeometryElement(data: indices, primitiveType: .triangles, primitiveCount: triangleCount, bytesPerIndex: 4)
        let geo = SCNGeometry(sources: sources, elements: [el])
        let m = SCNMaterial()
        m.lightingModel = .physicallyBased
        m.diffuse.contents = faceColors ? NSColor.white : tint
        m.metalness.contents = 0.05
        m.roughness.contents = 0.55
        m.isDoubleSided = true
        geo.materials = [m]
        let meshNode = SCNNode(geometry: geo)
        root.addChildNode(meshNode)

        if showEdges && lineCount >= 2 {
            let src = SCNGeometrySource(data: lines, semantic: .vertex, vectorCount: lineCount,
                                        usesFloatComponents: true, componentsPerVector: 3, bytesPerComponent: 4,
                                        dataOffset: 0, dataStride: 12)
            var idx = Data(count: lineCount * 4)
            idx.withUnsafeMutableBytes { raw in
                let p = raw.bindMemory(to: UInt32.self)
                for i in 0..<lineCount { p[i] = UInt32(i) }
            }
            let lel = SCNGeometryElement(data: idx, primitiveType: .line, primitiveCount: lineCount / 2, bytesPerIndex: 4)
            let lg = SCNGeometry(sources: [src], elements: [lel])
            let lm = SCNMaterial()
            lm.lightingModel = .constant
            lm.diffuse.contents = edgeColor
            lm.readsFromDepthBuffer = true
            lg.materials = [lm]
            let ln = SCNNode(geometry: lg)
            ln.renderingOrder = 10
            root.addChildNode(ln)
        }
        return root
    }
}

/// SceneKit viewport with turntable orbit, zoom and pan.
struct Viewport: NSViewRepresentable {
    var mesh: PreviewMesh?
    var faceColors: Bool
    var showEdges: Bool
    var tint: NSColor
    var resetToken: Int
    var meshID: String

    final class Coordinator {
        var lastMeshKey = "none"
        var lastKey = ""
        var lastReset = -1
        var contentNode = SCNNode()
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> SCNView {
        let v = SCNView(frame: .zero)
        let scene = SCNScene()
        v.scene = scene
        v.allowsCameraControl = true
        v.defaultCameraController.interactionMode = .orbitTurntable
        v.defaultCameraController.inertiaEnabled = true
        v.antialiasingMode = .multisampling4X
        v.backgroundColor = Theme.viewportBottom
        scene.background.contents = Viewport.gradientImage()
        v.autoenablesDefaultLighting = false
        v.preferredFramesPerSecond = 60

        let cam = SCNCamera()
        cam.zNear = 0.01
        cam.zFar = 100000
        cam.fieldOfView = 35
        let camNode = SCNNode()
        camNode.camera = cam
        camNode.name = "camera"
        scene.rootNode.addChildNode(camNode)
        v.pointOfView = camNode

        let amb = SCNLight()
        amb.type = .ambient
        amb.intensity = 300
        amb.color = NSColor(white: 0.85, alpha: 1)
        let ambN = SCNNode()
        ambN.light = amb
        scene.rootNode.addChildNode(ambN)

        let key = SCNLight()
        key.type = .directional
        key.intensity = 1100
        let keyN = SCNNode()
        keyN.light = key
        keyN.eulerAngles = SCNVector3(-0.9, 0.6, 0)
        camNode.addChildNode(keyN)

        let rim = SCNLight()
        rim.type = .directional
        rim.intensity = 500
        rim.color = NSColor(white: 0.9, alpha: 1)
        let rimN = SCNNode()
        rimN.light = rim
        rimN.eulerAngles = SCNVector3(0.8, -2.6, 0)
        scene.rootNode.addChildNode(rimN)

        let env = SCNLight()
        env.type = .directional
        env.intensity = 350
        let envN = SCNNode()
        envN.light = env
        envN.eulerAngles = SCNVector3(-2.2, 0, 0)
        scene.rootNode.addChildNode(envN)

        scene.rootNode.addChildNode(context.coordinator.contentNode)
        return v
    }

    func updateNSView(_ v: SCNView, context: Context) {
        let c = context.coordinator
        // the mesh arrives asynchronously after the path is known, so its
        // size is part of the identity
        let meshKey = "\(meshID)#\(mesh?.vertexCount ?? -1)/\(mesh?.triangleCount ?? -1)"
        let styleKey = "\(faceColors)-\(showEdges)-\(tint.description)"
        let meshChanged = meshKey != c.lastMeshKey
        if meshChanged || styleKey != c.lastKey {
            c.lastKey = styleKey
            c.contentNode.childNodes.forEach { $0.removeFromParentNode() }
            if let mesh {
                let edge = NSColor(white: 0.02, alpha: 0.85)
                c.contentNode.addChildNode(mesh.node(faceColors: faceColors, tint: tint, showEdges: showEdges, edgeColor: edge))
            }
        }
        if let mesh, meshChanged || c.lastReset != resetToken {
            c.lastReset = resetToken
            frame(v, mesh)
        }
        c.lastMeshKey = meshKey
    }

    private func frame(_ v: SCNView, _ mesh: PreviewMesh) {
        guard let cam = v.pointOfView else { return }
        let mn = mesh.bboxMin, mx = mesh.bboxMax
        let center = (mn + mx) / 2
        let size = max(simd_length(mx - mn), 1e-6)
        let dir = simd_normalize(SIMD3<Float>(0.9, -1.25, 0.85))
        // fit the bounding sphere into the narrower of the two view angles
        let vfov = Float((cam.camera?.fieldOfView ?? 35) * .pi / 180)
        let aspect = Float(max(v.bounds.width, 1) / max(v.bounds.height, 1))
        let hfov = 2 * atan(tan(vfov / 2) * aspect)
        let fov = min(vfov, hfov)
        let dist = (size / 2) / sin(fov / 2) * 1.08
        let pos = center + dir * dist
        cam.position = SCNVector3(pos.x, pos.y, pos.z)
        cam.camera?.zNear = Double(size) * 0.005
        cam.camera?.zFar = Double(size) * 50
        cam.look(at: SCNVector3(center.x, center.y, center.z), up: SCNVector3(0, 0, 1), localFront: SCNVector3(0, 0, -1))
        v.defaultCameraController.target = SCNVector3(center.x, center.y, center.z)
        v.defaultCameraController.worldUp = SCNVector3(0, 0, 1)
    }

    static func gradientImage() -> NSImage {
        let size = NSSize(width: 8, height: 512)
        let img = NSImage(size: size)
        img.lockFocus()
        let g = NSGradient(starting: Theme.viewportTop, ending: Theme.viewportBottom)
        g?.draw(in: NSRect(origin: .zero, size: size), angle: -90)
        img.unlockFocus()
        return img
    }
}
