import SwiftUI

enum Theme {
    // MARK: - Backgrounds
    static let background = Color(red: 0.10, green: 0.10, blue: 0.10)
    static let cardBackground = Color(red: 0.15, green: 0.15, blue: 0.15)
    static let surfaceBackground = Color(red: 0.12, green: 0.12, blue: 0.12)

    // MARK: - Accent
    static let accent = Color(red: 0.83, green: 0.66, blue: 0.26)
    static let accentDimmed = Color(red: 0.83, green: 0.66, blue: 0.26).opacity(0.55)
    static let accentSubtle = Color(red: 0.83, green: 0.66, blue: 0.26).opacity(0.12)

    // MARK: - Text
    static let textPrimary = Color(red: 0.94, green: 0.94, blue: 0.94)
    static let textSecondary = Color(red: 0.60, green: 0.60, blue: 0.60)
    static let textTertiary = Color(red: 0.40, green: 0.40, blue: 0.40)

    // MARK: - Semantic
    static let success = Color(red: 0.24, green: 0.75, blue: 0.42)
    static let error = Color(red: 0.88, green: 0.33, blue: 0.33)
    /// Slightly brighter than cardBackground so 1-pt strokes are legible
    static let border = Color(red: 0.26, green: 0.26, blue: 0.26)
    static let micIdle = accent
    static let micRecording = error

    // MARK: - Typography (New York serif for headers)
    static func serifFont(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight, design: .serif)
    }

    static let heroTitle   = Font.system(size: 48, weight: .bold,     design: .serif)
    static let navTitle    = Font.system(size: 17, weight: .semibold,  design: .serif)
    static let sectionTitle = Font.system(size: 18, weight: .semibold, design: .serif)
    static let headline    = Font.system(size: 17, weight: .semibold,  design: .serif)
    static let bodySerif   = Font.system(size: 16, weight: .regular,   design: .serif)
    static let captionSerif = Font.system(size: 13, weight: .regular,  design: .serif)

    // MARK: - Spacing scale
    static let spacingXS: CGFloat  = 4
    static let spacingSM: CGFloat  = 8
    static let spacingMD: CGFloat  = 16
    static let spacingLG: CGFloat  = 24
    static let spacingXL: CGFloat  = 32

    // MARK: - Corner radii
    static let radiusSM: CGFloat   = 8
    static let radiusMD: CGFloat   = 12
    static let radiusLG: CGFloat   = 16

    // MARK: - Touch targets
    static let minTouchHeight: CGFloat = 52
}
