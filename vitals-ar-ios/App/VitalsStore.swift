import Foundation
import Combine

/// Holds the latest vitals plus short histories, fed by either the demo generator or the live backend.
final class VitalsStore: ObservableObject {
    enum Connection: Equatable { case demo, connecting, live, offline(String) }

    @Published private(set) var vitals = Vitals()
    @Published private(set) var hrvHistory: [Double] = []
    @Published private(set) var hrHistory: [Double] = []
    @Published private(set) var connection: Connection = .demo
    /// Visit agenda sent by the backend (doctor's notes filed as agenda / instructions).
    @Published private(set) var agenda: [String]?

    private var demo: DemoVitalsSource?
    private var socket: WebSocketVitalsSource?
    private var historyTimer: Timer?

    init() {
        historyTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.sampleHistory() }
    }

    func useDemo() {
        socket?.stop(); socket = nil
        connection = .demo
        vitals = Vitals()
        let d = DemoVitalsSource { [weak self] u in self?.apply(u) }
        demo = d
        d.start()
    }

    func useBackend(url: URL) {
        demo?.stop(); demo = nil
        vitals = Vitals()
        connection = .connecting
        let s = WebSocketVitalsSource(url: url, onUpdate: { [weak self] u in self?.apply(u) },
                                      onState: { [weak self] state in self?.connection = state })
        socket = s
        s.start()
    }

    func apply(_ u: VitalsUpdate) {
        vitals.apply(u)
        if let topics = u.visit?.topics, topics != agenda { agenda = topics }
    }

    private func sampleHistory() {
        if let v = vitals.hrv { hrvHistory = Array((hrvHistory + [v]).suffix(60)) }
        if let v = vitals.hr { hrHistory = Array((hrHistory + [v]).suffix(60)) }
    }
}

/// Live backend: one JSON `VitalsUpdate` per WebSocket message. Reconnects automatically.
final class WebSocketVitalsSource {
    private let url: URL
    private let onUpdate: (VitalsUpdate) -> Void
    private let onState: (VitalsStore.Connection) -> Void
    private var task: URLSessionWebSocketTask?
    private var running = false

    init(url: URL, onUpdate: @escaping (VitalsUpdate) -> Void, onState: @escaping (VitalsStore.Connection) -> Void) {
        self.url = url
        self.onUpdate = onUpdate
        self.onState = onState
    }

    func start() {
        running = true
        connect()
    }

    func stop() {
        running = false
        task?.cancel(with: .goingAway, reason: nil)
    }

    private func connect() {
        guard running else { return }
        let t = URLSession.shared.webSocketTask(with: url)
        task = t
        t.resume()
        receive(t, first: true)
    }

    private func receive(_ t: URLSessionWebSocketTask, first: Bool) {
        t.receive { [weak self] result in
            guard let self, self.running, t === self.task else { return }
            switch result {
            case .success(let message):
                if first { DispatchQueue.main.async { self.onState(.live) } }
                let data: Data?
                switch message {
                case .string(let s): data = s.data(using: .utf8)
                case .data(let d): data = d
                @unknown default: data = nil
                }
                if let data, let u = try? VitalsUpdate.decoder.decode(VitalsUpdate.self, from: data) {
                    DispatchQueue.main.async { self.onUpdate(u) }
                }
                self.receive(t, first: false)
            case .failure(let error):
                DispatchQueue.main.async { self.onState(.offline(error.localizedDescription)) }
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) { self.connect() }
            }
        }
    }
}
