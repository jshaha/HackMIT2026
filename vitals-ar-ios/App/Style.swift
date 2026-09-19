import SwiftUI

/// Light frosted-glass theme: dark ink on white frost, with accents deep enough to read on it.
enum Palette {
    static let ink = Color(red: 0.07, green: 0.09, blue: 0.13)
    static let accent = Color(red: 0.0, green: 0.6, blue: 0.5)
    static let amber = Color(red: 0.86, green: 0.52, blue: 0.0)
    static let danger = Color(red: 0.86, green: 0.2, blue: 0.32)
    static let fatigue = Color(red: 0.2, green: 0.4, blue: 0.9)
    static let muted = ink.opacity(0.58)
    static let hairline = ink.opacity(0.1)
    static let glassEdge = Color.white.opacity(0.75)
}

extension Font {
    static func mono(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font { .system(size: size, weight: weight, design: .monospaced) }
}

/// Light frosted-glass card: blur, a milky white frost layer so dark text reads over any background,
/// a bright top highlight, a white edge and a soft shadow.
struct Glass: ViewModifier {
    var tint: Color = .clear
    var corner: CGFloat = 16

    func body(content: Content) -> some View {
        content
            .padding(10)
            .foregroundStyle(Palette.ink)
            .background {
                RoundedRectangle(cornerRadius: corner, style: .continuous)
                    .fill(.ultraThinMaterial)
                    .overlay(
                        RoundedRectangle(cornerRadius: corner, style: .continuous)
                            .fill(Color.white.opacity(0.5))
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: corner, style: .continuous)
                            .fill(LinearGradient(colors: [.white.opacity(0.55), tint.opacity(0.08), .clear],
                                                 startPoint: .topLeading, endPoint: .center))
                    )
            }
            .overlay(RoundedRectangle(cornerRadius: corner, style: .continuous).stroke(Palette.glassEdge, lineWidth: 1))
            .shadow(color: .black.opacity(0.18), radius: 16, y: 6)
    }
}

extension View {
    func glass(tint: Color = .clear, corner: CGFloat = 16) -> some View { modifier(Glass(tint: tint, corner: corner)) }
}

/// Small uppercase label used at the top of every card.
struct CardLabel: View {
    let text: String
    var icon: String?
    var color: Color = Palette.muted

    var body: some View {
        HStack(spacing: 5) {
            if let icon { Image(systemName: icon).font(.system(size: 9, weight: .bold)) }
            Text(text).font(.mono(9, .semibold)).tracking(1.2)
        }
        .foregroundStyle(color)
    }
}
