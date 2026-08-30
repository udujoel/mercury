# Mercury read Worker

The gap this closes: generation writes to R2, but nothing could read it.

    the app asks for      R2 actually stores
    /briefing/{id}        {id}.manifest.json
    /briefing/{id}/audio  {id}.m4a

Two mismatches, not one. The client speaks FastAPI routes while a bucket
serves flat object keys — and R2 buckets are private by default, so even with
matching paths the phone could not fetch without credentials. Embedding R2
keys in an iOS app is not an option.

This Worker sits in front of the bucket and translates. The bucket stays
private; the Worker holds the binding.

    GET /briefing/{id}        -> {id}.manifest.json   (application/json)
    GET /briefing/{id}/audio  -> {id}.m4a or {id}.wav (audio/*)
    GET /status               -> is today's briefing there and playable?
    GET /health               -> liveness, no auth

Briefing responses are `private, no-cache` with an etag, and R2 evaluates
`If-None-Match` for us. Not `max-age`: a briefing can be REPLACED — that is
what `--force` is for — and an hour of freely reusable cache meant the phone
kept playing a briefing that had already been fixed, with no way to invalidate
it. Revalidating costs a conditional request and a 304 rather than ~3MB.

Every briefing route requires the same `X-Mercury-Token` header the FastAPI
backend uses, so the iOS client needs no changes at all — only a different
`API_HOST`.

Free tier is 100,000 requests a day against a workload of roughly one request
each morning.

## Deploy

    npm install -g wrangler
    cd worker
    wrangler secret put MERCURY_API_TOKEN       # same value as backend/.env
    wrangler secret put USER_TIMEZONE           # same value as backend/.env
    wrangler secret put GITHUB_DISPATCH_TOKEN   # see docs/DEPLOYMENT.md

    wrangler deploy

USER_TIMEZONE is not optional. `/health` reports it, and the iOS client caches
that value (`BriefingClock`) to derive briefing ids locally — the background
task has no other way to know which briefing to prefetch. Leave it unset and
`/health` reports UTC, so the phone asks for the wrong day whenever the user's
zone is not UTC. That is the exact failure `BriefingClock.swift` exists to
prevent.

`wrangler.toml` binds the `mercury-briefings` bucket. Deploying prints the
`*.workers.dev` URL to put in `ios/Mercury/Secrets.xcconfig` as `API_HOST`.

## It also carries the schedule

ADR-010. GitHub's `schedule` event is best-effort by documentation — "some
queued jobs may be dropped" — and seven consecutive days it created the 04:00
run between 32 minutes and 12 hours late. Cloudflare Cron Triggers run on UTC
time with no equivalent caveat, so the punctual trigger lives here: four
checkpoints (`wrangler.toml`) that ask R2 whether today's briefing exists and
call GitHub's `workflow_dispatch` only when it does not.

`GITHUB_DISPATCH_TOKEN` is the one credential that costs: a fine-grained token
scoped to this repository with the Actions permission and nothing else. It is
used only from `scheduled()`, which no HTTP request can reach. The R2 check
itself needs no credential at all — the binding is already here — so the alarm
keeps working even when the token is the thing that broke.

## What it deliberately does not do

No generation, and no POST. Generation is a scheduled batch job (ADR-008) and
a Worker has neither the runtime budget nor the Python pipeline; this only
*asks GitHub* to run it. If the app is opened before the run has happened,
`/briefing/{id}` returns 404 and the client falls back exactly as it does
today.
