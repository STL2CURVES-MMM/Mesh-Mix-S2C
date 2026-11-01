import SwiftUI

/// Visual language shared with the Windows and Linux app: calm surfaces,
/// hairline borders, one accent. Four themes (Dark, the default; Light;
/// Original, 1.6.0's look; Match system), all with the same layout and corners.
enum AccentChoice: String, CaseIterable, Identifiable {
    case matte, cyan, mint, violet, orange, pink, lime

    var id: String { rawValue }

    /// The colour as chosen (for big fills, and the picker's dots).
    var baseColor: Color {
        switch self {
        case .matte: return Color(red: 0x7F / 255.0, green: 0xB4 / 255.0, blue: 0xD9 / 255.0)
        case .cyan: return Color(red: 0.13, green: 0.83, blue: 0.93)
        case .mint: return Color(red: 0.20, green: 0.93, blue: 0.68)
        case .violet: return Color(red: 0.62, green: 0.45, blue: 1.00)
        case .orange: return Color(red: 1.00, green: 0.55, blue: 0.18)
        case .pink: return Color(red: 1.00, green: 0.33, blue: 0.62)
        case .lime: return Color(red: 0.70, green: 0.95, blue: 0.20)
        }
    }

    /// The colour in the current theme: on Light, pale accents get a deeper
    /// shade for text, outlines and thin lines (matte blue its own darker one,
    /// the others about a third black).
    var color: Color {
        guard Theme.palette.light else { return baseColor }
        if self == .matte { return Color(red: 0x3F / 255.0, green: 0x7F / 255.0, blue: 0xB3 / 255.0) }
        let c = NSColor(baseColor).usingColorSpace(.sRGB) ?? NSColor(baseColor)
        return Color(red: Double(c.redComponent) * 0.66, green: Double(c.greenComponent) * 0.66,
                     blue: Double(c.blueComponent) * 0.66)
    }

    var nsColor: NSColor { NSColor(color) }

    /// The color that marks the preset Auto chose: one that stands apart
    /// from the accent color.
    var autoPick: Color { (self == .violet || self == .pink) ? AccentChoice.mint.color : AccentChoice.violet.color }

    var title: String { self == .matte ? "Matte blue" : rawValue.capitalized }
}

enum ThemeChoice: String, CaseIterable, Identifiable {
    case dark, light, original, system

    var id: String { rawValue }

    var title: String {
        switch self {
        case .dark: return "Dark"
        case .light: return "Light"
        case .original: return "Original"
        case .system: return "Match system"
        }
    }

    /// The palette this choice stands for (Match system: the computer's
    /// own dark or light setting).
    func palette(systemDark: Bool) -> Palette {
        switch self {
        case .dark: return .dark
        case .light: return .light
        case .original: return .original
        case .system: return systemDark ? .dark : .light
        }
    }

    /// The colour scheme windows and system controls use (nil: the system's).
    var scheme: ColorScheme? {
        switch self {
        case .light: return .light
        case .system: return nil
        default: return .dark
        }
    }
}

private func hex(_ v: Int, _ a: Double = 1) -> Color {
    Color(red: Double((v >> 16) & 0xFF) / 255, green: Double((v >> 8) & 0xFF) / 255, blue: Double(v & 0xFF) / 255).opacity(a)
}

private func nsHex(_ v: Int) -> NSColor {
    NSColor(red: CGFloat((v >> 16) & 0xFF) / 255, green: CGFloat((v >> 8) & 0xFF) / 255,
            blue: CGFloat(v & 0xFF) / 255, alpha: 1)
}

/// Every colour of a theme.
struct Palette: Equatable {
    let name: String
    let light: Bool
    let bg, panel, card, cardHover, stroke, strokeStrong, text, textDim, textFaint, good, warn, bad: Color
    let viewportTop, viewportBottom: NSColor

    static func == (a: Palette, b: Palette) -> Bool { a.name == b.name }

    static let dark = Palette(
        name: "dark", light: false, bg: hex(0x0E1116), panel: hex(0x141920), card: hex(0x1A2029),
        cardHover: hex(0x212936), stroke: Color.white.opacity(0.08), strokeStrong: Color.white.opacity(0.15),
        text: hex(0xE8EDF3), textDim: hex(0x9AA6B4), textFaint: hex(0x66717F),
        good: hex(0x5CC98A), warn: hex(0xE3B04F), bad: hex(0xE06B6E),
        viewportTop: nsHex(0x1A2029), viewportBottom: nsHex(0x0B0E12))

    static let light = Palette(
        name: "light", light: true, bg: hex(0xEEF1F5), panel: hex(0xF6F8FA), card: hex(0xFFFFFF),
        cardHover: hex(0xF1F4F8), stroke: Color.black.opacity(0.08), strokeStrong: Color.black.opacity(0.15),
        text: hex(0x1A1F26), textDim: hex(0x5A6573), textFaint: hex(0x8B95A2),
        good: hex(0x1E9E57), warn: hex(0xB7791F), bad: hex(0xD64545),
        viewportTop: nsHex(0xF4F6F9), viewportBottom: nsHex(0xDCE2E9))

    /// Manifold Mesh Mix 1.6.0's look, colour for colour.
    static let original = Palette(
        name: "original", light: false, bg: Color(red: 0.039, green: 0.043, blue: 0.055),
        panel: Color(red: 0.063, green: 0.070, blue: 0.086), card: Color(red: 0.090, green: 0.098, blue: 0.118),
        cardHover: Color(red: 0.115, green: 0.125, blue: 0.150), stroke: Color.white.opacity(0.07),
        strokeStrong: Color.white.opacity(0.14), text: Color.white.opacity(0.93), textDim: Color.white.opacity(0.58),
        textFaint: Color.white.opacity(0.36), good: Color(red: 0.30, green: 0.88, blue: 0.55),
        warn: Color(red: 1.00, green: 0.75, blue: 0.25), bad: Color(red: 1.00, green: 0.38, blue: 0.40),
        viewportTop: NSColor(red: 0.085, green: 0.092, blue: 0.112, alpha: 1),
        viewportBottom: NSColor(red: 0.035, green: 0.038, blue: 0.048, alpha: 1))
}

enum Theme {
    /// The current theme's colours. ContentView sets it from the settings and
    /// rebuilds its views when it changes, so everything reads the new one.
    static var palette: Palette = .dark

    static var bg: Color { palette.bg }
    static var panel: Color { palette.panel }
    static var card: Color { palette.card }
    static var cardHover: Color { palette.cardHover }
    static var stroke: Color { palette.stroke }
    static var strokeStrong: Color { palette.strokeStrong }
    static var text: Color { palette.text }
    static var textDim: Color { palette.textDim }
    static var textFaint: Color { palette.textFaint }
    static var good: Color { palette.good }
    static var warn: Color { palette.warn }
    static var bad: Color { palette.bad }

    static var viewportTop: NSColor { palette.viewportTop }
    static var viewportBottom: NSColor { palette.viewportBottom }

    /// A hairline or wash of the text's own colour: white on the dark themes,
    /// black on Light (white washes would vanish on a light background).
    static func ink(_ a: Double) -> Color { palette.light ? Color.black.opacity(a) : Color.white.opacity(a) }

    /// Text and marks drawn on an accent fill.
    static var onAccent: Color { palette.light ? Color.white : Color.black }

    // Corner radii, the same in every theme: softened squares rather than pills
    static let rBig: CGFloat = 8      // cards, panels, dialogs
    static let rMed: CGFloat = 6      // banners, pills, list rows, overlays
    static let rSmall: CGFloat = 4    // buttons, inputs, menus
    static let rTiny: CGFloat = 2     // progress bars

    static func mono(_ size: CGFloat = 11, _ weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight, design: .monospaced)
    }
}

struct CardBackground: ViewModifier {
    var highlighted: Bool = false
    var accent: Color = .clear
    var padding: CGFloat = 12

    func body(content: Content) -> some View {
        content
            .padding(padding)
            .background(
                RoundedRectangle(cornerRadius: Theme.rBig, style: .continuous)
                    .fill(highlighted ? Theme.cardHover : Theme.card)
            )
            .overlay(
                RoundedRectangle(cornerRadius: Theme.rBig, style: .continuous)
                    .strokeBorder(highlighted ? accent.opacity(0.85) : Theme.stroke, lineWidth: highlighted ? 1.2 : 1)
            )
    }
}

extension View {
    func card(highlighted: Bool = false, accent: Color = .clear, padding: CGFloat = 12) -> some View {
        modifier(CardBackground(highlighted: highlighted, accent: accent, padding: padding))
    }
}

/// Small uppercase section label.
struct SectionLabel: View {
    let text: String
    var body: some View {
        Text(text.uppercased())
            .font(.system(size: 10, weight: .semibold))
            .tracking(1.2)
            .foregroundStyle(Theme.textFaint)
    }
}

/// Rounded "pill" used for status and stats.
struct Pill: View {
    let text: String
    var color: Color = Theme.textDim
    var filled: Bool = false
    var icon: String? = nil

    var body: some View {
        HStack(spacing: 4) {
            if let icon { Image(systemName: icon).font(.system(size: 9, weight: .bold)) }
            Text(text).font(.system(size: 10.5, weight: .semibold))
        }
        .padding(.horizontal, 7)
        .padding(.vertical, 3)
        .foregroundStyle(filled ? Theme.onAccent.opacity(0.85) : color)
        .background(
            RoundedRectangle(cornerRadius: Theme.rMed, style: .continuous).fill(filled ? color : color.opacity(0.12))
        )
        .overlay(RoundedRectangle(cornerRadius: Theme.rMed, style: .continuous)
            .strokeBorder(color.opacity(filled ? 0 : 0.25), lineWidth: 1))
    }
}

/// Primary accent button style.
struct AccentButtonStyle: ButtonStyle {
    let accent: Color
    var prominent: Bool = true
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13, weight: .semibold))
            .padding(.vertical, 9)
            .padding(.horizontal, 14)
            .frame(maxWidth: .infinity)
            .foregroundStyle(prominent ? Theme.onAccent.opacity(isEnabled ? 0.88 : 0.5) : accent)
            .background(
                RoundedRectangle(cornerRadius: Theme.rSmall, style: .continuous)
                    .fill(prominent ? accent.opacity(isEnabled ? (configuration.isPressed ? 0.75 : 1) : 0.3)
                                    : accent.opacity(configuration.isPressed ? 0.22 : 0.12))
            )
            .overlay(
                RoundedRectangle(cornerRadius: Theme.rSmall, style: .continuous)
                    .strokeBorder(prominent ? Color.clear : accent.opacity(0.35), lineWidth: 1)
            )
            .shadow(color: prominent && isEnabled ? accent.opacity(0.35) : .clear, radius: 10, y: 2)
            .contentShape(Rectangle())
    }
}

/// Subtle icon button.
struct GhostButtonStyle: ButtonStyle {
    var tint: Color = Theme.textDim
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 12, weight: .medium))
            .foregroundStyle(tint)
            .padding(.horizontal, 8)
            .padding(.vertical, 5)
            .background(
                RoundedRectangle(cornerRadius: Theme.rSmall, style: .continuous)
                    .fill(Theme.ink(configuration.isPressed ? 0.12 : 0.05))
            )
            .overlay(RoundedRectangle(cornerRadius: Theme.rSmall, style: .continuous).strokeBorder(Theme.stroke))
            .contentShape(Rectangle())
    }
}

/// Thin accent progress bar.
struct ThinProgress: View {
    var value: Double
    var accent: Color
    var indeterminate: Bool = false
    @LocalState private var phase: CGFloat = 0

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Theme.ink(0.07))
                if indeterminate {
                    Capsule()
                        .fill(LinearGradient(colors: [accent.opacity(0), accent, accent.opacity(0)], startPoint: .leading, endPoint: .trailing))
                        .frame(width: geo.size.width * 0.35)
                        .offset(x: (geo.size.width * 1.35) * phase - geo.size.width * 0.35)
                        .onAppear {
                            withAnimation(.linear(duration: 1.2).repeatForever(autoreverses: false)) { phase = 1 }
                        }
                        // start the sweep again next time (a file read again
                        // shows the moving bar before the check reports progress)
                        .onDisappear { phase = 0 }
                } else {
                    Capsule()
                        .fill(accent)
                        .frame(width: max(4, geo.size.width * CGFloat(min(max(value, 0), 1))))
                        .shadow(color: accent.opacity(0.6), radius: 4)
                        .animation(.easeOut(duration: 0.25), value: value)
                }
            }
        }
        .frame(height: 4)
        .clipShape(Capsule())
    }
}

/// The command line toolchain ships without the SwiftUI macro plugin, so the
/// `@State` macro can't expand there. The underlying property wrapper works
/// fine; this alias lets us use it directly.
typealias LocalState<Value> = SwiftUI.State<Value>

/// Reports a view's width.
struct WidthReader: View {
    let onChange: (CGFloat) -> Void
    var body: some View {
        GeometryReader { g in
            Color.clear
                .onAppear { onChange(g.size.width) }
                .onChange(of: g.size.width) { _, w in onChange(w) }
        }
    }
}

/// Shows the whole text in a tooltip when hovering, but only when it doesn't
/// fit and is cut off with "…". Compares the shown width with the width the
/// text would need on one line.
struct TruncationHelp: ViewModifier {
    let text: String
    @LocalState private var shown: CGFloat = 0
    @LocalState private var full: CGFloat = 0

    func body(content: Content) -> some View {
        content
            .background(WidthReader { shown = $0 })
            .background(
                content
                    .fixedSize(horizontal: true, vertical: false)
                    .hidden()
                    .background(WidthReader { full = $0 })
                    .allowsHitTesting(false)
            )
            .help(full > shown + 0.5 ? text : "")
    }
}

extension View {
    /// Tooltip with the full text when this one-line text is cut off.
    func helpIfTruncated(_ text: String) -> some View {
        modifier(TruncationHelp(text: text))
    }
}
