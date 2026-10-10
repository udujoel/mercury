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

## 2026-09-20 — The newsletter segment never ran. Not once.

#51 merged on 2026-09-16. Between then and today, sixteen briefings
generated and every one of them reported success. The newsletter segment
ran **zero times**. `gh run list` shows no failures; the run logs contain no
line about newsletters at all, which is exactly what a morning with no
newsletters looks like.

**The cause.** The extractor was fed `selection["emails"]`.
`select_for_briefing` caps emails at six, and `score_emails` sorts
newsletters LAST because they are `Priority.LOW` — and they are LOW
precisely because they were never meant to be read out one at a time. On
2026-09-20 the inbox held 21 emails, 13 of them newsletters; the eight
ordinary ones filled all six slots and the extractor received an empty
list. I wrote a comment defending that choice — "those are the ones the
segment can actually talk about" — and it was exactly backwards. Selection
bounds what is read out INDIVIDUALLY. This segment summarises newsletters
as a GROUP and already had its own cap further down, `take_spoken`. Capping
twice, once by the wrong unit, produced nothing.

**Why it survived four days: it is intermittent.** On an inbox that is
mostly newsletters, some survive the cap. A spot check an hour before
writing this passed 5 of 11 through and looked fine. The composition of the
inbox decided whether the feature existed, so no single observation could
settle it.

**This is the fourth time.** `n_stories=0`, `music_set`, `topic_centroid`,
and now this — each a finished, tested feature that ran zero times in
production. CLAUDE.md has carried the advice since the first one: "when a
phase is marked done, check what CALLS the new code." I wrote the roadmap
entry repeating that warning the same week I committed this.

So the advice is not enough, because it is advice to a person who remembers
to follow it. `app/pipeline/census.py` runs the check on every briefing: it
records what each stage took in and gave back, logs one census line, and
warns on any stage that had input and produced nothing. `newsletters 13->0`
would have been on the first run's log four days ago. It never fails a run
— rule 7 applies to instrumentation too — and it emits a `::warning::`
annotation so the finding is on the run page rather than inside four
thousand lines of pip output. The watchdog's own header already records why
that matters: the 2026-08-27 failure "was found days later by reading gh
run list".

**Generalising it.** The bug class is not "someone forgot to call the
function". It is that a stage producing nothing is indistinguishable from a
stage with nothing to do, and every log in this pipeline was written to
describe work done rather than work skipped. Any stage that can legitimately
be empty now says so explicitly (`may_be_empty`), which forces the question
to be answered once, in code, instead of never.

**Also found, and NOT changed.** Every run 401s against ElevenLabs
(quota_exceeded, 5 credits remaining) and fails over to Google on the first
turn — so today's briefings are wholly Google-voiced, consistently, not
switching partway. Roadmap task 29 proposed pinning the workflow to Google;
that is withdrawn, because the owner asked for ElevenLabs to stay the
default and be restored when the quota resets, and pinning would prevent
exactly that. What remains worth doing is recording the failover where the
phone can see it.

## 2026-09-20 — what eight review findings said about the fix (PR #53)

The wiring fix was right and the way it was joined on was not, twice over.
Both are worth recording because neither is visible in a diff of the file
that changed.

**Widening an input is not the same as removing a filter.** The extractor
was starved by `select_for_briefing`'s six-email cap, so it was given the
whole post-mute inbox instead. But the list it replaced descended from
`inbox_emails`, which has already had the newsletters absorbed into world
stories subtracted. The fix escaped the cap and the dedup in one move, and
the two blocks are rendered independently — so one acquisition would be
covered in the world segment and again as "two of your newsletters led with
this". The correct input was `inbox_emails` all along: same escape from the
cap, subtraction intact.

Measured before accepting it, because the counter-argument is this module's
own thesis — an issue is a bundle, and dropping a whole absorbed issue also
drops the items in it that duplicate nothing. On a real morning: 12
newsletters, 4 promoted world stories, **0 absorbed**. The cost was not
measurable that day, and the gate is a stated acceptance criterion.

**A fake that answers nothing certifies nothing.** `FakeLLMProvider` had no
branch for the extraction prompt, so it fell through to `{}` and every
offline briefing produced an empty newsletter segment. That included
`test_the_story_is_offered_to_exactly_one_segment_of_the_briefing`, which
spans the newsletter block to the world block specifically to catch the
double coverage above. It was green throughout and proved nothing. Adding
the branch made it fail on the old wiring and pass on the new.

This is a sharper version of "a green suite certified a feature the product
had never run". There the caller was missing; here the caller existed and
the *fake* was the thing that never exercised it. A fake's `else: return {}`
branch is a silent test-coverage hole with no symptom.

**The census missed the runs it exists for.** `report()` sat after TTS and
the digest on the happy path, so a run where every turn failed synthesis —
`EmptyBriefingError` through the middle of the pipeline — was the one run
with no census line at all. It is a context manager now and reports in a
`finally`. Instrumentation for the bad morning has to outlive the exception,
which sounds obvious written down and was not obvious while writing it.

**Two stages measured the wrong quantity.** `desk` compared the digest
against every cluster including personal mail, so a day with no RSS and
fifteen ordinary emails would have been reported as starved — the false
positive that trains people to ignore the true one, in the very module
written to stop alarms being ignored. `script` summed two unrelated
ingestion counts. Both now compare like with like:

```
run census: newsletters 12->6  articles 157->4  inbox 13->1  script 11->56  desk 156->156
```

**MAX_TOKENS is not a bad roll.** It was left out of
`ContentBlocked.DETERMINISTIC` with a comment calling it "a bad roll rather
than a verdict". It is not: an answer that overflowed the output window
overflows again on an identical prompt. The retry bought five attempts at
the largest prompt in the pipeline and 22 seconds of backoff out of a
90-second budget.

**And the comment above the fix still argued for the bug.** Verbatim the
reasoning this log had already recorded as "exactly backwards", left sitting
above the corrected line. Correcting code and leaving the comment that
justified the old behaviour is how a fix gets reverted by the next reader.


## 2026-09-20 — the newsletter segment has now run in production, once

Merged #53 and dispatched a real run rather than trusting the merge. Run
35534389198, `completed/success`:

```
newsletters: 11 issue(s) handed to the extractor
newsletters: 11 issue(s) yielded 20 item(s) across 20 subject(s)
newsletters: 6 subject(s) spoken, 14 held back, 6 researched
stored briefing 2026-09-20-2106 in R2 (3,400,646 bytes) + manifest
run census: newsletters 11->6  articles 157->4  inbox 12->1  script 11->48  desk 154->154
```

Fetched back through the deployed Worker, HTTP 200: 7.2 minutes, 48 turns,
`degraded_sources: []`, and the inbox segment opens "Your inbox is dominated
by artificial intelligence, revealing a fascinating tension between rapid
innovation and rising apprehension" — then the second turn challenges it
rather than agreeing, which is rule 2. Compare the 05:50 run the same
morning, which spent ten of its inbox turns reading out one contract email.

**The evidence that settled the diagnosis.** `gh run view 35490104809 --log`
for the 05:50 briefing: 1,430 lines, and no `newsletters:` line anywhere in
it, while `app.pipeline.digest` in the same run counted **13 newsletter(s)**.
The extractor was handed an empty list and returned before reaching its log
call. The same absence holds across five runs sampled from 09-19 and 09-20,
all `completed/success`.

That log also settled the cost question on the fix: `absorbing 0 email(s);
0 further email(s) collapsed`. On the morning that failed, `inbox_emails`
and `emails` were the same list, so feeding the extractor the deduplicated
one would have cost nothing on the exact run this was about.

**What the census would have said on day one**, had it existed on 09-16:

```
run census: newsletters 13->0  articles 146->4  inbox 21->6  script 11->34  desk 137->137
STARVED STAGE: newsletters received 13 item(s) and produced nothing.
```

Four days and sixteen briefings earlier.

**Bot reviews are not review.** Kilo's check went red on this PR with
"Review failed: Assistant request was rate limited", and CodeRabbit posted
"Review rate limited". Neither produced a finding. Every one of the eight
real findings came from running `/code-review` deliberately — which is what
CLAUDE.md step 4 says and why it says it.


## 2026-09-20 — the census caught a starved stage and blamed the wrong thing

Run 35536697033 failed. The census did exactly what it was built to do,
first time out, and then misled the reader:

```
run census: newsletters 14->0  articles 154->4  inbox 15->6
STARVED STAGE: newsletters received 14 item(s) and produced nothing.
  This is the shape of every 'built but never called' bug in this
  project's history — check what actually reaches it
```

The stage was wired correctly. The real cause was four lines up:

```
google.api_core.exceptions.ResourceExhausted: 429 Your project has
exceeded its monthly spending cap.
```

**The Gemini project hit its monthly spend cap.** Nothing in the pipeline
can degrade around that — the LLM is not one of the sources rule 7 covers,
it is the thing that writes the script — so the run fails and the phone
keeps serving yesterday's briefings from R2. Only the owner can raise it,
in AI Studio. Worth knowing when planning verification: generating a real
briefing costs real quota, four scheduled runs a day already spend it, and
this session dispatched two extra verification runs on top.

The lesson worth keeping is about the instrument, not the cap. **An alarm
that names the wrong cause is only marginally better than no alarm.** It
spends the reader's afternoon on a bug that is not there, and that
afternoon is exactly what this module was written to save. A census exists
to shorten the distance between a symptom and its cause; one that
confidently points the wrong way lengthens it.

`report()` now takes the exception unwinding the run — `__exit__` already
had it — and distinguishes three cases:

- **an exception in flight** → "the run is failing with X; probably a
  consequence rather than a wiring defect"
- **`degraded` non-empty** → names the sources that failed. Passed by
  REFERENCE from `generate_briefing`, so later appends are visible.
- **neither** → the original alarm, and it keeps its full force, because
  a stage that produced nothing while everything upstream was healthy is
  still the shape of all four historical bugs.

The run-summary panel is written only in the third case. On a run that is
already red with a traceback, a second panel saying a stage produced
nothing invites precisely the wrong diagnosis.

One smaller thing: `google.api_core` prepends its own status code to the
message it was constructed with, so the error stringified as "429 429 Your
project...". Cosmetic, but this is a line someone reads in a hurry while
deciding whether they are looking at a wiring bug, and a stutter in it is
one more thing to rule out. The dedup is restricted to numeric codes —
a general "same word twice" rule would rewrite "boom boom baz", and
silently editing an error message is a worse bug than the stutter.


## 2026-09-20 — relaying is the dangerous half, not obeying

Task 32. The injection fence has always stopped the model OBEYING an email.
It does nothing about the model faithfully RELAYING one, and the live
example is not a jailbreak — it is the system working exactly as designed:

```
2026-09-13-1835: "It advises changing your password... This is a critical
security step to protect your account."
```

The model did not obey the email. It reported it, which is what the prompt
asks for. What makes that dangerous is the medium: audio strips the sender
address, the headers and the link — everything a reader uses to judge a
phishing mail — and replaces them with two trusted voices calling the
instruction critical.

Three things compound it. The selection is attacker-controlled
(`classify_priority` gives HIGH plus `requires_response` to anything
addressed to the listener containing a "?"). Task 27, merged hours earlier
today, widened it deliberately — account and security mail now escape the
newsletter floor — so the path from "a stranger sends a well-worded email"
to "the hosts read it as urgent" got SHORTER today. And `check_grounding`
scores a phishing claim as MORE grounded, not less, because the email is
the source data. A test pins that inversion, because it is the argument for
a separate gate rather than a tweak to that one.

**The thing the task description missed.** `total_issues` treats every
finding as one point, which is right for a flat turn and wrong for this: a
revision that dropped the phishing endorsement and picked up two clumsy
sentences scored worse and would have been discarded. Security is not a
quality dimension to be traded against pacing. The relay count is now
decided before the score and overrides it, and a relay surviving both draft
and revision is logged at ERROR.

**Two tests here passed vacuously before they passed honestly**, and it is
worth writing down how. `_run_critique_and_revise` validates a revision
against the word budget and the expected segment list BEFORE comparing
quality, so the first cut — a one-turn fixture with the default budget —
had its revision rejected upstream and returned the draft. The assertion
"the draft is kept" was satisfied for entirely the wrong reason. Mutation
testing caught it: removing the override changed nothing. A test whose
expected value is also its failure mode cannot tell you which one it saw.


## 2026-09-20 — the ranker had no reader to be connected to

Task 30, the third of the four "built and never called" bugs. The roadmap's
diagnosis was right and incomplete: `score_stories` accepts a
`topic_centroid` and no caller passes one, so `interest` is the constant
0.5 and its 0.20 weight cancels. What the entry did not say is WHY nobody
passed one — there was no setting. Nowhere in `UserSettings`, nothing on
the phone, no environment variable. The parameter was waiting on a feature
that had never been built, which is a different failure from a caller that
forgot, and it explains how it survived two phases.

So the fix is a settings surface plus the wiring, and the honest state is
that **the setting is still empty in production**. Nothing changes until
someone fills it in. To stop that becoming the same bug in a new place,
`_topic_centroid` logs on every run when the term is inactive.

**The test file reproduced the bug it was testing.** Seven tests, all
green, all calling `_build_world_stories` directly — and deleting
`interests=interests` from `generate_briefing`'s call to it left every one
of them passing. The parameter was never wrong; the CALLER never passed
it, and the tests entered below the caller.

Three sessions running, the same shape: #53's fake returning `{}`, #56's
hand-built `RunCensus`, now this. It is narrow enough to name.

**When a bug is "nobody called X", a test that calls X cannot detect it.**
The test has to start where the omission lives, which is one level up from
the thing that looks broken.

Worth keeping about the ranking itself: with 153 of 160 stories
single-outlet the cluster term is near-constant too, so the world segment
really was `0.12 x cluster + 0.08 x recency`. The 09-13 evidence — the two
stories that reached the audio were the two most recently published of
209, and "Ask HN: how can I browse HN in dark mode?" beat a Russian strike
on a Ukrainian train by six minutes — is a recency sort behaving exactly
as written.


## 2026-09-20 — two runs failed and nothing said so, for two hours

Checked the run history again at 22:36 UTC and found the state worse than
the earlier entry recorded:

```
2026-09-20T20:48:09Z  failure
2026-09-20T20:44:42Z  failure
2026-09-20T20:05:04Z  success      <- last good briefing, 2.5h earlier
...
watchdog, most recent:  2026-09-20T11:51:31Z   <- eleven hours earlier
```

The Gemini spend cap is the proximate cause and only the owner can lift
it. The interesting failure is the second one: **nothing reported that
generation had stopped.** `app.cli check` would have passed — a briefing
for today exists and is playable — and the watchdog, which runs only at
05:52 and 06:56 UTC, was not going to look again until morning.

Generation runs FOUR times a day. One of the four had a watchdog.

So task 31's two halves, and only one of them is done.

**Done, on the third attempt: `app.cli freshness`.** The first two cuts
were inert, and the reason is the same mistake in two costumes.

Cut one bolted `--max-age-hours` onto `check`. But `check` is DAY-SCOPED
— it resolves today's id — and a briefing filed under today can never be
older than the time since local midnight: at most 6.9 hours at the 05:52
checkpoint. Any floor loose enough to survive a healthy morning was
mathematically unreachable. Worse, on the morning after the 09-20 stall
there was no briefing for the new day at all, so `check` returned 1 and
the floor was never even evaluated.

So the flag was dead twice over, in the change written to stop things
being dead. `freshness` is its own command: newest briefing ANYWHERE, no
date to resolve, exit 5. It runs as a second step of the existing
watchdog job, so it costs no extra Actions minutes — which matters,
because this repo is priced at ~1,950-2,000 charged minutes against a
2,000 cap and the same review notes that when they run out the watchdog
stops too.

**Withdrawn, and this is the part worth recording.** The first cut also
added watchdog crons at 13:42, 19:42 and 01:42 UTC. Every one of them
would have stood down. `briefing-watchdog.yml` has a guard that exits 0
for any SCHEDULED run whose local hour is outside 03:00-12:00, written
for the morning backstops and correct for them — and a scheduled run
never carries a `briefing_id`, so it catches every entry. A sweep of all
598 IANA zones found NO timezone in which all three of those crons run;
the three-way intersection of passing UTC offsets is empty.

So the change titled "a watchdog for every slot" shipped a watchdog for
the same one slot as before, silently, green. **Built and never called,
in the commit written to detect built-and-never-called.** Five
independent review dimensions found it; I had not.

The lesson is narrower than "test your workflows". The guard was correct
code that encoded an assumption about WHEN it would run, and I added
callers at other times without reading it. A precondition living in a
guard is invisible to the person adding a caller unless something
asserts the coupling — which is the same shape as the Worker's
`SETTINGS_FIELDS` whitelist two PRs ago.

**And one about the tooling.** Mid-review a mutation sweep reported five
kills and then a restored tree with four failures. The code was right;
`__pycache__` was not. Restoring a file with `cp` can leave bytecode
whose mtime and size still satisfy Python's staleness check, so the
interpreter runs the mutant against the restored source. Every mutation
result in that sweep was untrustworthy. Clear `__pycache__` between
mutations, or the sweep measures the cache.

Worth adding to the same list: two review agents mutating one working
tree at once produced a `SyntaxError` neither of them wrote. Give a
mutation-testing agent a copy, not the tree.

**Still open: the `dayState` half.** A 00:12 briefing is 5.7 hours old at
the 05:52 checkpoint, so it passes a 7-hour floor while not covering the
morning at all. That is a DIFFERENT question — "does this cover the
morning?" rather than "has generation stopped?" — and conflating them is
how one number ends up answering two questions, which is the mistake
three separate reviews caught this session. It belongs in the Worker.

Worth noting for anyone verifying this locally: R2 credentials live only
in GitHub secrets, so `STORAGE_BACKEND=r2 python -m app.cli check` exits
2 on a developer machine. That is the correct "storage is not configured"
branch, not a failure of the check.


---

## 2026-09-21 - Jev triages the inbox, and two silent failures on the way

Email triage moved from `classify_priority` to a TypeSafe Jev judgment
(ADR-014). The reasoning is in the ADR. What is here is what the repo cannot
tell you afterwards.

**The Score scale is a level index, not a fraction.** Four criteria means
0.0-3.0. Measured against real mail: a bank account-restriction warning 3.00,
a colleague's dated request 1.99, a "Ready to upgrade?" marketing mail 0.00.
The first draft put the HIGH cut at 2.5 on the assumption of a 0-1 scale,
which demotes every "a person is waiting" email to MEDIUM. Nothing in a
fixture could have caught this: a fixture written under the wrong assumption
agrees with itself. The cut is 1.5.

**The key was configured and never used.** `build_judgment_provider` checks
`settings.typesafe_api_key`, which pydantic-settings reads from
`backend/.env`. The SDK's client reads `os.environ`, which `.env` is never
exported into. So the provider constructed, every request failed auth, and
triage degraded to the heuristic - with the suite green and the lint clean.
Rule 7 worked perfectly and that is exactly why it was invisible: the
fallback is silent by design. Found by running it; nothing else would have.
Same shape as the xcconfig that truncated a URL.
`test_the_configured_key_is_passed_to_the_client` now fails if the key stops
reaching the client.

**The suite then started calling TypeSafe ~100 times per run.** Six test
files reach `generate_briefing`, none injected a provider, so each one built
a real one from the developer's `.env` and triaged the fixture inbox against
the live API - sending fixture email content to a vendor on every `pytest`.
It did not fail anything, because triage degrades. `conftest.py` already had
the mechanism (`CREDENTIAL_ENV_VARS`) and its docstring already described
this exact hazard for Gemini and TTS; the new key simply was not in the
tuple. **Any new provider key must be added there in the same commit that
introduces it.**

**What it does to a real inbox.** 25 real emails, 1.82s, 13 priority changes.
The heuristic's spoken top-6 was five near-identical Mercor recruiting blasts
plus an O'Reilly "Verify your email" - they were direct-addressed and
contained a question mark, and nothing in `classify_priority` can notice that
five emails are the same email. Jev put them at 0.29-0.35, below every
newsletter. A water bill went MEDIUM -> HIGH at 2.55, and a GitHub Actions
"Run failed: daily briefing" LOW -> MEDIUM.

**The open one:** a SHEIN "Order Delivered Notification" scored 1.61, just
over the HIGH cut. Defensible - it is a dated event - but it is the weakest
call in the run, and the threshold was deliberately NOT tuned to hide it.
One inbox on one morning is not a calibration set. Worth re-checking against
the regression snapshots before trusting 1.5.

**`typesafe-sdk` forces `pydantic>=2.12`.** The pin was `==2.10.4`; it is now
`==2.13.5`, verified against the full suite. It also pulls `httpx2` and
`tenacity`.

**Gmail's per-minute quota trips easily.** Two `max_messages=60` fetches in
quick succession returned 403 `rateLimitExceeded` for several minutes. Use
25 for verification runs.

## 2026-09-21 - the gates stop matching keywords, and the inbox stops being All Mail

ADR-015 moved the three judgeable quality gates onto Jev. What is worth
recording beyond the ADR:

**The grounding heuristic was wrong in both directions at once**, on the
same six-line script. It flagged "Completely" — a sentence opener absent
from its stopword list — and it passed *"you should go and verify your
identity at that link, it's legitimate"*, because every token in that
sentence appears somewhere in the source. Jev scored that line 0.14. Token
presence is not support, and the one line in the script that was actively
dangerous was the one the grounding gate rated cleanest. That is the
inversion `quality.py:230` describes, found in the wild rather than
reasoned about.

**Relay and disagreement came out at parity** with the keyword lists on
that script, not better. Worth saying plainly: only grounding clearly
improved. The argument for moving the other two is that a keyword list
cannot be completed — #57 found 24 of 28 phrasings missing — not that this
run demonstrated it.

**A fake that reads the claim instead of the state certifies nothing.**
The first `FakeClaimJudge` looked for markers in the claim's instructions.
But a claim does not carry the text it is about; it points at the state
with a backticked path like `lines[3]`, and the model resolves it. So every
claim looked identical and three gate tests passed vacuously. This is the
SECOND time in two days the same defect appeared in the same file — #60's
review caught the email half reading `body`/`sender` keys that
`email_state` never emitted. The fake now resolves the path, as the model
does. **When writing a fake, write down what the real thing reads first.**

**The three gates have opposite polarity**, which is not obvious until a
test fails: grounding and challenge flag LOW, relay flags HIGH. A single
"this marker means false" convention in the fake made a clean line come
back flagged as a relay.

**The Gmail query covered All Mail, not the inbox.** `newer_than:1d
-category:promotions -category:social` with no `in:inbox` reaches
everything the account holds bar spam and trash. Measured live: 68 messages
under the old query, 67 under `in:inbox` — and the one it dropped was
labelled `SENT`. It was a forward Joel had sent himself, and the triage run
earlier the same day had put it in the spoken top six at consequence 1.81.
The briefing was reading his own outgoing mail back to him.

Spam was never the leak: `users.messages.list` takes `includeSpamTrash` and
it defaults to false. The same check found 5 spam/trash messages in the
window that the default had been quietly excluding all along. It is now
passed explicitly, so the guarantee is visible at the call site rather than
resting on a default.

## 2026-09-22 - clustering gets a second reader

ADR-016. Beyond the ADR:

**Jev is not marginal on this question.** Seven pairs, 0.94-0.96 for same
event and 0.01-0.04 for everything else. The SpaceX / Rocket Lab pair that
sits at cosine 0.6908 — the tightest non-merge this repo has measured, and
the reason 0.78 was chosen — comes back at 0.01. Embedding similarity had
0.089 of margin there; the semantic reading has 0.49.

**The threshold is still unproven and this does not prove it.** What the
verifier checks is the merges the threshold MADE. Whether 0.78 misses
merges it should make is the other half, still open, and deliberately not
acted on because that is the direction where a mistake is expensive
(ADR-003).

**A test fixture gave every item the same id.** `Item.model_post_init`
seeds the id from (source_name, url or title, timestamp), and the helper
passed a fixed url. So every item in a cluster collided, the
non-canonical filter emptied, and three tests asserted on zero claims
while reading as though they covered the split path. Worth remembering
when writing any Item fixture: **vary the url, or the ids collide
silently.** The helper now derives it from the title.

**And the first version of the summary log lied.** It computed "stories
affected" as `len(set(out ids)) - len(stories) + 1`, which reports 2 when
one cluster loses one member. Counted directly now. #56 is the standing
lesson and it caught me anyway — the number looked like a fact.

## 2026-09-22 - the personal ranking term was inert, and nothing said so

ADR-017. The finding is worth separating from the change.

**On a realistic input the lexical `personal` term scored every story
identically.** Following "TypeSafe", one calendar entry with
`priya@aveiro.test`, four RSS stories: all four came out at 0.226. Not
"ranked badly" — ranked not at all, with the order falling to sort
stability. The attendee match is `name in haystack` against the full email
address, and the followed entity was "TypeSafe" while the story was about
Jev. Both misses are structural and neither shows up as anything but a 0.0.

Judged, the same four ordered 0.92 / 0.74 / 0.09 / 0.04 — the funding round
of the company whose employee is on the calendar first, the followed
company's product second.

Review of #58 hardened this term against false positives and could not
touch the false negatives, because a substring match has no way to know
that Jev is TypeSafe's model or that priya@aveiro.test works at Aveiro
Labs. The fix that made it safe also made it silent.

**`Item.id` is not a key, and this is the THIRD time it has bitten.** The
relevance map was keyed on `Story.cluster_id`. `clustering._singleton` sets
`cluster_id = f"s{item.id}"`, so two unembeddable emails sharing a sender,
subject and timestamp produce two Stories with ONE cluster_id — the map
held one entry for two stories and the second verdict overwrote the first.
Measured: a story judged 0.95 took a colliding story's 0.03 and both ranked
as though neither mattered.

Same defect as `cluster_verify` on #62, three commits earlier, in a
different module. And `_build_world_stories` had already switched its
`absorbed` set to object identity for the same reason before either.

**Standing rule: never key a dict, set or lookup on `Item.id`, or on
anything derived from it, when the values must be distinct per object.**
Use `id(obj)`. `Item.id` is a content hash seeded on (source_name,
url-or-title, timestamp), every Gmail item is built with `url=None`, and a
live RSS run recorded 187 items with 186 distinct ids. It is a
deduplication hint, not an identity.

**`or` versus `is not None` decided whether the change could work at all.**
Written as `personal = judged.get(cid) or lexical`, a judged 0.0 — a real
"not about your day" — is falsy and falls through to the branch that gives
any email 0.5. The judgment could then only ever raise a score. Caught by
writing the test before believing the code.

## 2026-09-22 - the one place where the source data is model output

ADR-018. The gap is the part worth remembering.

`check_grounding` measures the script against `source_data`. For email and
calendar that block is raw ingested text. For newsletters it is the
headlines and gists `extract_items` GENERATED — so a hallucination there
does not get flagged, it becomes the thing everything else is flagged
against. The gate was structurally blind to the only source it did not
ingest.

Nothing about that is visible from either module alone. `extract_items`
looks like an extractor and `_flatten_source_text` looks like a formatter;
the problem is the edge between them.

**The design question turned out to be a false choice.** Verifying a gist
needs the passage it came from, and the passage is rarely in the first few
hundred characters — so it looked like "send whole newsletters to the
vendor, or leave the gap open". It is neither: the body is already in
memory locally, so code finds the span by token overlap and only the span
is sent. 700 characters out of a 6723-character issue, with the passage in
it. Retrieval in code, judgment in the model.

**Threshold 0.2, not 0.5**, because the two errors are not symmetric. A
false drop silently loses a real story — the exact failure this module
exists to prevent. A false keep costs only the behaviour that shipped
before. So uncertainty keeps the item and only a confident rejection drops
it.

## 2026-09-22 - two mistakes made twice, in the same week

Both of these were found by review, fixed, and then made again in code
written AFTER the fix. Recording them as rules rather than as four more war
stories.

**Wrap the work, not the await.** A `try` around only the network call
leaves the state building and the response handling outside it. A provider
that returns the wrong SHAPE rather than raising — a list, None, anything
without `.get` — then takes an AttributeError out of a function whose
docstring promises it never raises. Happened in `cluster_verify._apply`
(#62) and again in `newsletter_items.verify_extracted` (#64). Rule 7 is
about what the briefing survives, and "the provider answered strangely" has
to degrade exactly the way "the provider timed out" does. **When a function
documents that it never raises, put everything fallible inside the try and
say so in a comment.**

**Never key on `Item.id`.** Already written up above for the relevance map.
It also reached `cluster_verify._apply` (#62) and `NewsletterItem.email_id`,
which `_parse` now avoids by keeping the index pairing. Four places in one
codebase. It is a content hash seeded on (source_name, url-or-title,
timestamp), every Gmail item has `url=None`, and a live RSS run recorded 187
items with 186 distinct ids.

The common shape is worth naming: **both are invisible on the happy path and
in every fixture.** A green suite cannot distinguish "wrapped correctly"
from "never had a malformed response", or "keyed correctly" from "the
fixture gave everything distinct ids". Both need a test that constructs the
bad case on purpose.

## 2026-09-22 - the threshold gets an instrument, not a verdict

ADR-016's amendment. The part worth keeping is what the measuring
decisions turned out to depend on.

**The band's units are the threshold's units, and that was not obvious.**
The first instinct is to report the closest pair of items across two
clusters — it is the most alarming number and the easiest to compute. It is
also unusable. `cluster_items` runs `linkage="average"`, so 0.78 cuts on the
MEAN pairwise cosine between clusters; a log full of max-linkage numbers
cannot be read back as "0.78 should be 0.70" because it is denominated in
something else. Average linkage also hands over a free guarantee: the
algorithm stops merging exactly when every surviving cluster pair is further
apart than the threshold, so nothing the probe finds can be at or above it.
The half-open band is belt and braces for input that did not come from
`cluster_items`.

**The floor was chosen by one pair, on purpose.** 0.65 exists because
SpaceX Starlink against Rocket Lab Electron sits at 0.6908. That is the
tightest non-merge this repo has measured and the whole reason 0.78 was
picked, so a band that could not reach it would be collecting evidence with
the deciding case filtered out — which is worse than collecting none,
because an empty log would read as "nothing sits near the threshold" rather
than "I did not look there".

**The quadratic cost was in Python, not in the arithmetic.** 400 stories is
79,800 pairs, and reading each block out of the similarity matrix with
`np.ix_` cost 473ms — half a second of a 90-second briefing, spent on a
diagnostic nobody is waiting for. As one matmul (`assignᵀ · S · assign`,
divided by the outer product of the cluster sizes) the same answer takes
8.6ms, and 19ms at 800 stories. Worth remembering as a shape: the numpy was
never the problem, the per-pair Python round trip into numpy was.

**And the float32 that came with it.** Summing float32 similarities across a
cluster and rounding leaves the result a float32, and the nearest float32 to
0.69 widens to 0.6899999976158142. A tuning log whose numbers read as
rounding bugs is a tuning log nobody trusts. The similarity matrix stays
float32 (clustering's own precision); the averaging is float64.

**The first payload shipped the cosine to the model, and that was a
measurement bug, not a privacy one.** It read as helpful context. It is
the quantity under evaluation: told a pair scores 0.77, a model leans
"same event", and the inflation lands at the TOP of the band, where a
disagreement counts most toward moving the threshold. An instrument that
tells the sensor what it expects to read is not an instrument. The
similarity now appears only in the log, which also means the tests assert
on the log line rather than the request — the right place anyway, since
the log is the deliverable.

**Ordering is the only real defence against starving the other four calls,
so it is a test.** The probe is the fifth call against one shared
`judgment_briefing_budget_seconds`. It runs last — after triage, cluster
verification, personal relevance and the quality gates, and after the script
chain, but before `_scrub_email_bodies`, because the payload needs the
bodies. If the budget is gone its claims come back `None` and the briefing
is bit-for-bit what it would have been. A comment saying "runs last" is
exactly the kind of claim this repo has watched rot four times, so
`_OrderRecordingJudge` asserts the family order in a real `generate_briefing`
run instead.

**The measured cost of the fifth call is usually zero.** On the cross-source
fixture corpus at its calibrated threshold the band is EMPTY — the two
controls are nowhere near it — so the probe makes no request at all. The
cost tracks how crowded the band is, not how large the morning was. That is
also why the ordering test has to widen the band deliberately: at the
default there is nothing to order.

**Review found the two halves of ADR-016 talking past each other (#65).**
The probe receives the story list AFTER `verify_stories` has run, so an
ejected member and its narrowed parent arrive as two ordinary stories —
and their average linkage lands inside the band often enough to matter,
because the ejection turned on a member-vs-canonical judgment that says
nothing about where the cluster average sits once that member is gone.
Reproduced at cosine 0.6696.

The cost is not the wasted claim. The two directions ask in DIFFERENT
framings — member against canonical there, canonical against canonical
here — so they can disagree, and then the tuning log carries a `MISSED`
merge arguing to LOWER 0.78 and re-make a merge this same run rejected.
An entry pointing the wrong way is worse than no entry, and the direction
it points is ADR-003's expensive one. Parent-to-child pairs are skipped
now; **siblings are not**, because two members ejected from one cluster
were each judged against the canonical and never against each other, so
whether they are the same event is exactly the open question the probe is
for. Both halves have a test — the easy over-correction here is to skip
every pair touching a split child, which would quietly narrow coverage on
precisely the mornings a cluster went wrong.

Worth generalising: **when a pipeline stage feeds a second stage that asks
the same question a different way, the second one needs to know what the
first already ruled on.** Neither module is wrong read alone. The defect
is at the seam, which is where #64's was too.

**And the answer key was being rebuilt rather than read.**
`answers.get(f"probe:{index}")` reconstructed the string `_pair_claim` had
built, coupling them by a repeated literal. Change one and every lookup
returns `None`, every pair counts as unjudged, and the summary line reads
exactly like a spent budget — a failure nobody investigates. It zips
against `claims` with `strict=True` now. `_apply` in the same file already
did it that way; the second function in a module is where the first one's
lesson goes missing.

**What this does not do, stated plainly because the file it lives in
splits clusters.** It merges nothing and returns `None`. The threshold is
no better proven today than it was yesterday; what exists now is the
instrument, and the reading takes a week of real mornings.

## 2026-09-22 - the briefing died in libxml2, not in Python

The 16:55 run failed with no traceback:

```
malloc_consolidate(): unaligned fastbin chunk detected
Aborted (core dumped)
##[error]Process completed with exit code 134
```

Exit 134 is SIGABRT. That is glibc detecting a corrupted heap, thirty
seconds in, at the end of RSS full-text extraction — no Python exception,
so nothing in the pipeline's own error handling could have seen it, and
rule 7 has nothing to degrade to when the interpreter is aborted under it.

**Cause.** `trafilatura.extract` parses with a MODULE-LEVEL lxml parser,
`trafilatura.utils.HTML_PARSER`. lxml parser instances are not thread-safe
— lxml's docs require one per thread. `rss._extract_full_text` called it
through `asyncio.to_thread` under a semaphore of 5, so five threads drove
one libxml2 parser context at once. Latent since #2.

**What made it findable was that it was NOT deterministic.** The same
commit, `88c7a25`, ran clean at 11:29 and aborted at 16:55. That ruled out
a code regression from the day's five merges and pointed straight at a
race. Worth remembering as a diagnostic: identical SHA, one pass and one
crash, means timing or input, not logic.

**It does not reproduce on macOS.** `malloc_consolidate` is a glibc check
and the allocator here is different; 12 threads x 80 malformed documents
survived four attempts locally. A native crash that only appears in CI is
not necessarily flaky infrastructure — check whether the check itself is
libc-specific before dismissing it.

**The fix is nearly free.** A single-worker executor for extraction, so
"one parse at a time" is structural rather than a rule to remember. Fetches
stay concurrent, which is where the time actually goes: a parse is ~4ms,
and serialising 30 of them costs 0.02s.

**Unrelated, and the more important operational fact:** every OTHER recent
failure — eleven consecutive runs across 2026-09-20 and 09-21 — was
`429 ResourceExhausted`, the Gemini monthly spend cap, not this. Those
stopped of their own accord and 09-22 ran clean three times before the
abort. When a run fails, read the signature before assuming it is the same
problem as last time.

## 2026-10-07 - the story page in Particle's shape, and a morning that says what it lacks

Three things landed together, and two of them were found rather than planned.

**Particle's story anatomy.** The desk already had Particle's feed shape
(thumbnail rows and one promoted lead). What it lacked was Particle's
signature: the publishers, shown prominently, on every story. The eyebrow
said "4 ARTICLES", which counted cluster members rather than outlets and
asked to be trusted. It is now a row of overlapping outlet marks plus
"REUTERS +3", and the story page follows Particle's order: picture, headline,
who says so (directly under the headline), a listen pill, the points on a
card of their own, why it matters as a ruled aside, and coverage rows that
open the publisher with the whole row as the target.

Particle's own help pages and design write-ups were blocked by the egress
proxy from this session, so the design rests on what search results
confirmed (bullet summaries, sources showcased directly under the summary
with big tap targets, a listen control) rather than on screenshots. Worth
checking against the real app before anyone treats it as a copy.

**Monograms, not logos, on purpose.** Favicons would mean a hotlinked fetch
per outlet per row from sites that refuse hotlinks often enough that
`LeadCard` already needs a fallback, and ADR-012 says the edition has one
colour. Forty outlets bringing forty brand reds would make the desk a
sticker sheet. The marks are ink on paper.

**The headlines were in the hosts' typeface.** `LeadCard` and the story page
both set headlines in `MercuryType.greeting`, the serif that ADR-012 reserves
for what a host says, and `DesignTokenTests` could not see it because it
checks tokens, not which token a call site borrows. A new `headline` token is
bold SF, which is both the rule and what Particle does. The lesson is the
same shape as the census: a test of the vocabulary does not test its use.

**Task 33 found a backend bug on the way.** Writing the phone's sentence for
`degraded_sources` meant mapping every label, and `calendar` turned out to be
both a SOURCE label from ingestion and a `Segment` value. `cli._only_refusals`
tested membership in the whole `Segment` enum, on a docstring that said the
two vocabularies never overlap. So a calendar fetch that failed on its own
was filed as a model refusal: exit 0, a log line blaming the model, and no
retry, for a briefing missing the user's day. It now tests against
`REFUSABLE_SEGMENTS`, the three segments the ladder can actually give up.
It needed the calendar to fail without Gmail, which a shared OAuth token
makes rare, and that is why nothing noticed.

**Four tests on main were a slow clock bomb.** `test_interests_reach_the_ranker`
pinned its fixtures to 2026-09-20 and let `score_stories` read the real
clock. Seventeen days later `exp(-hours / 12)` is about 1e-15 for every
fixture, the minutes between them round away, the scores tie and the sort
falls back to input order. Freezing the clock at the fixtures' `NOW` did not
fix it, which is the part worth remembering: `_article` stamps items at
`NOW.replace(minute=59 - age)`, AFTER `NOW`, so every age clamped to zero and
the stories tied for the opposite reason. The clock has to sit after the
newest fixture (07:00). A frozen clock is only correct if it is frozen at a
time the fixtures agree with.

**Backend CI was red for everyone, not for this PR.** `pip-audit` reported
fourteen new advisories in `pyjwt 2.13.0` on 2026-10-07, so every backend run
failed before a test executed. It goes to 2.15.0 here, because two of the
fourteen are fixed only there. Worth remembering: `pip-audit` gates the whole
job, so a pin that was clean yesterday can turn an unrelated PR red today,
and the failure reads as this PR's until you see that no test ran.

**Not compiled here.** The session had no Swift toolchain. The iOS changes
were written against the existing patterns and read adversarially, and the
`ios` CI job on the PR is the first build they get.

## 2026-10-10 - the ranker is handed its clock instead of reading one

Review of #68 asked for the structural version of its frozen-clock fixture,
and this is it. `score_stories` takes `now`, and `generate_briefing` takes one
too (None meaning the wall clock), reads it once and hands it down through
`_build_world_stories`. The benefit is injectability, not run-wide
consistency: `score_stories` already read the clock once per call, and
ingestion, the digest and stitching still read their own. The first draft
of this entry claimed a single clock for the whole run, and review of #69
caught that it was not true.

**Why `now` is optional in one place and required in the other.**
`score_stories` keeps `None` as "use the wall clock", because it is a public
function and a caller outside the pipeline should not have to know about run
clocks. `_build_world_stories` has exactly one production caller, so `now` is
a required keyword there: a default would have let every test that calls it
inherit the real date without saying so, which is the bug.

**A guard, not a convention.** The monkeypatch in #68 fixed one file, while
`test_relevance.py` had the same hazard and had papered over it with
`pytest.approx` in #66 (two calls a few microseconds apart decayed
differently). An instruction to "pass `now=` in tests" would have held until
the next new test file. So the wall-clock read now lives in
`ranking._wall_clock`, and `tests/conftest.py` makes it raise for every test.
One test swaps in a fixed clock to keep the `None` fallback covered. With the
clock pinned, the `approx` in `test_relevance.py` went back to `==`, and an
exact comparison there is now the stronger assertion it was meant to be.

**The guard only sees `now=None`.** Review of #69 pointed out that a caller
reading `datetime.now(UTC)` itself and passing it on gets straight past the
conftest guard, because the ranker is still handed a clock. Checked by
hand: a fresh `datetime.now(UTC)` in place of `run_started_at` survived the
whole suite, and one in place of `now=now` was caught only by the four
ordering tests, and only because their fixtures happen to be old by now.
On the day they were written it would have passed. So the forwarding tests
assert IDENTITY with the clock they passed, which an aware datetime from
anywhere else cannot satisfy. Deleting either argument outright fails
anyway, as a missing required keyword or on the guard.

**A naive `now` is taken as UTC.** Item timestamps already went through
`_aware`; an injectable clock that skipped it raised a TypeError on the
first subtraction, from inside the world segment.

**What still reads the real date.** Production runs rank against the wall
clock by design, and so does a fixture run that does not pass `now`. RSS
ingestion anchors its recency window on the fixture's newest entry
(`_fixture_snapshot_time`), so an offline run replayed days later filters by
the snapshot's time and ranks by today's, and every story's recency is then
~0. That predates this change; `now=` is now the way to fix it for a replay,
and whether `generate_briefing` should default to the snapshot time is a
separate decision.

**One test fails in the cloud sandbox and nowhere else.**
`test_net_guard.py::test_public_url_allowed` resolves `arstechnica.com`, and
the sandbox's DNS cannot. It failed identically before this change.

## 2026-10-10 - the default briefing id was the one date still read in UTC

`generate_briefing` stamped a missing `briefing_id` from the UTC instant
while every other date in the run (`_parse_briefing_date`, `cli._user_now`,
`cron._target_briefing_id`) is the user's local day. At 00:11 in Lisbon that
names yesterday, so a run would land on yesterday's stored briefing while
its calendar segment talks about today. That is the same bug as the
"Friday, August 21st" cold open, one function over.

**Fixed although nothing reaches it.** The CLI, the API and the cron job
all pass an id, so only tests hit the default. It was fixed anyway because
a default that disagrees with the rest of the codebase is the one a future
caller inherits without reading. That is how this project's five earlier
date bugs arrived.

**Why the zone helper lives in `generate.py`.** There are now four copies
of "resolve USER_TIMEZONE, fall back to UTC": `cli._user_now`,
`cron._user_tz`, `api.briefing._today_briefing_id` and `generate._user_tz`.
`ingestion/calendar.py` makes a fifth lookup with no fallback at all, so a
bad zone fails the calendar source outright. `generate._user_tz` was lifted
out of `_parse_briefing_date` so the id and the greeting cannot drift apart.
Importing `cron`'s copy would point the pipeline at the scheduler, which
calls it. Collapsing them into `app/core` is a separate change. The first
draft of this entry counted three copies and said only the CLI logs a bad
zone name. Both were wrong: cubic found the API's copy, `cron._user_tz` logs
too, and the silent copies were this one and the API's. Review of #70 caught
it, and this one now logs.

**A naive `now` had to be pinned for this to hold.** `astimezone()` reads
a naive datetime as the HOST's local time, while `ranking._aware` reads it
as UTC. Without normalising, a naive `now` would have named the day from one
instant and ranked from another, and the day named would differ between a
Lisbon laptop and a UTC runner. The test pins the process `TZ` to Tokyo so
the difference shows on any host. A test that relied on the machine's zone
would pass on CI whether or not the fix was there.

**Test dates chosen to bite.** 2026-03-29 is the night Europe's clocks go
forward, so Lisbon is already UTC+1 at 23:30Z. Los Angeles is the opposite
direction, so a fix that always added a day would fail it.

**What review of #70 found.** A misspelt USER_TIMEZONE fell back to UTC
without a word, which quietly reinstates the bug this entry is about. It now
logs an error. `_parse_briefing_date` also fell back to the wall clock for an
id that is not a date, so a replay at `now=` greeted whatever day it was
re-run on. It now takes the run's clock, as a required keyword like
`_build_world_stories`. Neither was reachable from production. One finding
was declined: the default id has no `-HHMM` suffix, so two default runs on
one day share an id. That is deliberate. The scheduler names briefings the
same bare way, and collision policy belongs to the caller: cron skips an
existing id and the CLI picks a free one. `generate_briefing` stores what
it is told.

**Two failures that were the sandbox, not the code.** Inside the Claude
Code Bash sandbox, `test_generate_e2e.py::test_generate_briefing_end_to_end_produces_audio_and_valid_manifest`
and `test_net_guard.py::test_public_url_allowed` fail (ffmpeg cannot use a
temp file, and there is no DNS). The first draft of this entry blamed the
ffmpeg failure on Homebrew's ffmpeg 9.0.2. That was wrong. Run outside the
sandbox, the full suite is 1044 passed. Confirm by running unsandboxed
before diagnosing either of these.

## Standing facts worth not rediscovering

- **The Worker has no CI deploy.** Merging is not shipping. A route on `main`
  stays 404 in production until someone runs `wrangler deploy`. This once left
  the deployed Worker months behind the repo, and the news desk looked like an
  unbuilt feature on the phone while every suite was green.
- **ElevenLabs' free tier is not a base for a CI job.** Library voices return
  402; datacenter IPs get free API access disabled entirely (401); and the
  account can simply run out of credits, which is what is happening now. Each
  failure degrades to Google and ships green.
- **A green suite has certified unrun code four times.** Phase 2's news half
  ran with a hardcoded `n_stories=0`; `music_set` was stripped by a Worker
  whitelist so the setting never once applied; `score_stories` takes a
  `topic_centroid` no caller passes; the newsletter segment ran zero times
  across sixteen briefings. When a phase is marked done, check what **calls**
  the new code — and check what the FAKE does with it, because a fake whose
  `else` branch returns `{}` gives a green end-to-end test of a code path
  that never executed. `app/pipeline/census.py` now runs this check on every
  briefing rather than leaving it to whoever remembers.
- **`follow_ups` is built from the literal string `"(none)"`**
  (`script_chain.py:1347`) — the same shape as `n_stories=0`, still open as
  task 24.
- **The Gemini project has a monthly SPEND CAP.** When it is reached every
  call returns `429 ResourceExhausted: Your project has exceeded its
  monthly spending cap`, and every briefing run fails outright — there is
  no degrading around the model that writes the script. Only the owner can
  raise it, at https://ai.studio/spend. Generating a briefing to verify a
  change costs real quota on top of the four scheduled runs a day, so
  prefer ONE live verification run.
- **Two suites run only by hand.** There is no iOS job and no Worker job in
  CI. The Worker test written specifically to stop the next `music_set`-class
  bug is itself run by nothing.
