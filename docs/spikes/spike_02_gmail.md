# Spike 2 — Gmail fetch, extraction and heuristics

Status: offline path fully proven with fixtures. Live path implemented and
ready but unproven — no `credentials.json` exists on this machine (see
`BRIEF.md`, "Critical environment fact").

Script: `scripts/spike_02_gmail.py`
Shared auth: `scripts/_google_auth.py`
Fixture: `backend/tests/fixtures/gmail_messages.json`
Tests: `backend/tests/test_spike_02_gmail.py` (23 tests)

## What was proven offline

- **MIME walking.** `walk_mime_parts` recursively descends `payload.parts`,
  preferring the first `text/plain` leaf and falling back to `text/html`
  stripped through BeautifulSoup. Verified against three shapes taken
  directly from real Gmail `messages.get(format='full')` response trees:
  a `multipart/alternative` newsletter (plain + html siblings), an
  HTML-only single-part message, and a `multipart/mixed` message containing
  a nested `multipart/alternative` body plus a PDF attachment part (the
  attachment is correctly skipped because it carries a `filename` and no
  inline `body.data`).
- **base64url decoding.** `decode_base64url` normalizes `-`/`_` to `+`/`/`
  and re-pads to a multiple of 4 before decoding — Gmail almost always
  ships bodies with padding stripped, and a naive `base64.b64decode` throws
  `binascii.Error` on that input. Tested with intentionally unpadded input
  and with bytes chosen so the encoded form definitely contains `-`/`_`.
- **Cleaning.** Quote-chain stripping cuts everything from the first
  `On ... wrote:` line and any remaining `>`-prefixed lines; signature
  stripping cuts from a standalone `-- ` delimiter; tracking pixels
  (`width=1`/`height=1` `<img>` tags) and unsubscribe footer lines are
  regex-stripped; whitespace is collapsed. All four fixture emails round-trip
  through `clean_body` with no quote text, no signature block, and no
  tracking pixel URL surviving into the final `Item.body`.
- **Newsletter detection (PRD F-04).** `detect_newsletter` checks, in order,
  `List-Unsubscribe`, `Precedence: bulk`, `List-Id`, then a sender-string
  heuristic (`noreply@`, `newsletter@`, `@substack.com`, etc.), and returns
  which signal fired so misclassification is debuggable. Both newsletter
  fixtures (`List-Unsubscribe` case and a bare `noreply@` sender) are
  correctly flagged; both direct-human emails are not.
- **Priority / requires_response (PRD F-05).** `classify_priority` in one
  function with a docstring explaining the reasoning: newsletters are always
  `low` and never `requires_response`; a message with the user in `To`
  (not `Cc`) that contains a `?` or an ask-phrase ("can you", "let me
  know", ...) is `high` + `requires_response=True`; everything else is
  `medium`. Verified: the direct question email → high; the Cc-only case
  with a question in the body → medium (the ask isn't directed at the
  user); a plain acknowledgement reply → medium.
- **Graceful degradation.** One fixture message (`msg005`) has no `payload`
  key at all, simulating a malformed/partial API response. `main()` catches
  the exception per-message, logs to stderr, and continues — the run still
  produces 4 normalised `Item`s instead of crashing.

## Real output (`--fixture` mode)

```
$ backend/.venv/bin/python scripts/spike_02_gmail.py --fixture
[fixture] reading /Users/joud/sandbox/ML/mercury/backend/tests/fixtures/gmail_messages.json
skipping malformed message msg005: message msg005 has no payload
PRIORITY NEWSLETTER   CHARS  SENDER                       SUBJECT
-----------------------------------------------------------------
low      yes            174  Signals Weekly               This Week in Signals
low      yes             79  BrandCo Deals                Flash Sale: 30% off everything
high     no             155  Maria Chen                   Q3 budget review before Friday
medium   no              57  Maria Chen                   Re: Q3 budget review before Friday

4 messages parsed, 1 skipped as malformed.
```

`--json` dumps the normalised `Item` list; spot-checked that `msg004`'s
body is reduced to exactly `"Sounds good, I'll send the updated deck
tomorrow morning."` with the quote chain and signature both gone, and that
`msg002`'s HTML-only body decodes to clean text with no tags.

## Test and lint results (verbatim)

```
$ cd backend && ./.venv/bin/python -m pytest -q tests/test_spike_02_gmail.py
.......................  [100%]
23 passed in 0.1x s
```

```
$ cd backend && ./.venv/bin/ruff check scripts/spike_02_gmail.py scripts/_google_auth.py tests/test_spike_02_gmail.py
All checks passed!
```

(Full-suite `pytest -q` from `backend/` shows 2 unrelated failures in
`tests/test_spike_04_script.py`, owned by a different Phase 0 spike agent —
not this spike's files.)

## What needs live credentials (unproven)

- Whether `messages.list(q="newer_than:1d -category:promotions
  -category:social")` actually returns the expected set against a real
  inbox — the query syntax is standard Gmail search syntax and should work,
  but has not been executed against a live account.
- Real-world MIME shapes not represented in the fixture: deeply nested
  `multipart/related` (inline images referenced by `cid:`), `multipart/
  signed` (S/MIME or PGP), messages with a `charset` other than UTF-8 in
  the `Content-Type` header (the current decoder assumes UTF-8 and uses
  `errors="replace"`, which is a reasonable but unverified fallback), and
  very large messages that might hit `messages.get` payload size limits.
- OAuth token refresh behavior in practice (the code path exists in
  `_google_auth.py` and mirrors the standard `google-auth` pattern, but has
  never executed against Google's token endpoint).
- Real Gmail API rate limits / quota behavior under the `maxResults=60`
  cap plus one `messages.get` per message (61 requests per run in the worst
  case) — this is the shape Phase 1 ingestion will inherit, so it is worth
  checking with the Python client's default retry/backoff before Phase 1.

## Exact steps to run live

1. https://console.cloud.google.com/apis/library — enable **Gmail API**
   and **Google Calendar API** for your project (Spike 3 shares the same
   client).
2. https://console.cloud.google.com/apis/credentials/consent — configure
   the OAuth consent screen. Publishing status: **Testing**. Add your own
   Google account as the sole test user.
3. https://console.cloud.google.com/apis/credentials — **Create
   Credentials → OAuth client ID**, application type **Desktop app**.
4. Download the resulting JSON and save it as `credentials.json` at the
   repo root (`/Users/joud/sandbox/ML/mercury/credentials.json` — already
   gitignored). Or point `GOOGLE_CREDENTIALS_FILE` at another path.
5. Run `python scripts/spike_02_gmail.py --live`. First run opens a
   browser for the OAuth consent flow and caches the resulting token to
   `token.json` at the repo root (also gitignored, overridable via
   `GOOGLE_TOKEN_FILE`). Subsequent runs reuse and silently refresh it.

## Gotchas found

- Gmail API bodies are base64url **without padding** essentially always —
  a decoder that assumes standard base64 padding will throw on real data.
  This is the single most important thing to get right in this spike.
- `multipart/alternative` puts `text/plain` and `text/html` as *siblings*,
  not nested — a naive "first leaf with a body" walk will grab whichever
  the API happens to list first (often HTML), which is why the walk
  explicitly prefers `text/plain` by MIME type rather than by tree order.
- Attachments show up as parts with a `filename` and `body.attachmentId`
  instead of `body.data` — must be recognized and skipped, not treated as
  a candidate for `text/plain`/`text/html` (an attachment named
  `notes.txt` would otherwise be misread as the body).
- `List-Unsubscribe` is a far stronger newsletter signal than sender-string
  matching and should be checked first; several real senders (e.g.
  `hello@company.com`) send genuine one-to-one correspondence, so
  sender-heuristic patterns should stay a low-confidence fallback, not the
  primary signal, and the code returns *which* signal fired specifically so
  this can be tuned later without re-deriving the logic.
