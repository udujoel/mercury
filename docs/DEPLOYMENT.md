# Deployment

Mercury runs with **no hosted backend**. See ADR-008 for why.

```
  03:40 UTC  Cloudflare Cron Trigger ─┐
  04:12 UTC  GitHub schedule (backstop)├─► generate (~2 min) ─► Cloudflare R2
  04:41 UTC  GitHub schedule (backstop)┘                             │
                                                                     │
  05:08 UTC  watchdog ─► briefing missing? ─► red run + email        │
                                                                     │
  iPhone ◄──────────────── manifest + audio ───────────────────────┘
```

Nothing runs on a laptop. Nothing sleeps. Nothing costs money.

**Why two schedulers.** GitHub's `schedule` event is best-effort by
documentation — "some queued jobs may be dropped" — and on seven consecutive
days it created the 04:00 run between 32 minutes and 12 hours late. Cloudflare
Cron Triggers are punctual and the Worker already exists, so the real trigger
moved there and GitHub's schedule became the backstop. See ADR-010.

## What each piece does

| Piece | Role | Free tier | Why this one |
|---|---|---|---|
| GitHub Actions | Runs generation daily | 2000 min/mo private; a 2-min job uses ~60 | Already where the code lives; `apt install ffmpeg` works |
| Cloudflare R2 | Stores audio + manifests | 10 GB, **zero egress** | The phone downloads ~4 MB every morning; egress is what makes other object stores expensive |
| iPhone | Plays | — | ADR-001: the server generates, the phone plays |

## What is deliberately NOT deployed

**The voice / Join feature.** It needs a warm process to hit its 2.5-second
budget, and free-tier cold starts are 30-60 seconds. It is PRD priority P2, the
code is merged and tested, and it still works whenever you run the backend
locally. Deferred, not deleted — see ADR-008.

**The FastAPI app itself**, for the daily path. Generation writes straight to
R2 and the phone reads straight from R2. Run `uvicorn` locally when you want
on-demand generation or voice.

## The bucket

Already created: **`mercury-briefings`** (Standard class, ENAM region).

What is left is the API token, which has to be you — it is a credential, and
generating it in a dashboard is the only place it is ever shown in full:

1. Cloudflare dashboard → **R2** → **Manage R2 API Tokens** → **Create API token**
2. Permission: **Object Read & Write**
3. Scope it to **`mercury-briefings`** specifically rather than all buckets —
   the workflow has no reason to touch the other four buckets on this account
4. Copy the **Access Key ID** and **Secret Access Key** into the secrets below.
   The secret is shown once.

## Secrets

GitHub repository → Settings → Secrets and variables → Actions:

| Secret | What |
|---|---|
| `GEMINI_API_KEY` | AI Studio key |
| `GOOGLE_TTS_CREDENTIALS_JSON` | The **service-account** key — Google Cloud Console → IAM & Admin → Service Accounts → Keys → Add key → JSON. Top-level keys include `client_email` and `private_key`. |
| `GOOGLE_OAUTH_TOKEN_JSON` | The **user token** — `token.json` from the repo root, produced by running a spike locally. Contains `refresh_token`. |
| `R2_ACCOUNT_ID` | Cloudflare account id (R2 overview page, right-hand sidebar) |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | From an R2 API token, **Object Read & Write**. Generation only. |
| `R2_READONLY_ACCESS_KEY_ID` / `R2_READONLY_SECRET_ACCESS_KEY` | From a second R2 API token, **Object Read Only**. The watchdog only. See below. |
| `R2_BUCKET` | `mercury-briefings` — already created |
| `MERCURY_API_TOKEN` | Same value the iOS client sends |
| `USER_EMAIL`, `USER_NAME`, `USER_TIMEZONE` | Personal config |

The workflow writes the two JSON secrets to files at runtime. They are never
committed — `.gitignore` covers `credentials/`, `token.json` and `.env`.

### The watchdog's read-only R2 token — ACTION REQUIRED

`briefing-watchdog.yml` only reads. It has no Google credentials and no ffmpeg
for exactly that reason: it runs unattended every morning, so its blast radius
should be as small as the job it does. Handing it the write-capable generation
token contradicts that — a compromised dependency, or a mistyped command in
that workflow, could delete or overwrite the briefings it exists to protect.

**The repository owner must do this; nobody else can mint the token:**

1. Cloudflare dashboard → **R2 → API → Manage API Tokens → Create API Token**
2. Permission: **Object Read Only**. Scope it to the `mercury-briefings`
   bucket, not "all buckets".
3. Add the resulting pair as `R2_READONLY_ACCESS_KEY_ID` and
   `R2_READONLY_SECRET_ACCESS_KEY` in Actions secrets.
4. Then delete the fallback in `briefing-watchdog.yml` — the
   `|| secrets.R2_ACCESS_KEY_ID` half of each line — so the write token can
   never be reached from that workflow again.

Until step 3 is done the workflow falls back to the generation credentials, so
the watchdog keeps working rather than failing closed on a secret that does not
exist yet. That fallback is a stopgap, not the design.

### Three JSON files, easy to confuse

This project involves three similar-looking Google JSON files, and putting the
wrong one in a secret is the first thing that actually went wrong in
production:

| File | Top-level shape | Where it goes |
|---|---|---|
| `credentials.json` | `{"installed": {...}}` | **Nowhere.** Local OAuth only |
| `token.json` | has `refresh_token` | `GOOGLE_OAUTH_TOKEN_JSON` |
| `gcp-tts.json` | has `client_email`, `private_key` | `GOOGLE_TTS_CREDENTIALS_JSON` |

The workflow validates the shape of both before generating, so a mix-up fails
in ten seconds with a message naming the wrong secret, rather than after
install with a google.auth stack trace.

## Publishing the OAuth consent screen is REQUIRED, not optional

Not a hardening step, not a nice-to-have. **Until this is done, Mercury breaks
every seven days**, permanently and on a repeating cycle.

The Google OAuth consent screen is in **Testing** mode, where Google expires
refresh tokens after **7 days**. When that happens, Gmail and Calendar
ingestion fail — and CLAUDE.md rule 7 drops a failed source rather than failing
the run, so the briefing still ships, still sounds fine, and simply stops
mentioning the user's inbox and calendar. That is most of the product, gone,
with no error anyone sees.

**Do this once:**

1. https://console.cloud.google.com/auth/audience — or Google Cloud Console →
   **Google Auth Platform → Audience**
2. Under *Publishing status: Testing*, click **Publish app**
3. Confirm. For a single user on `gmail.readonly` and `calendar.readonly` this
   needs **no verification review** — it is one dialog.
4. Re-mint `token.json` locally (`python scripts/spike_02_gmail.py`) and update
   the `GOOGLE_OAUTH_TOKEN_JSON` secret. The old token stays expired; publishing
   does not revive it.

**How you will know if you skipped it.** The run now goes red and GitHub emails
you, with this in the log:

    ::error::The Google OAuth token has EXPIRED — email and calendar could not
    be read. While the consent screen is in Testing mode Google expires refresh
    tokens after 7 days...

That error is keyed off the exception type (`ExpiredCredentialsError`), not off
message text, so it cannot drift out of sync with the thing it describes.

Your own risk register rates "OAuth refresh token expires and the scheduler
breaks silently" as High impact, High likelihood. This is that risk. Publishing
the app is the mitigation; the loud error is only the smoke alarm.

## When a run goes red

Three distinct failures, three different exit codes from `app.cli`, all of them
reaching you as GitHub's ordinary failed-run email:

| Exit | Meaning | What to do |
|---|---|---|
| 1 | Generation failed, or produced nothing playable | Read the log. If it says NO AUDIBLE TURNS, check Google TTS credentials and quota — nothing was stored, so a re-run needs no `force` |
| 2 | Configuration is wrong | A secret is missing or malformed; the message names which |
| 3 | The briefing shipped **without** the inbox or calendar | Usually the expired token above. The audio is in R2 and the phone will play it — the red tick is there to tell you, not to stop you |

Exit 3 is the one that used to be exit 0. A briefing that shipped without the
user's actual day exited successfully, so Actions went green and no email was
sent; the 2026-08-27 failure was found days later by reading `gh run list`.
Pass `--allow-degraded` if you would rather a degraded briefing reported
success.

A run that never happened at all reports nothing, because there is nothing to
report — that is what the watchdog above is for.

## The read Worker

**Deployed:** `https://mercury-briefings.udujoel.workers.dev`


Generation writes to R2. Nothing could read it, for two reasons:

- the app asks for `/briefing/{id}`; R2 stores `{id}.manifest.json`. A bucket
  serves flat object keys, not routes.
- **R2 buckets are private by default.** There is no public URL, and putting
  R2 credentials in an iOS app is not an option.

`worker/` closes that gap. It sits in front of the bucket, translates the
routes, and checks the same `X-Mercury-Token` header the FastAPI backend uses
— so the iOS client needs no changes, only a different `API_HOST`.

The bucket stays private; only the Worker holds the binding.

```bash
npx wrangler login                        # interactive, once
cp worker/.dev.vars.example worker/.dev.vars
$EDITOR worker/.dev.vars                  # replace each <...> with a real value
scripts/push_worker_secrets.sh            # uploads them; skips untouched ones
cd worker && npx wrangler deploy
```

`worker/.dev.vars` is gitignored and is also the file `wrangler dev` reads, so
one filled-in copy serves local development and production. The push script
refuses a `MERCURY_API_TOKEN` under 32 characters and a `USER_TIMEZONE` that
is not an IANA zone, because both of those fail *quietly* in production — a
short token 503s every route, and an unparseable zone falls back to UTC and
makes the phone prefetch the wrong day. It uploads nothing that is still at
its `<placeholder>`: the Worker answers a missing secret with a 503 naming it,
which is a far better morning than a present-but-wrong credential returning
401 from GitHub inside a 03:40 cron tick.

`USER_TIMEZONE` is **not optional**, and this guide used to omit it. `/health`
reports it and the iOS client caches that value (`BriefingClock`) to derive
briefing ids locally — the background task has no other way to know which
briefing to prefetch. Leave it unset and `/health` reports UTC, so the phone
asks for the wrong day whenever the user's zone is not UTC.

Deploying prints a `*.workers.dev` URL. That becomes `API_HOST` below.

**Cost: zero.** Workers free tier is 100,000 requests a day; Mercury makes
about two. R2 free tier is 10GB with no egress charge; 30 days of ~3MB
briefings uses about 90MB.

## The schedule

Three layers. Each one exists because the layer above it can fail. ADR-010 has
the evidence and the trade-offs; this is what an operator has to do.

| When (UTC) | What | Where |
|---|---|---|
| 03:40 | Generate, if today's briefing is missing | Cloudflare Cron Trigger |
| 04:12, 04:41 | Generate, if still missing | GitHub `schedule` (backstop) |
| 04:20, 04:50 | Retry, if still missing | Cloudflare Cron Trigger |
| 05:08 | Generate anyway — deadline blown | Cloudflare Cron Trigger |
| 05:40 | Raise the alarm, **if still missing** | Cloudflare Cron Trigger |
| 05:52, 06:56 | Alarm backstop, if the Worker itself is dead | GitHub `schedule` |

**The alarm is conditional, and it is not raised in the same tick as a
generation dispatch.** 05:40 dispatches the watchdog only when R2 still has no
playable briefing, and the watchdog only goes red when its own check agrees.
The alarm used to be dispatched immediately after the 05:08 generation;
`workflow_dispatch` merely queues a run, so the watchdog read R2 before that
run had written anything and reported a missing briefing about one being
generated as it looked. A watchdog that cries wolf every morning is worse than
no watchdog.

**Five Cloudflare crons is the ceiling.** The free plan allows 5 Cron Triggers
*per account*, not per Worker. There is no room for a sixth checkpoint here or
for a cron on any other Worker in this account without the paid plan.

The deadline is **06:00 Europe/Lisbon**. Lisbon is UTC+1 in summer, so the
tight case is 05:00 UTC and every time above is sized against it. Cron is
UTC-only and has no DST on either platform, so in winter the fixed 05:00 UTC
deadline sits an hour ahead of the local one and the alarm can fire up to 52
minutes early. That is deliberate: by 05:40 four generation attempts have
failed and the last has had half an hour, so it is an early alarm about a real
absence — and the opposite error costs the user their morning. Making it
DST-correct would need a checkpoint after 06:00 UTC for winter *and* one before
it for summer, which is one cron more than the account limit allows.

Every checkpoint first asks R2 whether a playable briefing already exists, so
on a healthy morning all of them after the first are a single read.

### The dispatch token

The Cloudflare Worker needs to be able to say "run the workflow now". That is
the one new credential this design costs, and it is worth scoping tightly:

1. GitHub → **Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate new token**
2. **Repository access:** Only select repositories → `udujoel/mercury`. Not
   "All repositories".
3. **Permissions:** Repository permissions → **Actions: Read and write**.
   Nothing else. It does not need contents, secrets, or workflows.
4. Set an expiry you will actually notice, and put the date somewhere. This is
   the second credential in this project that expires silently; the first one
   is the Google OAuth token below.
5. Paste it into `worker/.dev.vars` as `GITHUB_DISPATCH_TOKEN` and run
   `scripts/push_worker_secrets.sh`. (Or, by hand:
   `cd worker && npx wrangler secret put GITHUB_DISPATCH_TOKEN`.)
6. Confirm it landed: `cd worker && npx wrangler secret list` should now show
   `GITHUB_DISPATCH_TOKEN` alongside `MERCURY_API_TOKEN` and `USER_TIMEZONE`.
   Secrets take effect immediately; no redeploy is needed for a secret alone.

`GITHUB_REPO` and `GITHUB_REF` are plain `[vars]` in `worker/wrangler.toml` —
a repo slug and a branch name are not secrets.

**If this token expires or is revoked**, the Worker logs
`dispatch of briefing.yml failed: HTTP 401` (Workers → mercury-briefings →
Logs). Both dispatches use this token, so the 05:40 checkpoint cannot raise the
alarm either. What is left is GitHub's own `schedule` — the 04:12/04:41
generation backstops and the 05:52/06:56 watchdog backstops — which is the
best-effort scheduler this ADR exists to stop relying on, and may be delayed or
dropped. **You lose punctuality AND timely alarming**, and the Worker's
`/status` endpoint is then the only reliable way to ask whether the briefing is
there. Do not read "the watchdog still alarms" into this: it alarms only if
GitHub happens to run it.

### Checking on it yourself

    curl -H "X-Mercury-Token: <token>" https://mercury-briefings.udujoel.workers.dev/status

Returns `{"briefing_id": "...", "state": "ok" | "empty" | "missing", "ok": ...}`.
Authenticated, unlike `/health`, because whether a briefing exists for today is
information about the user's morning.

## Pointing the iPhone at R2

`ios/Mercury/Secrets.xcconfig` (gitignored):

```
API_SCHEME = https
API_HOST = mercury-briefings.udujoel.workers.dev
MERCURY_API_TOKEN = <same value as the Actions secret>
```

**Set scheme and host separately.** xcconfig treats `//` as a comment, so
pasting a full URL silently truncates it to `https:` — a valid URL with no
host, which fails at runtime with no useful error. This has bitten twice.

## Retention

Old briefings are deleted after **30 days**, by a `python -m app.cli prune`
step in the daily workflow. Change the window with a repository *variable*
(Settings → Secrets and variables → Actions → Variables) named
`RETENTION_DAYS`, or locally in `backend/.env`.

It cannot delete today's briefing — there are two independent guards, and a
test for each. `RETENTION_DAYS=0` is refused rather than read as "delete
everything"; the minimum is 1.

To see what it would do without doing it:

```bash
cd backend && python -m app.cli prune --dry-run
```

**A correction, since this file was part of the problem.** Both this document
and `r2_storage.py` described a "7-day retention" for months. There was no such
thing — not in the code, not in the workflow, not in a bucket lifecycle rule.
Storage had been growing without limit the whole time, and the docs said
otherwise in the reassuring direction. See ADR-011.

## Running locally

Still works, unchanged, and is how you get voice and on-demand generation:

```bash
cd backend && set -a && . ./.env && set +a && \
  ./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Point the app at it with `API_SCHEME = http`, `API_HOST = localhost:8000`.

## Cost

Zero, plus whatever Gemini and Google TTS charge beyond their free tiers —
the PRD budgets 3-8 USD a month for TTS once the 1M free characters are used.
