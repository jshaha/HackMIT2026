import SwiftUI

/// Two themes, one seam. **Dark** is the original smoked-glass look — charcoal cards, white text, neon teal,
/// crimson HR — which sits back against the room instead of glaring over the patient. **Light** is the
/// white-frost variant. Cards express every colour as `ink` at some opacity, so flipping `ink` flips the
/// whole overlay; `Palette.isDark` is read once at launch (Settings writes it and rebuilds the view).
enum Palette {
    static var isDark = UserDefaults.standard.object(forKey: "darkGlass") as? Bool ?? true

    /// Text and, at low opacity, every fill and rule drawn on a card.
    static var ink: Color { isDark ? .white : Color(red: 0.07, green: 0.09, blue: 0.13) }
    static var accent: Color { isDark ? Color(red: 0.24, green: 0.94, blue: 0.78) : Color(red: 0.0, green: 0.6, blue: 0.5) }
    static var amber: Color { isDark ? Color(red: 1, green: 0.82, blue: 0.4) : Color(red: 0.86, green: 0.52, blue: 0.0) }
    static var danger: Color { isDark ? Color(red: 1, green: 0.33, blue: 0.44) : Color(red: 0.86, green: 0.2, blue: 0.32) }
    static var fatigue: Color { isDark ? Color(red: 0.36, green: 0.55, blue: 1) : Color(red: 0.2, green: 0.4, blue: 0.9) }
    static var muted: Color { ink.opacity(isDark ? 0.68 : 0.58) }
    static var hairline: Color { ink.opacity(isDark ? 0.2 : 0.1) }
    static var glassEdge: Color { isDark ? hairline : Color.white.opacity(0.75) }

    /// The body layer over the blur. Dark keeps a little smoke so white text holds up in a bright room;
    /// light needs a milky frost so dark text holds up over the camera image.
    static var glassFill: Color { isDark ? Color(red: 0.05, green: 0.07, blue: 0.1).opacity(0.38) : Color.white.opacity(0.5) }
    /// The highlight along a card's top edge, which is what makes it read as glass.
    static var glassSheen: Color { isDark ? Color.white.opacity(0.14) : Color.white.opacity(0.55) }
    /// The overlay is drawn in this scheme, so `.ultraThinMaterial` blurs dark or light to match.
    static var colorScheme: ColorScheme { isDark ? .dark : .light }
}

extension Font {
    static func mono(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font { .system(size: size, weight: weight, design: .monospaced) }
}

/// A frosted-glass card: blur, a body layer so text reads over any background, a top highlight, a hairline
/// edge and a soft drop shadow. `tint` washes the card faintly in a metric's own colour.
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
                            .fill(Palette.glassFill)
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: corner, style: .continuous)
                            .fill(LinearGradient(colors: [Palette.glassSheen, tint.opacity(Palette.isDark ? 0.12 : 0.08), .clear],
                                                 startPoint: .topLeading, endPoint: .center))
                    )
            }
            .overlay(RoundedRectangle(cornerRadius: corner, style: .continuous).stroke(Palette.glassEdge, lineWidth: 1))
            .shadow(color: .black.opacity(Palette.isDark ? 0.35 : 0.18), radius: 14, y: 6)
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
