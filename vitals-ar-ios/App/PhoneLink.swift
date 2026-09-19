import Foundation
import Network
import CoreImage
import AVFoundation

/// The phone is the sensor: it listens on port 8765, and the Mac pipeline (monitor_phone.py) connects —
/// over the USB cable via iproxy by default, so patient video never touches the network.
/// On the one WebSocket, the phone streams its rear-camera frames and mic audio, and receives vitals back.
///   phone → Mac  binary: "V" + float64 ts + JPEG,  "A" + float64 ts + PCM16 mono 16 kHz
///   Mac → phone  text:   one JSON `VitalsUpdate` per message
final class PhoneLink {
    static let shared = PhoneLink()
    static let port: NWEndpoint.Port = 8765

    var onUpdate: ((VitalsUpdate) -> Void)?
    var onConnected: ((Bool) -> Void)?

    /// Main-thread view of whether the Mac is connected (gates streaming).
    private(set) var isConnected = false

    private let queue = DispatchQueue(label: "phone-link", qos: .userInitiated)
    private var listener: NWListener?
    private var connection: NWConnection?
    private var pendingVideo = 0

    // Video encoding
    private let encodeQueue = DispatchQueue(label: "phone-link-jpeg", qos: .userInitiated)
    private let ciContext = CIContext(options: [.cacheIntermediates: false])
    private var encoding = false
    private var lastFrameTime = 0.0
    static let frameWidth: CGFloat = 640, frameInterval = 1.0 / 30, jpegQuality: CGFloat = 0.9

    // Audio conversion to 16 kHz mono PCM16
    private var converter: AVAudioConverter?
    private let audioFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!

    /// allowWiFi = false accepts only loopback connections (USB via usbmux); true also accepts the network.
    func start(allowWiFi: Bool) {
        stop()
        let ws = NWProtocolWebSocket.Options()
        ws.autoReplyPing = true
        ws.maximumMessageSize = 4 << 20
        let params = NWParameters.tcp
        params.defaultProtocolStack.applicationProtocols.insert(ws, at: 0)
        params.allowLocalEndpointReuse = true
        if !allowWiFi { params.requiredInterfaceType = .loopback }
        guard let l = try? NWListener(using: params, on: Self.port) else { return }
        l.newConnectionHandler = { [weak self] c in self?.accept(c) }
        l.stateUpdateHandler = { state in
            if case .failed(let e) = state { print("[link] listener failed: \(e)") }
        }
        listener = l
        l.start(queue: queue)
    }

    func stop() {
        listener?.cancel(); listener = nil
        connection?.cancel(); connection = nil
        setConnected(false)
    }

    private func accept(_ c: NWConnection) {
        connection?.cancel() // newest Mac connection wins
        connection = c
        pendingVideo = 0
        c.stateUpdateHandler = { [weak self, weak c] state in
            guard let self, let c, c === self.connection else { return }
            switch state {
            case .ready: self.setConnected(true)
            case .failed, .cancelled: self.connection = nil; self.setConnected(false)
            default: break
            }
        }
        c.start(queue: queue)
        receive(on: c)
    }

    private func receive(on c: NWConnection) {
        c.receiveMessage { [weak self] data, context, _, error in
            guard let self else { return }
            if let data, let meta = context?.protocolMetadata(definition: NWProtocolWebSocket.definition) as? NWProtocolWebSocket.Metadata,
               meta.opcode == .text, let u = try? VitalsUpdate.decoder.decode(VitalsUpdate.self, from: data) {
                DispatchQueue.main.async { self.onUpdate?(u) }
            }
            if error == nil { self.receive(on: c) }
        }
    }

    private func setConnected(_ v: Bool) {
        DispatchQueue.main.async {
            guard self.isConnected != v else { return }
            self.isConnected = v
            self.onConnected?(v)
        }
    }

    // MARK: Streaming

    /// Call with each ARFrame's camera image (main thread). Throttled to 30 fps, encoded off the main thread,
    /// dropped whenever the encoder or the link is behind so latency never builds up.
    func sendVideoFrame(_ buffer: CVPixelBuffer, timestamp: Double) {
        guard isConnected, !encoding, timestamp - lastFrameTime >= Self.frameInterval * 0.9 else { return }
        lastFrameTime = timestamp
        encoding = true
        encodeQueue.async { [weak self] in
            guard let self else { return }
            let image = CIImage(cvPixelBuffer: buffer)
            let s = Self.frameWidth / image.extent.width
            let scaled = image.transformed(by: CGAffineTransform(scaleX: s, y: s))
            let jpeg = self.ciContext.jpegRepresentation(
                of: scaled, colorSpace: CGColorSpace(name: CGColorSpace.sRGB)!,
                options: [CIImageRepresentationOption(rawValue: kCGImageDestinationLossyCompressionQuality as String): Self.jpegQuality])
            DispatchQueue.main.async { self.encoding = false }
            guard let jpeg else { return }
            self.queue.async {
                guard self.pendingVideo < 3 else { return } // link congested: drop this frame
                self.pendingVideo += 1
                self.send(kind: 0x56, timestamp: timestamp, payload: jpeg) { self.pendingVideo -= 1 }
            }
        }
    }

    /// Call from the mic tap with each input buffer (any thread).
    func sendAudio(_ buffer: AVAudioPCMBuffer, timestamp: Double) {
        guard isConnected else { return }
        if converter == nil || converter?.inputFormat != buffer.format {
            converter = AVAudioConverter(from: buffer.format, to: audioFormat)
        }
        guard let converter else { return }
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * 16000 / buffer.format.sampleRate) + 32
        guard let out = AVAudioPCMBuffer(pcmFormat: audioFormat, frameCapacity: capacity) else { return }
        var fed = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if fed { status.pointee = .noDataNow; return nil }
            fed = true
            status.pointee = .haveData
            return buffer
        }
        guard error == nil, out.frameLength > 0, let samples = out.int16ChannelData?[0] else { return }
        let pcm = Data(bytes: samples, count: Int(out.frameLength) * 2)
        queue.async { self.send(kind: 0x41, timestamp: timestamp, payload: pcm) {} }
    }

    private func send(kind: UInt8, timestamp: Double, payload: Data, done: @escaping () -> Void) {
        guard let c = connection else { done(); return }
        var message = Data([kind])
        var ts = timestamp.bitPattern.littleEndian
        withUnsafeBytes(of: &ts) { message.append(contentsOf: $0) }
        message.append(payload)
        let context = NWConnection.ContentContext(identifier: "stream", metadata: [NWProtocolWebSocket.Metadata(opcode: .binary)])
        c.send(content: message, contentContext: context, isComplete: true, completion: .contentProcessed { _ in done() })
    }

    /// The phone's Wi-Fi address, for connecting over the network instead of USB.
    static var wifiAddress: String? {
        var ifaddr: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&ifaddr) == 0, let first = ifaddr else { return nil }
        defer { freeifaddrs(ifaddr) }
        for p in sequence(first: first, next: { $0.pointee.ifa_next }) {
            let ifa = p.pointee
            guard ifa.ifa_addr.pointee.sa_family == UInt8(AF_INET), String(cString: ifa.ifa_name) == "en0" else { continue }
            var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            getnameinfo(ifa.ifa_addr, socklen_t(ifa.ifa_addr.pointee.sa_len), &host, socklen_t(host.count), nil, 0, NI_NUMERICHOST)
            return String(cString: host)
        }
        return nil
    }
}
