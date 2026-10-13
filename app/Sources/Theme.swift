import SwiftUI

/// Visual language shared with the "Manifold" family of tools:
/// near-black surfaces, hairline borders, one vivid accent.
enum AccentChoice: String, CaseIterable, Identifiable {
    case cyan, mint, violet, orange, pink, lime

    var id: String { rawValue }

    var color: Color {
        switch self {
        case .cyan: return Color(red: 0.13, green: 0.83, blue: 0.93)
        case .mint: return Color(red: 0.20, green: 0.93, blue: 0.68)
        case .violet: return Color(red: 0.62, green: 0.45, blue: 1.00)
        case .orange: return Color(red: 1.00, green: 0.55, blue: 0.18)
        case .pink: return Color(red: 1.00, green: 0.33, blue: 0.62)
        case .lime: return Color(red: 0.70, green: 0.95, blue: 0.20)
        }
    }

    var nsColor: NSColor { NSColor(color) }

    /// The color that marks the preset Auto chose: one that stands apart
    /// from the accent color.
    var autoPick: Color { (self == .violet || self == .pink) ? AccentChoice.mint.color : AccentChoice.violet.color }

    var title: String { rawValue.capitalized }
}

enum Theme {
    static let bg = Color(red: 0.039, green: 0.043, blue: 0.055)
    static let panel = Color(red: 0.063, green: 0.070, blue: 0.086)
    static let card = Color(red: 0.090, green: 0.098, blue: 0.118)
    static let cardHover = Color(red: 0.115, green: 0.125, blue: 0.150)
    static let stroke = Color.white.opacity(0.07)
    static let strokeStrong = Color.white.opacity(0.14)
    static let text = Color.white.opacity(0.93)
    static let textDim = Color.white.opacity(0.58)
    static let textFaint = Color.white.opacity(0.36)
    static let good = Color(red: 0.30, green: 0.88, blue: 0.55)
    static let warn = Color(red: 1.00, green: 0.75, blue: 0.25)
    static let bad = Color(red: 1.00, green: 0.38, blue: 0.40)

    static let viewportTop = NSColor(red: 0.085, green: 0.092, blue: 0.112, alpha: 1)
    static let viewportBottom = NSColor(red: 0.035, green: 0.038, blue: 0.048, alpha: 1)

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
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(highlighted ? Theme.cardHover : Theme.card)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
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
        .foregroundStyle(filled ? Color.black.opacity(0.85) : color)
        .background(
            Capsule().fill(filled ? color : color.opacity(0.12))
        )
        .overlay(Capsule().strokeBorder(color.opacity(filled ? 0 : 0.25), lineWidth: 1))
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
            .foregroundStyle(prominent ? Color.black.opacity(isEnabled ? 0.88 : 0.5) : accent)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(prominent ? accent.opacity(isEnabled ? (configuration.isPressed ? 0.75 : 1) : 0.3)
                                    : accent.opacity(configuration.isPressed ? 0.22 : 0.12))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
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
                RoundedRectangle(cornerRadius: 7, style: .continuous)
                    .fill(Color.white.opacity(configuration.isPressed ? 0.12 : 0.05))
            )
            .overlay(RoundedRectangle(cornerRadius: 7, style: .continuous).strokeBorder(Theme.stroke))
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
                Capsule().fill(Color.white.opacity(0.07))
                if indeterminate {
                    Capsule()
                        .fill(LinearGradient(colors: [accent.opacity(0), accent, accent.opacity(0)], startPoint: .leading, endPoint: .trailing))
                        .frame(width: geo.size.width * 0.35)
                        .offset(x: (geo.size.width * 1.35) * phase - geo.size.width * 0.35)
                        .onAppear {
                            withAnimation(.linear(duration: 1.2).repeatForever(autoreverses: false)) { phase = 1 }
                        }
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
