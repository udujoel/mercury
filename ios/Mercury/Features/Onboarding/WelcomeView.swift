import SwiftUI

/// The first screen, in the editorial register.
///
/// What this replaces: a centred wordmark and a tagline on `MercuryColor.canvas`,
/// which is a token from the dark ambient palette ADR-012 replaced. Onboarding
/// was the last part of the app still wearing the old design, so the first
/// thing anyone saw was the one screen that did not look like the product.
///
/// It also **reorders the flow**, which matters more than the paint. It used to
/// ask for Google access on the second screen, before saying what Mercury does
/// with it. That is the wrong order for the one permission the whole app
/// depends on: a person deciding whether to grant read access to their mail
/// deserves to know what is read, what is kept and what is discarded before
/// the question, not after.
///
/// So: what it is, then what it touches, then the permission, then the time.
struct WelcomeView: View {
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: MercurySpace.generous) {
                    masthead

                    VStack(alignment: .leading, spacing: MercurySpace.comfortable) {
                        Text("Your morning, narrated.")
                            .font(MercuryType.greeting)
                            .foregroundStyle(MercuryColor.ink.color)
                            .fixedSize(horizontal: false, vertical: true)

                        Text("Overnight, Mercury reads what would otherwise be "
                             + "twenty minutes of scrolling — your calendar, "
                             + "your inbox, and the news your newsletters are "
                             + "covering. It works out which of those are the "
                             + "same story, writes a script, and two hosts read "
                             + "it to you.")
                            .font(MercuryType.cardBody)
                            .foregroundStyle(MercuryColor.inkMuted.color)
                            .fixedSize(horizontal: false, vertical: true)

                        Text("By the time you wake up it is finished and "
                             + "waiting. There is no feed to scroll.")
                            .font(MercuryType.cardBody)
                            .foregroundStyle(MercuryColor.inkMuted.color)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    NavigationLink {
                        DataPromiseView()
                    } label: {
                        OnboardingButtonLabel("Continue")
                    }
                }
                .mercuryGutter()
                .padding(.vertical, MercurySpace.section)
            }
            .background(MercuryColor.surface.color.ignoresSafeArea())
            .navigationBarHidden(true)
        }
    }

    /// The same device as Home's: a name, then a 2pt rule in the day's accent.
    /// Arriving on a screen that already looks like the app is the point.
    private var masthead: some View {
        VStack(alignment: .leading, spacing: MercurySpace.snug) {
            Text("Mercury")
                .font(MercuryType.eyebrow)
                .textCase(.uppercase)
                .tracking(0.6)
                .foregroundStyle(MercuryColor.inkFaint.color)

            Rectangle()
                .fill(MercuryColor.accent.color)
                .frame(height: 2)
        }
    }
}

/// What Mercury reads, and what it keeps — BEFORE the permission is asked.
///
/// The privacy policy already says all of this in plain words, and the app
/// never repeated it. Someone deciding whether to grant read access to their
/// mail should not have to leave the app and find a web page to learn what
/// happens to it.
///
/// The claims here are checked against the code, not written from a template.
/// Where this screen and the pipeline ever disagree, the pipeline is what is
/// true and this screen is the bug.
struct DataPromiseView: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: MercurySpace.generous) {
                Text("What Mercury reads")
                    .font(MercuryType.greeting)
                    .foregroundStyle(MercuryColor.ink.color)
                    .fixedSize(horizontal: false, vertical: true)

                VStack(alignment: .leading, spacing: MercurySpace.comfortable) {
                    promise(
                        "Read-only, and structurally so",
                        "Mercury asks Google for `gmail.readonly` and "
                        + "`calendar.readonly`. It cannot send, reply to, "
                        + "delete, label or change anything — not as a policy "
                        + "that could be relaxed later, but because those are "
                        + "the only permissions it has."
                    )
                    promise(
                        "Your mail is never stored",
                        "Message bodies are held in memory for the few "
                        + "minutes a briefing takes to make, used to write the "
                        + "script, and discarded. There is no database of your "
                        + "inbox."
                    )
                    promise(
                        "What is kept",
                        "The audio, the script the hosts read, and where each "
                        + "item came from — a sender, a subject line, a link. "
                        + "All of it is deleted automatically after 30 days."
                    )
                }

                NavigationLink {
                    ConnectAccountsView()
                } label: {
                    OnboardingButtonLabel("Connect Google")
                }

                Link("Read the full privacy policy",
                     destination: URL(string: "https://joeludu.com/mercury/privacy")!)
                    .font(MercuryType.figureCaption)
                    .foregroundStyle(MercuryColor.accent.color)
            }
            .mercuryGutter()
            .padding(.vertical, MercurySpace.section)
        }
        .background(MercuryColor.surface.color.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
    }

    private func promise(_ title: String, _ body: String) -> some View {
        VStack(alignment: .leading, spacing: MercurySpace.tight) {
            Text(title)
                .font(MercuryType.cardTitle)
                .foregroundStyle(MercuryColor.ink.color)
            Text(body)
                .font(MercuryType.cardBody)
                .foregroundStyle(MercuryColor.inkMuted.color)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// The one button style onboarding uses, so four screens cannot drift into
/// four slightly different buttons.
struct OnboardingButtonLabel: View {
    let title: String

    init(_ title: String) { self.title = title }

    var body: some View {
        Text(title)
            .font(MercuryType.cardTitle)
            .foregroundStyle(MercuryColor.inkOnAccent.color)
            .frame(maxWidth: .infinity)
            .frame(minHeight: 50)
            .background(MercuryColor.accent.color, in: Capsule())
    }
}

#Preview("Welcome") {
    WelcomeView()
}

#Preview("What it reads") {
    NavigationStack { DataPromiseView() }
}
