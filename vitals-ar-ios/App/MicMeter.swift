import AVFoundation
import Combine

/// Live microphone level for the "voice" input indicator.
final class MicMeter: ObservableObject {
    @Published private(set) var level: Double = 0 // 0–1
    private let engine = AVAudioEngine()

    func start() {
        AVAudioSession.sharedInstance().requestRecordPermission { [weak self] granted in
            guard granted else { return }
            DispatchQueue.main.async { self?.run() }
        }
    }

    private func run() {
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playAndRecord, mode: .measurement, options: [.mixWithOthers, .defaultToSpeaker])
        try? session.setActive(true)
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0 else { return }
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
            guard let data = buffer.floatChannelData?[0] else { return }
            let n = Int(buffer.frameLength)
            var sum: Float = 0
            for i in 0..<n { sum += data[i] * data[i] }
            let rms = sqrt(sum / Float(max(n, 1)))
            let db = 20 * log10(max(rms, 1e-6))
            let level = Double(min(1, max(0, (db + 55) / 45)))
            DispatchQueue.main.async {
                guard let self else { return }
                self.level = self.level * 0.6 + level * 0.4
            }
        }
        try? engine.start()
    }
}
