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
