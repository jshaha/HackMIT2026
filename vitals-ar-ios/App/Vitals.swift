import Foundation

/// Everything the overlay shows. All fields are optional so the backend can send partial updates.
struct Vitals {
    var hr: Double?
    var hrConfidence = 0.0
    var br: Double?
    var hrv: Double? // RMSSD, ms
    var sdnn: Double?
    var fatigue: Double? // 0–1
    var fatigueThreshold = 0.6
    var fatigueLabel: String?
    var fatigueConfidence: Double?
    var fatigueDrivers: [String] = []
    var emotion: String?
    var valence = 0.0 // -1…1
    var arousal = 0.0 // -1…1
    var emotionProbs: [(label: String, p: Double)] = []
    var emotionContext: String?
    var contextSummary: String?
    var insights: [String] = []
    var agentStep: String? // observe | reason | classify | report
    var transcript: String?
    var speaking = false
    var signalQuality: Double?

    var fatigued: Bool { (fatigue ?? 0) >= fatigueThreshold }
}

/// Wire format, one JSON object per WebSocket message (see vitals/README.md). snake_case keys.
struct VitalsUpdate: Decodable {
    struct HR: Decodable { var bpm: Double?; var confidence: Double? }
    struct BR: Decodable { var rpm: Double? }
    struct HRV: Decodable { var rmssdMs: Double?; var sdnnMs: Double? }
    struct Fatigue: Decodable {
        var score: Double?; var threshold: Double?; var label: String?
        var confidence: Double?; var drivers: [String]?
    }
    struct Emotion: Decodable {
        var label: String?; var valence: Double?; var arousal: Double?
        var probs: [String: Double]?; var context: String?
    }
    struct Context: Decodable { var summary: String?; var insights: [String]?; var agentStep: String? }
    struct Voice: Decodable { var speaking: Bool?; var transcript: String? }
    struct Signal: Decodable { var quality: Double? }
    struct Visit: Decodable { var topics: [String]? }

    var hr: HR?, br: BR?, hrv: HRV?, fatigue: Fatigue?, emotion: Emotion?
    var context: Context?, voice: Voice?, signal: Signal?, visit: Visit?

    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()
}

extension Vitals {
    mutating func apply(_ u: VitalsUpdate) {
        if let v = u.hr?.bpm { hr = v }
        if let v = u.hr?.confidence { hrConfidence = v }
        if let v = u.br?.rpm { br = v }
        if let v = u.hrv?.rmssdMs { hrv = v }
        if let v = u.hrv?.sdnnMs { sdnn = v }
        if let f = u.fatigue {
            if let v = f.score { fatigue = v }
            if let v = f.threshold { fatigueThreshold = v }
            if let v = f.label { fatigueLabel = v }
            if let v = f.confidence { fatigueConfidence = v }
            if let v = f.drivers { fatigueDrivers = v }
        }
        if let e = u.emotion {
            if let v = e.label { emotion = v }
            if let v = e.valence { valence = v }
            if let v = e.arousal { arousal = v }
            if let v = e.probs { emotionProbs = v.map { ($0.key, $0.value) }.sorted { $0.p > $1.p } }
            if let v = e.context { emotionContext = v }
        }
        if let c = u.context {
            if let v = c.summary { contextSummary = v }
            if let v = c.insights { insights = v }
            if let v = c.agentStep { agentStep = v }
        }
        if let v = u.voice?.speaking { speaking = v }
        if let v = u.voice?.transcript { transcript = v }
        if let v = u.signal?.quality { signalQuality = v }
    }
}
