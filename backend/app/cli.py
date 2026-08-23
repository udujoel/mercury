"""
Command-line entrypoint for scheduled generation.

This is what the GitHub Actions cron runs (ADR-008). It exists so the daily
job does not need an HTTP server at all: the workflow calls this, it writes
the finished briefing to R2, and the phone reads from R2 directly.

    python -m app.cli generate [--length standard] [--briefing-id 2026-08-22]

Exit codes matter here — Actions decides whether to mark the run failed and
email you, and a briefing that silently did not generate is the failure mode
the whole schedule exists to prevent:

    0  a briefing exists for this id (generated now, or already there)
    1  generation failed
    2  configuration is wrong (missing credentials, bad storage config)
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


async def _generate(
    briefing_id: str, length: BriefingLength, *, force: bool = False
) -> int:
    from app.pipeline.generate import generate_briefing
    from app.synthesis.storage import build_storage

    try:
        storage = build_storage()
    except Exception as exc:
        log.error("storage is not configured: %s", exc)
        return 2

    if not force and storage.load_manifest(briefing_id) is not None:
        log.info(
            "briefing %s already exists; nothing to do (pass --force to "
            "regenerate, e.g. after fixing a run that shipped degraded)",
            briefing_id,
        )
        return 0
    if force:
        log.info("--force: regenerating %s even though it may exist", briefing_id)

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
    except Exception:
        log.exception("generation failed for %s", briefing_id)
        return 1

    log.info(
        "generated %s: %d turns, %.0fs of audio",
        manifest.briefing_id, len(manifest.turns), manifest.duration_ms / 1000,
    )

    # Surface a degraded briefing to the runner. A GitHub ::warning:: shows on
    # the run summary, so a briefing that shipped without the user's actual day
    # is visible without reading the log.
    #
    # Keyed off the ingestion failures, NOT the manifest's segment ids: the
    # script chain requires all seven canonical segments, so "calendar" and
    # "inbox" are present even when nothing was behind them. An earlier version
    # of this check inspected segment ids and could never fire.
    from app.pipeline.generate import failed_personal_sources

    failed = failed_personal_sources()
    if failed:
        print(
            f"::warning::Briefing {manifest.briefing_id} shipped without "
            f"{' and '.join(failed)}. It generated successfully but is missing "
            "personal content Mercury exists for — see the ingestion errors "
            "above."
        )
    return 0


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
        "--length",
        default=None,
        choices=[b.value for b in BriefingLength],
        help="defaults to BRIEFING_LENGTH",
    )

    args = parser.parse_args(argv)
    settings = get_settings()

    if args.command == "generate":
        briefing_id = args.briefing_id or _today_briefing_id(settings.user_timezone)
        raw_length = args.length or settings.briefing_length
        try:
            length = BriefingLength(raw_length)
        except ValueError:
            log.error("invalid briefing length %r", raw_length)
            return 2
        return asyncio.run(_generate(briefing_id, length, force=args.force))

    return 2


if __name__ == "__main__":
    sys.exit(main())
