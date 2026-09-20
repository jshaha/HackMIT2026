import Vision
import CoreVideo
import CoreGraphics

/// Runs face landmarks + hand pose on camera frames off the main thread (dropping frames while busy).
final class VisionTracker {
    struct Result {
        var face: VNFaceObservation?
        var hands: [VNHumanHandPoseObservation]
    }

    private let queue = DispatchQueue(label: "vision", qos: .userInteractive)
    private(set) var busy = false

    func process(_ buffer: CVPixelBuffer, completion: @escaping (Result) -> Void) {
        guard !busy else { return }
        busy = true
        queue.async { [weak self] in
            let faces = VNDetectFaceLandmarksRequest()
            let hands = VNDetectHumanHandPoseRequest()
            hands.maximumHandCount = 2
            // ARKit frames arrive in the sensor's landscape orientation, matching the app's landscape-right UI.
            let handler = VNImageRequestHandler(cvPixelBuffer: buffer, orientation: .up, options: [:])
            try? handler.perform([faces, hands])
            let best = (faces.results ?? []).max { $0.boundingBox.width < $1.boundingBox.width }
            let result = Result(face: best, hands: hands.results ?? [])
            DispatchQueue.main.async {
                self?.busy = false
                completion(result)
            }
        }
    }
}

/// Turns Vision observations into screen-space tracks: a smoothed closed face outline and a debounced pinch.
final class TrackSmoother {
    private var outline: [CGPoint] = []
    private var pinching = false
    private var releaseSince: Double?
    private var lastFaceTime = 0.0
    static let samples = 56

    /// `toView` maps normalized image coordinates (top-left origin) to view points.
    func face(_ o: VNFaceObservation?, now: Double, toView: (CGPoint) -> CGPoint) -> FaceTrack?? {
        guard let o else {
            if now - lastFaceTime > 0.8 { outline = []; return .some(nil) }
            return nil // keep showing the last face briefly
        }
        lastFaceTime = now
        let b = o.boundingBox
        defer { lastFaceRect = outline.isEmpty ? nil : lastFaceRectCandidate }
        let corners = [CGPoint(x: b.minX, y: 1 - b.maxY), CGPoint(x: b.maxX, y: 1 - b.minY)].map(toView)
        let rect = CGRect(x: min(corners[0].x, corners[1].x), y: min(corners[0].y, corners[1].y),
                          width: abs(corners[1].x - corners[0].x), height: abs(corners[1].y - corners[0].y))
        lastFaceRectCandidate = rect

        // Jawline from the landmarks, closed over the forehead with an arc.
        var raw: [CGPoint]
        if let contour = o.landmarks?.faceContour, contour.pointCount > 4 {
            let jaw = contour.normalizedPoints.map { p in
                toView(CGPoint(x: b.minX + CGFloat(p.x) * b.width, y: 1 - (b.minY + CGFloat(p.y) * b.height)))
            }
            raw = jaw + foreheadArc(from: jaw.last!, to: jaw.first!, top: rect.minY - rect.height * 0.08)
        } else {
            raw = (0..<32).map { i in
                let a = Double(i) / 32 * 2 * .pi
                return CGPoint(x: rect.midX + rect.width * 0.5 * CGFloat(cos(a)), y: rect.midY + rect.height * 0.58 * CGFloat(sin(a)))
            }
        }
        let resampled = Self.resample(Self.smoothClosed(raw), count: Self.samples)
        if outline.count == resampled.count {
            outline = zip(outline, resampled).map { CGPoint(x: $0.x + ($1.x - $0.x) * 0.4, y: $0.y + ($1.y - $0.y) * 0.4) }
        } else {
            outline = resampled
        }
        return .some(FaceTrack(rect: rect, outline: outline))
    }

    private var lastHand: HandTrack?
    private var lastHandTime = 0.0
    private var lastFaceRect: CGRect?
    private var lastFaceRectCandidate: CGRect?
    private var stableFrames = 0 // consecutive frames the current hand has been seen
    private var pinchFrames = 0 // consecutive frames the fingers have been closed

    /// Frames a hand must be seen steadily before it becomes a cursor.
    static let acquireFrames = 4

    /// Returns the pinch track. False positives (textures, hair, sleeves, the patient's face) are rejected:
    /// a hand must be confidently detected with most of its joints, be a plausible size, sit off the patient's
    /// face, and be seen steadily for a few frames before the cursor appears. Only the thumb and index tips plus
    /// a palm reference are needed, so a hand reaching toward the frame edge (wrist out of view) still tracks.
    func hand(_ hands: [VNHumanHandPoseObservation], now: Double, toView: (CGPoint) -> CGPoint) -> HandTrack? {
        func dist(_ a: CGPoint, _ b: CGPoint) -> CGFloat { hypot(a.x - b.x, a.y - b.y) }
        let noFaceZone = lastFaceRect?.insetBy(dx: -10, dy: -10)

        let candidates = hands.compactMap { o -> (thumb: CGPoint, index: CGPoint, size: CGFloat)? in
            guard o.confidence > 0.5, let joints = try? o.recognizedPoints(.all) else { return nil }
            let confident = joints.filter { $0.value.confidence > 0.3 }
            guard confident.count >= 12 else { return nil }
            func point(_ j: VNHumanHandPoseObservation.JointName) -> CGPoint? {
                confident[j].map { toView(CGPoint(x: $0.location.x, y: 1 - $0.location.y)) }
            }
            guard let t = point(.thumbTip), let i = point(.indexTip) else { return nil }
            // Hand scale: wrist→middle knuckle if visible, else palm width, else index finger length.
            let size: CGFloat
            if let w = point(.wrist), let m = point(.middleMCP) { size = dist(w, m) }
            else if let a = point(.indexMCP), let b = point(.littleMCP) { size = dist(a, b) * 1.3 }
            else if let a = point(.indexMCP) { size = dist(a, i) * 1.1 }
            else { return nil }
            guard size > 18, size < 420 else { return nil }
            if let zone = noFaceZone, zone.contains(mid(t, i)) { return nil }
            return (t, i, size)
        }

        // Stay with the same hand from frame to frame; a detection far from the current one starts over.
        let continuing = lastHand.flatMap { last -> (thumb: CGPoint, index: CGPoint, size: CGFloat)? in
            guard now - lastHandTime < 0.6 else { return nil }
            return candidates.min { dist(mid($0.thumb, $0.index), last.point) < dist(mid($1.thumb, $1.index), last.point) }
                .flatMap { dist(mid($0.thumb, $0.index), last.point) < 150 ? $0 : nil }
        }
        guard let h = continuing ?? candidates.max(by: { $0.size < $1.size }) else {
            // Brief tracking gaps keep an established cursor for a moment; never an unconfirmed one.
            if let last = lastHand, stableFrames >= Self.acquireFrames, now - lastHandTime < 0.4 { return last }
            reset()
            return nil
        }
        if continuing == nil { stableFrames = 0; pinchFrames = 0; pinching = false; lastHand = nil }
        stableFrames += 1

        // Pinch must hold for 2 frames to close; release is debounced.
        let ratio = dist(h.thumb, h.index) / max(1, h.size)
        if ratio < 0.3 {
            pinchFrames += 1
            if pinchFrames >= 2 { pinching = true }
            releaseSince = nil
        } else {
            pinchFrames = 0
            if ratio > 0.45 && pinching {
                if let since = releaseSince { if now - since > 0.12 { pinching = false; releaseSince = nil } } else { releaseSince = now }
            } else {
                releaseSince = nil
            }
        }

        // Smooth the pointer, but not while it moves fast.
        var p = mid(h.thumb, h.index)
        if let last = lastHand {
            let k: CGFloat = dist(p, last.point) > 30 ? 0.9 : 0.6
            p = CGPoint(x: last.point.x + (p.x - last.point.x) * k, y: last.point.y + (p.y - last.point.y) * k)
        }
        let track = HandTrack(point: p, pinching: pinching && stableFrames >= Self.acquireFrames)
        lastHand = track
        lastHandTime = now
        return stableFrames >= Self.acquireFrames ? track : nil
    }

    private func reset() {
        lastHand = nil
        stableFrames = 0
        pinchFrames = 0
        pinching = false
        releaseSince = nil
    }

    private func foreheadArc(from a: CGPoint, to b: CGPoint, top: CGFloat) -> [CGPoint] {
        let c = mid(a, b)
        let rx = max(1, hypot(b.x - a.x, b.y - a.y) / 2), ry = max(1, c.y - top)
        let ta = atan2(Double(a.y - c.y) / Double(ry), Double(a.x - c.x) / Double(rx))
        let tb = atan2(Double(b.y - c.y) / Double(ry), Double(b.x - c.x) / Double(rx))
        // Two ways round from a to b; take the one that passes over the top of the head (smaller y).
        let options = [tb - ta, tb - ta + (tb - ta > 0 ? -2 : 2) * .pi]
        let pts = options.map { d in (1..<12).map { i -> CGPoint in
            let t = ta + d * Double(i) / 12
            return CGPoint(x: c.x + rx * CGFloat(cos(t)), y: c.y + ry * CGFloat(sin(t)))
        } }
        return pts.min { $0[5].y < $1[5].y }!
    }

    /// Catmull–Rom through a closed polygon.
    static func smoothClosed(_ p: [CGPoint], steps: Int = 4) -> [CGPoint] {
        guard p.count > 3 else { return p }
        var out: [CGPoint] = []
        for i in 0..<p.count {
            let p0 = p[(i - 1 + p.count) % p.count], p1 = p[i], p2 = p[(i + 1) % p.count], p3 = p[(i + 2) % p.count]
            for s in 0..<steps {
                let t = CGFloat(s) / CGFloat(steps), t2 = t * t, t3 = t2 * t
                func f(_ a: CGFloat, _ b: CGFloat, _ c: CGFloat, _ d: CGFloat) -> CGFloat {
                    0.5 * (2 * b + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t2 + (-a + 3 * b - 3 * c + d) * t3)
                }
                out.append(CGPoint(x: f(p0.x, p1.x, p2.x, p3.x), y: f(p0.y, p1.y, p2.y, p3.y)))
            }
        }
        return out
    }

    /// Evenly spaced points along a closed polyline, starting from the topmost point (stable ordering).
    static func resample(_ p: [CGPoint], count: Int) -> [CGPoint] {
        guard p.count > 2 else { return p }
        let start = p.indices.min { p[$0].y < p[$1].y }!
        let ring = Array(p[start...] + p[..<start]) + [p[start]]
        var cum: [CGFloat] = [0]
        for i in 1..<ring.count { cum.append(cum[i - 1] + hypot(ring[i].x - ring[i - 1].x, ring[i].y - ring[i - 1].y)) }
        let total = cum.last!
        var out: [CGPoint] = [], j = 0
        for k in 0..<count {
            let d = total * CGFloat(k) / CGFloat(count)
            while j < cum.count - 2 && cum[j + 1] < d { j += 1 }
            let seg = max(cum[j + 1] - cum[j], 1e-6), t = (d - cum[j]) / seg
            out.append(CGPoint(x: ring[j].x + (ring[j + 1].x - ring[j].x) * t, y: ring[j].y + (ring[j + 1].y - ring[j].y) * t))
        }
        return out
    }
}
