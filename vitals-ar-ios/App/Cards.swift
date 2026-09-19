import SwiftUI

// MARK: - Visit: time left + topics to cover

/// Rendered as a panel in the scene. Interaction (pinch or tap) is handled by the overlay, which maps the
/// touch into this panel's local space using the frames reported here.
struct VisitCard: View {
    @ObservedObject var plan: VisitPlan
    var hover: UUID?
    var checking: Set<UUID>
    var pauseHover = false

    static let space = "visit"

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            clock
            Divider().overlay(Palette.hairline)
            HStack {
                CardLabel(text: "TO COVER")
                Spacer()
                Text("\(plan.coveredCount) of \(plan.totalCount) done").font(.mono(9)).foregroundStyle(Palette.muted)
            }
            VStack(alignment: .leading, spacing: 6) {
                ForEach(plan.topics) { topic in
                    TopicRow(title: topic.title, checked: checking.contains(topic.id), hovered: hover == topic.id)
                        .reportFrame(.topic(topic.id))
                        .transition(.asymmetric(insertion: .opacity, removal: .move(edge: .leading).combined(with: .opacity)))
                }
                if plan.topics.isEmpty {
                    Label("Everything covered", systemImage: "checkmark.circle.fill")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(Palette.accent)
                        .padding(.top, 4)
                }
            }
            Spacer(minLength: 0)
            if let last = plan.lastCovered {
                TimelineView(.periodic(from: last.at, by: 1)) { tl in
                    if tl.date.timeIntervalSince(last.at) < 5 {
                        HStack(spacing: 5) {
                            Image(systemName: "arrow.uturn.backward").font(.system(size: 9, weight: .semibold))
                            Text("Undo").font(.system(size: 11, weight: .semibold))
                            Text(last.topic.title).font(.system(size: 10)).foregroundStyle(Palette.muted).lineLimit(1)
                        }
                        .padding(.horizontal, 8).padding(.vertical, 5)
                        .background(Palette.ink.opacity(0.08), in: Capsule())
                        .reportFrame(.undo)
                        .transition(.opacity)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .glass()
        .coordinateSpace(.named(Self.space))
    }

    private var clock: some View {
        TimelineView(.periodic(from: .now, by: 1)) { tl in
            let left = plan.remaining(at: tl.date)
            let color = left < 60 ? Palette.danger : (left < 180 ? Palette.amber : Palette.ink)
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    CardLabel(text: plan.pausedAt == nil ? "TIME LEFT" : "PAUSED", icon: "clock")
                    Spacer()
                    Image(systemName: plan.pausedAt == nil ? "pause.fill" : "play.fill").font(.system(size: 10))
                        .frame(width: 24, height: 24)
                        .background(Palette.ink.opacity(pauseHover ? 0.25 : 0.08), in: Circle())
                        .reportFrame(.pause)
                }
                HStack(alignment: .firstTextBaseline, spacing: 4) {
                    Text(format(left)).font(.mono(28, .semibold)).foregroundStyle(color)
                        .contentTransition(.numericText(countsDown: true))
                    Text(left < 0 ? "over" : "of \(Int(plan.minutes)) min").font(.system(size: 10)).foregroundStyle(Palette.muted)
                }
                GeometryReader { g in
                    ZStack(alignment: .leading) {
                        Capsule().fill(Palette.ink.opacity(0.1))
                        Capsule().fill(color == Palette.ink ? Palette.accent : color)
                            .frame(width: g.size.width * min(1, max(0, 1 - left / (plan.minutes * 60))))
                    }
                }
                .frame(height: 4)
            }
        }
    }

    private func format(_ t: TimeInterval) -> String {
        let s = Int(abs(t).rounded())
        return (t < 0 ? "+" : "") + String(format: "%d:%02d", s / 60, s % 60)
    }
}

/// Hit targets inside the visit panel, reported in its local coordinate space.
enum VisitTarget: Hashable {
    case topic(UUID), pause, undo
}

struct VisitFramesKey: PreferenceKey {
    static let defaultValue: [VisitTarget: CGRect] = [:]
    static func reduce(value: inout [VisitTarget: CGRect], nextValue: () -> [VisitTarget: CGRect]) {
        value.merge(nextValue()) { $1 }
    }
}

extension View {
    func reportFrame(_ target: VisitTarget) -> some View {
        background(GeometryReader { g in
            Color.clear.preference(key: VisitFramesKey.self, value: [target: g.frame(in: .named(VisitCard.space))])
        })
    }
}

private struct TopicRow: View {
    let title: String
    let checked: Bool
    let hovered: Bool

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            ZStack {
                Circle().stroke(checked || hovered ? Palette.accent : Palette.ink.opacity(0.35), lineWidth: 1.2)
                if checked {
                    Circle().fill(Palette.accent)
                    Image(systemName: "checkmark").font(.system(size: 8, weight: .heavy)).foregroundStyle(.white)
                }
            }
            .frame(width: 15, height: 15)
            .padding(.top, 1)
            Text(title)
                .font(.system(size: 12.5))
                .strikethrough(checked, color: Palette.ink.opacity(0.6))
                .foregroundStyle(checked ? Palette.ink.opacity(0.45) : Palette.ink.opacity(0.92))
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(.vertical, 5).padding(.horizontal, 6)
        .background(hovered ? Palette.accent.opacity(0.18) : Palette.ink.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(hovered ? Palette.accent.opacity(0.6) : .clear))
        .animation(.easeOut(duration: 0.12), value: hovered)
    }
}

// MARK: - Vitals: HR, BR, HRV in one compact card

struct VitalsCard: View {
    let hr: Double?
    let hrConfidence: Double
    let br: Double?
    let hrv: Double?
    let hrvHistory: [Double]

    var body: some View {
        VStack(spacing: 7) {
            row(icon: "heart.fill", label: "HR", value: hr, unit: "bpm", tint: Palette.danger) {
                TimelineView(.animation(minimumInterval: 1 / 30)) { tl in
                    PulseWave(time: tl.date.timeIntervalSinceReferenceDate, bpm: hr ?? 70)
                        .stroke(Palette.danger, style: StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round))
                }
            }
            row(icon: "lungs.fill", label: "BR", value: br, unit: "/min", tint: Palette.accent) {
                TimelineView(.animation(minimumInterval: 1 / 30)) { tl in
                    BreathWave(time: tl.date.timeIntervalSinceReferenceDate, rpm: br ?? 14)
                        .stroke(Palette.accent, style: StrokeStyle(lineWidth: 1.5, lineCap: .round))
                }
            }
            row(icon: "waveform.path.ecg", label: "HRV", value: hrv, unit: "ms", tint: Palette.accent) {
                Sparkline(values: hrvHistory)
                    .stroke(Palette.accent, style: StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round))
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .glass()
    }

    private func row<Graph: View>(icon: String, label: String, value: Double?, unit: String, tint: Color,
                                  @ViewBuilder graph: () -> Graph) -> some View {
        HStack(spacing: 8) {
            Image(systemName: icon).font(.system(size: 10)).foregroundStyle(tint).frame(width: 14)
            Text(label).font(.mono(9, .semibold)).foregroundStyle(Palette.muted).frame(width: 26, alignment: .leading)
            HStack(alignment: .firstTextBaseline, spacing: 2) {
                Text(value.map { String(format: "%.0f", $0) } ?? "--").font(.mono(19, .semibold))
                Text(unit).font(.mono(9)).foregroundStyle(Palette.muted)
            }
            .frame(width: 70, alignment: .leading)
            graph().frame(height: 18)
        }
    }
}

// MARK: - Fatigue meter with classification

struct FatigueCard: View {
    let score: Double?
    let threshold: Double
    let confidence: Double?
    let drivers: [String]

    var body: some View {
        let s = score ?? 0
        let over = s >= threshold
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                CardLabel(text: "FATIGUE", icon: "bolt.heart.fill")
                Spacer()
                // Classification from the agent: calm outline when alert, solid when fatigued
                HStack(spacing: 4) {
                    Text(over ? "Fatigued" : "Alert").font(.system(size: 10, weight: .semibold))
                    if let c = confidence { Text("\(Int(c * 100))%").font(.mono(9)).opacity(0.75) }
                }
                .padding(.horizontal, 8).padding(.vertical, 3)
                .foregroundStyle(over ? .white : Palette.accent)
                .background(over ? Palette.amber : .clear, in: Capsule())
                .overlay(Capsule().stroke(over ? .clear : Palette.accent.opacity(0.5)))
            }
            HStack(alignment: .center, spacing: 10) {
                FatigueGauge(value: s, threshold: threshold).frame(width: 80, height: 46)
                HStack(alignment: .firstTextBaseline, spacing: 2) {
                    Text(score.map { String(format: "%.0f", $0 * 100) } ?? "--").font(.mono(26, .semibold))
                        .foregroundStyle(over ? Palette.amber : Palette.ink)
                    Text("/100").font(.mono(9)).foregroundStyle(Palette.muted)
                }
            }
            if !drivers.isEmpty {
                HStack(spacing: 4) {
                    ForEach(drivers.prefix(3), id: \.self) { d in
                        Text(d).font(.system(size: 9, weight: .medium)).lineLimit(1).fixedSize()
                            .padding(.horizontal, 6).padding(.vertical, 3)
                            .background(Palette.ink.opacity(0.08), in: Capsule())
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .glass()
        .animation(.easeOut(duration: 0.3), value: over)
    }
}

struct FatigueGauge: View {
    let value: Double
    let threshold: Double

    var body: some View {
        Canvas { ctx, size in
            let c = CGPoint(x: size.width / 2, y: size.height - 4)
            let r = min(size.width / 2, size.height) - 5
            func point(_ v: Double, _ radius: CGFloat) -> CGPoint {
                let a = Double.pi * (1 - v)
                return CGPoint(x: c.x + radius * cos(a), y: c.y - radius * sin(a))
            }
            var track = Path()
            track.addArc(center: c, radius: r, startAngle: .degrees(180), endAngle: .degrees(360), clockwise: false)
            ctx.stroke(track, with: .color(Palette.ink.opacity(0.12)), style: StrokeStyle(lineWidth: 5, lineCap: .round))
            var fill = Path()
            fill.addArc(center: c, radius: r, startAngle: .degrees(180), endAngle: .degrees(180 + 180 * min(1, max(0, value))), clockwise: false)
            ctx.stroke(fill, with: .color(value >= threshold ? Palette.amber : Palette.accent), style: StrokeStyle(lineWidth: 5, lineCap: .round))
            var tick = Path() // threshold
            tick.move(to: point(threshold, r - 7))
            tick.addLine(to: point(threshold, r + 6))
            ctx.stroke(tick, with: .color(Palette.ink.opacity(0.85)), style: StrokeStyle(lineWidth: 1.5, lineCap: .round))
        }
        .animation(.spring(duration: 0.6), value: value)
    }
}

// MARK: - Emotional state (context-aware)

struct EmotionCard: View {
    let label: String?
    let valence: Double
    let arousal: Double
    let probs: [(label: String, p: Double)]
    let context: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            CardLabel(text: "EMOTIONAL STATE", icon: "face.smiling")
            HStack(alignment: .center, spacing: 10) {
                AffectPlot(valence: valence, arousal: arousal).frame(width: 40, height: 40)
                VStack(alignment: .leading, spacing: 3) {
                    Text(label ?? "--").font(.system(size: 17, weight: .semibold))
                    ForEach(probs.prefix(2), id: \.label) { item in
                        HStack(spacing: 5) {
                            Text(item.label.capitalized).font(.system(size: 9)).foregroundStyle(Palette.muted).frame(width: 48, alignment: .leading)
                            GeometryReader { g in
                                ZStack(alignment: .leading) {
                                    Capsule().fill(Palette.ink.opacity(0.1))
                                    Capsule().fill(Palette.accent.opacity(0.8)).frame(width: g.size.width * item.p)
                                }
                            }
                            .frame(width: 70, height: 4)
                        }
                    }
                }
            }
            if let context {
                Text(context).font(.system(size: 10)).foregroundStyle(Palette.ink.opacity(0.7))
                    .lineLimit(2).fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .glass()
        .animation(.easeOut(duration: 0.4), value: label)
    }
}

/// Valence (x) × arousal (y) with the current state as a dot.
struct AffectPlot: View {
    let valence: Double
    let arousal: Double

    var body: some View {
        Canvas { ctx, size in
            let rect = CGRect(origin: .zero, size: size)
            ctx.stroke(Path(ellipseIn: rect.insetBy(dx: 1, dy: 1)), with: .color(Palette.ink.opacity(0.15)), lineWidth: 1)
            var cross = Path()
            cross.move(to: CGPoint(x: size.width / 2, y: 4)); cross.addLine(to: CGPoint(x: size.width / 2, y: size.height - 4))
            cross.move(to: CGPoint(x: 4, y: size.height / 2)); cross.addLine(to: CGPoint(x: size.width - 4, y: size.height / 2))
            ctx.stroke(cross, with: .color(Palette.ink.opacity(0.12)), lineWidth: 1)
            let p = CGPoint(x: size.width / 2 * (1 + CGFloat(max(-1, min(1, valence))) * 0.8),
                            y: size.height / 2 * (1 - CGFloat(max(-1, min(1, arousal))) * 0.8))
            ctx.fill(Path(ellipseIn: CGRect(x: p.x - 4, y: p.y - 4, width: 8, height: 8)), with: .color(Palette.accent))
        }
        .animation(.spring(duration: 0.8), value: valence)
        .animation(.spring(duration: 0.8), value: arousal)
    }
}

// MARK: - Context analysis (what the doctor actually reads)

struct ContextCard: View {
    let summary: String?
    let insights: [String]
    let step: String?
    let transcript: String?
    let speaking: Bool
    let micLevel: Double
    private let steps = ["observe", "reason", "classify", "report"]

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 8) {
                CardLabel(text: "CONTEXT", icon: "text.bubble", color: Palette.accent).fixedSize()
                Spacer()
                // Agent loop progress: quiet dots, the current step named in text
                let current = steps.firstIndex(of: step ?? "") ?? -1
                HStack(spacing: 4) {
                    ForEach(steps.indices, id: \.self) { i in
                        Circle().fill(i <= current ? Palette.accent.opacity(i == current ? 1 : 0.5) : Palette.ink.opacity(0.15))
                            .frame(width: 5, height: 5)
                    }
                    Text((step ?? "").capitalized).font(.system(size: 9.5, weight: .medium)).foregroundStyle(Palette.muted)
                        .fixedSize()
                }
                .animation(.easeOut(duration: 0.25), value: step)
            }
            Typewriter(text: summary ?? "Listening…")
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(Palette.ink.opacity(0.95))
                .frame(maxWidth: .infinity, minHeight: 34, alignment: .topLeading)
            HStack(spacing: 6) {
                ForEach(insights.prefix(2), id: \.self) { i in
                    Text(i).font(.system(size: 9.5, weight: .medium)).lineLimit(1).fixedSize()
                        .padding(.horizontal, 7).padding(.vertical, 3)
                        .background(Palette.ink.opacity(0.08), in: Capsule())
                }
                Spacer(minLength: 4)
                if let transcript {
                    HStack(spacing: 5) {
                        VoiceBars(level: speaking ? max(micLevel, 0.35) : micLevel * 0.5)
                        Text("“\(transcript)”").font(.system(size: 9.5)).italic().foregroundStyle(Palette.muted).lineLimit(1)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .glass(corner: 18)
    }
}

struct Typewriter: View {
    let text: String
    @State private var shown = 0

    var body: some View {
        Text(String(text.prefix(shown)))
            .task(id: text) {
                shown = 0
                for i in 0...text.count {
                    shown = i
                    try? await Task.sleep(for: .milliseconds(14))
                }
            }
    }
}

struct VoiceBars: View {
    let level: Double

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 30)) { tl in
            let t = tl.date.timeIntervalSinceReferenceDate
            HStack(spacing: 1.5) {
                ForEach(0..<5, id: \.self) { i in
                    let wobble = 0.55 + 0.45 * sin(t * 9 + Double(i) * 1.7)
                    Capsule().fill(Palette.accent)
                        .frame(width: 2, height: 3 + 11 * level * wobble)
                }
            }
            .frame(height: 14)
        }
    }
}

// MARK: - Shapes

struct Sparkline: Shape {
    let values: [Double]

    func path(in rect: CGRect) -> Path {
        var p = Path()
        guard values.count > 1, let lo = values.min(), let hi = values.max() else { return p }
        let span = max(hi - lo, 1)
        for (i, v) in values.enumerated() {
            let pt = CGPoint(x: rect.width * CGFloat(i) / CGFloat(values.count - 1),
                             y: rect.height * (1 - CGFloat((v - lo) / span)))
            i == 0 ? p.move(to: pt) : p.addLine(to: pt)
        }
        return p
    }
}

/// Scrolling photoplethysmogram: sharp systolic upstroke, dicrotic notch, slow decay.
struct PulseWave: Shape {
    let time: Double
    let bpm: Double

    func path(in rect: CGRect) -> Path {
        var p = Path()
        let seconds = 3.0, n = 90
        for i in 0...n {
            let x = Double(i) / Double(n)
            let beat = (time - seconds * (1 - x)) * bpm / 60
            let ph = beat - floor(beat)
            let y = exp(-pow((ph - 0.15) / 0.06, 2)) + 0.38 * exp(-pow((ph - 0.42) / 0.08, 2)) - 0.1 * ph
            let pt = CGPoint(x: rect.width * x, y: rect.height * (0.9 - 0.8 * y))
            i == 0 ? p.move(to: pt) : p.addLine(to: pt)
        }
        return p
    }
}

struct BreathWave: Shape {
    let time: Double
    let rpm: Double

    func path(in rect: CGRect) -> Path {
        var p = Path()
        let seconds = 15.0, n = 60
        for i in 0...n {
            let x = Double(i) / Double(n)
            let y = sin((time - seconds * (1 - x)) * 2 * .pi * rpm / 60)
            let pt = CGPoint(x: rect.width * x, y: rect.height * (0.5 - 0.42 * y))
            i == 0 ? p.move(to: pt) : p.addLine(to: pt)
        }
        return p
    }
}
