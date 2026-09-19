import SwiftUI
import ARKit
import RealityKit
import Vision

/// Rear-camera AR view. ARKit world tracking keeps the panels fixed in the room; Vision tracks the
/// patient's face (outline + 3D head position) and the clinician's pinch.
struct CameraView: UIViewRepresentable {
    let scene: SceneModel

    final class Coordinator: NSObject, ARSessionDelegate, SceneProjector {
        let scene: SceneModel
        weak var view: ARView?
        private let vision = VisionTracker()
        private let smoother = TrackSmoother()
        private var lastProjection = 0.0
        private var faceSeenSince: Double?

        init(scene: SceneModel) { self.scene = scene }

        // MARK: SceneProjector
        func ray(through point: CGPoint) -> (origin: SIMD3<Float>, direction: SIMD3<Float>)? { view?.ray(through: point) }
        func project(_ world: SIMD3<Float>) -> CGPoint? { view?.project(world) }
        var cameraPosition: SIMD3<Float> { view?.cameraTransform.translation ?? .zero }

        func session(_ session: ARSession, didUpdate frame: ARFrame) {
            guard let view, view.bounds.width > 0 else { return }
            let now = frame.timestamp
            if now - lastProjection > 1.0 / 30 {
                lastProjection = now
                scene.reproject(self)
            }
            // Live mode: stream the camera to the Mac pipeline (no-op unless the Mac is connected).
            PhoneLink.shared.sendVideoFrame(frame.capturedImage, timestamp: frame.timestamp)
            guard !vision.busy else { return }
            let size = view.bounds.size
            let orientation = view.window?.windowScene?.interfaceOrientation ?? .landscapeRight
            let display = frame.displayTransform(for: orientation, viewportSize: size)
            let intrinsics = frame.camera.intrinsics
            let cameraTransform = frame.camera.transform
            let imageSize = CGSize(width: CVPixelBufferGetWidth(frame.capturedImage), height: CVPixelBufferGetHeight(frame.capturedImage))
            let toView = { (p: CGPoint) -> CGPoint in
                let n = p.applying(display)
                return CGPoint(x: n.x * size.width, y: n.y * size.height)
            }
            vision.process(frame.capturedImage) { [weak self] result in
                guard let self else { return }
                if let face = self.smoother.face(result.face, now: now, toView: toView) { self.scene.face = face }
                self.scene.hand.track = self.smoother.hand(result.hands, now: now, toView: toView)

                // Track the head in 3D; anchor the panels around it once the face has been steady for a moment.
                if let f = result.face {
                    let head = Self.headPosition(f, intrinsics: intrinsics, camera: cameraTransform, imageSize: imageSize)
                    if let prev = self.scene.headWorld { self.scene.headWorld = prev + (head - prev) * 0.3 } else { self.scene.headWorld = head }
                    self.faceSeenSince = self.faceSeenSince ?? now
                    if (!self.scene.anchored || self.scene.reanchorRequested) && now - self.faceSeenSince! > 0.6 {
                        self.scene.anchor(head: self.scene.headWorld!, projector: self)
                    }
                } else {
                    self.faceSeenSince = nil
                }
            }
        }

        /// Head position in world space from the face box: distance from its pixel width (faces are ~15 cm wide)
        /// and the camera's focal length, then back-projected through the camera.
        static func headPosition(_ o: VNFaceObservation, intrinsics K: simd_float3x3, camera: simd_float4x4, imageSize: CGSize) -> SIMD3<Float> {
            let fx = K[0][0], fy = K[1][1], cx = K[2][0], cy = K[2][1]
            let W = Float(imageSize.width), H = Float(imageSize.height)
            let b = o.boundingBox
            let depth = fx * 0.15 / max(Float(b.width) * W, 1)
            let u = Float(b.midX) * W, v = (1 - Float(b.midY)) * H
            let p = SIMD4<Float>((u - cx) / fx * depth, -(v - cy) / fy * depth, -depth, 1)
            let w = camera * p
            return SIMD3(w.x, w.y, w.z)
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(scene: scene) }

    func makeUIView(context: Context) -> ARView {
        let view = ARView(frame: .zero, cameraMode: .ar, automaticallyConfigureSession: false)
        view.renderOptions.insert(.disableMotionBlur)
        context.coordinator.view = view
        view.session.delegate = context.coordinator
        let config = ARWorldTrackingConfiguration()
        config.worldAlignment = .gravity
        view.session.run(config)
        return view
    }

    func updateUIView(_ uiView: ARView, context: Context) {}
}

/// Simulator stand-in: a virtual camera drifting around a patient silhouette, so the 3D panels, face outline
/// and leader lines can be checked without a device.
struct SimulatedPatientView: View {
    let scene: SceneModel
    @State private var projector = SyntheticProjector()
    private let clock = Timer.publish(every: 1.0 / 30, on: .main, in: .common).autoconnect()
    static let head = SIMD3<Float>(0, 0.02, -0.85)

    var body: some View {
        GeometryReader { geo in
            let size = geo.size
            Canvas { ctx, _ in
                ctx.fill(Path(CGRect(origin: .zero, size: size)),
                         with: .linearGradient(Gradient(colors: [Color(white: 0.22), Color(white: 0.08)]),
                                               startPoint: .zero, endPoint: CGPoint(x: 0, y: size.height)))
                guard let face = scene.face?.rect else { return }
                var body = Path()
                body.addRoundedRect(in: CGRect(x: face.midX - face.width * 1.4, y: face.maxY + face.height * 0.25,
                                               width: face.width * 2.8, height: face.height * 2),
                                    cornerSize: CGSize(width: face.width * 0.9, height: face.width * 0.9))
                ctx.fill(body, with: .color(Color(red: 0.28, green: 0.36, blue: 0.44)))
                ctx.fill(Path(CGRect(x: face.midX - face.width * 0.22, y: face.maxY - face.height * 0.12,
                                     width: face.width * 0.44, height: face.height * 0.45)),
                         with: .color(Color(red: 0.72, green: 0.55, blue: 0.46)))
                ctx.fill(Path(ellipseIn: face), with: .color(Color(red: 0.8, green: 0.62, blue: 0.52)))
                ctx.fill(Path(ellipseIn: CGRect(x: face.minX - 3, y: face.minY - 8, width: face.width + 6, height: face.height * 0.42)),
                         with: .color(Color(red: 0.2, green: 0.14, blue: 0.1)))
            }
            .onReceive(clock) { date in
                projector.update(time: date.timeIntervalSinceReferenceDate, viewSize: size)
                // Face box and outline from the projected head.
                if let c = projector.project(Self.head), let e = projector.project(Self.head + SIMD3(0.075, 0.095, 0)) {
                    let rect = CGRect(x: c.x - abs(e.x - c.x), y: c.y - abs(e.y - c.y), width: 2 * abs(e.x - c.x), height: 2 * abs(e.y - c.y))
                    let outline = (0..<TrackSmoother.samples).map { i -> CGPoint in
                        let a = -Double.pi / 2 + Double(i) / Double(TrackSmoother.samples) * 2 * .pi
                        return CGPoint(x: rect.midX + rect.width * 0.54 * CGFloat(cos(a)), y: rect.midY + rect.height * 0.56 * CGFloat(sin(a)))
                    }
                    scene.face = FaceTrack(rect: rect, outline: outline)
                }
                if !scene.anchored || scene.reanchorRequested { scene.anchor(head: Self.head, projector: projector) }
                scene.headWorld = Self.head
                scene.reproject(projector)
            }
        }
    }
}

/// Pinhole camera orbiting gently around the origin (simulator only).
struct SyntheticProjector: SceneProjector {
    var position = SIMD3<Float>(0, 0, 0)
    var yaw: Float = 0, pitch: Float = 0
    var size = CGSize(width: 844, height: 390)
    let focal: Float = 620

    mutating func update(time t: Double, viewSize: CGSize) {
        size = viewSize
        position = SIMD3(Float(0.07 * sin(t * 0.6)), Float(0.015 * sin(t * 0.8)), Float(0.02 * sin(t * 0.45)))
        yaw = Float(0.08 * sin(t * 0.6))
        pitch = Float(0.02 * sin(t * 0.5))
    }

    private var rotation: simd_float3x3 {
        let cy = cos(yaw), sy = sin(yaw), cp = cos(pitch), sp = sin(pitch)
        let ry = simd_float3x3(SIMD3(cy, 0, -sy), SIMD3(0, 1, 0), SIMD3(sy, 0, cy))
        let rx = simd_float3x3(SIMD3(1, 0, 0), SIMD3(0, cp, sp), SIMD3(0, -sp, cp))
        return ry * rx
    }

    var cameraPosition: SIMD3<Float> { position }

    func ray(through p: CGPoint) -> (origin: SIMD3<Float>, direction: SIMD3<Float>)? {
        let d = SIMD3<Float>((Float(p.x) - Float(size.width) / 2) / focal, -(Float(p.y) - Float(size.height) / 2) / focal, -1)
        return (position, simd_normalize(rotation * d))
    }

    func project(_ w: SIMD3<Float>) -> CGPoint? {
        let c = rotation.transpose * (w - position)
        guard c.z < -0.01 else { return nil }
        return CGPoint(x: CGFloat(Float(size.width) / 2 + focal * c.x / -c.z), y: CGFloat(Float(size.height) / 2 - focal * c.y / -c.z))
    }
}
