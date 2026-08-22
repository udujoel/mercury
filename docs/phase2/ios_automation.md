# Phase 2 week 11 — iOS background prefetch, push handling, show notes

## Where this fits

Per ADR-001 and PRD 3.6, delivery is layered three ways. This week built the
iOS side of layers 2 and 3, and the client half of layer 1:

1. **PRIMARY — server cron + silent push.** Backend, built in parallel (see
   `docs/phase2/automation.md`). This client registers its APNs device token
   (`PushService.didRegister`) and handles the resulting silent push
   (`PushService.handleSilentPush` / `AppDelegate.application(_:didReceiveRemoteNotification:fetchCompletionHandler:)`).
   Confirmed against `backend/app/scheduler/cron.py::_send_push`: the push
   payload is `{"briefing_id": "<date>"}`, which is exactly the key
   `PushService.briefingIdPayloadKey` reads.
2. **SECONDARY — `BGProcessingTask` prefetch.** `BackgroundScheduler.swift`.
   Registered before launch completes, reschedules itself first inside its
   own handler, and only downloads — it never generates.
3. **FALLBACK — foreground fetch on launch.** Unchanged: `AppState.playBriefing()`
   already covers this; it is what actually guarantees delivery per
   ADR-001, since layers 1 and 2 are both opportunistic by design.

Also built: PRD F-19 show notes (`ShowNotesView.swift`) and F-17's
notification "Play" action.

## Files created

- `ios/Mercury/Core/Background/BackgroundScheduler.swift` — `BGProcessingTask`
  registration, pure `makeRequest(wakeTime:)` construction, `schedule`,
  and `handlePrefetch` (reschedule-first + expiration-safe completion).
- `ios/Mercury/Core/Notifications/PushService.swift` — APNs registration,
  device-token hand-off to `POST /devices/register`, silent-push handling,
  wake-notification "Play" action category, `UNUserNotificationCenterDelegate`.
- `ios/Mercury/Features/ShowNotes/ShowNotesView.swift` — source list grouped
  by `sourceType`, deep link per source, tap-to-seek via `turn_index`; logic
  split into `ShowNotesLogic` (grouping + `seekTargetMs`) so it's testable
  without SwiftUI.
- `ios/Mercury/MercuryTests/BackgroundSchedulerTests.swift` (6 tests)
- `ios/Mercury/MercuryTests/ShowNotesTests.swift` (9 tests)

## Files changed

- `ios/Mercury/App/MercuryApp.swift` — added `AppDelegate` (UIKit bridge for
  APNs callbacks + `BGTaskScheduler` registration, both of which SwiftUI's
  `App` protocol has no hook for), wired `PushService`/`BackgroundScheduler`
  closures to `AppState`, schedules a prefetch window and requests
  notification authorization on every cold launch once onboarding is done.
- `ios/Mercury/App/AppState.swift` — added `wakeTimeMinutesFromMidnight` /
  `nextWakeDate` (persisted wake-time setting, previously collected in
  onboarding but never stored), `prefetchBriefing(id:)` (download-only, no
  generate — the PRIMARY/SECONDARY entry point), `playPrefetched(id:)` (the
  F-17 "Play" action entry point, reuses a cached manifest when the id
  matches, else fetches).
- `ios/Mercury/Core/Models/BriefingManifest.swift` — added
  `turn(atSourceIndex:)`, resolving a `SourceRef.turnIndex` against
  `renderedTurns` (the compacted list) and returning `nil` out of range
  instead of trapping.
- `ios/Mercury/Core/Networking/Endpoints.swift` / `APIClient.swift` — added
  `registerDevice(token:)`, authorized identically to every other request
  (`X-Mercury-Token` via `APIClient.authorize`), body shape matched to
  `backend/app/api/devices.py::RegisterRequest` (`{"device_token": "..."}`).
- `ios/Mercury/Features/Onboarding/ScheduleSetupView.swift` — the wake-time
  and length pickers now actually persist to `AppState` on "Done" (previously
  `length` was collected and silently dropped), and kick off notification
  authorization + the first prefetch schedule right there.
- `ios/Mercury/Features/Home/HomeView.swift` — navigates to `PlayerView`
  when `playback.isPlaying` flips true from *any* source, not just its own
  button — covers playback started by the notification "Play" action, which
  calls `AppState.playPrefetched` directly.
- `ios/Mercury/Features/Player/PlayerView.swift` — the placeholder bookmark
  icon is now a `NavigationLink` to `ShowNotesView`.
- `ios/Mercury/Info.plist` — added `remote-notification` to
  `UIBackgroundModes` (required for a silent push to actually reach
  `didReceiveRemoteNotification` when the app isn't foregrounded — without
  it the push is simply not delivered to app code). The
  `BGTaskSchedulerPermittedIdentifiers` entry already existed from Phase 1
  and needed no change.
- `ios/Mercury/Mercury.xcodeproj/project.pbxproj` — new groups
  (`Core/Background`, `Core/Notifications`, `Features/ShowNotes`), file
  refs, and Sources/build-phase entries for everything above.

## Design decisions worth flagging

- **How the background task knows which briefing to prefetch.** There is no
  "latest briefing" endpoint. `briefing_id` is deterministically the
  generation date as `"YYYY-MM-DD"` in local time (PRD 3.7's SwiftData model
  comment, `sample_manifest.json`, and confirmed against the backend's own
  `_today_briefing_id(tz_name)` in `cron.py`, which also uses local time,
  never UTC). `AppDelegate.briefingId(for:)` computes the same string
  client-side with a `.current` timezone `DateFormatter`, so the background
  task can ask for the right id with zero server round trip. This assumes
  client and server agree on "today" — both use the device's / configured
  local timezone, not UTC, so this should hold, but a client whose clock or
  timezone has drifted from the server's `user_timezone` setting is a real
  edge case neither side currently detects.
- **`prefetchBriefing` never surfaces errors to `loadState`.** Both call
  sites (silent push, background task) can run with no UI on screen; there
  is nothing to show an error to, and no reason to disturb `loadState` if a
  fully unrelated foreground fetch is also in flight. It's `print`-logged
  and swallowed. The FALLBACK path is what the user actually sees if this
  fails.
- **`playPrefetched(id:)` falls back to `playBriefing()`** (full
  generate-or-fetch) when the notification payload has no `briefing_id` at
  all, rather than doing nothing. A malformed/stripped payload shouldn't
  leave a "Play" tap producing no UI response.
- **Double-completion guard in `BackgroundScheduler.handlePrefetch`.** The
  work task and the expiration handler race to call
  `task.setTaskCompleted`; iOS treats calling it twice as a programmer
  error, so an `NSLock`-guarded one-shot wraps both paths.

## What is test-verified vs merely compiling

**Test-verified (35/35 passing, up from 20):**
- `BackgroundSchedulerTests` (6): `makeRequest(wakeTime:)` uses the stable
  identifier `com.joeludu.mercury.prefetch` (matching both the Info.plist
  entry and the `prefetchIdentifier` constant), sets `earliestBeginDate` to
  exactly 30 minutes before the given wake time, sets
  `requiresNetworkConnectivity = true` and `requiresExternalPower = false`,
  and that different wake times produce different windows.
- `ShowNotesTests` (9): grouping by `sourceType` preserves first-seen order
  (not alphabetical, not `Dictionary`'s undefined order — asserted
  explicitly against a synthetic 3-source, 2-type list plus the real
  fixture), group labels are capitalized, empty input produces no groups,
  and `seekTargetMs` correctly resolves every source in the real fixture
  against its turn's `startMs`, plus three defensive cases: index 999
  (out of range), index -1 (negative), and an entirely empty manifest — all
  return `nil` rather than crashing.
- Both `xcodebuild build` and `xcodebuild test` (below) exit 0.

**Compiles but NOT test-verified (needs a physical device, or is
integration-level UI not covered by unit tests):**
- Whether `BGTaskScheduler.shared.submit` actually gets granted a run by
  iOS. `BGTaskScheduler` does not execute in the simulator at all — Apple
  documents this; `BackgroundSchedulerTests` intentionally tests only
  `makeRequest(wakeTime:)`, the pure construction logic, not scheduling or
  execution.
- Whether the registered handler in `BackgroundScheduler.register` actually
  fires when iOS grants a window, and whether `handlePrefetch`'s
  reschedule-before-work behavior survives a real expiration. Needs the
  LLDB trigger below on a physical device.
- Whether a real silent push reaches `didReceiveRemoteNotification` end to
  end (APNs sandbox, `remote-notification` background mode, actual device
  token round trip through `POST /devices/register`). The client-side
  parsing (`PushService.handleSilentPush`, `extractBriefingId`) is simple
  enough that I did not add a device-token/push-payload unit test harness
  for it — the brief asked for tests specifically on show notes and
  background-task construction, and a push-delivery test would mostly be
  testing `UIApplication`/APNs plumbing rather than Mercury logic. Flagging
  this as a real gap if push handling ever misbehaves in the field.
- The wake notification's "Play" action and default-tap routing
  (`PushService.userNotificationCenter(_:didReceive:)` →
  `AppState.playPrefetched` → `HomeView`'s `onChange(of: playback.isPlaying)`
  → navigation to `PlayerView`). Compiles and reads correctly by inspection;
  not covered by an automated UI test.
- `ShowNotesView`'s actual rendering (List, sections, `Link` deep-link
  buttons, tap gesture wiring to `onSeek`). `ShowNotesLogic` (the grouping
  and seek-mapping math it's built on) is fully unit tested; the SwiftUI
  layer itself is not — this project has no UI test target.

## Trigger the background task on a physical device

Per PRD 3.6, attach LLDB to a debug build running on device, pause it, and
run:

```
e -l objc -- (void)[[BGTaskScheduler sharedScheduler] _simulateLaunchForTaskWithIdentifier:@"com.joeludu.mercury.prefetch"]
```

The app must already be suspended in the background (not foregrounded, not
force-quit) for the simulated launch to invoke the registered handler.
Watch for `BackgroundScheduler`'s handler firing (add a breakpoint or log
line in `AppDelegate`'s `BackgroundScheduler.register` closure) and confirm
`task.setTaskCompleted` is eventually called — Xcode's debug navigator
reports whether the task completed, expired, or is still running.

## What needs a paid Apple Developer account

- **Push notifications at all.** APNs (sandbox or production) requires a
  paid account's provisioning profile with the Push Notifications
  capability enabled, plus an `.p8` auth key from the Apple Developer portal
  (see `backend/.env.example`'s `APNS_KEY_PATH`/`APNS_KEY_ID`/`APNS_TEAM_ID`).
  A free personal-team build cannot receive real APNs pushes — silent or
  visible — at all, so the entire PRIMARY layer is unverifiable without one.
- **Background Modes entitlement in a real provisioning profile.** The
  simulator and free-team builds can still *build* with `UIBackgroundModes`
  set, but actual background execution grants (both the silent push waking
  the app and `BGProcessingTask` windows) are governed by real device
  behavior under a proper profile — this is separate from, but compounds
  with, the APNs requirement above.
- **A physical device**, full stop, for anything in the "needs a physical
  device" list above — the simulator does not run `BGTaskScheduler` tasks
  and does not receive real APNs pushes.

## Known gaps / things I'd flag for follow-up

- Prefetched audio (`prefetchBriefing`) is cached only via
  `APIClient.downloadAudio`'s temp-directory file, not written through
  `BriefingStore`/SwiftData. The PRD's retention model (7 briefings, 7 days
  of audio) already exists in `BriefingStore` but isn't wired into the
  prefetch or push paths — `AppState.playBriefing()` doesn't call it either,
  so this predates this week's work rather than being introduced by it, but
  it means a prefetched file can be evicted by the OS's temp-directory
  cleanup before the FALLBACK foreground fetch would otherwise re-download
  it. Worth wiring `BriefingStore.save` into both `prefetchBriefing` and
  `playBriefing` in a follow-up pass.
- The client/server "what day is 'today'" agreement (see above) has no
  runtime check on either side; a clock/timezone drift would make the
  background task's precomputed `briefing_id` guess wrong, at which point it
  silently 404s (`prefetchBriefing` catches and logs) and the FALLBACK path
  still recovers on next open — degraded, not broken.
