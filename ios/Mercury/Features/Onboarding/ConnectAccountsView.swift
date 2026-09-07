import SwiftUI

/// The permission step, on the editorial register.
///
/// Phase 1 scope: Google (Gmail + Calendar), read-only scopes only
/// (`gmail.readonly`, `calendar.readonly` — CLAUDE.md rule 4). The OAuth flow
/// itself is `ios/Spikes/OAuthSpike/GoogleAuthService.swift`, validated in
/// Phase 0; wiring it into this screen is a later iteration.
///
/// What Mercury does with that access is now explained on the screen BEFORE
/// this one, which is where it belongs — this screen is the question, and
/// `DataPromiseView` is the information needed to answer it.
struct ConnectAccountsView: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: MercurySpace.generous) {
                Text("Connect Google")
                    .font(MercuryType.greeting)
                    .foregroundStyle(MercuryColor.ink.color)
                    .fixedSize(horizontal: false, vertical: true)

                // Deliberately inert for Phase 1. Per ADR-001 the SERVER
                // generates the briefing and holds its own Google token, so
                // the phone needs no grant of its own yet. The button here
                // used to flip a local flag and relabel itself "Connected",
                // which claimed an OAuth grant that never happened — and
                // gated Continue behind that claim, making onboarding a dead
                // end. Saying the true thing is better than a button that
                // lies about what it did.
                VStack(alignment: .leading, spacing: MercurySpace.tight) {
                    Text("Already connected")
                        .font(MercuryType.cardTitle)
                        .foregroundStyle(MercuryColor.ink.color)
                    Text("Your Google account is connected on the server, "
                         + "which is where briefings are made. Signing in from "
                         + "the app itself arrives with scheduled delivery — "
                         + "until then there is nothing to grant here.")
                        .font(MercuryType.cardBody)
                        .foregroundStyle(MercuryColor.inkMuted.color)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(MercurySpace.comfortable)
                .mercuryCard(.raised)

                NavigationLink {
                    ScheduleSetupView()
                } label: {
                    OnboardingButtonLabel("Continue")
                }
            }
            .mercuryGutter()
            .padding(.vertical, MercurySpace.section)
        }
        .background(MercuryColor.surface.color.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
    }
}

#Preview {
    NavigationStack { ConnectAccountsView() }
}
