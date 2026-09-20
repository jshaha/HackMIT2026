import Foundation

/// Scripted, physiologically plausible data for demos and for building the UI before the backend is ready.
/// Over ~80 s the patient drifts from alert into fatigue (HRV falls, speech slows), then recovers, and loops.
final class DemoVitalsSource {
    private let emit: (VitalsUpdate) -> Void
    private var timer: Timer?
    private var t = 0.0
    private var hr = 74.0, br = 14.0
    private var scriptIndex = -1

    private struct Beat {
        let at: Double
        let summary: String
        let insights: [String]
        let transcript: String
        let emotion: String
        let emotionContext: String
    }

    private let script: [Beat] = [
        Beat(at: 0, summary: "Baseline established. Vitals within normal range; speech fluent with typical prosody.",
             insights: ["HR 74 bpm, stable", "Speech rate 148 wpm"],
             transcript: "I'm doing alright, just here for my follow-up.", emotion: "Calm",
             emotionContext: "Consistent with a routine follow-up visit"),
        Beat(at: 14, summary: "HRV trending down 18% over 2 min while speech pauses lengthen. Monitoring for fatigue.",
             insights: ["RMSSD ↓ 18%", "Pause length ↑ 0.4 s"],
             transcript: "Honestly I've been working a lot of night shifts lately…", emotion: "Tired",
             emotionContext: "Mentions night shifts, which raises prior for sleep debt"),
        Beat(at: 28, summary: "Fatigue likely: suppressed HRV, slowed speech and reduced vocal energy align with the reported sleep loss.",
             insights: ["Reports ~4 h sleep", "Vocal energy ↓ 22%"],
             transcript: "I think I got maybe four hours last night.", emotion: "Fatigued",
             emotionContext: "Flat affect is explained by sleep loss, not low mood"),
        Beat(at: 44, summary: "Arousal rising as the conversation turns to medication. Stress response, not fatigue-driven.",
             insights: ["HR ↑ 9 bpm", "Pitch variability ↑"],
             transcript: "Is the new dose going to make me feel worse?", emotion: "Anxious",
             emotionContext: "Anxiety is topic-specific: medication concerns"),
        Beat(at: 60, summary: "Patient reassured; HR settling. Fatigue remains above threshold: recommend a sleep-hygiene discussion.",
             insights: ["Suggest: sleep screen", "HR recovering"],
             transcript: "Okay, that makes me feel a lot better, thanks.", emotion: "Relieved",
             emotionContext: "Valence improved after reassurance"),
    ]
    private let loop = 80.0
    private let steps = ["observe", "reason", "classify", "report"]

    init(emit: @escaping (VitalsUpdate) -> Void) { self.emit = emit }

    func start() {
        tick()
        timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in self?.tick() }
    }

    func stop() { timer?.invalidate() }

    private func tick() {
        t += 0.25
        let phase = t.truncatingRemainder(dividingBy: loop)

        // Fatigue: rises from ~0.3 to ~0.8 between 10 s and 35 s, eases to ~0.65 by the end.
        let fatigue = 0.3 + 0.5 * smoothstep(10, 35, phase) - 0.15 * smoothstep(55, 75, phase) + 0.02 * sin(t * 0.9)
        let anxious = smoothstep(42, 48, phase) * (1 - smoothstep(56, 62, phase))

        hr += ((72 + 12 * anxious - 4 * fatigue) - hr) * 0.05 + Double.random(in: -0.6...0.6)
        br += ((14 + 3 * anxious - 1.5 * fatigue) - br) * 0.05 + Double.random(in: -0.15...0.15)
        let rmssd = 58 - 30 * fatigue - 8 * anxious + Double.random(in: -1.5...1.5)

        let beat = script.last { $0.at <= phase } ?? script[0]
        let beatIndex = script.firstIndex { $0.at == beat.at } ?? 0
        let sinceBeat = phase - beat.at
        let step = steps[min(steps.count - 1, Int(sinceBeat / 1.6))]

        let valence = -0.5 * fatigue - 0.4 * anxious + 0.5 * smoothstep(60, 66, phase) + 0.2
        let arousal = -0.6 * fatigue + 0.9 * anxious + 0.2
        var probs = [
            "calm": max(0.05, 0.6 - fatigue * 0.5 - anxious * 0.4),
            "fatigued": max(0.05, fatigue * 0.7),
            "anxious": max(0.05, anxious * 0.8 + 0.05),
            "content": max(0.05, smoothstep(60, 66, phase) * 0.5),
        ]
        let sum = probs.values.reduce(0, +)
        for k in probs.keys { probs[k]! /= sum }

        var drivers: [String] = []
        if rmssd < 42 { drivers.append("Low HRV") }
        if fatigue > 0.45 { drivers.append("Slow speech") }
        if fatigue > 0.6 { drivers.append("Sleep debt") }

        var u = VitalsUpdate()
        u.hr = .init(bpm: hr, confidence: 0.86 + 0.08 * sin(t * 0.3))
        u.br = .init(rpm: br)
        u.hrv = .init(rmssdMs: rmssd, sdnnMs: rmssd * 1.3)
        u.fatigue = .init(score: fatigue, threshold: 0.6, label: fatigue >= 0.6 ? "fatigued" : "alert",
                          confidence: 0.6 + 0.35 * abs(fatigue - 0.6) / 0.4, drivers: drivers)
        u.emotion = .init(label: beat.emotion, valence: valence, arousal: arousal, probs: probs, context: beat.emotionContext)
        u.voice = .init(speaking: sin(t * 1.3) > -0.2, transcript: beat.transcript)
        u.signal = .init(quality: 0.88 + 0.08 * sin(t * 0.2))
        if beatIndex != scriptIndex || Int(sinceBeat * 4) % 4 == 0 {
            scriptIndex = beatIndex
            u.context = .init(summary: step == "report" ? beat.summary : nil,
                              insights: step == "report" ? beat.insights : nil, agentStep: step)
        }
        emit(u)
    }
}

private func smoothstep(_ a: Double, _ b: Double, _ x: Double) -> Double {
    let t = min(1, max(0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)
}
