import SwiftUI

/// The clinician's view while talking with the patient. Panels live in the room around the patient's head
/// (see `SceneModel`); a soft outline traces the patient's face with lines flowing out to each metric; the visit
/// checklist is operated by pinching in the scene (or tapping as a fallback).
struct OverlayView: View {
    let vitals: Vitals
    let hrvHistory: [Double]
    let micLevel: Double
    let connection: VitalsStore.Connection
    @ObservedObject var scene: SceneModel
    @ObservedObject var plan: VisitPlan
    /// Screen size and safe-area insets, measured by the parent through SwiftUI. (Reading UIKit window
    /// metrics inside `body` forces a re-entrant layout and SwiftUI stops updating the view.)
    let size: CGSize
    let inset: EdgeInsets
    /// Overall panel size, from Settings (0.6–1.2).
    let scale: CGFloat
    var openSettings: () -> Void

    @State private var visitFrames: [VisitTarget: CGRect] = [:]
    @State private var checking: Set<UUID> = []
    /// What a pinch would act on: the panel you're aiming at (screen centre, like gaze on Vision Pro) or the
    /// one your hand is over, plus the control within it.
    private struct Focus: Equatable {
        var panel: String?
        var control: VisitTarget?
    }
    @State private var pinch: PinchState?
    @State private var pinchFlash = false
    @State private var touchDrag: (id: String, start: CGSize)?

    /// A pinch in progress: a quick pinch activates what's under it; pinch-and-move drags the panel.
    private struct PinchState {
        var start: CGPoint
        var panel: String?
        var startOffset: CGSize
        var focus: Focus
        var began = Date()
        var last: CGPoint
        var dragging = false
        var cancelled = false // erratic motion: neither drag nor activate
    }

    /// A deliberate grab: hold the pinch this long, then move this far, before a panel starts moving.
    private static let grabHold: TimeInterval = 0.25
    private static let grabDistance: CGFloat = 26
    /// A pointer jump bigger than this between updates is a tracking glitch, not a hand movement.
    private static let glitchJump: CGFloat = 110

    /// Topmost first (matches draw order, reversed).
    private static let hitOrder = ["visit", "emotion", "fatigue", "vitals", "context"]

    var body: some View {
        let base = Layout.design(size: size, inset: inset, scale: scale, topics: plan.topics.count)
        let design = Dictionary(uniqueKeysWithValues: base.map { id, r in
            (id, r.offsetBy(dx: scene.offsets[id]?.width ?? 0, dy: scene.offsets[id]?.height ?? 0))
        })
        let v = vitals
        let focus = focusTarget(design: design)

        ZStack(alignment: .topLeading) {
            TimelineView(.animation(minimumInterval: 1 / 30)) { tl in
                Canvas { ctx, _ in drawOutlineAndLeaders(ctx, design: design, t: tl.date.timeIntervalSinceReferenceDate) }
            }
            .allowsHitTesting(false)

            panel("context", design, focused: focus.panel == "context") {
                ContextCard(summary: v.contextSummary, insights: v.insights, step: v.agentStep,
                            transcript: v.transcript, speaking: v.speaking, micLevel: micLevel)
            }
            panel("vitals", design, focused: focus.panel == "vitals") {
                VitalsCard(hr: v.hr, hrConfidence: v.hrConfidence, br: v.br, hrv: v.hrv, hrvHistory: hrvHistory)
            }
            panel("fatigue", design, focused: focus.panel == "fatigue") {
                FatigueCard(score: v.fatigue, threshold: v.fatigueThreshold, confidence: v.fatigueConfidence, drivers: v.fatigueDrivers)
            }
            panel("emotion", design, focused: focus.panel == "emotion") {
                EmotionCard(label: v.emotion, valence: v.valence, arousal: v.arousal, probs: v.emotionProbs, context: v.emotionContext)
            }
            panel("visit", design, focused: focus.panel == "visit") {
                VisitCard(plan: plan, hover: focus.control.flatMap { if case .topic(let id) = $0 { id } else { nil } },
                          checking: checking, pauseHover: focus.control == .pause)
                    .onPreferenceChange(VisitFramesKey.self) { visitFrames = $0 }
            }

            AimReticle(active: focus.panel != nil, pinching: pinchFlash)
                .position(x: size.width / 2, y: size.height / 2)
            CursorLayer(hand: scene.hand)

            StatusBar(connection: connection, signal: v.signalQuality, micLevel: micLevel,
                      recenter: { scene.reanchorRequested = true }, openSettings: openSettings)
                .fixedSize()
                .position(x: size.width / 2, y: max(inset.top, 10) + 14)
        }
        .frame(width: size.width, height: size.height, alignment: .topLeading)
        .contentShape(Rectangle())
        .simultaneousGesture(SpatialTapGesture().onEnded { activate(target(at: $0.location, design: design) ?? focus.control) })
        .simultaneousGesture(
            // Touch fallback: drag a panel with a finger on the screen.
            DragGesture(minimumDistance: 12)
                .onChanged { g in
                    if touchDrag == nil, let id = panel(at: g.startLocation, design: design) {
                        touchDrag = (id, scene.offsets[id] ?? .zero)
                        scene.dragging = id
                    }
                    if let d = touchDrag {
                        scene.setOffset(d.id, CGSize(width: d.start.width + g.translation.width, height: d.start.height + g.translation.height))
                    }
                }
                .onEnded { _ in
                    if touchDrag != nil { scene.dragging = nil; scene.saveOffsets() }
                    touchDrag = nil
                }
        )
        .onReceive(scene.hand.$track) { track in handlePinch(track, design: design, focus: focus) }
        #if DEBUG
        .task { await debugPinches() }
        #endif
        .ignoresSafeArea()
    }

    /// A flat card rendered onto its quad in the scene (or its flat design rect before anchoring).
    /// The panel being dragged is outlined in the accent colour.
    private func panel<Content: View>(_ id: String, _ design: [String: CGRect], focused: Bool = false,
                                      @ViewBuilder content: () -> Content) -> some View {
        let rect = design[id] ?? .zero
        let quad = scene.quads[id] ?? Quad(rect: rect)
        let lifted = scene.dragging == id
        let natural = Layout.naturalSize(id, screenHeight: rect.height / scale, scale: scale, topics: plan.topics.count)
        return content()
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous)
                .stroke(Palette.accent.opacity(lifted ? 1 : (focused ? 0.45 : 0)), lineWidth: lifted ? 2.5 / scale : 1.5 / scale))
            .overlay(alignment: .bottom) { // visionOS-style grab bar: pinch and move to reposition
                Capsule().fill(Palette.ink.opacity(lifted ? 0.5 : 0.25))
                    .frame(width: 34 / scale, height: 4 / scale)
                    .offset(y: 9 / scale)
                    .opacity(focused || lifted ? 1 : 0)
            }
            .frame(width: natural.width, height: natural.height)
            .scaleEffect(scale, anchor: .topLeading)
            .frame(width: rect.width, height: rect.height, alignment: .topLeading)
            .projectionEffect(Homography.projection(from: rect.size, to: quad))
    }

    /// The panel under a screen point, if any.
    private func panel(at p: CGPoint, design: [String: CGRect]) -> String? {
        Self.hitOrder.first { id in
            guard let rect = design[id], let local = Homography.unproject(p, size: rect.size, quad: scene.quads[id] ?? Quad(rect: rect))
            else { return false }
            return CGRect(origin: .zero, size: rect.size).contains(local)
        }
    }

    /// Vision Pro-style: what you aim at is focused; a pinch anywhere acts on it. If your hand happens to be
    /// over a panel, that takes precedence (direct touch), so both ways of pointing work.
    private func focusTarget(design: [String: CGRect]) -> Focus {
        let centre = CGPoint(x: size.width / 2, y: size.height / 2)
        let handPoint = scene.hand.track?.point
        if let p = handPoint, let id = panel(at: p, design: design) {
            return Focus(panel: id, control: id == "visit" ? target(at: p, design: design) : nil)
        }
        guard let id = panel(at: centre, design: design) else { return Focus() }
        return Focus(panel: id, control: id == "visit" ? target(at: centre, design: design, reach: 60) : nil)
    }

    private func handlePinch(_ track: HandTrack?, design: [String: CGRect], focus: Focus) {
        let pinching = track?.pinching ?? false
        if let t = track, pinching {
            if var p = pinch {
                let jump = hypot(t.point.x - p.last.x, t.point.y - p.last.y)
                let dx = t.point.x - p.start.x, dy = t.point.y - p.start.y
                if jump > Self.glitchJump {
                    if !p.dragging { p.cancelled = true } // tracking glitch: never let it start a drag
                } else {
                    p.last = t.point
                    if !p.dragging, !p.cancelled, p.focus.panel != nil, hypot(dx, dy) > Self.grabDistance {
                        if Date().timeIntervalSince(p.began) >= Self.grabHold {
                            p.dragging = true
                            scene.dragging = p.focus.panel
                            UIImpactFeedbackGenerator(style: .light).impactOccurred()
                        } else {
                            p.cancelled = true // flicked straight after pinching: not a deliberate grab
                        }
                    }
                    if p.dragging, let id = p.focus.panel {
                        scene.setOffset(id, CGSize(width: p.startOffset.width + dx, height: p.startOffset.height + dy))
                    }
                }
                pinch = p
            } else {
                // Pinch closed: act on whatever is focused right now, wherever the hand is.
                pinch = PinchState(start: t.point, panel: focus.panel,
                                   startOffset: focus.panel.flatMap { scene.offsets[$0] } ?? .zero,
                                   focus: focus, last: t.point)
                pinchFlash = true
                UIImpactFeedbackGenerator(style: .rigid).impactOccurred()
            }
        } else if let p = pinch {
            // Released (or hand lost): a drag drops the panel; a clean quick pinch selects.
            if p.dragging {
                scene.dragging = nil
                scene.saveOffsets()
            } else if !p.cancelled, track != nil {
                activate(p.focus.control)
            }
            pinch = nil
            pinchFlash = false
        }
    }

    private func localPoint(_ p: CGPoint, design: [String: CGRect]) -> CGPoint? {
        guard let rect = design["visit"] else { return nil }
        let quad = scene.quads["visit"] ?? Quad(rect: rect)
        guard let local = Homography.unproject(p, size: rect.size, quad: quad),
              CGRect(origin: .zero, size: rect.size).insetBy(dx: -40 * scale, dy: -30 * scale).contains(local) else { return nil }
        return CGPoint(x: local.x / scale, y: local.y / scale) // panel-local (unscaled) space
    }

    /// The control under a point, forgiving: anything within reach snaps to the nearest target.
    private func target(at p: CGPoint, design: [String: CGRect], reach: CGFloat = 26) -> VisitTarget? {
        guard let local = localPoint(p, design: design) else { return nil }
        let scored = visitFrames.map { key, r -> (VisitTarget, CGFloat) in
            let dx = max(r.minX - local.x, 0, local.x - r.maxX), dy = max(r.minY - local.y, 0, local.y - r.maxY)
            return (key, hypot(dx, dy))
        }
        return scored.filter { $0.1 <= reach }.min { $0.1 < $1.1 }?.0
    }

    private func activate(_ target: VisitTarget?) {
        switch target {
        case .topic(let id):
            guard let topic = plan.topics.first(where: { $0.id == id }), !checking.contains(id) else { return }
            withAnimation(.easeOut(duration: 0.2)) { _ = checking.insert(id) }
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.45) {
                withAnimation(.easeInOut(duration: 0.3)) { plan.cover(topic) }
                checking.remove(id)
            }
        case .pause:
            plan.togglePause()
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
        case .undo:
            withAnimation(.easeOut(duration: 0.25)) { plan.undo() }
        case nil:
            break
        }
    }

    #if DEBUG
    /// `-debugPinch` launch argument: pinch the first topic twice, through the full scene → panel mapping,
    /// with a hand cursor, so the in-scene interaction can be exercised in the simulator.
    private func debugPinches() async {
        guard ProcessInfo.processInfo.arguments.contains("-debugPinch") else { return }
        for _ in 0..<2 {
            try? await Task.sleep(for: .seconds(4))
            let design = Layout.design(size: size, inset: inset, scale: scale, topics: plan.topics.count)
            guard let rect = design["visit"], let first = plan.topics.first,
                  let frame = visitFrames[.topic(first.id)],
                  let point = Homography.apply(CGPoint(x: frame.midX, y: frame.midY), size: rect.size,
                                               quad: scene.quads["visit"] ?? Quad(rect: rect)) else { continue }
            scene.hand.track = HandTrack(point: point, pinching: false)
            try? await Task.sleep(for: .seconds(1))
            scene.hand.track = HandTrack(point: point, pinching: true)
            try? await Task.sleep(for: .milliseconds(300))
            scene.hand.track = HandTrack(point: point, pinching: false)
        }
        // Pinch the fatigue panel and drag it up-left.
        try? await Task.sleep(for: .seconds(2))
        guard let q = scene.quads["fatigue"] else { return }
        let start = q.center
        scene.hand.track = HandTrack(point: start, pinching: true)
        try? await Task.sleep(for: .milliseconds(350))
        for i in 1...30 {
            try? await Task.sleep(for: .milliseconds(33))
            let k = CGFloat(i) / 30
            scene.hand.track = HandTrack(point: CGPoint(x: start.x - 140 * k, y: start.y + 30 * k), pinching: true)
        }
        try? await Task.sleep(for: .milliseconds(600))
        scene.hand.track = HandTrack(point: CGPoint(x: start.x - 140, y: start.y + 30), pinching: false)
    }
    #endif

    // MARK: Face outline + leader lines

    /// Where a ray leaving the centre of `oval` in direction `dir` crosses its edge.
    private static func exit(toward dir: CGVector, of oval: CGRect) -> CGPoint {
        let c = CGPoint(x: oval.midX, y: oval.midY)
        let a = oval.width / 2, b = oval.height / 2
        let k = sqrt(pow(dir.dx / a, 2) + pow(dir.dy / b, 2))
        guard k > 1e-6 else { return c }
        return CGPoint(x: c.x + dir.dx / k, y: c.y + dir.dy / k)
    }

    private func drawOutlineAndLeaders(_ ctx: GraphicsContext, design: [String: CGRect], t: Double) {
        guard let face = scene.face else { return }
        let glow = Color(red: 0.3, green: 0.95, blue: 0.8) // bright teal: these lines sit on the camera image, not on frost
        // One clean closed circle around the patient — the original framing. A true circle, sized off the face
        // and centred on it, not a traced outline: it says "this is who we're reading" and nothing more.
        let r = min(max(max(face.rect.width, face.rect.height) * 0.72, 60), size.height * 0.42)
        let frame = CGRect(x: face.center.x - r, y: face.center.y - r, width: 2 * r, height: 2 * r)
        let ring = Path(ellipseIn: frame)
        ctx.stroke(ring, with: .color(.black.opacity(0.18)), lineWidth: 3)
        ctx.stroke(ring, with: .color(.white.opacity(0.32)), lineWidth: 1)

        // Each metric panel gets a line from the circle, in the panel's direction, to the panel's inner edge.
        let c = face.center
        let edges: [(String, (Quad) -> CGPoint)] = [
            ("vitals", { $0.leftMid }), ("fatigue", { $0.leftMid }), ("emotion", { $0.leftMid }), ("context", { $0.topMid }),
        ]
        for (id, edge) in edges {
            guard let rect = design[id] else { continue }
            let end = edge(scene.quads[id] ?? Quad(rect: rect))
            let dir = CGVector(dx: end.x - c.x, dy: end.y - c.y)
            let len = max(1, hypot(dir.dx, dir.dy))
            let start = Self.exit(toward: dir, of: frame)
            guard hypot(end.x - start.x, end.y - start.y) > 12, len > 1 else { continue }
            var line = Path()
            line.move(to: start)
            line.addLine(to: end)
            ctx.stroke(line, with: .color(.white.opacity(0.22)), lineWidth: 1)
            // Data flowing outward from the face to the metric
            ctx.stroke(line, with: .color(glow.opacity(0.75)),
                       style: StrokeStyle(lineWidth: 1.4, lineCap: .round, dash: [3, 9], dashPhase: CGFloat(-t * 24)))
            ctx.fill(Path(ellipseIn: CGRect(x: start.x - 2.5, y: start.y - 2.5, width: 5, height: 5)), with: .color(.white))
            ctx.fill(Path(ellipseIn: CGRect(x: end.x - 2.5, y: end.y - 2.5, width: 5, height: 5)), with: .color(glow))
        }
    }
}

/// Where you're aiming — the phone's equivalent of looking at something. Quiet until a panel is under it,
/// and it blooms on a pinch so the gesture is visibly registered.
private struct AimReticle: View {
    let active: Bool
    let pinching: Bool

    var body: some View {
        ZStack {
            Circle().stroke(Palette.ink.opacity(active ? 0.5 : 0.18), lineWidth: 1.5)
                .frame(width: pinching ? 26 : 16, height: pinching ? 26 : 16)
            Circle().fill(active ? Palette.accent.opacity(pinching ? 0.9 : 0.6) : Palette.ink.opacity(0.25))
                .frame(width: 5, height: 5)
        }
        .animation(.easeOut(duration: 0.15), value: pinching)
        .animation(.easeOut(duration: 0.2), value: active)
        .allowsHitTesting(false)
    }
}

/// Observes only the hand, so the cursor tracks the fingers at full rate without re-rendering the panels.
private struct CursorLayer: View {
    @ObservedObject var hand: HandModel

    var body: some View {
        if let t = hand.track { FingerCursor(pinching: t.pinching).position(t.point) }
    }
}

/// Where the app sees your pinch: a ring that fills when the fingers close.
private struct FingerCursor: View {
    let pinching: Bool

    var body: some View {
        ZStack {
            Circle().stroke(Palette.accent.opacity(pinching ? 0.9 : 0.4), lineWidth: 1.5)
                .frame(width: pinching ? 13 : 19, height: pinching ? 13 : 19)
            Circle().fill(Palette.accent.opacity(pinching ? 0.9 : 0.2)).frame(width: pinching ? 13 : 5, height: pinching ? 13 : 5)
        }
        .animation(.easeOut(duration: 0.12), value: pinching)
        .allowsHitTesting(false)
    }
}

private struct StatusBar: View {
    let connection: VitalsStore.Connection
    let signal: Double?
    let micLevel: Double
    let recenter: () -> Void
    let openSettings: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            // Only surface the connection when it matters (live backend or a problem); demo mode shows nothing.
            if let status {
                HStack(spacing: 5) {
                    Circle().fill(status.color).frame(width: 6, height: 6)
                    Text(status.text).font(.system(size: 10, weight: .semibold))
                }
                Divider().frame(height: 10)
            }
            HStack(spacing: 4) {
                Image(systemName: "mic.fill").font(.system(size: 9))
                VoiceBars(level: micLevel)
            }
            .foregroundStyle(Palette.muted)
            if let q = signal {
                Text("Signal \(Int(q * 100))%").font(.system(size: 10)).foregroundStyle(Palette.muted)
            }
            Button(action: recenter) { Image(systemName: "viewfinder").font(.system(size: 11)) }
                .foregroundStyle(Palette.ink.opacity(0.7))
            Button(action: openSettings) { Image(systemName: "gearshape.fill").font(.system(size: 11)) }
                .foregroundStyle(Palette.ink.opacity(0.7))
        }
        .padding(.horizontal, 12).padding(.vertical, 6)
        .foregroundStyle(Palette.ink)
        .background(.ultraThinMaterial, in: Capsule())
        .background(Palette.glassFill, in: Capsule())
        .overlay(Capsule().stroke(Palette.glassEdge))
    }

    private var status: (text: String, color: Color)? {
        switch connection {
        case .demo: return nil
        case .connecting: return ("Waiting for Mac", Palette.amber)
        case .live: return ("Live", Palette.accent)
        case .offline: return ("Offline", Palette.danger)
        }
    }
}
