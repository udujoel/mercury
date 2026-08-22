# Phase 2, week 9 — embeddings and story clustering

Owner surface: `app/providers/gemini_embeddings.py`, `app/providers/fake_embeddings.py`,
`app/pipeline/dedup.py`, `tests/fixtures/clustering_corpus.json`,
`tests/test_embeddings.py`, `tests/test_clustering_integration.py`,
`tests/test_dedup.py`. `app/pipeline/clustering.py` and `ranking.py` were
already written and correct — this work wires embeddings into them via
`build_stories`, it does not change them.

## What `build_stories` does

```python
async def build_stories(
    items: list[Item],
    *,
    embeddings: EmbeddingProvider,
    threshold: float | None = None,
) -> list[Story]: ...
```

1. Builds `item.embed_text` for every item, in order.
2. Calls `embeddings.embed(texts)` once (the live provider batches internally).
3. Refuses to guess if the returned vector count doesn't match the input
   count — logs and degrades instead of mis-attributing a vector to the
   wrong item.
4. Writes each vector back onto `item.embedding`, preserving order.
5. Delegates to the existing `cluster_items` (unmodified).
6. On **any** exception from the embedding call, logs and returns one
   singleton `Story` per item — rule 7, the briefing still ships.

## The corpus

`tests/fixtures/clustering_corpus.json` — 25 items, EMAIL and RSS mixed:

- **One event, four outlets, differently worded headlines**: Reuters (RSS,
  primary reporting), Stratechery (**EMAIL** newsletter), Benedict Evans
  (RSS), Platformer (RSS) — all covering "TechCorp acquires DataFlow for
  $2.3bn". Stratechery being EMAIL and the other three RSS makes this the
  same fixture that exercises the cross-source (newsletter + news article)
  case the brief called out separately — it didn't need a second, disjoint
  example.
- **Two superficially similar but genuinely distinct stories**: SpaceX's
  Starship 10th test flight vs Rocket Lab's Neutron debut test flight. Both
  share heavy surface vocabulary ("launches", "test flight", "reaches orbit
  successfully") but are unrelated events with different companies and
  products — the case that must NOT merge.
- **19 unrelated singletons**: work email thread (Priya/Sam), a city council
  notice, GitHub PR digest, sports, food, travel, tech, finance, fitness,
  book and HN items — enough negative space for spurious merges to actually
  show up if the threshold is wrong.

## FakeEmbeddingProvider — design and why plain hashing wasn't enough

The first version was a straightforward hashing-trick bag-of-words: hash
each non-stopword token into one of 256 dimensions with a deterministic
sign, sum, L2-normalise. It is deterministic and driven by input, but
**pairwise cosine similarity between genuinely related articles turned out
too low to be useful** — two articles covering the same acquisition share
maybe 5-8 of their 30+ unique words each, so cosine similarity landed around
0.2-0.3, indistinguishable from coincidental overlap between unrelated
items. Real embedding models don't have this problem because every output
dimension carries a semantic contribution from the whole sentence, not just
literal token identity — a sparse hash-of-tokens vector has no such thing.

The fix: weight **entity-like tokens** (a token whose original casing
carries a signal a lowercase bag-of-words throws away — an internal capital
letter as in `TechCorp`/`DataFlow`/`SpaceX`, an ALL-CAPS acronym, or a
digit) 5x over ordinary words before hashing. Two articles about the same
acquisition both say `TechCorp` and `DataFlow`; two articles about
different rocket launches do not share company or product names even
though they share generic verbs. This single change moved the offline
result from "unusable" to a clean, wide separation band (see below) without
adding randomness or canned output — it's still a pure function of the
input text.

## Offline results (FakeEmbeddingProvider — pytest)

Pairwise cosine similarity on the corpus (`app/embed_text` on `title +
body[:500]`), entity-weighted hash embedding:

| pair | cosine |
|---|---|
| Stratechery <-> Benedict Evans | 0.745 |
| Stratechery <-> Platformer | 0.742 |
| Reuters <-> Stratechery | 0.704 |
| Reuters <-> Benedict Evans | 0.670 |
| Reuters <-> Platformer | 0.668 |
| Benedict Evans <-> Platformer | 0.667 |
| **SpaceX <-> Rocket Lab (must not merge)** | **0.548** |
| next-highest unrelated pair (Apple/foldable <-> Olympics/swimming) | 0.318 |

Clean separation: event-cluster minimum pairwise similarity (0.667) sits
well above the rocket pair (0.548), which sits well above the next-highest
unrelated pair (0.318).

Threshold sweep with `cluster_items` (agglomerative, average linkage) on
the fake-embedded corpus:

| threshold | n_stories | event cluster | rockets merged |
|---|---|---|---|
| 0.50 | 21 | 4 outlets | **yes (wrong)** |
| 0.55 | 22 | 4 outlets | no |
| 0.58 | 22 | 4 outlets | no |
| **0.60** | **22** | **4 outlets** | **no** |
| 0.62 | 22 | 4 outlets | no |
| 0.65 | 22 | 4 outlets | no |
| 0.68 | 22 | 4 outlets | no |
| 0.70 | 23 | **3 outlets (wrong — Reuters splits)** | no |
| 0.75-0.82 | 24-25 | 1 (wrong) | no |

**Recommended FakeEmbeddingProvider test threshold: 0.60**, chosen from the
middle of the stable, correct band [0.55, 0.68] — not the edge of either
failure mode. `tests/test_clustering_integration.py::test_threshold_sweep_shows_a_stable_correct_band`
runs this sweep as an assertion, not just a report, so a future change that
narrows or destroys the band fails CI.

This threshold is specific to `FakeEmbeddingProvider`'s sparse,
entity-weighted hash embedding, which lives on a different similarity scale
than a dense semantic model. It is **not** the same number as
`settings.cluster_similarity_threshold` — see the live section below for
that.

## Live run — real `text-embedding-004` (gotcha: doesn't exist any more)

`settings.embedding_model` defaults to `"text-embedding-004"`. Calling it
live against this key fails immediately:

```
google.api_core.exceptions.NotFound: 404 models/text-embedding-004 is not
found for API version v1beta, or is not supported for embedContent.
```

`genai.list_models()` filtered to `embedContent` support returns, for this
key: `models/gemini-embedding-001`, `models/gemini-embedding-2-preview`,
`models/gemini-embedding-2`. **`text-embedding-004` has been retired** —
`gemini-embedding-001` is the current stable replacement. `GeminiEmbeddingProvider`
itself is model-name-agnostic (reads `settings.embedding_model`, falls back
to a constructor override); the live verification run below used
`GeminiEmbeddingProvider(model="gemini-embedding-001")` explicitly.
**Action for whoever owns `app/core/config.py`: update the
`embedding_model` default away from `text-embedding-004`.** I did not make
this change myself — `app/core/config.py` is outside the file list I was
given ownership of.

### Live cluster assignments (real `gemini-embedding-001`, full 25-item corpus)

Pairwise cosine, event cluster:

| pair | cosine |
|---|---|
| Reuters <-> Platformer | 0.8600 |
| Stratechery <-> Platformer | 0.8680 |
| Stratechery <-> Benedict Evans | 0.8508 |
| Benedict Evans <-> Platformer | 0.8310 |
| Reuters <-> Stratechery | 0.8013 |
| Reuters <-> Benedict Evans | 0.7885 |

Rocket pair (must not merge): **SpaceX <-> Rocket Lab = 0.6908** — clearly
below every event-cluster pair, exactly the separation the fixture is
designed to test for.

Threshold sweep, live embeddings:

| threshold | n_stories | event cluster | rockets merged |
|---|---|---|---|
| 0.70 | 21 | 4 outlets (correct) | no |
| 0.75 | 21 | 4 outlets (correct) | no |
| 0.78 | 22 | 4 outlets (correct) | no |
| 0.80 | 22 | 4 outlets (correct) | no |
| **0.82 (current settings default)** | 23 | **3 outlets — Reuters splits off (WRONG)** | no |
| 0.84-0.92 | 23-25 | 1 (Reuters alone) | no |

At the **current default of 0.82, the 4-outlet event does NOT fully
collapse** — Reuters (Reuters<->Benedict Evans = 0.7885, just under 0.82)
splits into its own singleton while the other three merge. Full cluster
dump at 0.82:

```
size=3  Stratechery | Benedict Evans | Platformer   (TechCorp/DataFlow acquisition)
size=1  Reuters      (TechCorp/DataFlow acquisition — split off, WRONG)
size=1  SpaceNews    (SpaceX Starship)
size=1  Ars Technica (Rocket Lab Neutron)
... (19 further correct singletons)
```

**Recommended production threshold: 0.78**, not 0.82. Evidence:

- 0.78 sits comfortably inside the [0.70, 0.80] band where all four outlets
  correctly collapse into one story.
- Margin above the rocket-pair ceiling (0.6908): 0.089 — the two must-not-merge
  items stay apart with real headroom, not by luck.
- Margin below the point where the cluster starts fragmenting (0.82, where
  Reuters splits): 0.04. Slimmer, but the whole [0.70, 0.80] band is safe, so
  0.78 isn't sitting on the edge of that failure either.
- ADR-003 says merging unrelated stories is far worse than leaving a
  duplicate — 0.78 was chosen from the middle of the verified-safe band
  rather than pushed as low as the data would allow, to keep that bias.

**Action for whoever owns `app/core/config.py`: change
`cluster_similarity_threshold` from `0.82` to `0.78`, and update
`embedding_model` away from the retired `text-embedding-004`.** This is a
one-corpus, one-run result — see "What's unproven" below.

## Cross-source dedup behaviour

Confirmed both offline (Fake) and live (real embeddings):

- The EMAIL item (Stratechery newsletter) and the three RSS items (Reuters,
  Benedict Evans, Platformer) about the same acquisition merge into one
  `Story`.
- `Story.source_names` for that cluster is
  `["Benedict Evans", "Platformer", "Reuters", "Stratechery"]` — sorted,
  cross-source, exactly the PRD F-08 attribution requirement.
- `_pick_canonical` (unmodified, in `clustering.py`) picks EMAIL as the
  canonical item per `AUTHORITY_ORDER` ("your own inbox always wins"),
  which for this fixture means the Stratechery commentary piece becomes
  canonical over Reuters' primary reporting. That's `clustering.py`'s
  existing, deliberate authority ordering, not something this work
  introduced — worth flagging to whoever owns `AUTHORITY_ORDER` if primary
  news reporting is meant to outrank an EMAIL newsletter opinion piece for
  the *canonical* pick specifically (source attribution via `source_names`
  is unaffected either way).

## Graceful degradation (rule 7)

`build_stories` is tested against two failure modes, both offline
(`tests/test_dedup.py`):

- The embedding call raises outright (simulated outage) → one singleton
  `Story` per item, run continues.
- The embedding call returns the wrong number of vectors (silent drop) →
  same degrade path, rather than zipping a vector onto the wrong item with
  no error anywhere.

## Verification

```
$ ./.venv/bin/python -m pytest -q
........................................................................ [ 29%]
........................................................................ [ 58%]
........................................................................ [ 87%]
..............................                                           [100%]
246 passed, 1 warning in 5.17s

$ ./.venv/bin/ruff check .
All checks passed!
```

## What's unproven

- **One corpus, one run.** The threshold recommendation (0.78) rests on a
  single 25-item synthetic corpus and a single live API call. It has not
  been validated against real inbox/RSS data at scale, non-English content,
  or a genuinely ambiguous pair (e.g. two competing outlets' takes on the
  same story that actually *disagree* on facts, which might legitimately
  read as more different than two that agree). If the RSS-ingestion agent's
  real fixtures change `Item.embed_text`'s inputs meaningfully (e.g. much
  longer bodies than this corpus's ~500-char excerpts), this table should
  be re-run against that data before the default is finalised.
- **No batching stress test against the live API.** `BATCH_SIZE = 50` in
  `gemini_embeddings.py` is a conservative default, not validated against
  the API's actual request-size limits — the live run here only sent 25
  texts in one batch.
- **`_pick_canonical` interaction** noted above (EMAIL outranks RSS
  primary reporting for the canonical pick) is inherited from
  `clustering.py`'s existing `AUTHORITY_ORDER` and out of this task's file
  ownership — flagged, not changed.
- **The entity-weighting heuristic in `FakeEmbeddingProvider`** is tuned to
  this corpus's naming style (compound/camelCase brand names like
  `TechCorp`, `DataFlow`, `SpaceX`). A corpus using plain-English company
  names throughout (no internal capitals) would get much weaker signal
  boost and might need a different fake-provider threshold than 0.60 — the
  sweep test would catch this if the corpus changes, but it hasn't been
  tried against a different naming style.
