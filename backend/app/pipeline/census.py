"""What each stage of the run took in, and what it gave back.

**Why this exists.** Four times now a finished, tested feature has run zero
times in production and nothing said so:

  - Phase 2's news half passed a hardcoded `n_stories=0` into every briefing
  - `music_set` was stripped by a Worker whitelist, so the setting never
    once applied
  - `score_stories` accepted `topic_centroid`, and no caller passed it
    (fixed in #58 — and the settings field it needed had never been built)
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
from collections.abc import Callable
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
    # What had already gone wrong at the moment this stage ran.
    #
    # A snapshot, not a live reference, and that is the whole point.
    # `script_chain._give_up` appends "world"/"inbox"/"follow_ups" to the
    # run's degraded list from INSIDE `generate_script`, long after the
    # newsletter extractor finished — so reading the list at report time
    # let a refusal that happened later "explain" a stage that had already
    # produced nothing. Review of #56 caught that being exactly the bug
    # #56 exists to fix: an alarm naming the wrong cause.
    #
    # Causality is the rule. Only something that had already failed when a
    # stage ran can account for that stage being empty.
    degraded_before: tuple[str, ...] = ()

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
    """Collected across a run, reported once at the end.

    Used as a context manager, so the report happens in a `finally` rather
    than on the happy path:

        with RunCensus() as census:
            census.record("newsletters", took=13, made=0)
            ...

    That is not tidiness. The first cut called `report()` inline near the
    end of the run, after TTS and after the digest — so the one run that
    produced nothing at all, the run where every turn failed synthesis and
    `EmptyBriefingError` came up through the middle of the pipeline, was
    the single run with no census line in its log. The census exists for
    the morning that went wrong; it has to outlive the exception.
    """

    stages: list[Stage] = field(default_factory=list)
    # Called once with the starved stage names, if there are any. The
    # alternative was leaving `report()`'s return value on the floor, which
    # is what the docstring below warns about in the same breath — a
    # warning nobody reads is how all four of these survived.
    on_starved: Callable[[list[str]], None] | None = None
    # The run's degraded-source list, by REFERENCE. `generate_briefing`
    # builds it during ingestion and appends to it as segments drop, and
    # holding the same object means the census sees those later appends
    # without a second plumbing path that could disagree with the first.
    degraded: list[str] = field(default_factory=list)

    def __enter__(self) -> RunCensus:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        """Report, whatever happened, and never change what happened.

        Returns False always: instrumentation that swallowed a live
        exception would turn a failed briefing into a silent success, which
        is a far worse version of the bug this module was written to catch.
        """
        try:
            self.report(failure=exc)
        except Exception:  # pragma: no cover - instrumentation must not bite
            log.warning("run census failed to report", exc_info=True)
        return False

    def record(
        self, name: str, *, took: int, made: int, may_be_empty: bool = False
    ) -> None:
        self.stages.append(
            Stage(
                name=name,
                took=took,
                made=made,
                may_be_empty=may_be_empty,
                degraded_before=tuple(self.degraded),
            )
        )

    def report(self, *, failure: BaseException | None = None) -> list[str]:
        """Log the census and return the names of any starved stages.

        Returned rather than only logged so a caller can put them on the
        manifest or a watchdog can act on them — a warning nobody reads is
        how all four of these survived.

        `failure` is the exception unwinding the run, when there is one.
        It changes what the warning SAYS, and a real incident is why. On
        2026-09-20 the Gemini project hit its monthly spend cap, every call
        409'd and then 429'd, and this printed "newsletters received 14
        item(s) and produced nothing... this stage may not be wired to
        anything". The stage was wired correctly. The API was dead. An
        alarm that names the wrong cause is only marginally better than no
        alarm: it sends the next reader hunting a bug that is not there,
        and the time it costs is the time this module exists to save.
        """
        if not self.stages:
            return []
        summary = "  ".join(f"{s.name} {s.took}->{s.made}" for s in self.stages)
        log.info("run census: %s", summary)

        starved = [s.name for s in self.stages if s.starved]
        # Why a stage is empty decides what to say about it, and it is
        # asked PER STAGE. Three cases, and only the third is the one this
        # module was built for.
        run_failed = (
            f"the run is failing with {type(failure).__name__}: "
            f"{_detail_of(failure)}"
            if failure is not None
            else ""
        )

        def _why(stage: Stage) -> str:
            if run_failed:
                return run_failed
            if stage.degraded_before:
                return "this had already failed when it ran: " + ", ".join(
                    sorted(stage.degraded_before)
                )
            return ""

        unexplained: list[str] = []
        for stage in (s for s in self.stages if s.starved):
            because = _why(stage)
            if because:
                # Reported, not shouted, and the annotation says the same
                # thing. Something that had already failed accounts for
                # this, and on a failing run the page is already showing it.
                log.warning(
                    "%s received %d item(s) and produced nothing — %s. "
                    "Probably a consequence rather than a wiring defect, "
                    "but worth confirming the stage recovers once that is "
                    "fixed",
                    stage.name,
                    stage.took,
                    because,
                )
                if failure is None:
                    # A DEGRADED run still shipped, so nobody is looking at
                    # a red run to explain it. A FAILING one does not need
                    # a second panel competing with its own traceback.
                    _annotate(
                        f"{stage.name} received {stage.took} item(s) and "
                        f"produced nothing, after {because}"
                    )
                continue

            unexplained.append(stage.name)

            log.warning(
                "STARVED STAGE: %s received %d item(s) and produced nothing, "
                "and nothing upstream failed. This is the shape of every "
                "'built but never called' bug in this project's history — "
                "check what actually reaches it",
                stage.name,
                stage.took,
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
        # The summary panel is only ever about UNEXPLAINED starvation.
        #
        # Its text asks the reader to go and check what reaches the stage,
        # which is the right question when nothing else went wrong and the
        # wrong one otherwise — so a stage with an explanation gets the
        # softer log line and annotation above, and never the panel. This
        # is narrower than `not because` was: that suppressed the panel
        # for EVERY starved stage as soon as any one of them had an
        # explanation.
        if unexplained and self.on_starved is not None:
            # Guarded like everything else here. A handler that raises must
            # not become the thing that loses the morning.
            try:
                self.on_starved(unexplained)
            except Exception:  # pragma: no cover - instrumentation must not bite
                log.warning("census starved-stage handler failed", exc_info=True)
        return starved


def _detail_of(failure: BaseException) -> str:
    """The exception's message, trimmed, without a doubled status code.

    `google.api_core` prepends its own numeric code to whatever the
    exception was constructed with, so today's spend-cap error stringifies
    as "429 429 Your project has exceeded...". Cosmetic, but this line
    exists to be read in a hurry by someone deciding whether they are
    looking at a wiring bug, and a stutter in it reads like a logging
    defect that then needs ruling out too.
    """
    # Collapsed to ONE LINE first. A multi-line message split the stage
    # name from "probably a consequence rather than a wiring defect", in a
    # module whose entire thesis is being legible inside 4,000 lines of pip
    # output — and `_annotate` only escapes newlines it is handed directly.
    detail = " ".join(str(failure).split())[:200]
    if not detail:
        # `asyncio.CancelledError()` really does stringify to "", and
        # "the run is failing with CancelledError: ." reads as a logging
        # bug the reader then has to rule out as well.
        return "no message"
    head, sep, rest = detail.partition(" ")
    # Only when the repeated token is a STATUS CODE. A bare
    # "starts with the same word twice" rule would quietly rewrite
    # "boom boom baz" to "boom baz", and silently editing an error
    # message is a worse bug than the stutter it tidies.
    if sep and head.isdigit() and rest.startswith(f"{head} "):
        detail = f"{head} {rest[len(head) + 1:]}"
    return detail


def _annotate(message: str) -> None:
    """A GitHub Actions warning annotation, when running in Actions.

    Silent elsewhere: a developer running `app.cli generate` locally does
    not need `::warning::` noise, and the log line above already said it.
    """
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    # One line, no newlines — a multi-line annotation is silently dropped.
    print(f"::warning::{message.replace(chr(10), ' ')}", file=sys.stdout, flush=True)
