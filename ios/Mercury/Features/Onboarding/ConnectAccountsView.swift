import SwiftUI

/// Phase 1 scope: Google (Gmail + Calendar), read-only scopes only
/// (`gmail.readonly`, `calendar.readonly` — CLAUDE.md rule 4). The OAuth
/// flow itself is `ios/Spikes/OAuthSpike/GoogleAuthService.swift`, validated
/// in Phase 0; wiring it into this screen is the next iteration on this
/// feature, not part of this MVP pass.
struct ConnectAccountsView: View {

    var body: some View {
        ZStack {
            MercuryColor.canvas.ignoresSafeArea()
            VStack(spacing: 20) {
                Spacer()
                Image(systemName: "envelope.badge.person.crop")
                    .font(.system(size: 48))
                    .foregroundStyle(MercuryColor.textPrimary)
                Text("Connect Google")
                    .font(MercuryType.cardTitle)
                    .foregroundStyle(MercuryColor.textPrimary)
                Text("Read-only access to Gmail and Calendar.\nMercury never sends, deletes, or modifies anything.")
                    .font(MercuryType.cardBody)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(MercuryColor.textSecondary)
                Spacer()

                // Deliberately inert for Phase 1. Per ADR-001 the SERVER
                // generates the briefing and holds its own Google token, so
                // the phone needs no grant of its own yet. The button used to
                // flip a local flag and relabel itself "Connected", which
                // claimed an OAuth grant that never happened — and gated
                // Continue behind that claim, making onboarding a dead end.
                Text("Google is connected on the server for now.\nIn-app sign-in arrives with scheduled delivery.")
                    .font(MercuryType.cardBody)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(MercuryColor.textTertiary)
                    .padding(.horizontal, 32)

                NavigationLink {
                    ScheduleSetupView()
                } label: {
                    Text("Continue")
                        .font(MercuryType.cardTitle)
                        .foregroundStyle(.black)
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(MercuryColor.textPrimary, in: Capsule())
                }
                .padding(.horizontal, 32)
                .padding(.bottom, 48)
            }
        }
    }
}

#Preview {
    NavigationStack { ConnectAccountsView() }
}
