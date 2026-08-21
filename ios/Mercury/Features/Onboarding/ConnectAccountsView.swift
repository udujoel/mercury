import SwiftUI

/// Phase 1 scope: Google (Gmail + Calendar), read-only scopes only
/// (`gmail.readonly`, `calendar.readonly` — CLAUDE.md rule 4). The OAuth
/// flow itself is `ios/Spikes/OAuthSpike/GoogleAuthService.swift`, validated
/// in Phase 0; wiring it into this screen is the next iteration on this
/// feature, not part of this MVP pass.
struct ConnectAccountsView: View {
    @State private var connected = false

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
                Button {
                    connected = true
                } label: {
                    Text(connected ? "Connected" : "Connect Google account")
                        .font(MercuryType.cardTitle)
                        .foregroundStyle(.black)
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(MercuryColor.textPrimary, in: Capsule())
                }
                .padding(.horizontal, 32)

                NavigationLink {
                    ScheduleSetupView()
                } label: {
                    Text("Continue")
                        .foregroundStyle(connected ? MercuryColor.textPrimary : MercuryColor.textTertiary)
                }
                .disabled(!connected)
                .padding(.bottom, 48)
            }
        }
    }
}

#Preview {
    NavigationStack { ConnectAccountsView() }
}
