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
