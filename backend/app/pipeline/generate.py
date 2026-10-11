"""
The Phase 1 pipeline orchestrator.

    ingest (concurrent) -> score/select -> script -> TTS+stitch -> manifest -> store

CLAUDE.md rule 7 — graceful degradation — is the thing this module is most
responsible for getting right: Gmail, Calendar and Weather are fetched
concurrently with `asyncio.gather(..., return_exceptions=True)`, and any
single source failing is logged and dropped rather than aborting the run.
A briefing missing its weather segment still ships.

CLAUDE.md rule 5 — raw email bodies are ephemeral — is enforced here too:
once the script chain has turned emails into a script, every `Item.body`
this run touched is cleared before the manifest is stored. Only the script,
audio and source references persist.

Dependency injection note: `app.ingestion.calendar`, `app.ingestion.weather`,
`app.pipeline.scoring` and `app.pipeline.script_chain` are owned by another
agent working in parallel and may not exist yet. Every one of them is an
injectable parameter here, defaulting to the real implementation when it can
be imported and to `None` otherwise, so this module (and its tests) work
today with fakes and pick up the real thing automatically once it lands —
no reimplementation, no stub files on disk.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.providers import EmbeddingProvider, LLMProvider, TTSProvider
from app.core.schemas import (
    INGESTION_SOURCE_LABELS,
    LENGTH_BUDGETS,
    BriefingLength,
    BriefingManifest,
    Item,
    SourceType,
    Story,
)
from app.core.tz import user_tz
from app.pipeline.census import RunCensus
from app.pipeline.cluster_verify import probe_threshold, verify_stories
from app.pipeline.dedup import build_stories
from app.pipeline.digest import (
    attach_research,
    build_digest,
    build_enrichment_prompt,
    digest_stories,
    enrich_stories,
    research_subjects,
    stories_to_enrich,
)
from app.pipeline.ranking import score_stories, topic_centroid
from app.pipeline.relevance import judge_personal_relevance
from app.synthesis.music import DEFAULT_SET as DEFAULT_MUSIC_SET
from app.synthesis.stitch import synthesize_briefing
from app.synthesis.storage import BriefingStorage, build_storage

try:
    from app.providers.gemini_research import (
        build_research_provider as _real_build_research_provider,
    )
except ImportError:  # pragma: no cover - the provider is optional

    def _real_build_research_provider():
        return None


# Distinguishes "the caller said nothing" from "the caller said no".
_UNSET = object()

log = logging.getLogger(__name__)

try:
    from app.ingestion.gmail import fetch_emails as _real_fetch_emails
except ImportError:  # pragma: no cover - present in this repo already
    _real_fetch_emails = None

try:
    from app.ingestion.calendar import fetch_events as _real_fetch_events
except ImportError:
    _real_fetch_events = None

try:
    from app.ingestion.weather import fetch_weather as _real_fetch_weather
except ImportError:
    _real_fetch_weather = None

try:
    from app.pipeline.scoring import (
        score_emails as _real_score_emails,
    )
    from app.pipeline.scoring import (
        select_for_briefing as _real_select_for_briefing,
    )
except ImportError:
    _real_score_emails = None
    _real_select_for_briefing = None

try:
    from app.ingestion.rss import fetch_articles as _real_fetch_articles
except ImportError:
    _real_fetch_articles = None

try:
    from app.core.providers import build_judgment_provider as _real_build_judgment
    from app.pipeline.triage import triage_emails as _real_triage_emails
except ImportError:  # pragma: no cover - the provider is optional
    _real_triage_emails = None

    def _real_build_judgment():
        return None


try:
    from app.pipeline.script_chain import generate_script as _real_generate_script
except ImportError:
    _real_generate_script = None


async def generate_briefing(
    *,
    llm: LLMProvider,
    tts: TTSProvider,
    briefing_id: str | None = None,
    length: BriefingLength = BriefingLength.STANDARD,
    embeddings: EmbeddingProvider | None = None,
    previous_degraded_attempts: int = 0,
    # The instant ranking ages stories against. None means the wall clock,
    # which is what every production caller wants; a test or a replay of a
    # saved snapshot passes the moment the snapshot represents instead.
    now: datetime | None = None,
    fetch_emails: Any = _real_fetch_emails,
    fetch_events: Any = _real_fetch_events,
    fetch_weather: Any = _real_fetch_weather,
    fetch_articles: Any = _real_fetch_articles,
    judgment_provider: Any = _UNSET,
    score_emails: Any = _real_score_emails,
    select_for_briefing: Any = _real_select_for_briefing,
    generate_script: Any = _real_generate_script,
    storage: BriefingStorage | None = None,
    # Segments the listener has turned off (ROADMAP task 15). Threaded
    # explicitly rather than through `apply_to_environment` like the scalar
    # settings: this is a list, and flattening one into an environment
    # variable to parse it back out is a round trip through a format that
    # cannot hold it.
    disabled_segments: list[str] | None = None,
    gmail_fixture: Path | None = None,
    calendar_fixture: Path | None = None,
    weather_fixture: Path | None = None,
    rss_fixture: Path | None = None,
    # Empty means the curated defaults, not "no news": an empty list is what
    # a client that has never touched the setting produces, and a briefing
    # with no news is not something anyone asks for by accident.
    feeds: list[str] | None = None,
    # Senders whose mail never reaches the briefing.
    muted_senders: list[str] | None = None,
    # ROADMAP task 30. Threaded explicitly rather than through
    # `apply_to_environment`, for the same reason `disabled_segments` is:
    # these are lists, and flattening one into an environment variable to
    # parse it back out is a round trip through a format that cannot hold
    # it.
    interests: list[str] | None = None,
    followed_entities: list[str] | None = None,
    # Which music set tops and tails the briefing, or "none" for silence.
    # None means the product default — this is the level that decides what a
    # briefing sounds like, as distinct from `synthesize_briefing`, which
    # adds only what it is given.
    music_set: str | None = None,
    # Task 18. Injected rather than built here so a test can hand in a fake
    # and so the one production caller decides — `generate_briefing` has a
    # single caller funnel, which is why `follow_up_candidates` could be
    # added the same way without repeating the `music_set` drift.
    #
    # `_unset` rather than None as the default: None must keep meaning
    # "research is OFF", and a test that passes nothing should get the real
    # provider like production does, not silently skip the stage.
    research_provider: Any = _UNSET,
    max_messages: int = 60,
    # PRD F-02 and the ROADMAP both specify today plus 72 hours. What is
    # SPOKEN is decided by select_for_briefing; this is only the fetch
    # window, so it should match the spec rather than pre-truncating it.
    hours_ahead: int = 72,
) -> BriefingManifest:
    """Run the full generation pipeline and return the stored manifest.

    `*_fixture=None` means "use the live source"; passing a fixture Path
    runs that source offline. Any source that raises — live or offline —
    is logged and dropped; the briefing still ships without that segment.
    """
    settings = get_settings()
    # The default has to agree with STORAGE_BACKEND too. Every caller in the
    # deployed path injects storage explicitly, so this only bites the one
    # that forgets — which is exactly when writing to local disk instead of R2
    # would go unnoticed.
    storage = storage or build_storage()
    # Read once here and handed to the ranker, so the only wall-clock read
    # on the ranking path is one a caller can replace. Not a run-wide clock:
    # ingestion, the digest and stitching still read their own.
    run_started_at = now if now is not None else datetime.now(UTC)
    # A naive `now` means UTC, as it already does to the ranker
    # (`ranking._aware`). Left naive, `astimezone` below would read it as the
    # HOST's local time, and the id would name a different day on a laptop
    # than on a UTC CI runner.
    if run_started_at.tzinfo is None:
        run_started_at = run_started_at.replace(tzinfo=UTC)
    # The id is the user's local day, the same day `_parse_briefing_date`
    # greets them with. Formatting the UTC instant filed a run at 00:11 in
    # Lisbon under yesterday's id, on top of yesterday's stored briefing.
    briefing_id = briefing_id or run_started_at.astimezone(user_tz()).strftime(
        "%Y-%m-%d"
    )
    briefing_date = _parse_briefing_date(briefing_id, now=run_started_at)

    (emails, events, weather, articles), degraded = await _ingest_concurrently(
        fetch_emails=fetch_emails,
        fetch_events=fetch_events,
        fetch_weather=fetch_weather,
        fetch_articles=fetch_articles,
        gmail_fixture=gmail_fixture,
        calendar_fixture=calendar_fixture,
        weather_fixture=weather_fixture,
        rss_fixture=rss_fixture,
        max_messages=max_messages,
        hours_ahead=hours_ahead,
        user_lat=settings.user_lat,
        user_lon=settings.user_lon,
        feeds=feeds,
    )

    # Muted senders, dropped AFTER the fetch rather than narrowed in the
    # Gmail query. Case-insensitive, and matched as a substring so "Substack"
    # mutes every newsletter from a sender whose name contains it — which is
    # how a person thinks about muting a publication.
    #
    # `all_emails` keeps everything that arrived. Muting suppresses the
    # READ-ALOUD, not the record: the desk still shows that a newsletter
    # came in, which is the promise the settings screen makes and the whole
    # reason the digest exists (ADR-013). Filtering `emails` alone made a
    # muted publication vanish from both, so the user could not tell muted
    # from never-arrived.
    all_emails = emails
    if muted_senders:
        wanted = [s.strip().lower() for s in muted_senders if s.strip()]
        if wanted:
            emails = [
                em
                for em in emails
                if not any(w in em.source_name.lower() for w in wanted)
            ]
            log.info(
                "muted %d of %d emails from %d muted sender(s); they remain "
                "on the desk",
                len(all_emails) - len(emails),
                len(all_emails),
                len(wanted),
            )

    # Newsletters and articles cluster TOGETHER, in one call, which is the
    # whole reason every source normalises into `Item` before anything else
    # happens (CLAUDE.md, "The pipeline"). Clustering the news in isolation
    # would dedupe Reuters against the BBC and leave the three newsletters
    # that covered the same acquisition to be read out separately —
    # precisely the "concatenated, not edited" failure ADR-003 exists to
    # prevent.
    # A context manager, so the census reports in a `finally`. Every
    # interesting failure in this pipeline happens BELOW here — a refusal
    # the ladder cannot absorb, an EmptyBriefingError when every turn fails
    # synthesis — and those are precisely the runs whose funnel is worth
    # reading. Reporting on the happy path only would have left the worst
    # morning of all as the one with no census line.
    with RunCensus(
        on_starved=_when_a_stage_is_starved,
        # By reference: ingestion has already filled it and the
        # script chain appends to it as segments drop, so the
        # census can tell a starved stage apart from a stage whose
        # source died upstream.
        degraded=degraded,
    ) as census:
        _LAST_CENSUS_STAGES.clear()
        if judgment_provider is _UNSET:
            judgment_provider = _real_build_judgment()
        world_stories, inbox_emails, all_stories = await _build_world_stories(
            judgment_provider=judgment_provider,
            emails=emails,
            articles=articles,
            events=events,
            embeddings=embeddings,
            length=length,
            interests=interests,
            followed_entities=followed_entities,
            now=run_started_at,
        )

        # Semantic triage before ordering (ADR-014). Runs on `inbox_emails`
        # rather than `emails` so it judges only what can still be spoken —
        # newsletters already absorbed into a world story are not paid for.
        # Already resolved above, before clustering — one provider instance
        # per briefing, so the judgment budget in ADR-015 is shared by
        # cluster verification, triage and the quality gates alike.
        if _real_triage_emails is not None and judgment_provider is not None:
            inbox_emails = await _real_triage_emails(inbox_emails, judgment_provider)

        scored_emails = _score_emails(score_emails, inbox_emails, events)

        if select_for_briefing is None:
            raise RuntimeError(
                "select_for_briefing is not available (app.pipeline.scoring not "
                "found and no fake was injected). Pass select_for_briefing= "
                "explicitly, or wait for app/pipeline/scoring.py to land."
            )
        # date=, so "your day" means the briefing day. Ingestion deliberately
        # fetches today plus 72 hours (PRD F-02) and this is the only place that
        # narrows it to what gets spoken; without it the calendar segment walked
        # the next three days under the heading "Your day".
        selection = select_for_briefing(
            scored_emails, events, length, date=briefing_date
        )

        # Take the newsletters apart before writing anything.
        #
        # `inbox_emails`, not `emails`. The two differ by exactly the newsletters
        # clustering already accounted for: the ones absorbed into a story the
        # world segment is about to speak, and the ones collapsed into a
        # newsletter-only cluster behind a keeper. Extracting from the full inbox
        # hands those back, and nothing downstream compares the two blocks —
        # `_format_newsletter_stories` and the WORLD block are rendered
        # independently — so the hosts would cover one acquisition in the world
        # segment and again as "two of your newsletters led with this". That is
        # the Phase 2 gate verbatim and the ADR-003 failure.
        #
        # It is not free. An absorbed issue carries five other items that go with
        # it, and this module's whole thesis is that an issue is a bundle rather
        # than one story. Measured on a real morning before choosing: 12
        # newsletters, 4 promoted world stories, 0 absorbed — the subtraction
        # cost nothing that day. The gate is a stated acceptance criterion and
        # the loss is bounded by the members of at most four clusters, so the
        # trade goes this way until a morning shows otherwise.
        for_extraction = _newsletters_for_extraction(inbox_emails)
        newsletter_stories, newsletter_remainder = await _extract_newsletter_stories(
            llm, for_extraction, judgment_provider=judgment_provider
        )
        # `took` is the count BEFORE the transactional gate, deliberately.
        # Measuring after it means a gate that empties the list reports
        # "newsletters 0->0" and never warns — and this is the exact stage
        # that ran zero times for four days while every run said success.
        # The line below it uses the pre-filter inbox count for the same
        # reason.
        census.record(
            "newsletters",
            took=sum(1 for e in inbox_emails if e.raw.get("is_newsletter")),
            made=len(newsletter_stories),
        )
        census.record("articles", took=len(articles), made=len(world_stories))
        # OTHER MAIL means everything the newsletter block did NOT take.
        #
        # Without this the newsletters arrive in the prompt TWICE — once
        # taken apart into stories, and once whole with their bodies, under
        # a header telling the hosts to cover them "by sender and subject
        # only". The bundle the whole feature exists to dismantle would be
        # sitting in the same prompt, intact.
        #
        # So the test is the COMPLEMENT of `_newsletters_for_extraction`,
        # not "is it a newsletter". Review of #55 found the difference is
        # not academic: transactional mail is excluded from extraction, so
        # excluding it here too sent it nowhere at all. A GTBank
        # "Transaction Notification" was off the desk, out of the
        # newsletter block, out of the rapid-fire tail and not urgent
        # enough to be escalated — the message the whole task was written
        # about reached no surface in the product.
        spoken_as_stories = {
            id(e) for e in _newsletters_for_extraction(selection["emails"])
        }
        other_mail = [e for e in selection["emails"] if id(e) not in spoken_as_stories]
        census.record(
            "inbox",
            took=len(emails),
            made=len(other_mail),
            # An inbox that is entirely newsletters legitimately
            # leaves nothing for the rapid-fire tail.
            may_be_empty=True,
        )

        if generate_script is None:
            raise RuntimeError(
                "generate_script is not available (app.pipeline.script_chain "
                "not found and no fake was injected). Pass generate_script= "
                "explicitly, or wait for app/pipeline/script_chain.py to land."
            )
        turns, sources = await generate_script(
            llm=llm,
            # The same provider that triaged the inbox now judges the
            # script (ADR-015). Threaded rather than rebuilt so one run
            # means one provider, and so a test that injects a fake for
            # triage gets the same fake in the gates.
            judgment_provider=judgment_provider,
            # THIS run's degradation list, handed in so the script chain can add
            # to it. A model that refuses the news drops the world segment, and
            # that has to reach the manifest rather than only the log — it is
            # what `cli.py` reads to decide whether to regenerate.
            degraded=degraded,
            newsletter_stories=newsletter_stories,
            newsletter_remainder=newsletter_remainder,
            emails=other_mail,
            events=selection["events"],
            background_events=selection.get("background_events", []),
            # PRD F-05 caps how many emails are read out individually; this is
            # how many were left over. It was computed and dropped on the floor,
            # so most of the inbox disappeared without so much as a "plus eleven
            # others, nothing urgent".
            aggregated_email_count=selection.get("aggregated_count", 0),
            # The inbox as it stood BEFORE selection cut it to the handful that
            # get read out — not literally every message, since `inbox_emails`
            # already excludes what was absorbed into a world story. The follow-ups
            # segment exists to catch what you owe a reply to, and the message
            # most worth catching is exactly the one that ranked too low to be
            # read out — so handing it `selection["emails"]` would make the
            # segment blind in the case it was built for.
            follow_up_candidates=inbox_emails,
            stories=world_stories,
            # Phase 2: outline -> draft -> critique -> revise. The chain keeps
            # the draft if revision scores worse on the quality gates, which a
            # live run showed is not hypothetical (19 issues -> 20).
            passes=4,
            weather=weather,
            length=length,
            date=briefing_date,
            disabled_segments=disabled_segments,
        )

        # ADR-016's other half, and it runs HERE for two reasons.
        #
        # Last in the judgment queue: the shared
        # `judgment_briefing_budget_seconds` covers triage, cluster
        # verification, personal relevance and the quality gates — four
        # calls that all change what the listener hears. This fifth one
        # changes nothing; it logs cross-cluster pairs that fell just short
        # of 0.78 so the threshold can be tuned against real mornings. A
        # diagnostic does not get to spend budget the product needs, so it
        # goes after them and takes what is left. If the budget is already
        # gone it reports that it learned nothing and the briefing is
        # unaffected.
        #
        # And before `_scrub_email_bodies`: the payload carries 300
        # characters of body, and a newsletter that nearly merged with an
        # article is exactly the pair worth looking at.
        await probe_threshold(all_stories, judgment_provider)

        # The LAST read of any body in this run, and it happens BEFORE the scrub
        # below on purpose. `build_enrichment_prompt` flattens the source text
        # into a prompt string; from here on the digest is built from metadata
        # alone, so no code path after the scrub can reach a body even by
        # mistake. See app/pipeline/digest.py.
        browsable = digest_stories(all_stories)
        enrichment_prompt = (
            build_enrichment_prompt(stories_to_enrich(browsable)) if browsable else ""
        )

        # CLAUDE.md rule 5: raw email bodies are ephemeral, held only for the
        # duration of this run. The script has been written; nothing downstream
        # needs the body text, so drop it from every Item this run touched
        # before anything is persisted.
        _scrub_email_bodies(emails)
        _scrub_email_bodies(scored_emails)
        _scrub_email_bodies(selection.get("emails", []))

        # Concurrent with synthesis, not before it. TTS is the long pole — a live
        # run is 71 seconds against the PRD's 90-second budget — and an
        # enrichment call in series would spend a tenth of the remaining margin
        # on a screen nobody is waiting for. Started first so it is already in
        # flight while the turns synthesise.
        cards_task = asyncio.create_task(enrich_stories(llm, enrichment_prompt))
        try:
            audio_bytes, manifest, audio_format = await synthesize_briefing(
                turns,
                tts=tts,
                briefing_id=briefing_id,
                audio_url_for=lambda _fmt: storage.audio_path_for(briefing_id),
                sources=sources,
                music_set=music_set or DEFAULT_MUSIC_SET,
                # THIS run's failures, carried down from ingestion rather than
                # re-read from the module global at the end — see
                # _ingest_concurrently for why the global cannot answer honestly.
                degraded_sources=degraded,
                # How many times this briefing has already been regenerated
                # because it shipped degraded. Bounds the retry — see
                # MAX_DEGRADED_RETRIES.
                degraded_attempts=((previous_degraded_attempts + 1) if degraded else 0),
            )
        except BaseException:
            # An EmptyBriefingError here means nothing is stored and the run
            # fails. Leaving the enrichment call running would hold the event
            # loop open on an LLM round trip for a briefing that no longer
            # exists — and, in the API process, would log its result against a
            # job that has already reported failure.
            cards_task.cancel()
            raise

        cards = await cards_task

        # Built from the MANIFEST, so `turn_index` is the post-compaction index.
        # `synthesize_briefing` drops turns whose TTS failed and remaps every
        # SourceRef onto the compacted list; deriving deep-links from anything
        # else lands them on the wrong sentence, which is a bug this project has
        # already shipped once.
        digest = build_digest(
            briefing_id=briefing_id,
            stories=all_stories,
            # Everything that arrived, including muted senders — see the note
            # where `all_emails` is set.
            emails=all_emails,
            manifest=manifest,
            cards=cards,
        )

        # Task 18: go and find out what else is true about the morning's
        # subjects, and attach it with its sources.
        #
        # AFTER the digest is built and BEFORE it is saved, deliberately. The
        # digest is what supplies the subjects and the outlet counts that decide
        # which are worth researching, and research must not be able to delay or
        # break the artifact it decorates — `research_subjects` never raises, is
        # capped by count and by wall clock, and returns an empty dict on any
        # failure, so the worst case is the digest that would have been saved
        # anyway (rule 7).
        if research_provider is _UNSET:
            research_provider = _real_build_research_provider()
        if research_provider is not None:
            found = await research_subjects(digest.stories, research_provider)
            digest = digest.model_copy(
                update={"stories": attach_research(digest.stories, found)}
            )

        # to_thread, because BriefingStorage is synchronous and R2Storage is boto3.
        # This is ~3MB up on a blocking socket, and it runs inside the API process
        # while the client polls /status — so calling it directly froze the server
        # for the whole upload.
        # What the script chain was HANDED, against what it wrote. The first cut
        # summed `emails + articles`, which are the raw ingestion counts feeding
        # two earlier stages — "157 articles + 13 emails -> 34 turns" is a ratio
        # between quantities with no relationship, and a funnel line that cannot
        # be read is not instrumentation.
        census.record(
            "script",
            took=(
                len(world_stories)
                + len(newsletter_stories)
                + len(other_mail)
                + len(selection["events"])
            ),
            made=len(turns),
        )
        # `browsable`, not `all_stories`. The desk deliberately shows only what
        # the wider press or a newsletter carried (`digest_stories` drops
        # personal mail), so measuring it against every cluster fires on a
        # perfectly good morning: RSS goes down, the inbox holds fifteen ordinary
        # work emails, and a stage that is behaving correctly is reported as
        # starved. An alarm that cries wolf on a quiet Wednesday is one nobody
        # reads by Thursday — which is the failure this whole module exists to
        # avoid, so it must not be the failure this module introduces.
        census.record("desk", took=len(browsable), made=len(digest.stories))
        _LAST_CENSUS_STAGES.extend(census.stages)
        # to_thread, because BriefingStorage is synchronous and R2Storage is boto3.
        # This is ~3MB up on a blocking socket, and it runs inside the API process
        # while the client polls /status — so calling it directly froze the server
        # for the whole upload.
        await asyncio.to_thread(
            storage.save, briefing_id, audio_bytes, audio_format, manifest, digest
        )
        return manifest


async def _ingest_concurrently(
    *,
    fetch_emails: Any,
    fetch_events: Any,
    fetch_weather: Any,
    fetch_articles: Any,
    gmail_fixture: Path | None,
    calendar_fixture: Path | None,
    weather_fixture: Path | None,
    rss_fixture: Path | None,
    max_messages: int,
    hours_ahead: int,
    user_lat: float,
    user_lon: float,
    feeds: list[str] | None = None,
) -> tuple[tuple[list[Item], list[Any], Any, list[Item]], list[str]]:
    """Fetch all four sources concurrently.

    Returns the four payloads AND the labels of the personal sources that
    failed in THIS invocation. The second half exists because the manifest
    needs to record it and a module global cannot answer the question
    honestly: `generate_briefing` runs minutes of LLM and TTS work between
    ingestion and synthesis, and both `app/scheduler/cron.py` and
    `POST /briefing/generate` drive it on the same event loop without a
    mutual gate. A second run starting in that window overwrites the global,
    and the manifest is then stamped with the wrong run's failures — a
    degraded briefing marked healthy, which is precisely the masking this
    was written to remove.

    Any source that fails (missing module, network error, malformed
    fixture, anything) is logged and replaced with an empty/None default —
    CLAUDE.md rule 7."""

    async def _emails() -> list[Item]:
        if fetch_emails is None:
            raise RuntimeError(
                "fetch_emails not available (app.ingestion.gmail.fetch_emails)"
            )
        return await fetch_emails(fixture=gmail_fixture, max_messages=max_messages)

    async def _events() -> list[Any]:
        if fetch_events is None:
            raise RuntimeError(
                "fetch_events not available (app.ingestion.calendar.fetch_events)"
            )
        return await fetch_events(fixture=calendar_fixture, hours_ahead=hours_ahead)

    async def _weather() -> Any:
        if fetch_weather is None:
            raise RuntimeError(
                "fetch_weather not available (app.ingestion.weather.fetch_weather)"
            )
        return await fetch_weather(lat=user_lat, lon=user_lon, fixture=weather_fixture)

    async def _articles() -> list[Item]:
        if fetch_articles is None:
            raise RuntimeError(
                "fetch_articles not available (app.ingestion.rss.fetch_articles)"
            )
        # `feeds` passed ONLY when there is one. Tests and callers inject
        # `fetch_articles` fakes that accept `fixture` alone, and partials
        # that already bind `feeds`; passing it unconditionally raised
        # TypeError, which `gather(return_exceptions=True)` then swallowed
        # into an empty news segment. A briefing quietly losing every world
        # story because of a keyword argument is exactly the failure this
        # degradation is supposed to protect against, not cause.
        if feeds:
            return await fetch_articles(fixture=rss_fixture, feeds=feeds)
        return await fetch_articles(fixture=rss_fixture)

    labels = INGESTION_SOURCE_LABELS
    defaults: tuple[Any, Any, Any, Any] = ([], [], None, [])
    results = await asyncio.gather(
        _emails(), _events(), _weather(), _articles(), return_exceptions=True
    )

    out: list[Any] = []
    for label, result, default in zip(labels, results, defaults, strict=True):
        if isinstance(result, BaseException):
            log.error(
                "ingestion source %r failed, dropping that segment "
                "(briefing still ships without it): %s",
                label,
                result,
            )
            out.append(default)
        else:
            out.append(result)

    # Rule 7 says any source can fail and the briefing still ships. It does
    # not say that should be quiet when the source that failed IS the product.
    # A real Actions run dropped BOTH email and calendar, shipped a
    # weather-and-news briefing, and exited 0 — a green tick over a briefing
    # missing the entire personal half of what Mercury is for.
    #
    # News is deliberately NOT in this set. A failed feed costs the world
    # segment, which rule 7 covers and the outline prompt already has an
    # empty-segment rule for; it does not mean the briefing lost the
    # personal content it exists to deliver, and folding it in here would
    # make the degraded-run warning fire on mornings that were fine.
    personal_failed = {
        label: result
        for label, result in zip(labels[:2], results[:2], strict=True)
        if isinstance(result, BaseException)
    }
    # Still recorded globally, because app/cli.py reads it after the fact to
    # decide the process exit code and there is exactly one run per process
    # there. But it is ALSO returned, and the manifest uses the returned
    # value — see below.
    _record_failed_sources(personal_failed)
    if len(personal_failed) == 2:
        log.error(
            "BOTH personal sources failed (%s) — this briefing has no calendar "
            "and no inbox in it, which is most of the point. Shipping it "
            "anyway per rule 7, but this is not a healthy run.",
            ", ".join(personal_failed),
        )

    return tuple(out), sorted(personal_failed)  # type: ignore[return-value]


def _is_newsletter(item: Item) -> bool:
    """An email the user subscribed to, per `gmail.detect_newsletter`.

    The gate the subtraction below serves is about NEWSLETTERS: three of them
    covering one acquisition is one story, and dropping the duplicates costs
    the listener nothing because the content is the story. An ordinary email
    is not interchangeable with the news it happens to mention — a world
    story renders the canonical ARTICLE's title and body, so an absorbed
    personal message survives only as a name in a source list, read out by
    nobody. Two colleagues writing about one project is not duplication
    either; they both wrote to the user.
    """
    return item.source_type is SourceType.EMAIL and bool(item.raw.get("is_newsletter"))


def _newsletters_for_extraction(emails: list[Item]) -> list[Item]:
    """Every newsletter still in play — NOT the ones selection kept.

    This distinction is the whole bug of 2026-09-16 to 09-20, during which
    the newsletter segment ran zero times across sixteen briefings.

    `select_for_briefing` caps emails at six and `score_emails` sorts
    newsletters last, because they are `Priority.LOW` — and they are LOW
    precisely because they were never meant to be read out one at a time.
    Feeding this segment from that cap asked the wrong budget a question it
    was built to answer "no" to. On a morning with eight ordinary emails,
    all six slots went to those and the extractor got an empty list.

    The two budgets are genuinely different. Selection limits how many
    emails are READ OUT INDIVIDUALLY. This segment SUMMARISES newsletters as
    a group and has its own cap further down the chain — `take_spoken`,
    which bounds how many merged SUBJECTS get discussed. Capping twice, once
    by the wrong unit, is what produced nothing.

    Callers pass `inbox_emails` rather than the raw inbox. This function is
    deliberately not the place that decides: it answers "which of these are
    newsletters", and which list to ask about is a dedup question that
    belongs at the call site, where the world stories are in scope.
    """
    # `is_transactional` excluded as well as required-`is_newsletter`. A
    # bank alert carries `List-Unsubscribe` so it arrives flagged as a
    # newsletter, but there is no journalism inside it to extract — the
    # model would be asked to find the news in a debit notification, spend
    # prompt budget doing it, and hand the body to Gemini on the way (rule
    # 5). ROADMAP task 27.
    return [
        item
        for item in emails
        if item.raw.get("is_newsletter") and not item.raw.get("is_transactional")
    ]


# The most recent run's census stages, for tests that need to assert on
# WHAT production recorded rather than on a hand-built stand-in. Review of
# #56 showed the alternative: removing `degraded=degraded` from the census
# below left the whole suite green, because every test built its own.
#
# Read-only for everything but `generate_briefing`, and never load-bearing
# — nothing in the pipeline reads it.
_LAST_CENSUS_STAGES: list = []


def _when_a_stage_is_starved(names: list[str]) -> None:
    """Put the finding where someone will actually see it.

    By the time this runs the briefing has been built and stored, so it
    cannot reach `degraded_sources` on the manifest or ask `cli.py` to
    regenerate — those decisions happen upstream, and a stage that produced
    nothing is not the same as a source that failed. What is still
    available is how loudly the finished run complains.

    It deliberately does NOT fail the run. The briefing shipped; a red run
    would say the opposite of the thing the owner most needs to know, and
    on a false positive it would say it about a perfectly good morning.
    Rule 7 is about the morning surviving, and it survived.

    So: the job summary, which renders as a panel at the top of the run
    page rather than as line 3,200 of pip output. The `::warning::`
    annotation in `census.py` already fires; this exists because the
    annotation alone was not enough — the 2026-08-27 failure sat unnoticed
    for days and was "found by reading gh run list", and the newsletter
    segment ran zero times for four days while every run said success.
    Writing it twice, in two places with different failure modes, is the
    cheapest insurance available against a third.
    """
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    listed = "\n".join(f"- `{name}`" for name in names)
    try:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(
                "\n### Starved stage(s) in this run\n\n"
                f"{listed}\n\n"
                "Each of these received input and produced nothing. The "
                "briefing still shipped, so this run is green — but every "
                '"built and never actually called" bug in this project '
                "looked exactly like this in the log. Check what reaches "
                "the named stage before assuming it was a quiet morning.\n"
            )
    except OSError:  # pragma: no cover - instrumentation must not bite
        log.warning("could not write the starved-stage run summary", exc_info=True)


async def _extract_newsletter_stories(
    llm,
    newsletters: list[Item],
    judgment_provider: Any = None,
) -> tuple[list, int]:
    """Newsletters -> discrete items -> merged subjects -> researched.

    Never raises, and returns [] on any failure. Rule 7: the briefing ships
    without the newsletter segment rather than not at all — which is the
    same contract every other source here already has.

    Research runs on the merged subjects rather than the raw items, so the
    eight calls go to eight different questions. Researching items would
    spend nine of them on the one subject nine newsletters carried.
    """
    # Said out loud even when there is nothing to say.
    #
    # This is the line whose absence hid the bug for four days. The first
    # cut returned early on an empty list without logging, so sixteen
    # consecutive briefings produced no newsletter segment and every run
    # log looked exactly like a morning with no newsletters in it. A stage
    # that can quietly do nothing must announce when it does — silence is
    # indistinguishable from success, and that is how `n_stories=0`,
    # `music_set` and `topic_centroid` all survived too.
    log.info("newsletters: %d issue(s) handed to the extractor", len(newsletters))
    if not newsletters:
        return [], 0
    try:
        from dataclasses import replace

        from app.pipeline.newsletter_items import (
            extract_items,
            merge_by_subject,
            rank,
            research,
            take_spoken,
        )
        from app.providers.gemini_research import build_research_provider

        # The provider that judged the inbox also checks that these
        # extracted items are actually in the newsletters they claim to be
        # from (ADR-018) — before `_flatten_source_text` promotes them to
        # source data the grounding gate measures against.
        items = await extract_items(llm, newsletters, judgment_provider)
        if not items:
            return [], 0
        stories = rank(merge_by_subject(items))
        # Capped BEFORE research, so the eight grounded-search calls go to
        # the six subjects that will actually be discussed plus a couple of
        # spares — rather than to items nobody will hear.
        stories, remainder = take_spoken(stories)
        found = await research(stories, build_research_provider())
        if found:
            stories = [
                replace(story, research=found[story.subject])
                if story.subject in found
                else story
                for story in stories
            ]
        log.info(
            "newsletters: %d subject(s) spoken, %d held back, %d researched, "
            "top subject carried by %d outlet(s)",
            len(stories),
            remainder,
            len(found),
            len(stories[0].outlets) if stories else 0,
        )
        return stories, remainder
    except Exception:
        log.warning("newsletter extraction failed", exc_info=True)
        return [], 0


async def _topic_centroid(
    interests: list[str] | None, embeddings: EmbeddingProvider | None
):
    """The listener's interests as one normalised vector, or None.

    None is the ordinary case and means the ranker behaves exactly as it
    did before — `interest` falls back to its 0.5 constant, which after
    the rescale in `score_stories` means "orthogonal", i.e. no
    information either way.

    Never raises, and now actually doesn't. Review of #58 found
    `topic_centroid(vectors)` sitting OUTSIDE the try, so a provider
    returning ragged vectors raised straight out of here, through
    `_build_world_stories` (no try there either) and killed the whole
    briefing — a ranking refinement taking down the morning, which is
    rule 7 exactly backwards.
    """
    wanted = [t.strip() for t in (interests or []) if t.strip()]
    if not wanted or embeddings is None:
        log.info(
            "ranking: no interest topics configured, so the world segment "
            "is ranked without the interest term (set `interests` in the "
            "user settings to change that)"
        )
        return None
    try:
        vectors = await embeddings.embed(wanted)
        centroid = topic_centroid(vectors)
    except Exception:
        log.warning(
            "ranking: could not embed interest topics; ranking without "
            "the interest term",
            exc_info=True,
        )
        return None

    # Logged AFTER computing, and keyed on what came back.
    #
    # The first cut logged "ranked against 1 interest topic(s)" for a
    # settings value of [" "] — every topic filtered out, centroid None,
    # the term inert, and the morning log affirming it was active. The
    # line this PR leans on as its safety net was able to state the
    # opposite of the truth.
    if centroid is None:
        log.warning(
            "ranking: %d interest topic(s) produced no usable centroid, so "
            "the world segment is ranked WITHOUT the interest term",
            len(wanted),
        )
        return None
    if len(vectors) != len(wanted):
        # `dedup.build_stories` refuses this case outright rather than
        # guessing an order mapping. Here the centroid is order-free, so
        # a subset is still meaningful — but it must not be reported as
        # the whole.
        log.warning(
            "ranking: embedded %d of %d interest topic(s); the centroid "
            "covers the subset",
            len(vectors),
            len(wanted),
        )
    log.info(
        "ranking: world segment ranked against %d interest topic(s): %s",
        len(vectors),
        ", ".join(wanted[:6]),
    )
    return centroid


async def _build_world_stories(
    *,
    emails: list[Item],
    articles: list[Item],
    events: list[Any],
    embeddings: EmbeddingProvider | None,
    length: BriefingLength,
    now: datetime,
    interests: list[str] | None = None,
    followed_entities: list[str] | None = None,
    judgment_provider: Any = None,
) -> tuple[list[Story], list[Item], list[Story]]:
    """Cluster the day's news and decide what the world segment gets.

    Returns (stories for the world segment, emails still owed to the inbox,
    EVERY story clustered this run).

    That third value is the digest's whole reason to exist. The world
    segment is the top few by score — four at standard length out of the
    couple of hundred a real morning clusters — and the rest used to be
    dropped on the floor here, unreachable by anything. They are the
    difference between the edit and the desk (`app/pipeline/digest.py`).

    Everything goes into ONE `build_stories` call — newsletters and articles
    together. That is the point of the shared `Item` schema, and clustering
    news on its own would leave three newsletters covering one acquisition
    to be read out one after another.

    A story reaches the world segment when at least one RSS item is in it.
    That rule needs no newsletter classifier and gets the split right in both
    directions: a newsletter a publication also covered becomes one world
    story with every source attributed, while a newsletter nobody else
    carried — and every ordinary email — stays in the inbox where it belongs.
    Without it, a personal email that happened to cluster with an article
    would be promoted into "your world".

    Keyed on object identity, not `Item.id`: that is a content hash of
    source_name + url-or-title + timestamp, so two messages from one sender
    with one subject at one timestamp collide however different their bodies,
    and absorbing the first silently removed the second.

    Emails absorbed into a SELECTED story are then removed from the inbox
    list, which is the Phase 2 gate stated exactly: the same story does not
    appear twice however many newsletters covered it. Absorbed is computed
    after the cap, so a newsletter whose story missed the cut is still read
    out as email rather than vanishing from the briefing entirely.

    `now` is required rather than defaulted: it is the clock ranking ages
    every story against, and the one caller that should supply it from
    the wall clock is `generate_briefing`, once per run. A default here
    would let a test inherit the real date without saying so.
    """
    clusterable = emails + articles
    if not clusterable:
        return [], emails, []

    stories = await build_stories(clusterable, embeddings=embeddings)
    # A second opinion on what the 0.78 threshold merged (ADR-016).
    # Split direction only: pulling a member back out costs a duplicate,
    # which ADR-003 calls the cheaper error by a wide margin.
    stories = await verify_stories(stories, judgment_provider)
    # The WHOLE fetch window, not the briefing day. `select_for_briefing`
    # narrows events to today because the calendar segment is "the day walked
    # chronologically" and the cold open is an observation "about the day
    # ahead" — both make a claim about today. Ranking makes none: an attendee
    # is a personal-relevance signal, and a story about someone you are seeing
    # on Thursday is exactly the prep PRD F-02's 72 hours exist for. Narrowing
    # it here does not protect a today story — that story's score does not
    # move either way — it only stops a Thursday one outranking news you have
    # no connection to at all.
    # ROADMAP task 30: the ranker has accepted a topic centroid since
    # Phase 2 and no caller ever passed one, so `interest` was a constant
    # and its 0.20 weight cancelled out. Third of the four "built and
    # never called" bugs in this project's history.
    centroid = await _topic_centroid(interests, embeddings)
    # Ask whether each story matters to THIS person before weighing it
    # (ADR-017). Replaces the lexical personal term where it answers, and
    # leaves it in place where it does not.
    judged_personal = await judge_personal_relevance(
        stories,
        events=events,
        followed_entities=followed_entities,
        interests=interests,
        provider=judgment_provider,
    )
    stories = score_stories(
        stories,
        calendar_events=events,
        topic_centroid=centroid,
        followed_entities=set(followed_entities or ()),
        judged_personal=judged_personal,
        now=now,
    )

    world = [
        story
        for story in stories
        if any(m.source_type is SourceType.RSS for m in story.members)
    ]
    world = world[: LENGTH_BUDGETS[length]["stories"]]

    absorbed = {
        id(member)
        for story in world
        for member in story.members
        if _is_newsletter(member)
    }

    # A cluster the world segment did not take is still a cluster.
    #
    # Promotion requires an RSS member, so three newsletters covering one
    # acquisition that no wired feed happened to carry used to arrive in the
    # inbox as three separate emails and be read out one after another. The
    # gate in CLAUDE.md is "the same story never appears twice REGARDLESS OF
    # HOW MANY NEWSLETTERS COVERED IT", and the no-feed case is precisely the
    # one that sentence is about — PRD F-08's narrower "newsletter and news
    # feed" wording was satisfied while the gate itself was not. Mercury's
    # news arrives largely as newsletters, so this is not an edge case.
    #
    # Collapsed rather than promoted. The world segment is for what the wider
    # press carried; a story only your own newsletters covered is inbox
    # material that happened to arrive three times.
    promoted = {story.cluster_id for story in world}
    superseded: set[str] = set()
    for story in stories:
        if story.cluster_id in promoted:
            continue
        members = [m for m in story.members if _is_newsletter(m)]
        if len(members) < 2:
            continue
        keeper = next((m for m in members if m is story.canonical), members[0])
        superseded.update(id(m) for m in members if m is not keeper)
        # Not silent. Dropping the duplicates without saying so would trade
        # one wrong behaviour for another — the hosts would read a story that
        # three publications thought worth writing about as though one did.
        # Naming the others is the multi-source signal the digest is built on,
        # and it is the honest version of "the same story never appears twice".
        keeper.raw["also_covered_by"] = sorted(
            m.source_name for m in members if m is not keeper
        )

    inbox_emails = [
        em for em in emails if id(em) not in absorbed and id(em) not in superseded
    ]

    log.info(
        "news: %d articles + %d emails clustered into %d stories; %d reach "
        "the world segment, absorbing %d email(s); %d further email(s) "
        "collapsed into a newsletter-only cluster",
        len(articles),
        len(emails),
        len(stories),
        len(world),
        len(absorbed),
        len(superseded),
    )
    return world, inbox_emails, stories


# Set by the ingestion gather so callers (app.cli) can report a degraded run
# without re-deriving it. Inferring from the manifest does not work: the script
# chain REQUIRES all seven canonical segments, so "calendar" and "inbox" are
# present even when there was no calendar and no inbox data behind them.
_failed_personal_sources: dict[str, BaseException] = {}


def _record_failed_sources(failed: dict[str, BaseException]) -> None:
    _failed_personal_sources.clear()
    _failed_personal_sources.update(failed)


def failed_personal_sources() -> list[str]:
    """Which of email/calendar failed during the most recent run."""
    return list(_failed_personal_sources)


def failed_personal_source_errors() -> dict[str, BaseException]:
    """...and why, as the exception objects rather than their messages.

    The objects, deliberately. `app.cli` has to tell an expired OAuth token —
    a known cause with a permanent fix, and the failure this deployment is
    most likely to hit — apart from a transient API error, and the honest way
    to do that is the exception TYPE. Matching on message text would break the
    first time somebody rewords a string.
    """
    return dict(_failed_personal_sources)


def _score_emails(
    score_emails: Any, emails: list[Item], events: list[Any]
) -> list[Item]:
    if score_emails is None:
        return emails
    sig = inspect.signature(score_emails)
    if len(sig.parameters) >= 2:
        try:
            return score_emails(emails, events)
        except TypeError:
            pass
    return score_emails(emails)


def _scrub_email_bodies(items: list[Item]) -> None:
    for item in items:
        item.body = ""


def _parse_briefing_date(briefing_id: str, *, now: datetime) -> datetime:
    """`briefing_id` is normally a "YYYY-MM-DD" string (see the manifest's
    docstring in app/core/schemas.py), but `generate_script`'s `date`
    parameter is typed as a real `datetime`. Parse it back out; for a
    caller-supplied id that isn't date-shaped, fall back to `now` (the
    run's clock, aware) rather than the wall clock, so a replay greets the
    morning it replays."""
    tz = user_tz()
    # The DAY, whether or not the id carries a time of day.
    #
    # Ids gained a `-HHMM` suffix so several briefings can share a date
    # instead of overwriting one another, and this parser is the reason the
    # suffix is sliced off rather than parsed: what the script chain wants is
    # the day to greet the listener with. It is told the day and never the
    # clock time on purpose — see the note in `generate_script`, where a host
    # announcing a time hours before the listener hears it is confidently
    # wrong in a way they can check instantly.
    from app.synthesis.storage import briefing_day

    try:
        return datetime.strptime(briefing_day(briefing_id), "%Y-%m-%d").replace(
            tzinfo=tz
        )
    except ValueError:
        return now.astimezone(tz)
