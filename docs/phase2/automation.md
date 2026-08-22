# Phase 2 week 11 — scheduler and push delivery

## The three-layer delivery strategy, and where this fits

Per ADR-001 ("the server generates, the phone plays"), iOS background
execution windows are too short (~30s) and unreliable to run generation on
device. Delivery of a finished briefing to the phone is therefore layered:

1. **PRIMARY — server cron + silent push.** `app/scheduler/cron.py` runs
   `generate_briefing` once a day, at the user's wake time minus a lead
   (`generation_lead_minutes`), in `settings.user_timezone`. On success it
   sends an APNs silent push (`app/notifications/apns.py`) to every
   registered device as a hint that new content exists.
2. **SECONDARY — `BGProcessingTask` prefetch (iOS side, another agent).**
   Opportunistic; iOS decides whether and when it runs. Should find the
   briefing already generated (this layer never triggers generation itself)
   and prefetch the audio/manifest so playback is instant on open.
3. **FALLBACK — foreground fetch on app launch (iOS side).** The guarantee.
   If the push never arrived and the background task never ran, opening the
   app fetches whatever is newest from `GET /briefing/{id}` directly. This
   is the layer that actually makes the product reliable — the other two are
   latency optimizations on top of it.

This week's work is layer 1 end to end, plus the device-token registration
API that layers 1 and the iOS side both depend on.

## What is proven

- **Fire-time arithmetic and DST correctness.** `_compute_fire_time` does
  pure local-clock subtraction (wake time minus lead); the actual date/time
  resolution — including DST transitions — is delegated to APScheduler's
  `CronTrigger` given `settings.user_timezone` via `ZoneInfo`, never UTC.
  `tests/test_scheduler.py::test_cron_trigger_holds_local_wall_time_across_dst_boundary`
  schedules 07:00 minus 30 minutes in `Europe/Lisbon` across that timezone's
  actual spring-forward date (2026-03-29) and asserts the local fire time
  (06:30) is unchanged on both sides while the UTC offset changes — proving
  the schedule tracks local wall-clock time, not a fixed UTC instant. This
  is the scheduler-side counterpart to the `_parse_briefing_date` fix
  already in `app/pipeline/generate.py`; `_today_briefing_id` here is the
  matching fix for "which day is today" (uses `datetime.now(tz)`, never
  `datetime.now(UTC)`).
- **Idempotence.** `run_daily_briefing_job` checks
  `storage.load_manifest(briefing_id)` before doing anything; if it already
  exists, generation and push are both skipped
  (`test_second_run_same_day_is_a_no_op`). Verified with a real (not merely
  asserted) run in the live demo below — running the job twice back to back
  only generates once.
- **Failure isolation.** A raising `generate_briefing` is caught, logged
  with `log.exception`, and the job returns normally rather than
  propagating — `test_failing_generation_does_not_raise` calls the job body
  directly with a provider that always raises and asserts it does not raise
  out, and that no push fires for a briefing that was never actually made.
- **Coalescing / misfire handling.** The job is registered with
  `coalesce=True`, `misfire_grace_time=3600`, `max_instances=1` — a laptop
  asleep through the fire window collapses any missed fires into a single
  run instead of a burst, and a fire more than an hour late is skipped
  rather than run against a stale "now". (Not independently unit-tested —
  this is APScheduler's own behavior, configured correctly; verifying it
  would mean actually sleeping the process for an hour, which isn't a useful
  test.)
- **Disabled by default.** `settings.scheduler_enabled` defaults to
  `False`; `start_scheduler` returns `None` and starts nothing when it's
  off. The full test suite runs with it off — no test schedules real
  generation.
- **Auth on `/devices/*`.** Both routes are behind the same
  `require_api_token` dependency as `/briefing/*`
  (`test_devices_api.py::test_register_requires_token`,
  `test_delete_requires_token` — 401 without the header). Device tokens are
  truncated in every log line (`_truncate_token`), never written in full.
- **APNs config fails loudly, never fakes a send.** `APNsProvider.__init__`
  raises `APNsConfigurationError` naming every missing env var when
  `apns_key_path`/`apns_key_id`/`apns_team_id`/`apns_topic` aren't set, and
  raises with the exact bad path if the `.p8` file can't be read. There is
  no code path where a misconfigured `APNsProvider` silently "succeeds".
- **End-to-end live run (real process, real log output, not predicted).**
  Ran the actual FastAPI app with `SCHEDULER_ENABLED=true`, `WAKE_TIME` set
  one minute in the future, `Europe/Lisbon`, `GENERATION_LEAD_MINUTES=0`,
  one token registered via `register_token()`, and `generate_briefing` /
  the push transport swapped for fakes (so the demo stays fully offline —
  no live Gemini/TTS/APNs call). Real log output from that run:

  ```
  2026-08-22 09:34:40,140 INFO apscheduler.scheduler: Scheduler started
  2026-08-22 09:34:40,140 INFO app.scheduler.cron: scheduler started: job
    'mercury-daily-briefing' daily at 09:35 Europe/Lisbon (wake_time=09:35
    minus 0 min lead)
  2026-08-22 09:35:00,001 INFO apscheduler.executors.default: Running job
    "run_daily_briefing_job (trigger: cron[hour='9', minute='35'], next run
    at: 2026-08-23 09:35:00 WEST)" (scheduled at 2026-08-22 09:35:00+01:00)
  2026-08-22 09:35:00,012 INFO demo: fake generate_briefing running for
    2026-08-22
  2026-08-22 09:35:00,012 INFO app.scheduler.cron: scheduled briefing
    generation for 2026-08-22 complete
  2026-08-22 09:35:00,016 INFO app.scheduler.cron: push for 2026-08-22:
    accepted
  2026-08-22 09:35:00,016 INFO apscheduler.executors.default: Job
    "run_daily_briefing_job (...)" executed successfully
  2026-08-22 09:35:55,141 INFO app.scheduler.cron: scheduler stopped
  FAKE PUSH CALLS: [('demo-device-token-abcdef123456',
    {'briefing_id': '2026-08-22'})]
  ```

  This shows the real scheduler: job scheduled, waited for its actual fire
  time (not simulated), fired, ran the job body, and delivered a push
  through the real `_send_push` code path with only the transport faked.

## What is unproven — needs a real Apple Developer account and a device

- **A real APNs HTTP/2 call.** `APNsProvider.send_silent` has never been
  exercised against `api.sandbox.push.apple.com` or `api.push.apple.com`.
  The JWT signing (ES256 over the `.p8` key, `kid`/`iss` claims) is
  implemented per Apple's documented token-auth scheme but is untested
  against Apple's actual signature verification — a subtly wrong claim,
  algorithm, or key format could round-trip through PyJWT/cryptography
  cleanly and still be rejected by APNs.
- **A device actually receiving and acting on the push.** The whole premise
  of this module's docstring — "a silent push is a hint, iOS throttles and
  drops it freely" — is exactly why a 200 from APNs does not mean the
  device woke up and refreshed. Only a real device on real iOS with the app
  installed can show that end of the chain, and even then, intermittent
  non-delivery is expected behavior, not a bug to chase.
- **The `.p8` key file path resolution against a real credentials
  directory** — tested here only against `tmp_path` fixtures and a
  deliberately-missing path.
- **HTTP/2 negotiation itself** (the `h2` package) has not been exercised
  against a real HTTP/2 server; `httpx.AsyncClient(http2=True)` was smoke
  tested only by construction, not by a real handshake.

## Exact steps to create the APNs key (do this once, in the Apple Developer
portal)

1. Sign in at https://developer.apple.com/account with the Apple Developer
   account this app is registered under (needs the App Manager or Admin
   role — a plain Developer role cannot create keys).
2. **Certificates, Identifiers & Profiles → Keys → the "+" button.**
3. Give it a name (e.g. "Mercury APNs"), check **Apple Push Notifications
   service (APNs)**, continue, then **Register**.
4. **Download the key immediately** — Apple only lets you download a given
   key's `.p8` file once, ever. If you miss this step you must revoke the
   key and create a new one.
5. Note the **Key ID** shown on the confirmation page (10 characters,
   alphanumeric) — this is `APNS_KEY_ID`.
6. Your **Team ID** is on the membership page (Account → Membership
   details), also 10 characters — this is `APNS_TEAM_ID`.
7. `APNS_TOPIC` is the app's bundle identifier exactly as registered under
   Identifiers (e.g. `com.example.mercury`) — this is the APNs "topic" a
   push is addressed to, and it must match the app's actual bundle ID or
   every push is rejected with `BadTopic`.
8. Move the downloaded `.p8` file into `backend/credentials/` (matching the
   existing convention for `gcp-tts.json`), and point `APNS_KEY_PATH` at it
   in `backend/.env`. Do **not** commit it — it is a credential, same tier
   as the Google TTS service account file.
9. Leave `APNS_USE_SANDBOX=true` for anything short of an App Store build —
   Xcode debug/TestFlight builds register with the sandbox APNs
   environment, not production, and a push sent to the wrong environment is
   silently dropped rather than erroring in a way that would tell you the
   environment was wrong.

## Gotchas

- **`.p8` keys can be downloaded exactly once.** Store it somewhere safe the
  moment you get it; losing it means revoking and regenerating (and
  updating `APNS_KEY_ID` everywhere it's configured).
- **Sandbox vs production is silent.** A push sent to
  `api.push.apple.com` for a device provisioned against the sandbox
  environment (or vice versa) does not error — APNs just never delivers it.
  If a push isn't arriving on a real device, check `APNS_USE_SANDBOX`
  before anything else.
- **`APNS_TOPIC` must exactly equal the bundle ID**, not a display name or
  anything else — a mismatch is a `BadTopic` rejection, not a silent drop,
  so at least that failure mode is loud (check the response body; this
  provider logs it via `log.warning` on any non-200).
- **A silent push is not guaranteed, ever**, regardless of correct
  configuration — see the module docstring on `app/notifications/apns.py`.
  Do not spend debugging time assuming "the code is broken" when a push
  doesn't show up; check the FALLBACK layer (foreground fetch on launch)
  is actually working before suspecting this layer.
- **The device-token JSON store
  (`{AUDIO_OUTPUT_DIR}/device_tokens.json`) is explicitly a placeholder.**
  It has no per-user keying, no expiry, and doesn't consume Apple's APNs
  feedback service for tokens that have gone stale (uninstalled app,
  reset device). Fine for one user and a couple of devices; the real store
  is the SQLAlchemy database already wired up via `settings.database_url`.
- **`generate_briefing`'s own credential-detection logic
  (`app.api.briefing._credentials_configured`) is reused by the scheduler**
  (`_build_live_providers` in `cron.py`) rather than duplicated, so a
  scheduled run and a manual `POST /briefing/generate` agree on what
  "configured" means. If that detection logic changes, both call sites pick
  it up automatically — but it also means a bug there affects the
  scheduler too.
- **New Python dependencies**: `pyjwt`, `cryptography`, and `httpx[http2]`
  (adds `h2`/`hpack`/`hyperframe`) were not previously in
  `backend/requirements.txt` or the venv; both have been updated. Anyone
  syncing an existing venv needs
  `pip install -r backend/requirements.txt` again.

## Files created / modified

Created:
- `backend/app/scheduler/__init__.py`
- `backend/app/scheduler/cron.py`
- `backend/app/notifications/__init__.py`
- `backend/app/notifications/apns.py`
- `backend/app/api/devices.py`
- `backend/tests/test_scheduler.py`
- `backend/tests/test_apns.py`
- `backend/tests/test_devices_api.py`
- `docs/phase2/automation.md` (this file)

Modified (settings/wiring only, no restructuring):
- `backend/app/core/config.py` — added `apns_key_path`, `apns_key_id`,
  `apns_team_id`, `apns_topic`, `apns_use_sandbox`, `scheduler_enabled`,
  `generation_lead_minutes`.
- `backend/app/main.py` — mounted `devices.router`; added a FastAPI
  `lifespan` that calls `start_scheduler`/`shutdown_scheduler`.
- `backend/.env.example` — matching entries for the new settings.
- `backend/requirements.txt` — added `pyjwt`, `cryptography`,
  `httpx[http2]`.

## Verification

```
cd backend && ./.venv/bin/python -m pytest -q
304 passed, 1 warning in 5.48s
```

```
cd backend && ./.venv/bin/ruff check .
All checks passed!
```

(304 = the pre-existing 275 plus 29 new tests across
`test_scheduler.py`, `test_apns.py`, `test_devices_api.py`.)
