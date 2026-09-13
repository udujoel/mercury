import SwiftUI

/// The one primary action on Home.
///
/// It replaces `BriefingCard`, which was the front card of the fanned deck
/// and carried the greeting, a blurred ambient wash and an animating waveform
/// pill. The greeting is now the page's own headline, the wash went with the
/// dark register, and this is left doing one job: a big, obvious, single tap
/// to the thing you opened the app for.
///
/// It also absorbs the loading and error states, which used to float over the
/// deck as an `ultraThinMaterial` spinner and a red debug dump pinned to the
/// bottom of the screen. Putting them here means the state of the briefing is
/// shown by the control that fetches it, rather than somewhere else on the
/// page.
struct TodaysBriefingCard: View {
    @Environment(\.dynamicTypeSize) private var typeSize

    /// "Today's briefing", usually — but Home passes the actual day when
    /// what is on the device is older than today. A card that says "Today's"
    /// over yesterday's runtime is the kind of confident wrongness that costs
    /// trust in everything else on the page.
    let title: String
    let state: LoadState
    let summary: String?
    let isPlaying: Bool
    /// A rebuild landed and has not been looked at yet.
    ///
    /// Drawn as a ring in the day's accent plus a line naming what changed,
    /// rather than a dot or a badge. A badge says "something"; the point of
    /// the rebuild is that THIS briefing is not the one you already heard,
    /// and the card itself is the thing that changed.
    var isFresh: Bool = false
    let onPlay: () -> Void
    let onRetry: () -> Void

    var body: some View {
        Group {
            if case .failed(let message) = state {
                failure(HomeErrorPresentation.present(message))
            } else {
                action
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(MercurySpace.comfortable)
        .mercuryCard(.raised)
        .overlay {
            // A ring rather than a fill: ADR-012's register is ink on paper
            // and the accent appears twice on this page by design. Tinting
            // the whole card would make the freshest thing on screen the
            // loudest, which is a different claim from "this is new".
            if isFresh {
                RoundedRectangle(cornerRadius: MercuryColor.cardCornerRadius,
                                 style: .continuous)
                    .strokeBorder(MercuryColor.accent.color, lineWidth: 2)
            }
        }
        .animation(.easeOut(duration: 0.35), value: isFresh)
        .accessibilityElement(children: .contain)
        // VoiceOver gets the fact in words. A ring is invisible to it, and
        // "your briefing was rebuilt" is the whole message.
        .accessibilityHint(isFresh ? "Rebuilt since you last listened" : "")
    }

    // MARK: - Ready / loading

    private var action: some View {
        Button(action: onPlay) {
            layout {
                glyph
                VStack(alignment: .leading, spacing: MercurySpace.tight) {
                    Text(title)
                        .font(MercuryType.cardTitle)
                        .foregroundStyle(MercuryColor.ink.color)
                    Text(subtitle)
                        .font(MercuryType.figureCaption)
                        .foregroundStyle(MercuryColor.inkMuted.color)
                    if isFresh {
                        Text("Rebuilt from your latest mail")
                            .font(MercuryType.figureCaption)
                            .foregroundStyle(MercuryColor.accent.color)
                    }
                }
                Spacer(minLength: 0)
            }
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(isLoading)
        .accessibilityLabel(isPlaying ? "\(title), playing" : "Play \(title.lowercased())")
        .accessibilityValue(subtitle)
    }

    private var subtitle: String {
        if isLoading { return "Getting today's briefing…" }
        // No summary means no manifest yet — the honest line is an
        // invitation, not an invented runtime.
        return summary ?? "Tap to play"
    }

    private var isLoading: Bool { state == .loading }

    @ViewBuilder
    private var glyph: some View {
        ZStack {
            if isLoading {
                ProgressView()
                    .tint(MercuryColor.accent.color)
            } else {
                // The second and last appearance of the day's accent on this
                // screen. Drawn as ink rather than as a filled disc: the
                // accents are pinned to clear 4.5:1 as FOREGROUND on paper
                // (ADR-012), which is the contrast this relies on.
                Image(systemName: isPlaying ? "pause.fill" : "play.fill")
                    .font(.title2)
                    .foregroundStyle(MercuryColor.accent.color)
            }
        }
        .frame(width: 44, height: 44)
        .accessibilityHidden(true)
    }

    // MARK: - Failure

    private func failure(_ presentation: HomeErrorPresentation) -> some View {
        VStack(alignment: .leading, spacing: MercurySpace.base) {
            Text(presentation.headline)
                .font(MercuryType.cardBody)
                .foregroundStyle(MercuryColor.ink.color)
                .fixedSize(horizontal: false, vertical: true)

            if let detail = presentation.detail {
                Text(detail)
                    .font(MercuryType.caption)
                    .foregroundStyle(MercuryColor.inkFaint.color)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Button(action: onRetry) {
                Text("Try again")
                    .font(MercuryType.chapterTab)
                    .foregroundStyle(MercuryColor.accent.color)
                    .frame(minHeight: 44)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Try again")
            .accessibilityHint("Fetches today's briefing again")
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .contain)
    }

    // MARK: - Layout

    /// The glyph sits beside the text until the type gets large enough that
    /// doing so leaves the text two words wide.
    @ViewBuilder
    private func layout<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: MercurySpace.snug) { content() }
        } else {
            HStack(spacing: MercurySpace.base) { content() }
        }
    }
}

#Preview("Ready") {
    VStack(spacing: 20) {
        TodaysBriefingCard(title: "Today's briefing", state: .ready,
                           summary: "6 min · 14 sources",
                           isPlaying: false, onPlay: {}, onRetry: {})
        TodaysBriefingCard(title: "Today's briefing", state: .loading, summary: nil,
                           isPlaying: false, onPlay: {}, onRetry: {})
        // What Home shows when the cron has not run and the only briefing on
        // the device is older than today.
        TodaysBriefingCard(title: "Thursday's briefing", state: .ready,
                           summary: "8 min · 11 sources",
                           isPlaying: false, onPlay: {}, onRetry: {})
        TodaysBriefingCard(title: "Today's briefing",
                           state: .failed("Network error: Error Domain=NSURLErrorDomain Code=-1009"),
                           summary: nil, isPlaying: false, onPlay: {}, onRetry: {})
    }
    .padding()
    .background(MercuryColor.surface.color)
}
