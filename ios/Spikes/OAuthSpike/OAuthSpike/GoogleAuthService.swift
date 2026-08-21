import AuthenticationServices
import CryptoKit
import Foundation
import Observation
import UIKit

/// OAuth 2.0 Authorization Code flow with PKCE against Google's endpoints,
/// for a native iOS client.
///
/// Scopes are intentionally minimal and read-only. Mercury's whole trust
/// model (CLAUDE.md rule 4) depends on being structurally incapable of
/// sending or modifying anything in the user's mailbox or calendar, so we
/// never request `gmail.modify`, `gmail.send`, or a calendar write scope
/// here, and never will.
enum GoogleOAuthScopes {
    static let value =
        "openid email " +
        "https://www.googleapis.com/auth/gmail.readonly " +
        "https://www.googleapis.com/auth/calendar.readonly"
}

enum GoogleAuthError: Error, LocalizedError {
    case missingClientID(String)
    case userCancelled
    case stateMismatch
    case missingCallbackURL
    case missingAuthorizationCode
    case tokenExchangeFailed(status: Int, body: String)
    case missingRefreshToken
    case userInfoFetchFailed(status: Int)
    case notSignedIn

    var errorDescription: String? {
        switch self {
        case .missingClientID(let detail):
            return detail
        case .userCancelled:
            return "Sign-in was cancelled."
        case .stateMismatch:
            return "OAuth state parameter did not match. Aborting — this " +
                "could indicate a CSRF attempt against the redirect."
        case .missingCallbackURL:
            return "The authorization callback did not return a URL."
        case .missingAuthorizationCode:
            return "The authorization callback did not include a `code` " +
                "parameter."
        case .tokenExchangeFailed(let status, let body):
            return "Token exchange failed with HTTP \(status): \(body)"
        case .missingRefreshToken:
            return "Google did not return a refresh token. This happens " +
                "if the user has already granted consent for this client " +
                "and Google is reusing a prior grant — revoke access at " +
                "https://myaccount.google.com/permissions and try again. " +
                "We always request access_type=offline and prompt=consent " +
                "specifically to avoid this."
        case .userInfoFetchFailed(let status):
            return "Fetching user info failed with HTTP \(status)."
        case .notSignedIn:
            return "Not signed in."
        }
    }
}

/// Config resolved from the app's Info.plist, which in turn is populated at
/// build time from Secrets.xcconfig (gitignored) via $(GOOGLE_IOS_CLIENT_ID)
/// substitution. Never hardcode a client id in source.
enum GoogleAuthConfig {

    static var clientID: Result<String, GoogleAuthError> {
        guard let raw = Bundle.main.infoDictionary?["GOOGLE_IOS_CLIENT_ID"] as? String,
              !raw.isEmpty,
              raw != "$(GOOGLE_IOS_CLIENT_ID)",
              !raw.contains("YOUR_IOS_CLIENT_ID") else {
            return .failure(.missingClientID(
                "GOOGLE_IOS_CLIENT_ID is not configured. Create " +
                "ios/Spikes/OAuthSpike/Secrets.xcconfig from " +
                "Secrets.example.xcconfig and set GOOGLE_IOS_CLIENT_ID to " +
                "the iOS OAuth client id from Google Cloud Console."
            ))
        }
        return .success(raw)
    }

    /// Google's documented convention for native iOS redirect URIs: the
    /// client id's numeric/alphanumeric prefix (everything before
    /// ".apps.googleusercontent.com"), reversed into a DNS-style scheme.
    /// e.g. "1234-abc.apps.googleusercontent.com" becomes
    /// "com.googleusercontent.apps.1234-abc". This is also injected into
    /// Info.plist's CFBundleURLTypes by a build-time Run Script phase (see
    /// project.pbxproj) so the two never drift apart.
    static func urlScheme(for clientID: String) -> String {
        let suffix = ".apps.googleusercontent.com"
        let prefix = clientID.hasSuffix(suffix)
            ? String(clientID.dropLast(suffix.count))
            : clientID
        return "com.googleusercontent.apps.\(prefix)"
    }
}

struct GoogleUserInfo: Decodable {
    let email: String
    let emailVerified: Bool?

    enum CodingKeys: String, CodingKey {
        case email
        case emailVerified = "email_verified"
    }
}

@Observable
final class GoogleAuthService: NSObject {

    private(set) var accessToken: String?
    private(set) var accessTokenExpiry: Date?

    private let keychainService = "com.joeludu.mercury.oauthspike"
    private let keychainAccount = "google_refresh_token"

    private var webAuthSession: ASWebAuthenticationSession?

    // MARK: - Public state

    var hasStoredRefreshToken: Bool {
        (try? KeychainStore.read(service: keychainService, account: keychainAccount)) != nil
    }

    // MARK: - Sign in

    @MainActor
    func signIn() async throws -> GoogleUserInfo {
        let clientID = try GoogleAuthConfig.clientID.get()
        let scheme = GoogleAuthConfig.urlScheme(for: clientID)
        let redirectURI = "\(scheme):/oauth2redirect"

        let verifier = Self.makeCodeVerifier()
        let challenge = Self.codeChallenge(for: verifier)
        let state = Self.makeState()

        let authURL = try Self.buildAuthorizationURL(
            clientID: clientID,
            redirectURI: redirectURI,
            codeChallenge: challenge,
            state: state
        )

        let callbackURL = try await presentWebAuthSession(
            url: authURL,
            callbackScheme: scheme
        )

        guard
            let components = URLComponents(url: callbackURL, resolvingAgainstBaseURL: false),
            let queryItems = components.queryItems
        else {
            throw GoogleAuthError.missingCallbackURL
        }

        let returnedState = queryItems.first(where: { $0.name == "state" })?.value
        guard returnedState == state else {
            throw GoogleAuthError.stateMismatch
        }

        guard let code = queryItems.first(where: { $0.name == "code" })?.value else {
            throw GoogleAuthError.missingAuthorizationCode
        }

        try await exchangeCodeForTokens(
            code: code,
            codeVerifier: verifier,
            redirectURI: redirectURI,
            clientID: clientID
        )

        return try await fetchUserInfo()
    }

    func signOut() throws {
        try KeychainStore.delete(service: keychainService, account: keychainAccount)
        accessToken = nil
        accessTokenExpiry = nil
    }

    /// Returns a currently-valid access token, refreshing from the stored
    /// refresh token if the cached one is missing or within 60 seconds of
    /// expiry. Access tokens are kept in memory only — never persisted —
    /// since they're short-lived (typically 1 hour) and cheap to reissue
    /// from the refresh token, which is the thing worth protecting.
    func validAccessToken() async throws -> String {
        if let accessToken, let expiry = accessTokenExpiry, expiry.timeIntervalSinceNow > 60 {
            return accessToken
        }

        guard let refreshToken = try KeychainStore.read(
            service: keychainService, account: keychainAccount
        ) else {
            throw GoogleAuthError.missingRefreshToken
        }

        let clientID = try GoogleAuthConfig.clientID.get()
        try await refresh(refreshToken: refreshToken, clientID: clientID)

        guard let accessToken else { throw GoogleAuthError.notSignedIn }
        return accessToken
    }

    // MARK: - Authorization URL

    private static func buildAuthorizationURL(
        clientID: String,
        redirectURI: String,
        codeChallenge: String,
        state: String
    ) throws -> URL {
        var components = URLComponents(string: "https://accounts.google.com/o/oauth2/v2/auth")!
        components.queryItems = [
            URLQueryItem(name: "client_id", value: clientID),
            URLQueryItem(name: "redirect_uri", value: redirectURI),
            URLQueryItem(name: "response_type", value: "code"),
            URLQueryItem(name: "scope", value: GoogleOAuthScopes.value),
            URLQueryItem(name: "code_challenge", value: codeChallenge),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
            URLQueryItem(name: "state", value: state),
            // offline + consent together are what make Google actually
            // hand back a refresh_token. `access_type=offline` alone is not
            // sufficient once the user has an existing grant for this
            // client — Google silently omits the refresh token on repeat
            // authorizations unless `prompt=consent` forces the consent
            // screen (and therefore a fresh grant) every time.
            URLQueryItem(name: "access_type", value: "offline"),
            URLQueryItem(name: "prompt", value: "consent"),
        ]
        guard let url = components.url else {
            throw GoogleAuthError.missingCallbackURL
        }
        return url
    }

    // MARK: - ASWebAuthenticationSession

    @MainActor
    private func presentWebAuthSession(
        url: URL,
        callbackScheme: String
    ) async throws -> URL {
        try await withCheckedThrowingContinuation { continuation in
            let session = ASWebAuthenticationSession(
                url: url,
                callbackURLScheme: callbackScheme
            ) { callbackURL, error in
                if let error = error as? ASWebAuthenticationSessionError,
                   error.code == .canceledLogin {
                    continuation.resume(throwing: GoogleAuthError.userCancelled)
                    return
                }
                if let error {
                    continuation.resume(throwing: error)
                    return
                }
                guard let callbackURL else {
                    continuation.resume(throwing: GoogleAuthError.missingCallbackURL)
                    return
                }
                continuation.resume(returning: callbackURL)
            }

            // Reuse the user's existing Google session/cookies in the
            // shared browser context where possible, rather than forcing a
            // fresh login every single spike run.
            session.prefersEphemeralWebBrowserSession = false
            session.presentationContextProvider = self
            self.webAuthSession = session

            if !session.start() {
                continuation.resume(throwing: GoogleAuthError.userCancelled)
            }
        }
    }

    // MARK: - Token exchange

    private struct TokenResponse: Decodable {
        let accessToken: String
        let expiresIn: Int
        let refreshToken: String?
        let idToken: String?
        let tokenType: String
        let scope: String

        enum CodingKeys: String, CodingKey {
            case accessToken = "access_token"
            case expiresIn = "expires_in"
            case refreshToken = "refresh_token"
            case idToken = "id_token"
            case tokenType = "token_type"
            case scope
        }
    }

    /// Native clients are "public clients" per RFC 8252 — they cannot keep
    /// a client_secret confidential since it ships inside the app binary
    /// and can be extracted. Google's iOS OAuth client type reflects this:
    /// it is issued WITHOUT a secret. PKCE (RFC 7636) replaces the secret
    /// as the mechanism that binds the authorization code to this specific
    /// app instance — only the process holding `codeVerifier` (which never
    /// left the device) can redeem the code, so a stolen code is useless.
    private func exchangeCodeForTokens(
        code: String,
        codeVerifier: String,
        redirectURI: String,
        clientID: String
    ) async throws {
        var request = URLRequest(url: URL(string: "https://oauth2.googleapis.com/token")!)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")

        let params = [
            "code": code,
            "client_id": clientID,
            "code_verifier": codeVerifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirectURI,
        ]
        request.httpBody = Self.formEncode(params)

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw GoogleAuthError.tokenExchangeFailed(status: -1, body: "no response")
        }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? "<undecodable>"
            throw GoogleAuthError.tokenExchangeFailed(status: http.statusCode, body: body)
        }

        let decoder = JSONDecoder()
        let token = try decoder.decode(TokenResponse.self, from: data)

        self.accessToken = token.accessToken
        self.accessTokenExpiry = Date().addingTimeInterval(TimeInterval(token.expiresIn))

        guard let refreshToken = token.refreshToken else {
            throw GoogleAuthError.missingRefreshToken
        }
        try KeychainStore.save(
            refreshToken, service: keychainService, account: keychainAccount
        )
    }

    private func refresh(refreshToken: String, clientID: String) async throws {
        var request = URLRequest(url: URL(string: "https://oauth2.googleapis.com/token")!)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")

        let params = [
            "client_id": clientID,
            "refresh_token": refreshToken,
            "grant_type": "refresh_token",
        ]
        request.httpBody = Self.formEncode(params)

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw GoogleAuthError.tokenExchangeFailed(status: -1, body: "no response")
        }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? "<undecodable>"
            throw GoogleAuthError.tokenExchangeFailed(status: http.statusCode, body: body)
        }

        let decoder = JSONDecoder()
        let token = try decoder.decode(TokenResponse.self, from: data)
        self.accessToken = token.accessToken
        self.accessTokenExpiry = Date().addingTimeInterval(TimeInterval(token.expiresIn))
        // Google does not always re-issue a refresh_token on refresh; keep
        // the existing one in Keychain unless a new one is supplied.
        if let newRefreshToken = token.refreshToken {
            try KeychainStore.save(
                newRefreshToken, service: keychainService, account: keychainAccount
            )
        }
    }

    // MARK: - Userinfo

    private func fetchUserInfo() async throws -> GoogleUserInfo {
        guard let accessToken else { throw GoogleAuthError.notSignedIn }

        var request = URLRequest(
            url: URL(string: "https://openidconnect.googleapis.com/v1/userinfo")!
        )
        request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw GoogleAuthError.userInfoFetchFailed(status: -1)
        }
        guard (200..<300).contains(http.statusCode) else {
            throw GoogleAuthError.userInfoFetchFailed(status: http.statusCode)
        }
        return try JSONDecoder().decode(GoogleUserInfo.self, from: data)
    }

    // MARK: - PKCE / state helpers

    private static func makeCodeVerifier() -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        _ = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        return base64URLEncode(Data(bytes))
    }

    private static func codeChallenge(for verifier: String) -> String {
        let hashed = SHA256.hash(data: Data(verifier.utf8))
        return base64URLEncode(Data(hashed))
    }

    private static func makeState() -> String {
        var bytes = [UInt8](repeating: 0, count: 16)
        _ = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        return base64URLEncode(Data(bytes))
    }

    private static func base64URLEncode(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    private static func formEncode(_ params: [String: String]) -> Data {
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-._~"))
        let pairs = params.map { key, value -> String in
            let encodedKey = key.addingPercentEncoding(withAllowedCharacters: allowed) ?? key
            let encodedValue = value.addingPercentEncoding(withAllowedCharacters: allowed) ?? value
            return "\(encodedKey)=\(encodedValue)"
        }
        return pairs.joined(separator: "&").data(using: .utf8)!
    }
}

// MARK: - ASWebAuthenticationPresentationContextProviding

extension GoogleAuthService: ASWebAuthenticationPresentationContextProviding {
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        // Spike scope: single window scene, single key window.
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
            .first { $0.isKeyWindow } ?? ASPresentationAnchor()
    }
}
