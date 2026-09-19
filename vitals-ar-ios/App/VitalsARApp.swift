import SwiftUI

@main
struct VitalsARApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
                .statusBarHidden()
                .persistentSystemOverlays(.hidden)
                .preferredColorScheme(.light)
        }
    }
}

struct ContentView: View {
    @StateObject private var store = VitalsStore()
    @StateObject private var scene = SceneModel()
    @StateObject private var mic = MicMeter()
    @StateObject private var plan = VisitPlan()
    @AppStorage("useBackend") private var useBackend = false
    @AppStorage("allowWiFi") private var allowWiFi = false
    @State private var showSettings = false
    @State private var screen = CGSize(width: 844, height: 390)
    @State private var insets = EdgeInsets()

    var body: some View {
        ZStack {
            #if targetEnvironment(simulator)
            SimulatedPatientView(scene: scene).ignoresSafeArea()
            #else
            CameraView(scene: scene).ignoresSafeArea()
            #endif
            OverlayView(vitals: store.vitals, hrvHistory: store.hrvHistory, micLevel: mic.level,
                        connection: store.connection, scene: scene, plan: plan,
                        size: fullSize, inset: insets) { showSettings = true }
        }
        .background(
            Color.clear
                .ignoresSafeArea()
                .onGeometryChange(for: CGSize.self) { $0.size } action: { screen = $0; updateDesign() }
                .onGeometryChange(for: EdgeInsets.self) { $0.safeAreaInsets } action: { insets = $0; updateDesign() }
        )
        .onChange(of: store.agenda) { _, items in if let items { plan.syncAgenda(items) } }
        .onAppear {
            connect()
            mic.start()
        }
        .sheet(isPresented: $showSettings) {
            SettingsView(useBackend: $useBackend, allowWiFi: $allowWiFi, connection: store.connection, plan: plan,
                         resetPanels: { scene.resetOffsets() }) {
                showSettings = false
                connect()
            }
            .presentationDetents([.large])
        }
    }

    /// The measured size excludes the safe area; the overlay draws edge to edge.
    private var fullSize: CGSize {
        CGSize(width: screen.width + insets.leading + insets.trailing, height: screen.height + insets.top + insets.bottom)
    }

    private func updateDesign() {
        scene.design = Layout.design(size: fullSize, inset: insets)
        scene.bounds = CGRect(origin: .zero, size: fullSize)
        scene.reanchorRequested = true
    }

    private func connect() {
        if useBackend { store.useLive(allowWiFi: allowWiFi) } else { store.useDemo() }
    }
}

struct SettingsView: View {
    @Binding var useBackend: Bool
    @Binding var allowWiFi: Bool
    let connection: VitalsStore.Connection
    @ObservedObject var plan: VisitPlan
    let resetPanels: () -> Void
    let done: () -> Void
    @State private var topicsText = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("Visit") {
                    Stepper("Appointment length: \(Int(plan.minutes)) min", value: $plan.minutes, in: 5...90, step: 5)
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Topics to cover (one per line)").font(.footnote).foregroundStyle(.secondary)
                        TextEditor(text: $topicsText)
                            .frame(minHeight: 120)
                            .font(.system(size: 15))
                    }
                    Button("Reset panel positions", action: resetPanels)
                    Button("Start new visit") {
                        plan.topicsText = topicsText
                        plan.restart()
                        done()
                    }
                }
                Section {
                    Picker("Data source", selection: $useBackend) {
                        Text("Demo data").tag(false)
                        Text("Live (Mac pipeline)").tag(true)
                    }
                    .pickerStyle(.segmented)
                    if useBackend {
                        LabeledContent("Status", value: connection == .live ? "Mac connected" : "Waiting for Mac")
                        Toggle("Allow Wi-Fi connections", isOn: $allowWiFi)
                    }
                } header: {
                    Text("Data source")
                } footer: {
                    if useBackend {
                        Text(allowWiFi
                             ? "On the Mac: python monitor_phone.py --phone ws://\(PhoneLink.wifiAddress ?? "<phone-ip>"):8765"
                             : "Plug this iPhone into the Mac with a cable, then on the Mac run: python monitor_phone.py. The camera and microphone stream to the Mac over USB.")
                    }
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        if topicsText != plan.topicsText { plan.topicsText = topicsText }
                        done()
                    }
                }
            }
            .onAppear { topicsText = plan.topicsText }
        }
    }
}
