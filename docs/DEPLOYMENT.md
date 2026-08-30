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
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | From an R2 API token, **Object Read & Write** |
| `R2_BUCKET` | `mercury-briefings` — already created |
| `MERCURY_API_TOKEN` | Same value the iOS client sends |
| `USER_EMAIL`, `USER_NAME`, `USER_TIMEZONE` | Personal config |

The workflow writes the two JSON secrets to files at runtime. They are never
committed — `.gitignore` covers `credentials/`, `token.json` and `.env`.

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

## The one thing that will break weekly if you skip it

The Google OAuth consent screen is in **Testing** mode, where **refresh tokens
expire after 7 days**. Unattended generation will stop every week, silently,
with a `invalid_grant` in the Actions log.

Fix once: Google Cloud Console → **Google Auth Platform → Audience →
Publish app**. For a single user on `gmail.readonly` and `calendar.readonly`
this needs no verification review. Then regenerate `token.json` locally and
update the `GOOGLE_OAUTH_TOKEN_JSON` secret.

Your own risk register rates "OAuth refresh token expires and the scheduler
breaks silently" as High impact, High likelihood. This is that risk, and this
is the mitigation.

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
npx wrangler login          # interactive, once
cd worker
npx wrangler secret put MERCURY_API_TOKEN       # same value as backend/.env
npx wrangler secret put USER_TIMEZONE           # same value as backend/.env
npx wrangler secret put GITHUB_DISPATCH_TOKEN   # see "The schedule" below
npx wrangler deploy
```

`USER_TIMEZONE` is **not optional**, and this guide used to omit it. `/health`
reports it and the iOS client caches that value (`BriefingClock`) to derive
briefing ids locally — the background task has no other way to know which
briefing to prefetch. Leave it unset and `/health` reports UTC, so the phone
asks for the wrong day whenever the user's zone is not UTC.

Deploying prints a `*.workers.dev` URL. That becomes `API_HOST` below.

**Cost: zero.** Workers free tier is 100,000 requests a day; Mercury makes
about two. R2 free tier is 10GB with no egress charge; a 7-day retention of
~3MB briefings uses about 20MB.

## The schedule

Three layers. Each one exists because the layer above it can fail. ADR-010 has
the evidence and the trade-offs; this is what an operator has to do.

| When (UTC) | What | Where |
|---|---|---|
| 03:40 | Generate, if today's briefing is missing | Cloudflare Cron Trigger |
| 04:12, 04:41 | Generate, if still missing | GitHub `schedule` (backstop) |
| 04:20, 04:50 | Retry, if still missing | Cloudflare Cron Trigger |
| 05:08 | Generate **and raise the alarm** — deadline blown | Cloudflare Cron Trigger |
| 05:27, 06:56 | Alarm backstop, if the Worker itself is dead | GitHub `schedule` |

The deadline is **06:00 Europe/Lisbon**. Lisbon is UTC+1 in summer, so the
tight case is 05:00 UTC and every time above is sized against it. Cron is
UTC-only and has no DST on either platform, so these shift by an hour relative
to local time across the year — deliberately, and winter simply gets more
margin.

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
5. `cd worker && npx wrangler secret put GITHUB_DISPATCH_TOKEN`

`GITHUB_REPO` and `GITHUB_REF` are plain `[vars]` in `worker/wrangler.toml` —
a repo slug and a branch name are not secrets.

**If this token expires or is revoked**, the Worker logs
`dispatch of briefing.yml failed: HTTP 401` (Workers → mercury-briefings →
Logs) and the system degrades to the GitHub backstop: the briefing still
generates, late and unpredictably, and the watchdog still alarms. You lose
punctuality, not the briefing.

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
