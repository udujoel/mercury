# Deployment

Mercury runs with **no hosted backend**. See ADR-008 for why.

```
  05:00 local
      │
      ▼
  GitHub Actions cron ──► generate (127s) ──► Cloudflare R2
                                                   │
  iPhone ◄─────────── manifest + audio ────────────┘
```

Nothing runs on a laptop. Nothing sleeps. Nothing costs money.

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

## Secrets

GitHub repository → Settings → Secrets and variables → Actions:

| Secret | What |
|---|---|
| `GEMINI_API_KEY` | AI Studio key |
| `GOOGLE_TTS_CREDENTIALS_JSON` | Contents of the GCP service-account key |
| `GOOGLE_OAUTH_TOKEN_JSON` | Contents of `token.json` (Gmail + Calendar refresh token) |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` | Cloudflare R2 |
| `MERCURY_API_TOKEN` | Same value the iOS client sends |
| `USER_EMAIL`, `USER_NAME`, `USER_TIMEZONE` | Personal config |

The workflow writes the two JSON secrets to files at runtime. They are never
committed — `.gitignore` covers `credentials/`, `token.json` and `.env`.

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

## Pointing the iPhone at R2

`ios/Mercury/Secrets.xcconfig` (gitignored):

```
API_SCHEME = https
API_HOST = <your-bucket>.<account>.r2.cloudflarestorage.com
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
