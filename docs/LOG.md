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

## 2026-09-13 — The first CI run found three things no test could

Task 23 added `ios.yml` and `worker.yml`. The iOS job failed fourteen seconds
into its first execution, and what it found is worth more than the workflow
that found it.

**1. The iOS app had not built from a clean checkout since task 4.**

    error: lstat(.../ios/Mercury/Resources/waiting-bed.mp3):
           No such file or directory (in target 'Mercury')

The waiting room's audio bed was never committed. `.gitignore` carries a
blanket `*.mp3`, and the exception below it names `assets/music/` — not the
iOS target's own Resources. `WaitingRoom` lists the file in the app target's
resources build phase, so a fresh clone could not build the app at all.
Invisible to everyone, because every machine that had ever run the app had
the file on disk.

**Second time for the same rule.** The `.gitignore` comment already narrates
it swallowing the nine music tracks on their first commit, with the PR
claiming they shipped while the repository held only a README. The fix then
added an exception for `assets/music/` and left the rule that caused it — so
it caught the very next audio file to arrive. A fix that special-cases the
victim does not stop the mechanism.

**2. The test written to prevent exactly this cannot.**
`WaitingRoomTests.testTheBedIsInTheBundle` was added as "the app-icon lesson
as an assertion". It asks the BUILT BUNDLE for the file — and locally the
bundle has it, because the build copies it from a working tree that has it.
**A test of the artefact cannot see a gap in the source.** Only a clean
checkout can.

**3. `DawnPalette.swift:153` did not compile on Xcode 16.4.**
`base - hueBand + 2 * hueBand * Double(i) / Double(steps - 1)` — five
literals, two conversions, four operators, inferred closure return type. The
type checker gave up. Xcode 26 solves it locally, so the code was never
correct-by-construction, only lucky with a toolchain.

### The class these share

The developer's machine was carrying the project: a file that existed only
locally, a test that could only observe local state, and a compiler more
generous than the one anyone else would use.

That is a different failure class from the two this project already knows.
The audit finds "nothing calls this". A live probe finds "production
disagrees with the code". **Neither can find "this only works here" — that
needs a machine that is not here.**

### Also, a mistake worth keeping

Fixing the worker job, the script was changed to `node --test test/` and
reported as working on the strength of the output's TAIL — duration, skipped,
todo. It does not work: node treats the path as a module and fails with
MODULE_NOT_FOUND. `# pass 0 # fail 1` was in the part not read. Read the
pass/fail line, not the line that always prints.

---

## 2026-09-13 — `source_count` counted emails, not publications

Found while testing whether task 17 could be built on clusters instead of on
the `topics` tag. It can — and the check turned up a bug in the number that
makes clusters worth showing.

`source_count` was `len(members)`: the raw cluster size. The live
2026-09-11 digest shows what that buys. The highest-scoring story in the
entire digest was **"Earn $60 for Vodafone App Feedback", `source_count: 4`**
— one survey company that had emailed four times. "Houthi Advance Threatens
Global Trade" claimed 4 and listed the BBC twice; "US Holds 9/11 25th
Anniversary Memorials" claimed 3 and listed Al Jazeera twice.

**The number is not decoration.** `DeskFeed.leadStory` picks the desk's lead
with `max(sourceCount)`, and the clustering's whole promise — one story that
three newsletters and two wires covered reaching you once, with all five
attributed — is a claim about distinct OUTLETS. A daily that sends three
editions is one outlet with three editions.

**The client had already solved it.** `BriefingDigest.key(for:)` keys on the
item id, falling back to title-and-sender, "since two newsletters can share a
subject line". Same question, same answer; the backend never asked it. Second
time this session that the correct idiom was already in the codebase and the
new code did not use it (the first being `scoring.py`'s timestamp sort).

**Not deduplicated by title**, deliberately: two publications writing their
own headline for one event is exactly what the number is FOR.

### And the finding that prompted it

A cluster already IS a subject, with its own coverage attached. "OpenAI
Agents API Now in Public Beta" ← TLDR, TLDR DevOps, TLDR AI. That is
task 17's "what subjects arrived, and which senders covered each", available
today from `DigestStory.headline` + `sources`, with no prompt change and no
schema change.

So task 17 has two possible shapes, and the cluster-based one is far cheaper:
- the `topics` tag needs the extraction rewritten to emit subjects
- the CLUSTER needs nothing; it needs rendering

The catch is coverage, and the number moves once `source_count` is fixed.
**15 of 209** stories have two or more distinct OUTLETS — not the 24 the
broken count reported, because the survey company that emailed four times
drops out along with every other single-sender cluster.

What is left is what a corroborated-news section should look like:

    [3] OpenAI Agents API Now in Public Beta     TLDR, TLDR AI, TLDR DevOps
    [3] Houthi Advance Threatens Global Trade    CNBC, the BBC, the NYT
    [2] Lawyer Punished for AI Fake Testimony    Ars Technica, The Verge
    [2] Social Security COLA Projected to Rise   CNBC, MarketWatch

**DECIDED 2026-09-13: outlets, not publishers.** TLDR, TLDR AI and TLDR
DevOps count as three, in the owner's words because "the newsletters are
obviously different". They have separate editorial and separate audiences,
and a reader who sees all three named learns something true — that three
newsletters they subscribe to each thought this worth carrying. The
publisher-level view would have required a field that does not exist and
would have collapsed a real signal to make a theoretical one.

So the fork for task 17, with honest numbers:

- **Cluster-led, buildable now.** ~15 rows, roughly 7% of the digest. Short,
  true, reads well. No prompt change, no schema change, no re-run.
- **Subject extraction rewritten first.** Covers far more of the digest and
  gives task 18 something specific to research — at the cost of a prompt
  change, a schema change, and every existing digest keeping the old shape.

---

## 2026-09-12 — `topics` is a category tag, not a subject

Scoping task 25 ("render what already arrives") against the live 2026-09-11
digest, and the render turns out not to be the job.

**The join works.** Inverting `DigestNewsletter.story_ids` through
`DigestStory.topics` resolves 107 times across 44 newsletters and reaches 32
topics. Mechanically, task 17's "which senders covered each subject" is
already computable from what ships today.

**The topics are the wrong grain.** What comes back is `ai`, `society`,
`history`, `culture`, `remembrance` — categories, not subjects — and `ai`
alone spans 16 stories. The cause is one line in `prompts.py`:

> `topics` is one to three lowercase single words.

A single lowercase word can only ever be a category. So a topic-led section
built on this field renders "AI — 11 senders", barely better than the list it
replaces, and it gives task 18 nothing to research: "go find out more about
`ai`" is not a research question, and the second step is that task's entire
value.

Also: only **40 of 209** stories carry topics at all, because
`DIGEST_MAX_ENRICHED = 40` caps enrichment.

**Consequence.** Task 17 is a prompt and schema change, not a rendering job —
subjects like "Apple's CEO handoff to Ternus" rather than `apple`, and a
decision about whether the coarse tags are still wanted alongside them for
grouping. Task 25 stands alone: the `newsletters` list is ready to render
sender-led and answers "what arrived" before subjects get finer.

**Why the audit missed it.** Eight agents could find features nothing calls.
This one is called, populated, decoded by the client, and correct against its
own spec — the spec asks for the wrong thing. No test would fail. It took
reading real output and asking whether it answers the question the feature
exists for: the ten second test, pointed at the page instead of the audio.

---

## 2026-09-12 — The follow-ups segment had no follow-ups

`script_chain.py` passed the literal string `"(none)"` into the prompt, so
**every briefing ever generated contained a follow-ups segment written from
nothing** — while the segment was a full citizen everywhere else: in the
`Segment` enum, with a label, a prompt definition, a word budget, required by
the validator, and offered to the user as a toggle reading "Where you owe
someone a reply". The same shape as the `n_stories=0` incident.

**The data was never missing.** `classify_priority` has set
`Item.requires_response` since Phase 1, and `ranking.py:50` reads it as an
urgency signal for scoring. The flag that answers this segment's question was
computed on every run and used for everything except the segment named after
it.

**Decision: candidates come from the inbox before selection.** The message
most worth catching here is the one that ranked too low to be read out. Safe
to add as a parameter because `generate_script` has exactly ONE production
caller, which all three entry points funnel through; with three call sites it
would have been another `music_set`.

**Decision: no bodies.** The inbox segment already quotes them. Repeating one
would spend the follow-ups budget re-reading mail just heard, and widen rule
5's exposure for nothing.

**Two review findings on my own work, both real.** Mercury's own instructions
were inside `_fence_untrusted` — the exact mistake the same file documents
avoiding, since a rule fenced with the data tells the model to obey it and
distrust it in one breath. And `_owed_replies` sorted datetimes directly,
which raises `TypeError` when `parsedate_to_datetime` returns a naive value
for a `Date` header carrying `-0000` (RFC 5322's "timezone unknown"). That
would have killed a whole briefing from inside a formatting helper, against
rule 7 — and `scoring.py:49` already had the safe idiom one file away.

**A test design that worked.** `_fake_generate_script` says in its own
comment that it stands in for the real chain's SIGNATURE, "so a new keyword
the pipeline starts passing shows up here as a TypeError rather than silently
taking a different path in tests than in production." It did exactly that, on
the first run, in eight tests at once.

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

## 2026-09-14 — Eight lenses, three answers

Ran the project past eight independent reviews framed as *a thing someone uses
at 7am*, not as a codebase: the morning ritual, the content, the desk, what
breaks unattended, cost, the data model, trust, and the gap against what it is
for. 49 findings, in `docs/REVIEW-2026-09-14.md`, tasks 27-34 in the roadmap.

**The finding is the convergence, not the count.** Five lenses that could not
see each other's work — content, desk, data, trust, gap — traced their single
worst problem back to the same ten-line function. `detect_newsletter` fires on
`List-Unsubscribe`, which every bank and payment app sets, so the flag meant to
keep private correspondence off a journalism screen is the flag that puts bank
balances on it. Reviewing by SUBSYSTEM would never have produced that;
subsystem reviews produce subsystem findings. Reviewing by what the user
experiences produced five symptoms with one cause.

**The second is a repeat of this project's signature bug, and it went
unnoticed through a green suite and an 8-agent audit.** `score_stories` takes
`topic_centroid` and `followed_entities`; nobody passes either. So `interest` is
a constant, its weight cancels, and the world segment is a recency sort with
extra steps. `topic_centroid()` is written, correct and tested. This is
`n_stories=0` and `music_set` a third time — and the lesson in CLAUDE.md,
"check what CALLS the new code", was already written down from the first two.
What actually caught it was asking why a dark-mode Ask HN thread beat a Russian
strike on a Ukrainian train, then reading backwards. **Start from the bad
output, not from the code.**

**The third is that both real cost cliffs are invisible by construction.**
Google TTS is at ~88% of its free million characters a month and Actions at
~82% of 2,000 minutes, and neither has a counter anywhere. Worse, both fail
*quietly and green*: TTS exhaustion drops a turn and exits 0, and when Actions
minutes run out the watchdog stops too, because the alarm is itself an Actions
workflow. A monitor that shares a failure domain with the thing it monitors is
not a monitor. Meanwhile the documented cost table tracks R2 storage, which is
at 4% of tier and will not be reached in a decade.

Also worth recording: the six-hourly change of 2026-09-07 is upstream of most
of this. It quadrupled the TTS burn, pushed Actions to the cap, quadrupled
publisher blocking, multiplied the repetition, and disarmed the Worker's
morning ladder (the `00:12` cron produces a briefing dated for the new day, so
every checkpoint sees "ok"). It is currently masked only by GitHub running that
cron four hours late; the October DST change unmasks it. No single diff
contained the collision — the same shape as the tunnel-serving-a-real-inbox
bug, which is the second time a defect has lived between two individually
sensible changes.

**On reviewing the reviewer.** The review's own suggested patch for the
research timeout would have crashed — it initialised `results = []` before the
`try`, and `zip(chosen, results, strict=True)` raises ValueError on six
subjects against zero results. The finding was right and the fix was wrong. A
second finding asserted a header was not an option for Gemini auth; one live
call showed `x-goog-api-key` works with the `google_search` tool. Both were
worth acting on and neither was worth applying.

## 2026-09-15 — The model refused to read the news, and the morning died

Run 35024541910 failed with `block_reason: PROHIBITED_CONTENT` on the draft
prompt. Not a source failing, not a transport error: Gemini declined to write
a script from that day's news. The exception that escaped was
`ValueError: Invalid operation: The `response.parts` quick accessor requires
a single candidate` — a message about an accessor, for a policy decision.

**The fix nobody would have written first.** The obvious response is
`safety_settings`, and it is insufficient in a way that only reading the SDK
reveals: `BlockReason` has four values (SAFETY, OTHER, BLOCKLIST,
PROHIBITED_CONTENT) and `HarmCategory` has four *different* ones. There is no
threshold that reaches PROHIBITED_CONTENT. Turning every adjustable category
to BLOCK_NONE is still worth doing — a wire report of a strike on a train
reads as DANGEROUS_CONTENT to a filter tuned for a general audience — but it
would have shipped looking like a fix and failed again on the next unlucky
news day.

**The first cut of the fix had the same bug it was fixing.** It allowlisted
`finish_reason` in (STOP, MAX_TOKENS, FINISH_REASON_UNSPECIFIED) and passed
those to `.text`. Reading the SDK settles it: `.text` raises whenever `parts`
is empty, and its own branch list *begins* with those three names. A
candidate can finish STOP with zero parts — Gemini 2.5 Flash does it when
thinking tokens eat the output budget — and that walked straight past the
guard into the identical ValueError. The allowlist was also a NARROWING in
the other direction: RECITATION and OTHER arrive with partial text often
enough to matter, and the old `.text` returned it for `_complete_json`'s
repair loop to fix.

The lesson is small and general: **ask the question the failing code
actually asks.** The SDK raises on empty parts. Checking parts is both
simpler than enumerating eleven finish reasons and complete; checking
finish_reason is a guess at what implies empty parts, and it was wrong in
both directions at once.

**A monitor inside its own failure domain is not a monitor.** Related, found
while pricing this: when Actions minutes run out, `briefing-watchdog.yml`
stops too, because the alarm that exists to report a missing briefing is
itself an Actions workflow.

**The degradation ladder, and why one rung was not enough.** The first cut
dropped `stories` and retried. Two problems. It left `world` in the resolved
segment list — so the model was still instructed to write "two to four
clustered stories" from zero items, which is `n_stories=0` rebuilt by hand
and ungroundable under rule 3. And it dropped the wrong half first: Mercury's
news arrives largely AS EMAIL, and `_flatten_source_text` puts newsletter
bodies in the same prompt as the articles, so an offending paragraph is at
least as likely to be in a newsletter. The ladder now has two rungs — without
the news, then without the inbox as well — each DISABLING its segments rather
than emptying them, and each recorded in `degraded_sources` so the manifest
says the briefing is not the one that was configured.

## 2026-09-15 — The dedup premise was right; the unit was wrong

The 2026-09-14 review found cross-source dedup firing 8 times in 5,590
stories and concluded the premise was false — that Joel's sources simply do
not overlap, and the shared `Item` schema was built for an overlap that is
not there. That conclusion was wrong, and the owner's triage request is what
exposed it.

Clustering embeds a WHOLE newsletter. Morning Brew's Tuesday edition covers
the Fed, a chip ruling and two earnings calls; The Information's covers the
Fed and three different things. Both are "about" the Fed, and neither
embedding is: each is dominated by its own four-way mixture. **The unit being
compared was a bundle, not a story.**

Extract the items first and the same morning looks completely different. 49
newsletters produced 117 items across 96 subjects, ten of them carried by
more than one outlet — and the top subject, the AI development slowdown
debate, was carried by NINE. One morning's newsletters contain more
cross-source agreement than the clusterer found in 5,590 stories.

Worth generalising: a measurement can be correct and its interpretation
still be exactly backwards. "Dedup almost never fires" was true. "Therefore
these sources do not overlap" did not follow, and the cost of believing it
would have been deleting the feature that was about to start working.

**The cap was found by listening, and could not have been found any other
way.** The first live run put all ~60 merged subjects into the prompt. The
model dutifully mentioned nearly every one: twelve consecutive turns of
"PC-ALM offers a local alternative to backpropagation. Tau is a coding
agent. Telus warned customers." A list read aloud — the "concatenated, not
edited" failure ADR-003 exists to prevent. `rank()` had ordered them
correctly, which is exactly why ordering was not the fix: ordering a
firehose still gives you a firehose. Six subjects offered, at most four
discussed, the remainder counted out loud.

With the cap, the same inbox produced four stories with real disagreement —
one host asking whether a slowdown call is safety or positioning, another
pointing out the slowdown is already visible in the benchmarks. That
exchange was assembled from nine newsletters that each mentioned one piece
of it. Under the old shape it was "Mindstream covered several topics".

**Urgency had to be narrow, and running it proved why twice.** A first cut
matched "account restricted" and missed "your account HAS BEEN restricted" —
real subject lines put words in the middle. The same cut flagged a digest
whose body read "Cloudflare users saw unusual sign-in alerts": someone
else's incident, reported as news. Newsletters are now never urgent whatever
they say, which is the single biggest source of false positives here, and
false positives are the expensive direction — the segment's whole value is
that when it says something needs attention, it does.

- **The injection fence does not stop impersonation.** It stops the model
  OBEYING untrusted text. A newsletter body containing this pipeline's own
  block delimiters forged a second outlet inside its own fenced block, and
  outlet count is both the ranking signal and the sentence the hosts are
  told to say. `_strip_fence_markers` had already made this argument about
  the fence's own tokens; every new delimiter needs the same treatment.
- **Naive vs aware datetimes have now cost this project three times.**
  `clustering._as_utc`, `script_chain._owed_replies`, and
  `newsletter_items.latest`. Gmail returns a NAIVE datetime for a `-0000`
  Date header. Any `max()`, `sorted()` or comparison over timestamps from
  more than one source needs `_aware()` first.
- **A cap and an ordering are different things.** `rank()` ordered 60
  newsletter subjects perfectly and the segment was still unlistenable,
  because everything ordered still reached the prompt. Ordering a firehose
  gives you an ordered firehose.

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
