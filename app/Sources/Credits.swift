import AppKit
import SwiftUI

/// Credits: who makes the app and the people who support it. Opened from the Credits
/// button in the Software Update window. Supporters who gave a name for the app are listed
/// by it; everyone else is an anonymous supporter. Add new names to `supporters` (and to
/// SUPPORTERS in the Windows and Linux app's desktop/mmm_app/credits.py, which must match).
enum Credits {
    /// Each supporter's name as they asked for it to appear, or nil for an anonymous one.
    static let supporters: [String?] = [nil, nil, nil]
    static let anonymous = "Anonymous supporter"
    static let makers: [(name: String, what: String)] = [
        ("Tuna", "Manifold Mesh Mix: the app, its checks and repairs"),
        ("Duvdivan", "stl2curves: finding threads, fillets and freeform areas in the mesh"),
    ]
    static let nameNote = "Want your name here? When you donate, write the name you'd like shown in the app in your "
        + "message. If you don't give one, you're listed as an anonymous supporter."

    static var names: [String] {
        supporters.map { n in
            let t = n?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return t.isEmpty ? anonymous : t
        }
    }
}

struct CreditsView: View {
    @EnvironmentObject var settings: Settings
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: "heart")
                    .font(.system(size: 26, weight: .light))
                    .foregroundStyle(settings.accent.color)
                VStack(alignment: .leading, spacing: 4) {
                    Text("Credits").font(.system(size: 15, weight: .bold))
                    Text("Thank you to everyone who makes Mesh Mix-S2C possible.")
                        .font(.system(size: 12))
                        .foregroundStyle(Theme.textDim)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            SectionLabel(text: "Made by")
            VStack(alignment: .leading, spacing: 10) {
                ForEach(Credits.makers, id: \.name) { m in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(m.name).font(.system(size: 12.5, weight: .semibold))
                        Text(m.what).font(.system(size: 11)).foregroundStyle(Theme.textDim)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .card(padding: 12)
            SectionLabel(text: "Supporters")
            VStack(alignment: .leading, spacing: 8) {
                ForEach(Array(Credits.names.enumerated()), id: \.offset) { _, name in
                    HStack(spacing: 8) {
                        Image(systemName: "heart").font(.system(size: 11)).foregroundStyle(settings.accent.color)
                        Text(name).font(.system(size: 12))
                            .foregroundStyle(name == Credits.anonymous ? Theme.textDim : Theme.text)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .card(padding: 12)
            Text(Credits.nameNote)
                .font(.system(size: 11))
                .foregroundStyle(Theme.textDim)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                Spacer()
                Button("Close") { dismiss() }
                    .buttonStyle(GhostButtonStyle())
                    .keyboardShortcut(.cancelAction)
                Button {
                    NSWorkspace.shared.open(Support.url)
                } label: {
                    Label("Support the Project", systemImage: "heart").padding(.horizontal, 8)
                }
                .buttonStyle(AccentButtonStyle(accent: settings.accent.color))
                .fixedSize()
            }
        }
        .padding(20)
        .frame(width: 440)
        .background(Theme.panel)
        .foregroundStyle(Theme.text)
        .tint(settings.accent.color)
        .preferredColorScheme(settings.theme.scheme)
    }
}
