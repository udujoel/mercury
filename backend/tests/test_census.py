"""The check that would have caught all four of them.

`n_stories=0`, `music_set`, `topic_centroid`, and the newsletter segment:
each was a finished, tested feature that ran zero times in production, and
each was invisible in the run log because a stage producing nothing logs
nothing.
"""

from __future__ import annotations

import logging

from app.pipeline.census import RunCensus


def test_a_stage_with_input_and_no_output_is_named():
    """The shape of every one of the four."""
    census = RunCensus()
    census.record("newsletters", took=13, made=0)
    assert census.report() == ["newsletters"]


def test_a_stage_with_no_input_is_not_an_alarm():
    """A Sunday with no newsletters is a quiet morning, not a defect. An
    alarm that fires on quiet mornings is one nobody reads by Wednesday."""
    census = RunCensus()
    census.record("newsletters", took=0, made=0)
    assert census.report() == []


def test_a_stage_allowed_to_be_empty_is_not_an_alarm():
    """Research on subjects the web has not covered, or follow-ups on a
    morning you owe nobody a reply."""
    census = RunCensus()
    census.record("research", took=6, made=0, may_be_empty=True)
    assert census.report() == []


def test_a_healthy_run_reports_nothing_starved():
    census = RunCensus()
    census.record("newsletters", took=13, made=6)
    census.record("articles", took=146, made=4)
    assert census.report() == []


def test_the_warning_says_what_to_check(caplog):
    """A warning that does not say what to do is one that gets skimmed."""
    census = RunCensus()
    with caplog.at_level(logging.WARNING):
        census.record("newsletters", took=13, made=0)
        census.report()
    assert "STARVED STAGE" in caplog.text
    assert "check what actually reaches it" in caplog.text


def test_reporting_an_empty_census_is_harmless():
    assert RunCensus().report() == []


def test_a_starved_stage_is_annotated_on_the_run_page(capsys, monkeypatch):
    """A warning inside 4,000 lines of pip output is a warning nobody reads.

    The watchdog's own header records the precedent: the 2026-08-27 failure
    "was found days later by reading gh run list".
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    census = RunCensus()
    census.record("newsletters", took=13, made=0)
    census.report()
    out = capsys.readouterr().out
    assert out.startswith("::warning::")
    assert "may not be wired to anything" in out
    assert out.count("\n") == 1, "a multi-line annotation is silently dropped"


def test_nothing_is_printed_outside_actions(capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    census = RunCensus()
    census.record("newsletters", took=13, made=0)
    census.report()
    assert capsys.readouterr().out == ""


# ──────────────────────────────────────────────────────────────
# The consumer. `report()` returned the starved names and nothing
# read them, which is the failure the module's own docstring warns
# about one paragraph earlier.
# ──────────────────────────────────────────────────────────────


def test_a_starved_stage_reaches_the_handler():
    from app.pipeline.census import RunCensus

    seen: list[list[str]] = []
    census = RunCensus(on_starved=seen.append)
    census.record("newsletters", took=13, made=0)
    census.record("articles", took=146, made=4)
    census.report()

    assert seen == [["newsletters"]]


def test_a_healthy_run_does_not_wake_the_handler():
    """The alarm has to stay quiet on a good morning or it trains people
    to ignore it — which is the thing this module exists to prevent."""
    from app.pipeline.census import RunCensus

    seen: list[list[str]] = []
    census = RunCensus(on_starved=seen.append)
    census.record("newsletters", took=12, made=6)
    census.record("inbox", took=13, made=0, may_be_empty=True)
    census.report()

    assert seen == []


def test_a_raising_handler_cannot_cost_the_morning(caplog):
    """Rule 7 reaches the instrumentation's instrumentation."""
    import logging

    from app.pipeline.census import RunCensus

    def _explode(_names):
        raise RuntimeError("handler is broken")

    census = RunCensus(on_starved=_explode)
    census.record("newsletters", took=13, made=0)
    with caplog.at_level(logging.WARNING):
        assert census.report() == ["newsletters"]
    assert "starved-stage handler failed" in caplog.text


def test_the_run_summary_names_the_stage(tmp_path, monkeypatch):
    """Written where a person looks, not only where a grep would find it.

    The `::warning::` annotation already fires. This is the second channel,
    because the annotation alone demonstrably was not enough: the
    2026-08-27 failure was "found days later by reading gh run list".
    """
    from app.pipeline.generate import _when_a_stage_is_starved

    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    _when_a_stage_is_starved(["newsletters"])

    written = summary.read_text()
    assert "Starved stage(s)" in written
    assert "`newsletters`" in written


def test_the_run_summary_is_silent_outside_actions(monkeypatch):
    """A developer running `app.cli generate` gets the log line and nothing
    else; there is no run page to annotate."""
    from app.pipeline.generate import _when_a_stage_is_starved

    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    _when_a_stage_is_starved(["newsletters"])  # must not raise


# ──────────────────────────────────────────────────────────────
# Why a stage is empty decides what to say about it.
#
# 2026-09-20, GitHub Actions run 35536697033: the Gemini project hit its
# monthly spend cap, every call 429'd, and this module printed
# "newsletters received 14 item(s) and produced nothing... this stage may
# not be wired to anything". The stage was wired correctly. The API was
# dead. An alarm that names the wrong cause sends the next reader hunting
# a bug that is not there — which is the time this module exists to save.
# ──────────────────────────────────────────────────────────────


def _exhausted() -> Exception:
    """The real exception from that run, by name and shape."""
    return type(
        "ResourceExhausted", (Exception,), {}
    )("429 Your project has exceeded its monthly spending cap.")


def test_a_failing_run_is_not_reported_as_a_wiring_defect(caplog):
    import logging

    from app.pipeline.census import RunCensus

    census = RunCensus()
    census.record("newsletters", took=14, made=0)
    with caplog.at_level(logging.WARNING):
        assert census.report(failure=_exhausted()) == ["newsletters"]

    assert "STARVED STAGE" not in caplog.text, (
        "the stage was wired correctly and the provider was dead; blaming "
        "the wiring costs the next reader an afternoon"
    )
    assert "ResourceExhausted" in caplog.text
    assert "spending cap" in caplog.text
    assert "consequence rather than a wiring defect" in caplog.text


def test_a_degraded_run_names_the_source_that_failed(caplog):
    import logging

    from app.pipeline.census import RunCensus

    census = RunCensus(degraded=["rss", "calendar"])
    census.record("articles", took=146, made=0)
    with caplog.at_level(logging.WARNING):
        census.report()

    assert "STARVED STAGE" not in caplog.text
    assert "calendar, rss" in caplog.text


def test_a_clean_run_still_gets_the_full_alarm(caplog):
    """The case this module was built for must keep its force. Nothing
    upstream failed, so an empty stage is the wiring question."""
    import logging

    from app.pipeline.census import RunCensus

    census = RunCensus()
    census.record("newsletters", took=13, made=0)
    with caplog.at_level(logging.WARNING):
        census.report()

    assert "STARVED STAGE: newsletters" in caplog.text
    assert "built but never called" in caplog.text


def test_the_run_summary_is_not_written_for_somebody_elses_failure():
    """The summary panel is for a finding someone has to chase. On a run
    that is already red with a traceback, a second panel saying a stage
    produced nothing invites exactly the wrong diagnosis."""
    from app.pipeline.census import RunCensus

    seen: list[list[str]] = []
    census = RunCensus(on_starved=seen.append)
    census.record("newsletters", took=14, made=0)
    census.report(failure=_exhausted())
    assert seen == []

    census = RunCensus(on_starved=seen.append)
    census.record("newsletters", took=14, made=0)
    census.report()
    assert seen == [["newsletters"]], "a clean run must still raise it"


def test_the_context_manager_hands_the_exception_to_the_report(caplog):
    """The wiring, not just the logic. `__exit__` receives the exception
    and has to pass it on — today's incident reached `report()` through
    exactly this path, during unwinding."""
    import logging

    import pytest

    from app.pipeline.census import RunCensus

    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(RuntimeError, match="spend cap"),
        RunCensus() as census,
    ):
        census.record("newsletters", took=14, made=0)
        raise RuntimeError("spend cap reached")

    assert "STARVED STAGE" not in caplog.text
    assert "spend cap reached" in caplog.text


def test_a_doubled_status_code_is_not_repeated_back():
    """`google.api_core` prepends its own code to the message it was given,
    so the live 429 stringifies as "429 429 Your project has exceeded...".
    This line is read by someone deciding whether they are looking at a
    wiring bug; a stutter in it is one more thing to rule out."""
    from google.api_core.exceptions import ResourceExhausted

    from app.pipeline.census import _detail_of

    # The REAL library exception, because the doubling is the library's
    # doing — a hand-rolled stand-in does not reproduce it, and a test
    # that cannot see the defect cannot protect the fix.
    real = ResourceExhausted(
        "429 Your project has exceeded its monthly spending cap."
    )
    assert "429 429" in str(real), "precondition: the library doubles it"
    assert _detail_of(real).startswith("429 Your project")
    assert "429 429" not in _detail_of(real)

    # And an ordinary message is never rewritten, including one that
    # genuinely repeats a word.
    assert _detail_of(RuntimeError("boom boom")) == "boom boom"
    assert _detail_of(RuntimeError("boom boom baz")) == "boom boom baz"
