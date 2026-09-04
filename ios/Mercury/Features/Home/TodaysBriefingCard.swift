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

    let state: LoadState
    let summary: String?
    let isPlaying: Bool
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
    }

    // MARK: - Ready / loading

    private var action: some View {
        Button(action: onPlay) {
            layout {
                glyph
                VStack(alignment: .leading, spacing: MercurySpace.tight) {
                    Text("Today's briefing")
                        .font(MercuryType.cardTitle)
                        .foregroundStyle(MercuryColor.ink.color)
                    Text(subtitle)
                        .font(MercuryType.figureCaption)
                        .foregroundStyle(MercuryColor.inkMuted.color)
                }
                Spacer(minLength: 0)
            }
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(isLoading)
        .accessibilityLabel(isPlaying ? "Today's briefing, playing" : "Play today's briefing")
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
        TodaysBriefingCard(state: .ready, summary: "6 min · 14 sources",
                           isPlaying: false, onPlay: {}, onRetry: {})
        TodaysBriefingCard(state: .loading, summary: nil,
                           isPlaying: false, onPlay: {}, onRetry: {})
        TodaysBriefingCard(state: .failed("Network error: Error Domain=NSURLErrorDomain Code=-1009"),
                           summary: nil, isPlaying: false, onPlay: {}, onRetry: {})
    }
    .padding()
    .background(MercuryColor.surface.color)
}
