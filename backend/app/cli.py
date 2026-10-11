"""
Command-line entrypoint for scheduled generation.

This is what the GitHub Actions cron runs (ADR-008). It exists so the daily
job does not need an HTTP server at all: the workflow calls this, it writes
the finished briefing to R2, and the phone reads from R2 directly.

    python -m app.cli generate [--length standard] [--briefing-id 2026-08-22]
    python -m app.cli check    [--briefing-id 2026-08-22]
    python -m app.cli prune    [--retention-days 30] [--dry-run]

Exit codes matter here — Actions decides whether to mark the run failed and
email you, and a briefing that silently did not generate is the failure mode
the whole schedule exists to prevent:

    0  a briefing exists for this id (generated now, or already there)
    1  generation failed, or produced nothing playable
    2  configuration is wrong (missing credentials, bad storage config)
    3  a briefing shipped, but WITHOUT the user's inbox or calendar
    4  storage could not answer — the read failed, or the stored manifest is
       corrupt. NOT the same as 1: the watchdog gates its "go and regenerate"
       instructions on 1, and a 403 or an unparseable object is not evidence
       about the briefing at all.
    5  a briefing is present and playable but STALE, past `--max-age-hours`.
       Distinct from 1 for the same reason 4 is: 1 means "there is nothing to
       play", and the watchdog answers that by retrying and then telling the
       operator to regenerate. Neither response fits a briefing that exists,
       plays, and is simply old — retrying cannot make it newer, and the
       remedy is to find out why the later runs did not happen.

`check` is the other half of ADR-010. GitHub's scheduler is documented as
best-effort ("some queued jobs may be dropped"), and a dropped run leaves no
run record, so there is nothing to fail and nothing to send the standard
failed-run email. The only way to notice is to ask storage whether the
briefing is actually there, from something that is not GitHub's scheduler.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime

from app.core.config import apply_to_environment, get_settings
from app.core.providers import build_embedding_provider
from app.core.schemas import REFUSABLE_SEGMENTS, BriefingLength
from app.core.tz import resolve_tz

# How many times a briefing may be generated in a day while it keeps
# shipping degraded, counting the first one. Three: the original plus two
# retries.
#
# Named for what it counts. As MAX_DEGRADED_RETRIES = 2 it read as "two
# retries" and delivered one, because the stored count includes the initial
# run: that run stores 1, its retry stores 2, and `>= 2` then blocks the
# third. An off-by-one nobody would see from production, since the symptom
# is a briefing that heals slightly less often than intended.
#
# Not unbounded, because what degrades a briefing is almost always a
# credential a human has to replace, and eight checkpoints fire on a broken
# day. Not one, because the whole point is that a source which recovers at
# 06:00 heals the same morning rather than the next.
MAX_DEGRADED_ATTEMPTS = 3

log = logging.getLogger("mercury.cli")


def _user_now(tz_name: str) -> datetime:
    """Now in the USER's timezone. Never UTC — this project has had five
    separate date bugs, and the scheduler, the API and this entrypoint must
    all name the same briefing the same way."""
    return datetime.now(resolve_tz(tz_name))


def _today_briefing_id(tz_name: str) -> str:
    """Today's DAY, for retention and for naming the day a run belongs to."""
    return _user_now(tz_name).strftime("%Y-%m-%d")


def _free_briefing_id(day: str, preferred_hhmm: str) -> str:
    """An id for `day` that is unique AND sorts after everything already there.

    Two problems, one answer.

    **Same minute.** Two runs starting inside one minute produced the same
    id, so the second was skipped by generation's idempotency check — or,
    with `--force`, overwrote the first. The extra briefing simply did not
    exist.

    **A run that crosses local midnight.** `--briefing-day` pins the DAY, so
    a checkpoint deciding at 23:58 and a job starting at 00:01 produce
    `<yesterday>-0001` — which sorts BEFORE `<yesterday>-2212` and every
    other run of that day. Newest-first selection is lexicographic
    everywhere in this project, so the freshest briefing would have been
    filed as the oldest and the phone would have shown a stale one.

    Both are fixed by refusing to go backwards: the later of the clock and
    one minute past the newest id already stored for that day.
    """
    from app.synthesis.storage import briefing_day, build_storage

    candidate = f"{day}-{preferred_hhmm}"
    try:
        existing = [
            i for i in build_storage().list_briefing_ids() if briefing_day(i) == day
        ]
    except Exception:
        # Cannot list, so cannot know. The clock is the honest guess, and a
        # listing failure must not decide whether a briefing happens.
        log.debug("could not list briefings to pick a free id", exc_info=True)
        return candidate

    newest = max(existing, default=None)
    if newest is None or candidate > newest:
        return candidate

    # The first FREE minute at or after one past the newest.
    #
    # The first cut clamped at 23:59, which collides at exactly the point
    # the clamp was meant to protect: with a briefing already at 23:59 and
    # an earlier clock, the bump returned 23:59 again — the same id, which
    # is the collision this function exists to prevent, arrived at by the
    # code preventing it.
    taken = {i[-4:] for i in existing if len(i) > 10}
    hhmm = newest[-4:] if len(newest) > 10 else "0000"
    after_newest = int(hhmm[:2]) * 60 + int(hhmm[2:]) + 1

    def first_free(start: int) -> str | None:
        for minutes in range(start, 24 * 60):
            stamp = f"{minutes // 60:02d}{minutes % 60:02d}"
            if stamp not in taken:
                return f"{day}-{stamp}"
        return None

    # Preferred: sorts after everything, which is what keeps newest-first
    # meaningful.
    ordered = first_free(after_newest)
    if ordered is not None:
        return ordered

    # Nothing left after the newest — a day already holding a briefing at
    # 23:59. Ordering is now impossible, and the choice is between a
    # briefing that exists but does not sort last and no briefing at all.
    #
    # Rule 7 decides it: a NAMING concern must not cost the morning. The
    # run gets a free minute wherever one is, so nothing is overwritten and
    # nothing fails; it simply is not the newest of its day, which is a
    # cosmetic wrong in a situation that needs 1439 briefings to reach.
    anywhere = first_free(0)
    if anywhere is not None:
        log.warning(
            "%s already holds a briefing at %s, so this run cannot sort "
            "last; naming it %s instead",
            day,
            max(taken),
            anywhere,
        )
        return anywhere

    # 1440 briefings in one day. There is no name left that does not destroy
    # one, and silently overwriting is the one option worse than failing.
    raise ValueError(
        f"no free minute left on {day}: it already holds {len(existing)} "
        "briefings, and every remaining name would overwrite one"
    )


def _morning_briefing_id(storage, day: str, now_hhmm: str) -> str | None:
    """The newest briefing that covers `day`'s MORNING, if any.

    What `check` asks about whenever it is given a day rather than a full
    id: by the scheduled backstop, which names nothing, and by the Worker's
    alarm, which names the bare day when the morning is missing or stale.
    Resolving the bare day as a literal key looked for `<day>.manifest.json`,
    which no timed run writes, so the watchdog reported "no briefing" with a
    playable one in the bucket and its re-checks could never see a morning
    run land.

    The floor applies only once it is morning where the user is. Before
    then every briefing for the day counts, which is what this did before
    the floor existed: a checkpoint in a timezone where it fires before
    03:00 local would otherwise never be satisfied by anything it caused.

    Takes `storage` already built, so a MISCONFIGURED store (exit 2) stays
    distinguishable from one that could not answer (exit 4) — the fixes are
    different, and the first cut of this merged them. RAISES when the
    listing fails, for the reason `_newest_briefing_id` documents.
    """
    from app.synthesis.storage import (
        MORNING_FLOOR_HHMM,
        briefing_day,
        covers_morning,
    )

    floor_applies = now_hhmm >= MORNING_FLOOR_HHMM
    ids = [
        i
        for i in storage.list_briefing_ids()
        if briefing_day(i) == day and (not floor_applies or covers_morning(i))
    ]
    return max(ids) if ids else None


def _newest_briefing_id(today_id: str) -> str | None:
    """The most recent briefing stored for `today_id`'s day, if any.

    Ids are fixed-width and date-prefixed, so the newest is simply the
    largest string for that day — the same property the archive and R2's
    own listing rely on.

    RAISES when storage cannot be reached, and that is the whole point of
    the distinction. `check` uses this to find today's briefing, and a
    swallowed listing error made "I could not ask" indistinguishable from
    "there is nothing" — so a storage outage or a rotated R2 key reported a
    perfectly good briefing as MISSING, exited 1 instead of 4, and told the
    watchdog to spend a generation replacing something that was already
    there. `_check` has always separated those two answers; this is the one
    place that had quietly merged them again.
    """
    from app.synthesis.storage import briefing_day, build_storage

    ids = build_storage().list_briefing_ids()
    today = [i for i in ids if briefing_day(i) == briefing_day(today_id)]
    return max(today) if today else None


def _resolve_briefing_id(explicit: str | None, fallback: str) -> str | None:
    r"""The id both subcommands operate on, or None if it is malformed.

    A malformed id silently becomes a storage key nothing ever reads — and
    `check` would then report a perfectly healthy briefing as missing, which
    is a worse outcome than the bad write it was originally guarding.

    `is_briefing_id` rather than a local regex, because this used to be its
    own `\d{4}-\d{2}-\d{2}` copy and accepted `2026-01-99` — a key retention
    cannot parse as a date and could therefore never age out.

    `fallback` is what an absent `--briefing-id` means, and it differs by
    subcommand now that a day can hold several briefings: `generate` wants a
    NEW id, and `check` wants the newest EXISTING one. Passing today's day to
    both — which is what this did — would have made `generate` overwrite the
    morning's first run and `check` report a healthy day as missing.
    """
    from app.synthesis.storage import is_briefing_id

    briefing_id = explicit or fallback
    if not is_briefing_id(briefing_id):
        log.error(
            "briefing id %r is not a real date, optionally with a -HHMM time; "
            "refusing to act on an id nothing will look for",
            briefing_id,
        )
        return None
    return briefing_id


def _freshness(max_age_hours: float) -> int:
    """Has generation stopped, whatever day it is?

    A DIFFERENT QUESTION from `check`, and separating them is the whole
    point. `check` is day-scoped: it resolves today's id and asks whether
    that briefing exists and plays. Freshness is pipeline-scoped: it asks
    how long it has been since ANY briefing was produced.

    Review of #59 priced the confusion exactly. A `--max-age-hours` flag
    bolted onto `check` could not fire at all, because a briefing filed
    under today can never be older than the time since local midnight —
    at the 05:52 checkpoint that is at most 6.9 hours, so any floor loose
    enough to survive a healthy morning was mathematically unreachable.
    And on the morning after the 2026-09-20 stall there was no briefing
    for that day at all, so `check` returned 1 and the floor was never
    even evaluated. Two ways of being inert, in the change written to
    stop things being inert.

    Asking about the newest briefing ANYWHERE has neither problem: no
    date to resolve, so no wrong-day false alarm and no dependence on the
    hour it runs.

    Exit codes match `check` so the watchdog can treat them alike:
    0 fresh, 5 stale, 2 storage misconfigured, 4 storage could not answer.
    """
    from app.synthesis.storage import build_storage

    try:
        storage = build_storage()
    except Exception as exc:
        log.error("storage is not configured: %s", exc)
        return 2

    try:
        ids = storage.list_briefing_ids()
    except Exception:
        log.exception("could not list briefings")
        print(
            "::error::Could not list briefings from storage. This says "
            "nothing about whether generation is healthy — check the R2 "
            "credentials before concluding anything."
        )
        return 4

    if not ids:
        # Nothing at all is not staleness; it is a first run, or an empty
        # bucket, and `check` is the command with something to say about
        # a missing briefing.
        log.info("no briefings in storage; freshness has nothing to measure")
        return 0

    newest = max(ids)
    try:
        manifest = storage.load_manifest(newest)
    except Exception:
        log.exception("could not read the manifest for %s", newest)
        print(
            f"::error::Could not read the newest briefing {newest}. This "
            "says nothing about whether generation is healthy."
        )
        return 4

    if manifest is None:
        # A briefing id with no manifest: audio written, the run died
        # before the manifest. `check` reports that shape properly.
        log.info("newest briefing %s has no manifest; leaving it to `check`", newest)
        return 0

    generated = manifest.generated_at
    if generated.tzinfo is None:
        # Naive timestamps have cost this project four bugs. The manifest
        # is written with UTC, so assume it rather than raising on the
        # subtraction — an alarm that dies on a date bug is no alarm.
        generated = generated.replace(tzinfo=UTC)
    age_hours = (datetime.now(UTC) - generated).total_seconds() / 3600

    if age_hours < -1:
        # A briefing from the future is clock skew, and a negative age
        # passes every floor silently and forever.
        log.warning(
            "newest briefing %s is stamped %.1fh in the FUTURE; freshness "
            "cannot mean anything until that is fixed",
            newest,
            -age_hours,
        )
        return 0

    if age_hours > max_age_hours:
        print(
            f"::error::The newest briefing anywhere is {newest}, "
            f"{age_hours:.1f} hours old, past the {max_age_hours:g}-hour "
            "floor. Generation has stopped producing new ones.\n"
            "1. `gh run list --workflow=briefing.yml` — did the later runs "
            "happen at all? A DROPPED run leaves no record, and the answer "
            "is simply to re-run the workflow.\n"
            "2. If they ran and FAILED, read the newest failure before "
            "re-running: on 2026-09-20 the Gemini project hit its monthly "
            "spend cap and every run after 20:44 failed with 429, so "
            "re-running would have changed nothing."
        )
        log.error(
            "newest briefing %s is %.1fh old, past the %.1fh floor",
            newest,
            age_hours,
            max_age_hours,
        )
        return 5

    log.info(
        "newest briefing %s is %.1fh old, inside the %.1fh floor",
        newest,
        age_hours,
        max_age_hours,
    )
    return 0


def _check(briefing_id: str) -> int:
    """Is there a briefing for this id the phone could actually play?

    Deliberately does NOT stop at "the manifest exists". Two real failure
    shapes in this project pass that test and fail the user:

      - a run where every TTS call failed still stored a manifest, with zero
        turns and zero milliseconds of audio, and then blocked its own
        replacement forever because generation is idempotent;
      - a forced regeneration that deleted the old audio and then failed left
        a manifest pointing at an object that no longer exists, so the phone
        got 200 on the manifest and 404 on the audio.

    Both look exactly like success from the Actions log. So this asks for the
    audio too, and calls a zero-turn briefing what it is.

    """
    from app.synthesis.storage import MIN_PLAYABLE_AUDIO_BYTES, build_storage

    try:
        storage = build_storage()
    except Exception as exc:
        log.error("storage is not configured: %s", exc)
        return 2

    try:
        manifest = storage.load_manifest(briefing_id)
    except Exception:
        # Exit 4, not 1. A read failure is NOT "no briefing" — R2 raises on
        # anything that is not a genuine 404 precisely so a rotated token
        # cannot be reported as a missing briefing, and an unparseable
        # manifest object is not an absent one either. Both mean "storage
        # could not answer the question", and the watchdog reports that
        # differently from "the briefing is not there": the second sends the
        # operator to regenerate, which for a 403 spends real Gemini and TTS
        # money on a briefing that already exists.
        log.exception("could not read the manifest for %s", briefing_id)
        print(
            f"::error::Could not read briefing {briefing_id} from storage. This "
            "says nothing about whether the briefing exists — the object may be "
            "there and unreadable, or the R2 credentials may have been rotated "
            "or under-scoped. Check the R2 secrets before regenerating anything."
        )
        return 4

    if manifest is None:
        print(
            f"::error::No briefing for {briefing_id}. Nothing generated it, or "
            "the run that should have was delayed or dropped by the scheduler. "
            "Re-run the 'daily briefing' workflow (Actions -> daily briefing -> "
            "Run workflow)."
        )
        return 1

    if not manifest.turns or manifest.duration_ms <= 0:
        print(
            f"::error::Briefing {briefing_id} exists but has "
            f"{len(manifest.turns)} turns and {manifest.duration_ms}ms of "
            "audio. That is an empty briefing, and because generation is "
            "idempotent it will block its own replacement — re-run with "
            "force=true."
        )
        return 1

    try:
        audio = storage.load_audio(briefing_id)
    except Exception:
        log.exception("could not read the audio for %s", briefing_id)
        print(
            f"::error::Could not read audio for briefing {briefing_id}. The "
            "manifest read fine, so this is a storage problem, not a missing "
            "briefing."
        )
        return 4

    if audio is None:
        print(
            f"::error::Briefing {briefing_id} has a manifest but NO AUDIO. The "
            "phone will get 200 on the manifest and 404 on the audio. Re-run "
            "with force=true."
        )
        return 1

    # Present is not playable. `audio is None` is false for a zero-byte object,
    # so an upload that died mid-flight passed this check and the run went
    # green while the phone got a 200 and nothing to play — and idempotency
    # then preserved that state for the rest of the day.
    audio_bytes, _ = audio
    if len(audio_bytes) < MIN_PLAYABLE_AUDIO_BYTES:
        print(
            f"::error::Briefing {briefing_id} has a manifest and "
            f"{len(audio_bytes)} bytes of audio, which is not audio. The "
            "upload did not finish. Re-run with force=true."
        )
        return 1

    log.info(
        "briefing %s is present and playable: %d turns, %.0fs of audio",
        briefing_id,
        len(manifest.turns),
        manifest.duration_ms / 1000,
    )
    return 0


def _prune(retention_days: int, today_id: str, *, dry_run: bool) -> int:
    """Delete briefings past the retention window.

    Housekeeping, and it runs in the same workflow as generation, so the bar is
    that it can never be the reason a morning reports failure. A storage error
    here is worth an exit code; it is not worth the briefing.
    """
    from app.synthesis.storage import (
        build_storage,
        prune_briefings,
        validate_retention_days,
    )

    try:
        storage = build_storage()
    except Exception as exc:
        log.error("storage is not configured: %s", exc)
        return 2

    # Before the dry-run branch, not inside prune_briefings alone. A preview
    # of a command the real run would reject is not a preview of anything:
    # `--dry-run --retention-days 0` listed today's briefing as doomed and
    # exited 0, which reads as a description of what would happen.
    try:
        validate_retention_days(retention_days)
    except ValueError as exc:
        log.error("retention is misconfigured: %s", exc)
        return 2

    if dry_run:
        from datetime import date, timedelta

        try:
            cutoff = (
                date.fromisoformat(today_id) - timedelta(days=retention_days)
            ).isoformat()
            doomed = [
                briefing_id
                for briefing_id in storage.list_briefing_ids()
                if briefing_id < today_id and briefing_id < cutoff
            ]
        except Exception:
            log.exception("could not list briefings")
            return 1
        log.info(
            "--dry-run: retention is %d days (cutoff %s); would delete %d "
            "briefing(s): %s",
            retention_days,
            cutoff,
            len(doomed),
            ", ".join(doomed) or "none",
        )
        return 0

    try:
        prune_briefings(storage, retention_days=retention_days, today_id=today_id)
    except ValueError as exc:
        log.error("retention is misconfigured: %s", exc)
        return 2
    except Exception:
        log.exception("retention failed")
        return 1
    return 0


def _only_refusals(degraded: list[str]) -> bool:
    """Is every entry a SEGMENT the model refused, rather than a SOURCE that
    failed?

    `_ingest_concurrently` records source labels — email, calendar, weather,
    news. The refusal ladder records segment names — world, inbox,
    follow_ups. Tested against `REFUSABLE_SEGMENTS`, the ladder's actual
    vocabulary, NOT the whole `Segment` enum: that shares `calendar` with
    the sources, so a calendar fetch that failed on its own was filed as a
    model refusal and never retried. Both sets live in `schemas.py`, beside
    each other, and a test keeps them disjoint.
    """
    refusable = {segment.value for segment in REFUSABLE_SEGMENTS}
    return bool(degraded) and all(name in refusable for name in degraded)


async def _generate(
    briefing_id: str,
    length: BriefingLength,
    *,
    force: bool = False,
    allow_degraded: bool = False,
) -> int:
    from app.pipeline.generate import generate_briefing
    from app.synthesis.stitch import EmptyBriefingError
    from app.synthesis.storage import build_storage, existing_manifest

    try:
        storage = build_storage()
    except Exception as exc:
        log.error("storage is not configured: %s", exc)
        return 2

    # existing_manifest, not load_manifest: a manifest that will not parse must
    # not block its own replacement. Generation is idempotent, so the one state
    # that most needs regenerating would otherwise be the one state that never
    # can be.
    previous_degraded_attempts = 0
    existing = None if force else existing_manifest(storage, briefing_id)
    if (
        existing is not None
        and existing.degraded_sources
        and existing.degraded_attempts >= MAX_DEGRADED_ATTEMPTS
    ):
        # Stop retrying. The thing that degrades a briefing is almost always
        # a credential a human has to replace, and it stays broken until
        # they do — so an unbounded retry means every one of the day's eight
        # checkpoints runs a full generation, for a briefing that cannot
        # improve. Google TTS's free tier is a million characters a month
        # and a briefing is about six thousand.
        #
        # Loud rather than silent: the run still exits 3 further down, and
        # this says why the self-healing stopped instead of leaving someone
        # to wonder why the retry gave up.
        log.error(
            "briefing %s has shipped without %s on %d attempts — not "
            "regenerating again today. This needs a person: check the "
            "credentials for that source. --force overrides.",
            briefing_id,
            ", ".join(existing.degraded_sources),
            existing.degraded_attempts,
        )
        return 3

    if (
        existing is not None
        and existing.degraded_sources
        and _only_refusals(existing.degraded_sources)
    ):
        # A refusal is not a source failure, and regenerating cannot fix it.
        #
        # The ladder in `script_chain.generate_script` records the segments
        # it gave up when the model declined to write them, and it records
        # them here so the PHONE can say the briefing is not the one that was
        # configured. But the retry below exists for credentials and network
        # — things that come back. A content filter is deterministic on
        # content, so a regeneration pays a full LLM and TTS bill to be
        # refused again, three times, against a character allowance already
        # measured at ~88% and an Actions budget at ~82%. It then exits 3
        # advising "check the credentials for that source", when no source
        # failed.
        #
        # So: reported, not retried. A briefing minus its news is exactly
        # what rule 7 promises, and it is already stored.
        log.warning(
            "briefing %s shipped without %s because the model refused to "
            "write %s — reporting rather than regenerating, since a content "
            "refusal is deterministic and a retry would only spend quota",
            briefing_id,
            ", ".join(existing.degraded_sources),
            "them" if len(existing.degraded_sources) > 1 else "it",
        )
        return 0

    if existing is not None and existing.degraded_sources:
        # A briefing that shipped without its whole personal half is exactly
        # the one worth retrying, and idempotency made it the one that never
        # could be. Observed three days running: the first checkpoint exits 3
        # and goes red having stored a weather-and-news briefing, and every
        # later checkpoint reports success because that briefing is already
        # there. The retry existed and could not reach the thing it was for.
        #
        # Same shape as the empty-TTS guard in stitch.py (ADR-010's commit):
        # a bad artifact that blocks its own replacement is worse than no
        # artifact, because it looks like success from the outside.
        log.warning(
            "briefing %s exists but shipped without %s — regenerating rather "
            "than reporting success, since that is the state most worth "
            "replacing",
            briefing_id,
            ", ".join(existing.degraded_sources),
        )
        previous_degraded_attempts = existing.degraded_attempts
        existing = None

    if existing is not None:
        log.info(
            "briefing %s already exists; nothing to do (pass --force to "
            "regenerate, e.g. after fixing a run that shipped degraded)",
            briefing_id,
        )
        return 0
    if force:
        # Note what this deliberately does NOT do any more: delete the existing
        # audio first. It used to, so that a stale m4a could not outrank a
        # freshly written wav in load_audio's format order. But deleting before
        # regenerating means a regeneration that then FAILS leaves the old
        # manifest pointing at audio that no longer exists — the phone gets 200
        # on the manifest and 404 on the audio, which is a worse briefing than
        # the stale one it replaced, and it is now the state of the day.
        #
        # The stale-format problem belongs to the storage layer, which is the
        # only place that knows what it just wrote: both backends now clear the
        # other formats immediately AFTER the new audio lands, and before the
        # manifest that points at it. Nothing is ever deleted on the strength
        # of a regeneration that has not happened yet.
        log.info("--force: regenerating %s over the existing briefing", briefing_id)

    # The settings the app saved, if it ever has.
    #
    # Read BEFORE the providers are built, because it can change which
    # provider is built at all. This is what makes the settings screen more
    # than decoration: a value the phone writes and the cron never reads is
    # a control that appears to work, which is worse than no control.
    #
    # `apply_to_environment` rather than threading a settings object through
    # every call site: `get_settings()` is already the one place every
    # component asks about configuration, and adding a second source of
    # truth beside it is how the two drift.
    try:
        user_settings = await asyncio.to_thread(storage.load_settings)
    except Exception:
        # `load_settings` handles a missing or corrupt FILE, but not a
        # transient R2 error or a 403 from a rotated token — those raise, and
        # raising here aborted the run before generation started. A
        # preferences read must never be able to cost the morning; the
        # environment is a working configuration.
        log.error(
            "could not read saved settings; using the environment. Saved "
            "preferences are NOT being applied to this run.",
            exc_info=True,
        )
        user_settings = None

    if user_settings is not None:
        length = user_settings.length_or(length)
        apply_to_environment(user_settings)
        log.info(
            "applied saved settings (length=%s, provider=%s, feeds=%d, "
            "muted senders=%d, segments off=%s)",
            length.value,
            user_settings.tts_provider or "unset",
            len(user_settings.feeds),
            len(user_settings.muted_senders),
            ",".join(user_settings.disabled_segments or []) or "none",
        )

    try:
        from app.core.providers import build_tts_provider
        from app.providers.gemini import GeminiProvider

        llm, tts = GeminiProvider(), build_tts_provider()
    except RuntimeError as exc:
        log.error("credentials are not configured: %s", exc)
        return 2

    # Embeddings are what make cross-source dedup work (ADR-003), and they
    # are the only provider whose absence is survivable: build_stories
    # degrades to unclustered singletons, so the briefing still ships, it
    # just ships with the same story told twice. Not a reason to exit 2
    # alongside the LLM and TTS credentials, and not a reason to be quiet
    # about it either.
    embeddings = build_embedding_provider()

    try:
        manifest = await generate_briefing(
            briefing_id=briefing_id,
            length=length,
            llm=llm,
            tts=tts,
            embeddings=embeddings,
            storage=storage,
            previous_degraded_attempts=previous_degraded_attempts,
            feeds=user_settings.feeds if user_settings else None,
            muted_senders=user_settings.muted_senders if user_settings else None,
            interests=user_settings.interests if user_settings else None,
            followed_entities=(
                user_settings.followed_entities if user_settings else None
            ),
            music_set=user_settings.music_set if user_settings else None,
            disabled_segments=(
                user_settings.disabled_segments if user_settings else None
            ),
        )
    except EmptyBriefingError as exc:
        # Named separately from a generic failure because the operator action
        # is different: nothing was stored, so there is no stale briefing to
        # force past, and the thing to go and look at is the TTS provider.
        log.error("generation produced no audio for %s: %s", briefing_id, exc)
        # Name the provider that is actually configured. This said "check
        # Google TTS credentials and quota" unconditionally, which after
        # TTS_PROVIDER existed sent the operator to the wrong dashboard on
        # the one morning they are reading it in a hurry.
        from app.core.config import get_settings

        provider = (get_settings().tts_provider or "google").strip() or "google"
        print(
            f"::error::Briefing {briefing_id} produced NO AUDIBLE TURNS and was "
            f"not stored. Every TTS call failed — check {provider} credentials "
            "and quota. Nothing was written, so a re-run needs no --force."
        )
        return 1
    except Exception:
        log.exception("generation failed for %s", briefing_id)
        return 1

    log.info(
        "generated %s: %d turns, %.0fs of audio",
        manifest.briefing_id,
        len(manifest.turns),
        manifest.duration_ms / 1000,
    )

    # Surface a degraded briefing to the runner.
    #
    # Keyed off the ingestion failures, NOT the manifest's segment ids: the
    # script chain requires all seven canonical segments, so "calendar" and
    # "inbox" are present even when nothing was behind them. An earlier version
    # of this check inspected segment ids and could never fire.
    from app.pipeline.generate import (
        failed_personal_source_errors,
        failed_personal_sources,
    )

    failed = failed_personal_sources()
    if not failed:
        return 0

    print(
        f"::warning::Briefing {manifest.briefing_id} shipped without "
        f"{' and '.join(failed)}. It generated successfully but is missing "
        "personal content Mercury exists for — see the ingestion errors "
        "above."
    )

    # The one failure with a known cause and a permanent fix. While the OAuth
    # consent screen is in Testing mode Google expires refresh tokens after
    # seven days, so unattended generation stops every week — and because rule
    # 7 drops a failed source rather than raising, the only symptom is a
    # briefing that quietly stopped mentioning the user's inbox. Say the whole
    # thing, including the console path: this recurs, and the point is that it
    # is recognised instantly the second time.
    expired = sorted(
        label
        for label, exc in failed_personal_source_errors().items()
        if type(exc).__name__ == "ExpiredCredentialsError"
    )
    if expired:
        print(
            f"::error::The Google OAuth token has EXPIRED — {' and '.join(expired)} "
            "could not be read. While the consent screen is in Testing mode "
            "Google expires refresh tokens after 7 days, so this will now "
            "happen every week until it is fixed. Permanent fix: Google Cloud "
            "Console -> Google Auth Platform -> Audience -> Publish app (no "
            "verification review is needed for a single user on read-only "
            "scopes). Then re-mint token.json locally and update the "
            "GOOGLE_OAUTH_TOKEN_JSON secret."
        )

    if allow_degraded:
        log.warning(
            "briefing %s is degraded but --allow-degraded was passed; "
            "reporting success",
            manifest.briefing_id,
        )
        return 0

    # Exit non-zero even though the briefing was stored. Rule 7 is about what
    # SHIPS, not about what gets reported: the briefing is in R2 and the phone
    # will play it, and stale-free audio beats none. What changes is that the
    # run stops claiming success, because a green tick over a briefing with no
    # inbox in it is exactly how the 2026-08-27 failure went unnoticed for
    # days. GitHub emails on a failed run, and that email is the entire point.
    print(
        f"::error::Briefing {manifest.briefing_id} is DEGRADED: no "
        f"{' and no '.join(failed)}. The audio is in storage and the phone "
        "will play it, so this run is marked failed to tell you, not to stop "
        "you. Pass --allow-degraded if a degraded briefing should report "
        "success."
    )
    return 3


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="generate today's briefing")
    gen.add_argument(
        "--briefing-id",
        default=None,
        help="exact id to write; defaults to a new one for today",
    )
    gen.add_argument(
        "--briefing-day",
        default=None,
        help=(
            "day to generate for (YYYY-MM-DD); the run's time is appended. "
            "What the scheduler passes, so the runner cannot pick a "
            "different day than the checkpoint that decided to run."
        ),
    )
    gen.add_argument(
        "--force",
        action="store_true",
        help="regenerate even if this briefing already exists",
    )
    gen.add_argument(
        "--allow-degraded",
        action="store_true",
        help="exit 0 even if the briefing shipped without email or calendar",
    )
    gen.add_argument(
        "--length",
        default=None,
        choices=[b.value for b in BriefingLength],
        help="defaults to BRIEFING_LENGTH",
    )

    chk = sub.add_parser(
        "check",
        help="verify a briefing exists and is playable; exit 1 if it is not",
    )
    chk.add_argument("--briefing-id", default=None, help="defaults to today, local")

    fresh = sub.add_parser(
        "freshness",
        help="exit 5 if the newest briefing ANYWHERE is older than the floor",
    )
    fresh.add_argument(
        "--max-age-hours",
        type=float,
        required=True,
        help=(
            "how old the newest briefing may be before generation counts "
            "as stopped. Day-independent, unlike `check` — see ROADMAP "
            "task 31."
        ),
    )

    prune = sub.add_parser(
        "prune", help="delete briefings older than the retention window"
    )
    prune.add_argument(
        "--retention-days",
        type=int,
        default=None,
        help="defaults to RETENTION_DAYS",
    )
    prune.add_argument(
        "--dry-run",
        action="store_true",
        help="report what retention would consider, delete nothing",
    )

    args = parser.parse_args(argv)
    settings = get_settings()

    # "Today" is resolved once, in the USER's timezone, and every subcommand
    # uses the same answer — including prune, whose entire safety argument
    # rests on knowing which briefing is today's.
    today_id = _today_briefing_id(settings.user_timezone)

    # Before any day resolution, deliberately. Freshness asks about the
    # newest briefing ANYWHERE, so it needs no date — and routing it
    # through the `--briefing-id` machinery below would reintroduce the
    # wrong-day problem that made the first cut of this unreachable.
    if args.command == "freshness":
        return _freshness(args.max_age_hours)

    if args.command == "prune":
        return _prune(
            args.retention_days
            if args.retention_days is not None
            else settings.retention_days,
            today_id,
            dry_run=args.dry_run,
        )

    # What an absent `--briefing-id` means, and it is not the same question
    # for both subcommands now that a day can hold several briefings.
    #
    # `generate` wants a NEW id: naming a run after the day alone is what
    # made the second run of a morning overwrite the first, which is the
    # whole reason ids carry a time. `check` wants the newest one that
    # EXISTS — checking today's day id would look for a key nothing wrote
    # and report a perfectly healthy morning as missing.
    if args.command == "generate":
        # `--briefing-day` names the day and lets this run name the minute.
        # The scheduler decided which day is missing; the runner must not
        # re-derive it hours later, on the other side of local midnight.
        now = _user_now(settings.user_timezone)
        day = getattr(args, "briefing_day", None) or now.strftime("%Y-%m-%d")
        fallback = _free_briefing_id(day, now.strftime("%H%M"))
    elif args.command == "check":
        # A full id is checked as named. A bare DAY, or nothing at all, means
        # "is this morning covered?", which is answered by the newest
        # briefing for the day that was made for the morning (task 31).
        day = args.briefing_id or today_id
        if args.briefing_id and len(args.briefing_id) > len(today_id):
            fallback = args.briefing_id
        else:
            from app.synthesis.storage import build_storage

            try:
                storage = build_storage()
            except Exception as exc:
                log.error("storage is not configured: %s", exc)
                return 2
            try:
                now_hhmm = _user_now(settings.user_timezone).strftime("%H%M")
                fallback = _morning_briefing_id(storage, day, now_hhmm) or day
            except Exception:
                log.exception("could not list briefings for %s", day)
                print(
                    "::error::Could not list briefings from storage. This says "
                    "nothing about whether today's briefing exists — check the "
                    "R2 credentials before regenerating anything."
                )
                return 4
            # Already resolved; `_resolve_briefing_id` must not swap the
            # bare day back in over it.
            args.briefing_id = None
    elif args.briefing_id:
        # Named explicitly, so there is nothing to look up — and looking
        # anyway would turn a storage outage into a failure of a command
        # that did not need storage to choose its subject.
        fallback = args.briefing_id
    else:
        # A listing failure is NOT "no briefing". Reporting it as one exits 1
        # and sends the watchdog off regenerating something that exists;
        # exit 4 is the code that already means "storage would not answer".
        try:
            fallback = _newest_briefing_id(today_id) or today_id
        except Exception:
            log.exception("could not list briefings for %s", today_id)
            print(
                "::error::Could not list briefings from storage. This says "
                "nothing about whether today's briefing exists — check the R2 "
                "credentials before regenerating anything."
            )
            return 4

    briefing_id = _resolve_briefing_id(args.briefing_id, fallback)
    if briefing_id is None:
        return 2

    if args.command == "check":
        return _check(briefing_id)

    if args.command == "generate":
        raw_length = args.length or settings.briefing_length
        try:
            length = BriefingLength(raw_length)
        except ValueError:
            log.error("invalid briefing length %r", raw_length)
            return 2
        return asyncio.run(
            _generate(
                briefing_id,
                length,
                force=args.force,
                allow_degraded=args.allow_degraded,
            )
        )

    return 2


if __name__ == "__main__":
    sys.exit(main())
