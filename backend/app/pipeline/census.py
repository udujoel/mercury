"""What each stage of the run took in, and what it gave back.

**Why this exists.** Four times now a finished, tested feature has run zero
times in production and nothing said so:

  - Phase 2's news half passed a hardcoded `n_stories=0` into every briefing
  - `music_set` was stripped by a Worker whitelist, so the setting never
    once applied
  - `score_stories` accepts `topic_centroid`, and no caller passes it
  - the newsletter segment was fed from a cap that ranks newsletters last,
    and produced nothing for sixteen consecutive briefings

Every one of those was green in CI, and every one was invisible in the run
log — because a stage that produces nothing logs nothing, and a log with no
line about newsletters looks exactly like a morning with no newsletters.

CLAUDE.md's standing advice is "when a phase is marked done, check what
CALLS the new code". That is advice to a person who remembers to follow it.
This is the same check, run by the machine, on every briefing.

**What it deliberately does NOT do.** It never fails a run. A census that
can break the morning is a worse trade than the bug it catches — rule 7
applies to the instrumentation too. It logs, and a human or a watchdog
reads it.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Stage:
    """One pipeline step's intake and output."""

    name: str
    took: int
    made: int
    # Some stages legitimately produce nothing from real input — the
    # follow-ups segment on a morning you owe nobody a reply, research on
    # subjects the web has not covered. Those set this, and are reported
    # without the alarm.
    may_be_empty: bool = False

    @property
    def starved(self) -> bool:
        """Had something to work with, and produced nothing anyway.

        The shape of all four bugs above. Not proof of a defect — but it is
        the question nobody was asking, and in four for four cases the
        answer was yes.
        """
        return self.took > 0 and self.made == 0 and not self.may_be_empty


@dataclass
class RunCensus:
    """Collected across a run, reported once at the end."""

    stages: list[Stage] = field(default_factory=list)

    def record(
        self, name: str, *, took: int, made: int, may_be_empty: bool = False
    ) -> None:
        self.stages.append(
            Stage(name=name, took=took, made=made, may_be_empty=may_be_empty)
        )

    def report(self) -> list[str]:
        """Log the census and return the names of any starved stages.

        Returned rather than only logged so a caller can put them on the
        manifest or a watchdog can act on them — a warning nobody reads is
        how all four of these survived.
        """
        if not self.stages:
            return []
        summary = "  ".join(f"{s.name} {s.took}->{s.made}" for s in self.stages)
        log.info("run census: %s", summary)

        starved = [s.name for s in self.stages if s.starved]
        for stage in (s for s in self.stages if s.starved):
            log.warning(
                "STARVED STAGE: %s received %d item(s) and produced nothing. "
                "This is the shape of every 'built but never called' bug in "
                "this project's history — check what actually reaches it",
                stage.name, stage.took,
            )
            # And on the run page, not only in the scrollback.
            #
            # The log line alone is not enough and this project has the
            # receipts: the watchdog's own header records that the
            # 2026-08-27 failure "was found days later by reading gh run
            # list". A WARNING inside 4,000 lines of pip output is a
            # warning nobody reads — which is how the newsletter segment
            # ran zero times for four days while every run said "success".
            #
            # `::warning::` annotates the run WITHOUT failing it. Rule 7
            # applies to the instrumentation: a starved stage is a defect
            # worth shouting about, not a reason to lose the morning.
            _annotate(
                f"{stage.name} received {stage.took} item(s) and produced "
                f"nothing — this stage may not be wired to anything"
            )
        return starved


def _annotate(message: str) -> None:
    """A GitHub Actions warning annotation, when running in Actions.

    Silent elsewhere: a developer running `app.cli generate` locally does
    not need `::warning::` noise, and the log line above already said it.
    """
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    # One line, no newlines — a multi-line annotation is silently dropped.
    print(f"::warning::{message.replace(chr(10), ' ')}", file=sys.stdout, flush=True)
