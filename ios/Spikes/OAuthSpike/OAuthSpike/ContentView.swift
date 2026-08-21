import Observation
import SwiftUI

@Observable
final class ContentViewModel {

    enum Status: Equatable {
        case idle
        case signingIn
        case signedIn(email: String)
        case error(String)
    }

    private(set) var status: Status = .idle
    private(set) var refreshTokenInKeychain = false

    private let auth: GoogleAuthService

    init(auth: GoogleAuthService = GoogleAuthService()) {
        self.auth = auth
        refreshTokenInKeychain = auth.hasStoredRefreshToken
    }

    @MainActor
    func connectTapped() async {
        status = .signingIn
        do {
            let userInfo = try await auth.signIn()
            status = .signedIn(email: userInfo.email)
        } catch {
            status = .error(error.localizedDescription)
        }
        refreshTokenInKeychain = auth.hasStoredRefreshToken
    }

    func disconnectTapped() {
        do {
            try auth.signOut()
            status = .idle
        } catch {
            status = .error(error.localizedDescription)
        }
        refreshTokenInKeychain = auth.hasStoredRefreshToken
    }
}

struct ContentView: View {

    @State private var viewModel = ContentViewModel()

    var body: some View {
        VStack(spacing: 20) {
            Text("OAuth Spike")
                .font(.title2.bold())

            switch GoogleAuthConfig.clientID {
            case .failure(let error):
                configMissingView(message: error.errorDescription ?? "Missing configuration.")
            case .success:
                signedInStateView
            }
        }
        .padding()
    }

    @ViewBuilder
    private var signedInStateView: some View {
        switch viewModel.status {
        case .idle, .error:
            Button("Connect Google") {
                Task { await viewModel.connectTapped() }
            }
            .buttonStyle(.borderedProminent)

            if case .error(let message) = viewModel.status {
                Text(message)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
            }

        case .signingIn:
            ProgressView("Signing in…")

        case .signedIn(let email):
            VStack(spacing: 12) {
                Label(email, systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.green)

                Text(viewModel.refreshTokenInKeychain
                     ? "Refresh token stored in Keychain"
                     : "No refresh token in Keychain")
                    .font(.footnote)
                    .foregroundStyle(.secondary)

                Button("Disconnect", role: .destructive) {
                    viewModel.disconnectTapped()
                }
            }
        }
    }

    private func configMissingView(message: String) -> some View {
        VStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle")
                .font(.largeTitle)
                .foregroundStyle(.orange)
            Text(message)
                .font(.footnote)
                .multilineTextAlignment(.center)
                .padding(.horizontal)
        }
    }
}

#Preview {
    ContentView()
}
