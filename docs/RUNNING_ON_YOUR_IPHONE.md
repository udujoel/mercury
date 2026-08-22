# Running Mercury on your own iPhone

**No paid Apple Developer membership required.** A free Apple ID gets the app
onto your own device. This document is the whole procedure, plus an honest
account of the one thing you give up.

## What free signing actually costs you

An earlier note in this project claimed a paid account blocked Phase 3's voice
gate. **That was wrong**, and the correction matters because it changes what is
worth building next.

| Capability | Free personal team | Notes |
|---|---|---|
| Install on your own device | ✅ | Profile expires after 7 days, re-run from Xcode |
| Microphone | ✅ | Usage description only |
| `SFSpeechRecognizer` (on-device) | ✅ | **No entitlement needed** — the Join flow works |
| Background audio | ✅ | `UIBackgroundModes: audio` |
| `BGProcessingTask` prefetch | ✅ | `UIBackgroundModes: processing` |
| Local notifications | ✅ | Needs no entitlement |
| **APNs / remote push** | ❌ | Needs the Push Notifications capability = paid |
| TestFlight distribution | ❌ | Paid only — relevant at Phase 5, not now |

So exactly one thing is genuinely blocked: **server-initiated silent push**.

## Why that is survivable

ADR-001 specifies three delivery layers precisely so no single one is critical:

```
PRIMARY    server cron -> APNs silent push          ← unavailable when free
SECONDARY  BGProcessingTask prefetch                ← works
FALLBACK   foreground fetch on app launch           ← always works
```

On top of that, `PushService.scheduleLocalWakeNotification` now arms a repeating
**local** notification at your wake time whenever APNs registration fails. It
carries the same "Play" action as the push version (PRD F-17).

What you lose versus a paid account: the wake notification fires on the device's
own clock rather than when the server actually finishes generating. If a run is
late, the notification still appears and the app fetches on open. In practice
the briefing is generated hours before the wake time, so this rarely differs.

## Setup, once

1. **Xcode → Settings → Accounts →** add your Apple ID (free is fine).
2. Open `ios/Mercury/Mercury.xcodeproj`. Select the **Mercury** target →
   **Signing & Capabilities**:
   - tick **Automatically manage signing**
   - **Team**: your personal team
   - **Bundle Identifier**: must be globally unique. If `com.joeludu.mercury`
     is taken, append something — e.g. `com.joeludu.mercury.ju`.
   - If a **Push Notifications** capability is listed, **remove it**. Free
     provisioning fails outright if it is present. The `remote-notification`
     background mode in `Info.plist` is harmless to leave; registration simply
     fails at runtime and the local-notification fallback takes over.
3. **On the iPhone**: Settings → Privacy & Security → **Developer Mode** → on,
   then restart the phone. (iOS 16+ requires this.)
4. Plug the phone in, trust the computer, pick it as the run destination, Run.
5. First launch will refuse to open: **Settings → General → VPN & Device
   Management → your Apple ID → Trust**.

**The 7-day expiry is the real friction.** A free provisioning profile dies
after a week and the app stops launching. Re-running from Xcode re-signs it.
Nothing is lost — briefings and resume positions live in SwiftData on device.

## Pointing the phone at your Mac

The phone cannot reach `localhost`, and App Transport Security blocks plain HTTP
to a LAN IP. Use a tunnel — this is already proven working:

```bash
cloudflared tunnel --url http://localhost:8000
```

Then in `ios/Mercury/Secrets.xcconfig` (gitignored):

```
API_SCHEME = https
API_HOST = your-tunnel-name.trycloudflare.com
MERCURY_API_TOKEN = <the value from backend/.env>
```

**Set scheme and host separately.** xcconfig treats `//` as a comment, so
pasting a full URL silently truncates it to `https:` — a valid URL with no host,
which fails at runtime with no useful error. This has bitten this project twice.

Start the backend with the scheduler on:

```bash
cd backend && set -a && . ./.env && set +a && \
  SCHEDULER_ENABLED=true ./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## What to test on the device that the simulator cannot show you

- **The Join flow.** `SFSpeechRecognizer` and real audio ducking do not work in
  the simulator. Tap the mic mid-briefing, ask something, and judge whether the
  gap feels under 2.5 seconds — the Phase 3 gate is a number, but it is also a
  feeling.
- **Background audio**: lock the screen, confirm playback continues and the
  lock-screen controls appear.
- **`BGProcessingTask`**: it does not fire on demand. Force it from Xcode with
  the debugger paused:
  ```
  e -l objc -- (void)[[BGTaskScheduler sharedScheduler] _simulateLaunchForTaskWithIdentifier:@"com.joeludu.mercury.prefetch"]
  ```
- **The ten second test**, on headphones, on a real morning. That is the only
  measurement that has ever mattered here.
