# Mercury iOS client — Phase 1 MVP

Status: builds clean, unit tests pass (20/20). A prior session had already
written most of this app before hitting a context limit; this pass finished
it — fixed a broken hand-written `project.pbxproj`, a broken test target,
two real playback bugs, and a networking/decoding mismatch against the
backend contract — rather than starting over.

## What was already there vs. what this pass did

Already present and kept as-is (reviewed line by line, all correct):
onboarding flow (`WelcomeView` → `ConnectAccountsView` → `ScheduleSetupView`),
the four home cards and `CardDeck` fan/drag interaction, `PlayerView` and its
three zones (`TranscriptView`, `ChapterTabs`, `TransportControls`),
`AmbientBackground`, `Colors`/`Typography`, SwiftData persistence
(`PersistenceModels.swift`, `BriefingStore.swift`), and the full unit test
suite (`BinarySearchTests`, `ManifestDecodingTests`, `SegmentSeekTests`,
`ResumePositionTests`).

Fixed in this pass:

1. **`project.pbxproj` group paths.** Every intermediate `PBXGroup`
   (`App`, `Core`, `Audio`, `Models`, `Networking`, `Persistence`,
   `DesignSystem`, `Features`, `Home`, `Onboarding`, `Player`) had `name` set
   but no `path`, so Xcode resolved every file reference directly against
   `SRCROOT` instead of its real subdirectory — the project didn't build at
   all ("Build input files cannot be found"). Added `path = <dir>;` to each.

2. **`AmbientBackground.swift` used `MeshGradient`**, which is iOS 18+, while
   the project targets iOS 17 (per CLAUDE.md and the PRD). Added an
   `if #available(iOS 18.0, *)` branch with a layered `LinearGradient` /
   `RadialGradient` fallback that reproduces the same three-tone identity on
   iOS 17.

3. **`MercuryTests` had no `TEST_HOST` / `BUNDLE_LOADER`**, so
   `@testable import Mercury` linked but every app symbol was undefined at
   link time. Added both build settings pointing at the `Mercury.app` binary
   in both Debug and Release configs.

4. **`Info.plist` had no `CFBundleIdentifier`/`CFBundleExecutable`/etc.**
   The app target uses a hand-written `Info.plist`
   (`GENERATE_INFOPLIST_FILE = NO`), which was missing every standard
   bundle key. The plain `build` action didn't catch this (it doesn't
   install anything), but `xcodebuild test` failed to install the app on
   the simulator with "Missing bundle ID". Added the standard keys using
   `$(PRODUCT_BUNDLE_IDENTIFIER)`, `$(EXECUTABLE_NAME)`, etc. — the
   `-expandbuildsettings` flag Xcode already uses for this file expands them.

5. **`APIClient.generateBriefing()` decoded the wrong shape.**
   `backend/app/api/briefing.py`'s `POST /briefing/generate` returns
   `{"status": "ok", "manifest": {...}}` (`GenerateResponse`), not a bare
   `BriefingManifest` — unlike the other two endpoints, which do return the
   manifest directly. The client was decoding the wrapper's shape as if it
   were the manifest itself, which would have failed on every real call.
   Added a `GenerateResponseBody` wrapper type and a dedicated
   `requestGenerate()` path; the other two endpoints are unaffected.

6. **`downloadAudio()` derived the file extension from `suggestedFilename`**,
   which FastAPI's plain `Response(content=..., media_type=...)` never sets
   (no `Content-Disposition` header), so it always fell through to the
   `"m4a"` default — silently wrong whenever the backend's no-ffmpeg
   fallback actually served `.wav`, i.e. always, on this machine. Switched
   to reading the response's `Content-Type` (`response.mimeType`) and
   mapping `audio/wav` → `wav`, `audio/mp4` → `m4a`, matching
   `backend/app/synthesis/storage.py::AUDIO_MEDIA_TYPES` exactly.

7. **Two `PlaybackCoordinator` bugs** (ADR-005 territory, not covered by
   the required unit tests since they need real audio hardware):
   - `play()` unconditionally called `player.scheduleFile(...)` every time,
     including on resume from pause. `AVAudioPlayerNode` queues rather than
     replaces, so pause → play would have enqueued a second overlapping
     playback of the whole file instead of resuming. Now it only
     (re)schedules when nothing is currently queued (tracked via
     `hasScheduled`); pause/resume just calls `player.pause()`/`player.play()`.
   - `tick()` computed `positionMs` straight from
     `playerTime(forNodeTime:).sampleTime`, which resets to 0 relative to
     whatever was last scheduled — so after any `seek(toMs:)`, the reported
     position would count up from 0 again instead of from the seek target,
     breaking transcript sync for the rest of playback. Added
     `scheduleBaseMs`, set on every (re)schedule, added back on every tick.
   - `load()` also now tolerates being called a second time (loading a new
     briefing into an existing coordinator): stops any prior playback,
     resets `hasScheduled`/`scheduleBaseMs`/position/current turn, and
     guards `engine.attach`/`engine.start` against being called on an
     already-attached node / already-running engine.

## Module layout

```
ios/Mercury/
  App/                    MercuryApp (SwiftData container), AppState (@Observable root)
  Core/
    Audio/                PlaybackCoordinator (AVAudioEngine), NowPlayingService (MPNowPlayingInfoCenter)
    Models/                BriefingManifest.swift — Turn, BriefingSegment, SourceRef, BriefingManifest (Codable)
    Networking/           APIClient, Endpoints (APIConfig reads API_BASE_URL from Info.plist)
    Persistence/          PersistenceModels (SwiftData @Model types), BriefingStore (actor)
  DesignSystem/           AmbientBackground, Colors, Typography
  Features/
    Onboarding/           WelcomeView, ConnectAccountsView, ScheduleSetupView
    Home/                 HomeView, CardDeck, BriefingCard, WeatherCard, CalendarCard, InboxCard
    Player/                PlayerView, TranscriptView, ChapterTabs, TransportControls
  MercuryTests/           BinarySearchTests, ManifestDecodingTests, SegmentSeekTests, ResumePositionTests, Fixtures/
  Secrets.xcconfig / Secrets.example.xcconfig
  Info.plist
  Mercury.xcodeproj/      hand-written project.pbxproj (no xcodegen/tuist available)
```

## Pointing it at the backend

1. `cp ios/Mercury/Secrets.example.xcconfig ios/Mercury/Secrets.xcconfig`
   (already gitignored; the example defaults to `http://localhost:8000`).
2. Run the backend: `cd backend && .venv/bin/uvicorn app.main:app --reload`
   (or whatever entrypoint `app/main.py` exposes).
3. Build and run the `Mercury` scheme against an iOS 17+ simulator. Tapping
   the greeting card calls `POST /briefing/generate`, downloads the audio,
   and starts playback.
4. `NSAppTransportSecurity` in `Info.plist` only relaxes ATS for
   `localhost`, nothing else — pointing `API_BASE_URL` at a non-localhost
   HTTP (not HTTPS) host will fail by design.

## What is test-verified vs. merely compiling

**Test-verified** (20/20 passing, `xcodebuild test`):
- Manifest JSON decoding against a schema-faithful fixture
  (`MercuryTests/Fixtures/sample_manifest.json`, structurally diffed
  against a real manifest generated by
  `backend/app/pipeline/generate.py::generate_briefing` with the fake
  providers — same top-level keys, same `Turn`/`BriefingSegment`/`SourceRef`
  field names and snake_case wire format), including the ISO8601 date and
  the "don't hardcode `.m4a`" check.
- `BriefingManifest.turn(containing:)` binary search: start boundary,
  midpoint, exclusive end boundary (instant belongs to the *next* turn),
  the silent gap between turns returns `nil`, position after the last turn
  returns `nil`, and the empty-manifest case.
- `segmentStartMs(id:)` / `segment(for:)`: known and unknown segment IDs,
  a turn landing in the gap after a segment's own start still resolving to
  that segment, and chapter-tap seek offsets matching every segment's
  `start_ms` exactly.
- SwiftData resume-position round trip: save → default 0 → update → read
  back the same value, unknown-briefing-ID resolves to `nil`, turns/sources
  persist and are readable back, and re-saving the same `briefing_id`
  upserts (no duplicate row) without clobbering an already-set resume
  position.

**Compiles and was reviewed, not exercised by the test suite** (no
simulator audio hardware / no live backend in this pass):
- The actual `POST /briefing/generate` → download → `PlaybackCoordinator.load`
  → `play()` round trip against a running backend.
- `AVAudioEngine` playback itself: the pause/resume and seek-offset fixes
  above are reasoned through against `AVAudioPlayerNode`/`AVAudioUnitTimePitch`
  semantics but not verified against real audio output.
- Lock-screen / Control Center integration (`NowPlayingService`,
  `MPRemoteCommandCenter`) — needs a device/simulator run with audio
  actually playing.
- The onboarding flow, home card deck drag/tap interaction, and player UI
  — visually reviewed against PRD 3.2–3.4, not screenshot-tested.
- `duck(to:)` / `restoreVolume(over:)` in `PlaybackCoordinator` are stubs
  with a comment (`// Ramp mainMixerNode.outputVolume over seconds.`) —
  correctly out of scope, since voice interrupt is Phase 3, but flagging so
  it isn't mistaken for a forgotten TODO.

## What is stubbed for later phases (by design, per the brief's scope cut)

- `TransportControls`' "Join" mic button is rendered and disabled with an
  accessibility label naming it Phase 3 — no voice interrupt code exists.
- `ConnectAccountsView` has a local `connected` toggle instead of the real
  Google OAuth flow; the brief explicitly deferred wiring
  `ios/Spikes/OAuthSpike/GoogleAuthService.swift` in to a later iteration.
- `WeatherCard`, `CalendarCard`, `InboxCard` on the home screen render
  static placeholder data, not a live preview fetch — only the greeting
  card's tap-to-play path talks to the backend, which is what the phase
  gate ("tap the greeting card and within ninety seconds two hosts are
  discussing your actual meetings and your actual inbox") requires.
- `BGTaskSchedulerPermittedIdentifiers` reserves a prefetch task identifier
  in `Info.plist` but nothing schedules it yet (Phase 2 territory per
  ROADMAP.md).

## Gotchas for whoever picks this up next

- The backend currently serves WAV (no ffmpeg on this machine). Everything
  in the client already derives format from the response rather than
  assuming an extension — but this was a real, silent bug before this pass
  (see fix #6 above) and is exactly the kind of thing that regresses
  quietly if a future edit reintroduces a hardcoded `.m4a` or
  `.wav`. If you touch `APIClient.downloadAudio` or
  `PlaybackCoordinator.load`, re-check that assumption.
- Any new file added under `ios/Mercury/` needs a `path` set on its parent
  `PBXGroup` in `project.pbxproj` (or, more robustly, add the file's own
  relative `path` including the subdirectory) — Xcode will not
  auto-resolve it from `name` alone, and the failure mode is a cryptic
  "Build input files cannot be found" with no line number.
- `MercuryTests` requires `TEST_HOST`/`BUNDLE_LOADER` in both Debug and
  Release configs to keep linking against `@testable import Mercury`. If a
  future pass regenerates the test target's build settings from scratch,
  this is easy to drop silently — the failure is a linker "symbol(s) not
  found" with app-module symbols, not an obviously-missing-setting error.

## Verbatim final command output

```
$ xcodebuild -project Mercury.xcodeproj -scheme Mercury \
    -destination 'generic/platform=iOS Simulator' -quiet build
BUILD_EXIT:0
(no output — clean build, zero warnings)
```

```
$ xcodebuild -project Mercury.xcodeproj -scheme Mercury \
    -destination 'platform=iOS Simulator,name=iPhone 17' test
...
Test Suite 'BinarySearchTests' passed — 7 tests, 0 failures
Test Suite 'ManifestDecodingTests' passed — 4 tests, 0 failures
Test Suite 'ResumePositionTests' passed — 4 tests, 0 failures
Test Suite 'SegmentSeekTests' passed — 5 tests, 0 failures
Test Suite 'MercuryTests.xctest' passed — Executed 20 tests, with 0 failures (0 unexpected) in 0.044 (0.078) seconds
Test Suite 'All tests' passed — Executed 20 tests, with 0 failures (0 unexpected) in 0.044 (0.087) seconds

** TEST SUCCEEDED **
EXIT:0
```
