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
    private var historyTimer: Timer?

    init() {
        historyTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.sampleHistory() }
    }

    func useDemo() {
        PhoneLink.shared.stop()
        connection = .demo
        vitals = Vitals()
        let d = DemoVitalsSource { [weak self] u in self?.apply(u) }
        demo = d
        d.start()
    }

    /// Live: the phone streams camera + mic to the Mac pipeline (monitor_phone.py) and shows what comes back.
    func useLive(allowWiFi: Bool) {
        demo?.stop(); demo = nil
        vitals = Vitals()
        connection = .connecting
        let link = PhoneLink.shared
        link.onUpdate = { [weak self] u in self?.apply(u) }
        link.onConnected = { [weak self] up in self?.connection = up ? .live : .connecting }
        link.start(allowWiFi: allowWiFi)
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
