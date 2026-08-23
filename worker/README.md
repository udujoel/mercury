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
    GET /health               -> liveness, no auth

Every briefing route requires the same `X-Mercury-Token` header the FastAPI
backend uses, so the iOS client needs no changes at all — only a different
`API_HOST`.

Free tier is 100,000 requests a day against a workload of roughly one request
each morning.

## Deploy

    npm install -g wrangler
    cd worker
    wrangler secret put MERCURY_API_TOKEN     # same value as backend/.env
    wrangler deploy

`wrangler.toml` binds the `mercury-briefings` bucket. Deploying prints the
`*.workers.dev` URL to put in `ios/Mercury/Secrets.xcconfig` as `API_HOST`.

## What it deliberately does not do

No generation, no POST. Generation is a scheduled batch job (ADR-008) and a
Worker has neither the runtime budget nor the Python pipeline. If the app is
opened before the cron has run, `/briefing/{id}` returns 404 and the client
falls back exactly as it does today.
