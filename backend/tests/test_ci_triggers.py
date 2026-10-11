"""Each test suite runs once per change, not twice.

The suites triggered on `push` to EVERY branch and on `pull_request`, so
every push to a branch with an open PR ran each suite twice. In October
2026 that was half of all CI runs: 17 of 34 backend, 6 of 12 iOS and 5 of
8 Worker runs were branch pushes duplicating a PR run. The iOS job runs on
macOS, which bills at 10x.

That matters because GitHub Actions is also where the briefing is
generated. The private repo's minutes ran out on 2026-09-22, and every
briefing from the 23rd to the 30th failed with "The job was not started
because ... your spending limit needs to be increased". The watchdog
couldn't say so, because it is an Actions workflow too.

`push` stays, restricted to `main`. The comments in each workflow explain
why: a filter on `pull_request` alone lets a direct push to the default
branch through with the suite never running.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
SUITES = ["backend.yml", "ios.yml", "worker.yml"]


def _spec(name: str) -> dict:
    spec = yaml.safe_load((WORKFLOWS / name).read_text())
    # PyYAML parses the bare key `on:` as the boolean True.
    spec["on"] = spec.get("on") or spec.get(True)
    return spec


@pytest.mark.parametrize("name", SUITES)
def test_a_branch_push_does_not_duplicate_the_pr_run(name):
    on = _spec(name)["on"]

    assert on["push"].get("branches") == ["main"], (
        f"{name}: `push` must be limited to main. On other branches the "
        "`pull_request` trigger already runs this suite."
    )
    assert "pull_request" in on


@pytest.mark.parametrize("name", SUITES)
def test_both_triggers_watch_the_same_paths(name):
    """The property the existing comments defend: no path that a PR tests
    may reach `main` by direct push untested, and vice versa."""
    on = _spec(name)["on"]

    assert on["push"]["paths"] == on["pull_request"]["paths"]


def _group(expression: str, *, event: str, ref: str, sha: str, workflow: str) -> str:
    """Evaluate the one expression shape these workflows use for the group:
    `${{ github.workflow }}-${{ github.event_name == 'pull_request' &&
    github.ref || github.sha }}`. Anything else fails loudly rather than
    being guessed at, so a rewrite has to come with a matching evaluator."""
    expected = (
        "${{ github.workflow }}-${{ github.event_name == 'pull_request' "
        "&& github.ref || github.sha }}"
    )
    assert expression == expected, f"unrecognised concurrency group: {expression}"
    return f"{workflow}-{ref if event == 'pull_request' else sha}"


@pytest.mark.parametrize("name", SUITES)
def test_a_superseded_pr_run_is_cancelled(name):
    """Two quick pushes to a PR share a group, and the newer cancels the
    older."""
    concurrency = _spec(name)["concurrency"]
    group = concurrency["group"]
    pr = dict(event="pull_request", ref="refs/pull/7/merge", workflow=name)

    assert _group(group, sha="aaa", **pr) == _group(group, sha="bbb", **pr)
    assert concurrency["cancel-in-progress"] == (
        "${{ github.event_name == 'pull_request' }}"
    )


@pytest.mark.parametrize("name", SUITES)
def test_main_runs_never_share_a_group(name):
    """GitHub keeps one running and one PENDING run per group, and cancels
    an older pending run when a newer one queues, even with
    `cancel-in-progress: false`. Keyed by ref, three quick merges to `main`
    would leave the middle commit untested. Keyed by commit, every landed
    commit gets its own run."""
    group = _spec(name)["concurrency"]["group"]
    main = dict(event="push", ref="refs/heads/main", workflow=name)

    assert _group(group, sha="aaa", **main) != _group(group, sha="bbb", **main)
