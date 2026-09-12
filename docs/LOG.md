# Working log

The running record of what was done, what was decided, and what it cost.

**What belongs here.** Decisions and their reasoning, bugs found in production
and how they were found, things that turned out to be untrue, and the shape of
mistakes worth not repeating. Newest first.

**What does not.** Anything the code already says. This is not a changelog —
`git log` is the changelog. A line belongs here only if a reader six months
from now could not reconstruct it from the repository.

**How it relates to the other docs.** `docs/DECISIONS.md` holds the ADRs: the
decisions that constrain everything after them, written once and rarely
revised. This file holds the narrative around them — including decisions too
small for an ADR, and the evidence that prompted one. `docs/ROADMAP.md` is
what is *planned*; this is what *happened*.

---

## 2026-09-12 — The audit: almost everything is built, almost none of it arrives

Eight audit agents over the code, sixteen adversarial verification passes, and
a probe of the live system. The result reframed the roadmap.

**The finding.** "The show" — tasks 1 to 15, the entire audio and player phase
— is essentially complete in source and largely absent in production. The gap
is not unfinished work. It is merged work that nothing delivers.

Evidence, all from the live system rather than from the code:

| Probe | Result |
|---|---|
| `GET /settings` | `{}` — every toggle has only ever used its env-var fallback |
| ElevenLabs, 4 consecutive runs | `401 quota_exceeded`, fails over to Google, **exits 0 green** |
| Every shipped manifest | `words: null` on all 38 turns |
| Last 40 briefing runs | all `event=schedule`; the Cloudflare punctual trigger has never dispatched one |
| The inbox segment | 188s of a 490s briefing — **38%** |

**The inbox segment is the sharpest evidence.** Six of its fourteen turns are
two hosts discussing a report about Joel's own GitHub commits, with Host B
asking "what were the key areas of development highlighted in that report?" A
€3.15 taxi receipt is read out *before* the warning that his Binance account
is about to be restricted. That is not a triage problem alone — it is a rank
and budget problem: the segment spent 45% of its airtime on its lowest-value
item because that email happened to be long and structured. Buckets will not
fix it on their own.

**Decision: the next phase is delivery, not features.** Tasks 20–26 were
created for the gap. Nothing in them is new scope.

**Three stale-backlog corrections.** Four UI-refinement boxes were closed
(verified deleted by grep), task 12's `[~]` was closed (12b superseded it),
and `P1-fastapi` was closed (ADR-008 removed the HTTP surface). An open box
that describes finished work makes the backlog lie about how much is left.

**A near-miss worth recording.** The audit was about to cut CLAUDE.md's phase
gates as "duplicated from ROADMAP.md". They are not in ROADMAP.md at all —
checked before cutting. The "when unsure, keep it" rule earned its place.

---

## 2026-09-12 — An identifier changed shape, and three sites kept predicting it

PR #33 gave a day four briefings, so ids went from `2026-09-11` to
`2026-09-11-2155`. Every site that *produced* or *fetched* ids was updated.
**Three sites that *predicted* ids were not, and none of them appears in
#33's diff.**

1. **The nightly prefetch** (`MercuryApp.swift`) derived a bare day and asked
   R2 for it. Its comment argued no round trip was needed. Every background
   prefetch 404ed for five days — and ADR-001's whole promise is that the
   audio is finished before you wake.
2. **The rebuild match** (`AppState.noteRebuildLanded`) compared the recorded
   day against the landed timed id with `==`. It never matched, so only the
   20-minute expiry cleared the spinner.
3. **The offline fallback** (`playStoredBriefing`) asked SwiftData for a
   bare-day id under an exact-match predicate. Found while *reviewing* the fix
   for the first two — the review's own thesis predicted a third site, so it
   went looking.

**Why none of this was caught.** All three still compiled and all three still
returned perfectly valid strings. Only the meaning changed, so a grep for the
old format finds nothing. And the test fixtures encoded the bug:
`ManifestFixtures.todayId` returns a bare day, so every existing rebuild test
spoke a dialect production had abandoned.

**Lesson, generalised.** A test suite inherits its blind spot from its
fixtures. When an identifier changes shape, the fixtures are the *first* thing
to update, not the last — and the review question is "what is newly
**reachable**", not "what is newly written".

**Decision: name the concept.** `BriefingID.day(of:)` / `sameDay(_:_:)` mirror
the server's `briefing_day` and `DAY_PREFIX_LENGTH`. The phone had the idea
open-coded once as `String(id.prefix(10))` and missing everywhere else. An
unnamed concept cannot be grepped for, which is exactly why the third site
stayed invisible until someone went looking for it.

**Decision: the prefetch selects, it does not compute.** `briefingToPrefetch`
returns today's newest, or nothing. `index.first` would always put something
on the phone — and on a failed morning that something is yesterday's briefing
presented as today's, which *also* suppresses the waiting room, whose entire
job is to make an empty morning audible.

---

## 2026-09-12 — The briefing did not end

Reported from real use: "when the narration is done, the podcast doesn't seem
to stop. it still looks like its playing, even on the lock screen."

Three defects on one path, and **every one was an absence** — which is why 327
green tests never saw it. Nothing was wrong with the code that existed.

- `finish()` announced the end by setting the playback rate to `0.0`. That is
  how iOS spells **paused**. `NowPlayingService` had no `clear()` at all.
- `setActive(true)` appeared five times in the app; its opposite appeared
  **zero** times. Mercury held audio focus after it stopped making sound.
- `HomeView` observed `isPlaying` and only ever navigated *to* the player.
  There was no edge back.

**Decision: key the return home on `hasFinished`, not `!isPlaying`.**
`isPlaying` goes false on every pause, and yanking someone out of the player
because they paused to hear something else would be a worse bug than the one
being fixed.

**A trap the fix created, and where it is handled.** Clearing a card obliges
you to own rebuilding it. `nowPlaying.update` ran only from `load`, and replay
after a finish never calls `load` again — it would have landed in
`setPlaybackState`, whose `nowPlayingInfo ?? [:]` cheerfully publishes a card
with no title, no name and no artwork. The rebuild hangs off the single choke
point every revival passes through, so the next entry point cannot be
forgotten the way `finish()` was.

---

## 2026-09-12 — Research moves from Groq to Gemini

Task 18 originally specified Groq's free tier. Changed at the user's
direction, and it is the better call for a reason worth recording: **Gemini is
already this project's LLM.** `GEMINI_API_KEY` is in the environment,
`GeminiProvider` is the working implementation, and its free tier has carried
every generation for months without once hitting the datacenter-IP block that
made the ElevenLabs free tier unusable from CI. Groq would have meant a second
vendor, a second key, and a second free tier to be surprised by.

The SDK (`google-generativeai` 0.8.3) exposes `GoogleSearchRetrieval` with
`GroundingMetadata` and `GroundingChunk`, so search results come back with
**source URIs attached**. That is what makes rule 3 — every claim grounded in
an openable source — satisfiable rather than aspirational.

It still sits behind a `ResearchProvider` protocol. The protocol was never
about Groq; it is about not welding the research step to whoever is cheapest
this quarter.

**Constraint discovered while scoping it.** Task 16 says "the digest carries
the buckets", which collides with a deliberate, documented decision:
`digest.py:151` keeps personal email *off* the browse surface, because
rendering a private message as a browsable story with an LLM-written headline
puts correspondence into a screen built for published journalism. The triage
block has to be a sibling of `stories`, the way `newsletters` already is — not
new story cards.

---

## Standing facts worth not rediscovering

- **The Worker has no CI deploy.** Merging is not shipping. A route on `main`
  stays 404 in production until someone runs `wrangler deploy`. This once left
  the deployed Worker months behind the repo, and the news desk looked like an
  unbuilt feature on the phone while every suite was green.
- **ElevenLabs' free tier is not a base for a CI job.** Library voices return
  402; datacenter IPs get free API access disabled entirely (401); and the
  account can simply run out of credits, which is what is happening now. Each
  failure degrades to Google and ships green.
- **A green suite has certified unrun code twice.** Phase 2's news half ran
  with a hardcoded `n_stories=0`; `music_set` was stripped by a Worker
  whitelist so the setting never once applied. When a phase is marked done,
  check what **calls** the new code.
- **`follow_ups` is built from the literal string `"(none)"`**
  (`script_chain.py:1347`) — the same shape as `n_stories=0`, still open as
  task 24.
- **Two suites run only by hand.** There is no iOS job and no Worker job in
  CI. The Worker test written specifically to stop the next `music_set`-class
  bug is itself run by nothing.
