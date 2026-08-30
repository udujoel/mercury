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
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.core.schemas import BriefingLength

log = logging.getLogger("mercury.cli")


def _today_briefing_id(tz_name: str) -> str:
    """Today in the USER's timezone. Never UTC — this project has had five
    separate date bugs, and the scheduler, the API and this entrypoint must
    all name the same briefing the same way."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        log.error("invalid USER_TIMEZONE %r; falling back to UTC", tz_name)
        tz = ZoneInfo("UTC")
    return datetime.now(tz).strftime("%Y-%m-%d")


def _resolve_briefing_id(explicit: str | None, today_id: str) -> str | None:
    r"""The id both subcommands operate on, or None if it is malformed.

    A malformed id silently becomes a storage key nothing ever reads — and
    `check` would then report a perfectly healthy briefing as missing, which
    is a worse outcome than the bad write it was originally guarding.

    `is_briefing_id` rather than a local regex, because this used to be its
    own `\d{4}-\d{2}-\d{2}` copy and accepted `2026-01-99` — a key retention
    cannot parse as a date and could therefore never age out.
    """
    from app.synthesis.storage import is_briefing_id

    briefing_id = explicit or today_id
    if not is_briefing_id(briefing_id):
        log.error(
            "briefing id %r is not a real YYYY-MM-DD date; refusing to act on "
            "an id nothing will look for", briefing_id,
        )
        return None
    return briefing_id


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
        briefing_id, len(manifest.turns), manifest.duration_ms / 1000,
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
            retention_days, cutoff, len(doomed), ", ".join(doomed) or "none",
        )
        return 0

    try:
        prune_briefings(
            storage, retention_days=retention_days, today_id=today_id
        )
    except ValueError as exc:
        log.error("retention is misconfigured: %s", exc)
        return 2
    except Exception:
        log.exception("retention failed")
        return 1
    return 0


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
    if not force and existing_manifest(storage, briefing_id) is not None:
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
        log.info("--force: regenerating %s over the existing briefing",
                 briefing_id)

    try:
        from app.providers.gemini import GeminiProvider
        from app.providers.google_tts import GoogleTTSProvider

        llm, tts = GeminiProvider(), GoogleTTSProvider()
    except RuntimeError as exc:
        log.error("credentials are not configured: %s", exc)
        return 2

    try:
        manifest = await generate_briefing(
            briefing_id=briefing_id, length=length, llm=llm, tts=tts, storage=storage
        )
    except EmptyBriefingError as exc:
        # Named separately from a generic failure because the operator action
        # is different: nothing was stored, so there is no stale briefing to
        # force past, and the thing to go and look at is the TTS provider.
        log.error("generation produced no audio for %s: %s", briefing_id, exc)
        print(
            f"::error::Briefing {briefing_id} produced NO AUDIBLE TURNS and was "
            "not stored. Every TTS call failed — check Google TTS credentials "
            "and quota. Nothing was written, so a re-run needs no --force."
        )
        return 1
    except Exception:
        log.exception("generation failed for %s", briefing_id)
        return 1

    log.info(
        "generated %s: %d turns, %.0fs of audio",
        manifest.briefing_id, len(manifest.turns), manifest.duration_ms / 1000,
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
            "reporting success", manifest.briefing_id,
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
    gen.add_argument("--briefing-id", default=None, help="defaults to today, local")
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

    if args.command == "prune":
        return _prune(
            args.retention_days
            if args.retention_days is not None
            else settings.retention_days,
            today_id,
            dry_run=args.dry_run,
        )

    briefing_id = _resolve_briefing_id(args.briefing_id, today_id)
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
