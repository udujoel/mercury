"""The user's timezone, resolved in exactly one place.

Every date in a briefing is the USER's date, never UTC's. Generated at 00:11
in Europe/Lisbon, datetime.now(UTC) still reads as the previous day, and
this project has shipped that bug in several forms: a cold open greeting the
wrong day, a scheduler filing a briefing under yesterday, a default id
colliding with yesterday's stored briefing.

Five modules used to resolve USER_TIMEZONE themselves, and they disagreed.
Two logged a bad zone name, two fell back to UTC silently, and calendar
ingestion did not fall back at all, so a typo dropped the calendar segment
while the rest of the briefing quietly dated itself in UTC.
`tests/test_tz.py` fails if a module constructs a `ZoneInfo` anywhere else.
"""

from __future__ import annotations

import logging
from datetime import UTC, tzinfo
from zoneinfo import ZoneInfo

from app.core.config import get_settings

log = logging.getLogger(__name__)


def resolve_tz(name: str) -> tzinfo:
    """`name` as a zone, or UTC, loudly, if it does not resolve.

    Catches everything rather than only `ZoneInfoNotFoundError`: an empty
    string, a trailing slash or a path-like key raises `ValueError`, and a
    briefing must degrade on a bad setting, never fail (CLAUDE.md rule 7).
    The fallback is logged because it reintroduces the very bug the user's
    zone exists to prevent, and nothing downstream can tell.

    The fallback is `datetime.UTC`, not `ZoneInfo("UTC")`: with no system
    zone database and no `tzdata` package, `ZoneInfo("UTC")` raises too, and
    the one value that must always work would be the one that crashed.
    """
    try:
        return ZoneInfo(name)
    except Exception:
        log.error("cannot resolve USER_TIMEZONE %r; dating the briefing in UTC", name)
        return UTC


def user_tz() -> tzinfo:
    """The configured user's zone, through `resolve_tz`."""
    return resolve_tz(get_settings().user_timezone)
