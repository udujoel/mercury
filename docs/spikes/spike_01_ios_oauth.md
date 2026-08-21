# Spike 1 — iOS OAuth via ASWebAuthenticationSession

Goal (PRD): a single-screen iOS app that signs in with Google via
`ASWebAuthenticationSession`, prints the signed-in email, and stores the
refresh token in Keychain.

## What was proven

The project builds cleanly with a hand-written `project.pbxproj` — no
xcodegen or tuist involved. Verbatim result of the exact acceptance command:

```
$ cd ios/Spikes/OAuthSpike && xcodebuild -project OAuthSpike.xcodeproj \
    -scheme OAuthSpike -destination 'generic/platform=iOS Simulator' -quiet build
2026-08-21 17:56:04.746 xcodebuild[41543:5068745] [MT] IDERunDestination: Supported platforms for the buildables in the current scheme is empty.
warning: GOOGLE_IOS_CLIENT_ID is not configured (see Secrets.example.xcconfig); leaving placeholder URL scheme in Info.plist
warning: Run script build phase 'Inject Google URL Scheme' will be run during every build because it does not specify any outputs. To address this issue, either add output dependencies to the script phase, or configure it to run in every build by unchecking "Based on dependency analysis" in the script phase. (in target 'OAuthSpike' from project 'OAuthSpike')
$ echo $?
0
```

`BUILD SUCCEEDED` is confirmed by a verbose (non-`-quiet`) run of the same
command, and the app bundle is produced at
`Build/Products/Debug-iphonesimulator/OAuthSpike.app` in DerivedData. The
`[MT] IDERunDestination` and script-phase-output warnings are cosmetic
(no shared `.xcscheme` file is committed — Xcode autocreates one — and the
Run Script phase has no declared outputs) and do not affect the exit code.

Statically verified, by reading the code and the successful build:

- **Keychain, not UserDefaults.** `KeychainStore.swift` uses
  `kSecClassGenericPassword` exclusively, with save/read/delete for a String
  under a service+account key. Never touches `UserDefaults`.
- **`kSecAttrAccessibleAfterFirstUnlock`.** Chosen (and commented) because
  Mercury's BGProcessingTask (Spike 7) needs to read the refresh token while
  the device is locked but has been unlocked at least once since boot.
  `WhenUnlocked` would break that background read; `Always`-family is
  deprecated and broader than needed.
- **PKCE, no client secret.** `GoogleAuthService` generates a 32-byte random
  `code_verifier`, derives `code_challenge` via SHA-256 (CryptoKit) +
  base64url, and sends `code_challenge_method=S256`. The token exchange
  POST to `https://oauth2.googleapis.com/token` sends `code_verifier` and
  `client_id` only — no `client_secret` field exists anywhere in the code,
  which is correct because a native app is a public client per RFC 8252 and
  cannot keep a secret confidential.
- **Scopes are exactly** `openid email
  https://www.googleapis.com/auth/gmail.readonly
  https://www.googleapis.com/auth/calendar.readonly` — defined once in
  `GoogleOAuthScopes.value`, matching CLAUDE.md rule 4. No write/send/modify
  scope appears anywhere in the spike.
- **`access_type=offline` + `prompt=consent`** are both set on the
  authorization URL. Commented in code and below.
- **State verified on return.** A random state is generated, attached to the
  auth URL, and compared against the callback's `state` query item before
  the authorization code is even read; mismatch throws
  `GoogleAuthError.stateMismatch` before touching the code.
- **Typed errors.** `GoogleAuthError` is an `Error & LocalizedError` enum
  covering user-cancelled, state-mismatch, missing-refresh-token, and
  token-exchange HTTP failure (with status + body), plus a few more.
- **`validAccessToken()`** refreshes when the cached token is missing or
  within 60 seconds of expiry, using the stored refresh token; access tokens
  are never persisted, only kept in memory.
- **Config surfacing.** `GoogleAuthConfig.clientID` returns a `Result`; if
  `GOOGLE_IOS_CLIENT_ID` is unset, empty, still the raw `$(...)` build
  variable, or the placeholder text, `ContentView` shows the exact file and
  key to fill in (`ios/Spikes/OAuthSpike/Secrets.xcconfig`,
  `GOOGLE_IOS_CLIENT_ID`) instead of crashing. Confirmed live in this
  build — `Secrets.xcconfig` currently holds the placeholder value and the
  app compiles and would show that message on launch.
- **No hardcoded client id** anywhere in committed source. `Secrets.xcconfig`
  (gitignored, contains only the placeholder) supplies it via Info.plist's
  `$(GOOGLE_IOS_CLIENT_ID)` substitution.

## What could not be proven

There is no Google OAuth client on this machine (per the shared brief), so
none of the following were exercised end to end:

- Actually completing a sign-in: presenting the Google consent screen,
  approving, and receiving a real callback URL.
- The `ASWebAuthenticationSession` presentation flow at runtime (simulator
  boot, tap-through) — only compiled, not launched/tapped.
- A real token exchange response from `oauth2.googleapis.com/token`, and
  whether Google's JSON field names match `TokenResponse`'s `CodingKeys`
  exactly (documented and stable, but unverified against a live response).
- Whether `prompt=consent` reliably returns a `refresh_token` on first
  consent for a Testing-mode OAuth consent screen (documented Google
  behavior, not observed).
- The Run Script phase's derived URL scheme actually round-tripping through
  a real `ASWebAuthenticationSession` callback on-device or in-simulator.
- `kSecAttrAccessibleAfterFirstUnlock` actually surviving a device lock in a
  background task (that's Spike 7's job).

## Google Cloud Console click-path (for when credentials exist)

1. https://console.cloud.google.com → select/create the Mercury project.
2. **APIs & Services → OAuth consent screen** → User type **External**,
   publishing status **Testing**, add the developer's own Google account as
   a test user (per ROADMAP.md Phase 0 checklist item).
3. **APIs & Services → Enabled APIs** → enable **Gmail API** and **Google
   Calendar API** (both already listed as Phase 0 prerequisites).
4. **APIs & Services → Credentials → + Create Credentials → OAuth client
   ID**.
   - Application type: **iOS**.
   - Name: anything (e.g. "Mercury OAuth Spike").
   - **Bundle ID: `com.joeludu.mercury.oauthspike`** — must match exactly,
     it's how Google's server validates the redirect at token-exchange time.
5. Copy the generated client id (form
   `NNNNNNNNNNNN-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.apps.googleusercontent.com`).
6. `cp Secrets.example.xcconfig Secrets.xcconfig` inside
   `ios/Spikes/OAuthSpike/`, set `GOOGLE_IOS_CLIENT_ID` to that value.
   `Secrets.xcconfig` is gitignored — never commit it.

No client secret is generated or needed for the iOS client type.

## Callback-scheme mechanics

Google's documented convention for native iOS redirect URIs is the
reverse-DNS form of the client id's prefix: given
`1234-abc.apps.googleusercontent.com`, the callback scheme is
`com.googleusercontent.apps.1234-abc`, and the app registers a custom URL
scheme with the OS for it. Two places need this exact string and they must
never drift apart:

1. **`GoogleAuthConfig.urlScheme(for:)`** derives it at runtime from
   whatever `GOOGLE_IOS_CLIENT_ID` was read from Info.plist, and passes it
   as `callbackURLScheme` to `ASWebAuthenticationSession`, and builds
   `redirect_uri` as `"<scheme>:/oauth2redirect"`.
2. **The "Inject Google URL Scheme" Run Script build phase** (in
   `project.pbxproj`) computes the same string from the
   `GOOGLE_IOS_CLIENT_ID` build setting (same xcconfig value) via
   `PlistBuddy`, and writes it into the built `Info.plist`'s
   `CFBundleURLTypes` so the OS knows to hand the callback back to this app.

Because both derive from the same source value with the same string
operation, they cannot silently disagree the way two hand-typed copies
could. Info.plist itself carries a placeholder scheme
(`com.googleusercontent.apps.unconfigured`) pre-configuration so the plist
is always structurally valid.

## Why PKCE + no client secret is correct here

Per RFC 8252 (OAuth 2.0 for Native Apps), a mobile app is a "public client":
its binary is fully readable by the end user, so any embedded secret is not
actually secret. Google's iOS OAuth client type reflects this — it is
issued with no client secret at all. PKCE (RFC 7636) replaces the secret as
the anti-interception mechanism: the app generates a random `code_verifier`
kept only in process memory, derives `code_challenge = SHA256(verifier)`
sent with the initial authorization request, and later proves possession of
the verifier at token-exchange time. An attacker who intercepts the
authorization code (e.g. via a malicious app also registering the same
custom URL scheme) cannot redeem it without the verifier, which never left
this process.

## `access_type=offline` + `prompt=consent`

`access_type=offline` is necessary but not sufficient for getting a
`refresh_token` back — Google only issues one on a request that also
produces a fresh consent grant. If the user already granted this exact
client access before, a plain `offline` request without `prompt=consent`
often returns an access token only, silently omitting the refresh token
because Google treats it as "nothing new to consent to." Forcing
`prompt=consent` guarantees the consent screen (and thus a fresh grant, and
thus a refresh token) every time — which matters because Mercury's whole
overnight-generation model depends on having a durable refresh token, not a
one-hour access token.

## Gotchas hit

- **Info.plist string substitution is read-only text replacement** — it
  cannot derive `com.googleusercontent.apps.<prefix>` from
  `GOOGLE_IOS_CLIENT_ID` on its own (no string-splitting in xcconfig/plist
  substitution). Solved with the Run Script build phase described above
  rather than asking whoever fills in `Secrets.xcconfig` to also hand-derive
  and duplicate the scheme.
- **No shared `.xcscheme` was committed.** `xcodebuild -scheme OAuthSpike`
  still resolved and built via Xcode's scheme autocreation, but it emits an
  `IDERunDestination: Supported platforms ... is empty` warning. Did not
  affect the exit code in repeated runs, but a shared scheme would silence
  it if this stops being a throwaway spike.
- **Simulator builds need signing disabled explicitly** —
  `CODE_SIGNING_ALLOWED = NO` and `CODE_SIGNING_REQUIRED = NO` on the target
  build settings, since there is no development team configured on this
  machine.
- **`GENERATE_INFOPLIST_FILE = NO`** is required alongside a hand-written
  `INFOPLIST_FILE` path — modern Xcode templates default to a generated
  Info.plist and will silently ignore a committed one (or conflict) if this
  isn't set explicitly.

## Files created

- `ios/Spikes/OAuthSpike/OAuthSpike.xcodeproj/project.pbxproj`
- `ios/Spikes/OAuthSpike/OAuthSpike/OAuthSpikeApp.swift`
- `ios/Spikes/OAuthSpike/OAuthSpike/ContentView.swift`
- `ios/Spikes/OAuthSpike/OAuthSpike/GoogleAuthService.swift`
- `ios/Spikes/OAuthSpike/OAuthSpike/KeychainStore.swift`
- `ios/Spikes/OAuthSpike/OAuthSpike/Info.plist`
- `ios/Spikes/OAuthSpike/Secrets.example.xcconfig` (committed template)
- `ios/Spikes/OAuthSpike/Secrets.xcconfig` (gitignored, placeholder value)
- `docs/spikes/spike_01_ios_oauth.md` (this report)
- Appended one ignore rule to `.gitignore`
