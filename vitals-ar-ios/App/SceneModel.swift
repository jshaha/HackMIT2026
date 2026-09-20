import SwiftUI
import Combine
import simd

/// The patient's face on screen: bounding box plus a smooth closed outline (jawline + forehead).
struct FaceTrack: Equatable {
    var rect: CGRect
    var outline: [CGPoint]
    var center: CGPoint { CGPoint(x: rect.midX, y: rect.midY) }
}

/// A tracked hand: where the thumb–index pinch is on screen and whether it's closed.
struct HandTrack: Equatable {
    var point: CGPoint
    var pinching: Bool
}

final class HandModel: ObservableObject {
    @Published var track: HandTrack?
}

/// World-anchored overlay panels. Each panel is laid out flat on screen first (the "design" rects), then
/// lifted into 3D at the patient's head depth, angled toward the patient, and fixed in the world. Every frame
/// the corners are re-projected so panels keep their place as the phone moves.
final class SceneModel: ObservableObject {
    @Published private(set) var quads: [String: Quad] = [:]
    @Published var face: FaceTrack?
    /// Hand updates live in their own object so the cursor can move without re-rendering every panel.
    let hand = HandModel()
    @Published private(set) var anchored = false

    /// Flat screen layout, set by the view when the screen size is known.
    var design: [String: CGRect] = [:]
    var reanchorRequested = false

    /// Where the clinician has dragged each panel, as screen offsets from its default spot (persisted).
    @Published private(set) var offsets: [String: CGSize] = SceneModel.loadOffsets()
    /// The panel currently being dragged: it tracks the hand exactly instead of floating.
    @Published var dragging: String?

    func placed(_ id: String) -> CGRect? {
        design[id].map { $0.offsetBy(dx: offsets[id]?.width ?? 0, dy: offsets[id]?.height ?? 0) }
    }

    /// Move a panel, keeping it fully on screen.
    func setOffset(_ id: String, _ offset: CGSize) {
        guard let d = design[id] else { return }
        let b = bounds.isEmpty ? d : bounds.insetBy(dx: 6, dy: 6)
        let x = min(max(offset.width, b.minX - d.minX), b.maxX - d.maxX)
        let y = min(max(offset.height, b.minY - d.minY), b.maxY - d.maxY)
        offsets[id] = CGSize(width: x, height: y)
    }

    func saveOffsets() {
        UserDefaults.standard.set(offsets.mapValues { [$0.width, $0.height] }, forKey: "panelOffsets")
    }

    func resetOffsets() {
        offsets = [:]
        saveOffsets()
    }

    private static func loadOffsets() -> [String: CGSize] {
        let raw = UserDefaults.standard.dictionary(forKey: "panelOffsets") as? [String: [Double]] ?? [:]
        return raw.compactMapValues { $0.count == 2 ? CGSize(width: $0[0], height: $0[1]) : nil }
    }

    private var world: [String: [SIMD3<Float>]] = [:]

    /// Panel tilt: side panels turn toward the patient; the context panel tips its bottom edge forward.
    static let yaw: [String: Float] = ["visit": -12, "vitals": 12, "fatigue": 12, "emotion": 12]
    static let pitch: [String: Float] = ["context": -10]

    /// Visible screen area; panels are never allowed to stay outside it.
    var bounds: CGRect = .zero

    /// Latest head position in world space (set by the tracker); the panels softly follow it.
    var headWorld: SIMD3<Float>?

    /// Snap panels around the head right now.
    func anchor(head: SIMD3<Float>, projector: SceneProjector) {
        headWorld = head
        let built = build(head: head, projector: projector)
        guard !built.isEmpty else { return }
        world = built
        anchored = true
        reanchorRequested = false
        reproject(projector)
    }

    /// Project panels to the screen. Panels hold their place in the room for small, quick motions (real
    /// perspective and a little float) but follow the view closely (~0.2 s), and snap back fast if any corner
    /// leaves the screen, so a handheld phone never loses them off the edge.
    func reproject(_ projector: SceneProjector) {
        guard anchored else { return }
        let target = headWorld.map { build(head: $0, projector: projector) } ?? [:]
        var q: [String: Quad] = [:]
        for (id, current) in world {
            var c = current
            let projected = c.compactMap { projector.project($0) }
            let offscreen = projected.count < 4 || projected.contains { !bounds.insetBy(dx: -4, dy: -4).contains($0) }
            if let t = target[id] {
                let k: Float = id == dragging ? 1 : (offscreen && !bounds.isEmpty ? 0.45 : 0.15)
                c = zip(c, t).map { $0 + ($1 - $0) * k }
                world[id] = c
            }
            let p = c.compactMap { projector.project($0) }
            if p.count == 4 { q[id] = Quad(tl: p[0], tr: p[1], br: p[2], bl: p[3]) }
        }
        quads = q
    }

    /// Lift the flat design rects into a plane through the head facing the camera, then tilt each panel.
    private func build(head: SIMD3<Float>, projector: SceneProjector) -> [String: [SIMD3<Float>]] {
        guard !design.isEmpty else { return [:] }
        let n = simd_normalize(projector.cameraPosition - head)
        var result: [String: [SIMD3<Float>]] = [:]
        for id in design.keys {
            guard let rect = placed(id) else { continue }
            let corners = Quad(rect: rect)
            let pts = [corners.tl, corners.tr, corners.br, corners.bl].compactMap { p -> SIMD3<Float>? in
                guard let ray = projector.ray(through: p) else { return nil }
                let denom = simd_dot(ray.direction, n)
                guard abs(denom) > 1e-5 else { return nil }
                return ray.origin + ray.direction * (simd_dot(head - ray.origin, n) / denom)
            }
            guard pts.count == 4 else { continue }
            result[id] = tilt(pts, yawDeg: Self.yaw[id] ?? 0, pitchDeg: Self.pitch[id] ?? 0)
        }
        return result
    }

    /// Rotate a world quad about its own vertical axis (yaw) and horizontal axis (pitch).
    private func tilt(_ c: [SIMD3<Float>], yawDeg: Float, pitchDeg: Float) -> [SIMD3<Float>] {
        let center = (c[0] + c[1] + c[2] + c[3]) / 4
        let r = simd_normalize(c[1] - c[0]), u = simd_normalize(c[0] - c[3])
        let n = simd_normalize(simd_cross(r, u)) // toward the camera
        let yaw = yawDeg * .pi / 180, pitch = pitchDeg * .pi / 180
        return c.map { p in
            var o = p - center
            let a = simd_dot(o, r)
            o = o - r * a + (r * cos(yaw) + n * sin(yaw)) * a
            let b = simd_dot(o, u)
            o = o - u * b + (u * cos(pitch) + n * sin(pitch)) * b
            return center + o
        }
    }
}

/// Screen layout used before anchoring and as the source for the 3D panels.
enum Layout {
    static let visitW: CGFloat = 200, rightW: CGFloat = 220

    static func design(size: CGSize, inset: EdgeInsets) -> [String: CGRect] {
        let L = inset.leading + 12, R = size.width - inset.trailing - 12
        let top = max(inset.top, 10) + 30, bottom = size.height - max(inset.bottom, 10)
        let gapL = L + visitW + 12, gapR = R - rightW - 12
        let contextW = min(420, gapR - gapL)
        var y = top
        var rects: [String: CGRect] = ["visit": CGRect(x: L, y: top, width: visitW, height: bottom - top)]
        for (id, h) in [("vitals", 104.0), ("fatigue", 118.0), ("emotion", 124.0)] {
            rects[id] = CGRect(x: R - rightW, y: y, width: rightW, height: h)
            y += h + 8
        }
        rects["context"] = CGRect(x: (gapL + gapR - contextW) / 2, y: bottom - 100, width: contextW, height: 100)
        return rects
    }
}
