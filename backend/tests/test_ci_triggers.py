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


def _ios_steps() -> list[dict]:
    return _spec("ios.yml")["jobs"]["test"]["steps"]


def _step(name_prefix: str) -> dict:
    return next(s for s in _ios_steps() if s.get("name", "").startswith(name_prefix))


def _run_step(step: dict, tmp_path, *, stubs: dict[str, str], env: dict[str, str]):
    """Run a workflow step's script under bash, with commands stubbed on PATH."""
    import os
    import subprocess

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in stubs.items():
        stub = bin_dir / name
        stub.write_text("#!/bin/bash\n" + body + "\n")
        stub.chmod(0o755)
    script = tmp_path / "step.sh"
    script.write_text(step["run"])
    return subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", **env},
    )


def test_ios_ci_selects_the_newest_released_xcode_26(tmp_path):
    """ROADMAP task 60. `macos-15` defaults to Xcode 16.4 (Swift 6.1, the
    iOS 18 SDK), so CI could not compile an iOS 26 API: the first push of
    #76 failed there and nowhere else. The step is RUN here, against fake
    app bundles, so a wrong choice fails rather than a missing word."""
    apps = tmp_path / "Applications"
    for name in (
        "Xcode_16.4.app",
        "Xcode_26.0.1.app",
        "Xcode_26.3.0.app",
        "Xcode_26.4_beta_2.app",
        "Xcode_26.4_Release_Candidate.app",
    ):
        (apps / name).mkdir(parents=True)
    step = _step("Use the newest released Xcode")

    result = _run_step(
        step,
        tmp_path,
        stubs={"sudo": 'echo "sudo $*"'},
        env={"XCODE_APPS_DIR": str(apps), **step["env"]},
    )

    assert result.returncode == 0, result.stderr
    assert f"sudo xcode-select -s {apps}/Xcode_26.3.0.app" in result.stdout


def test_ios_ci_fails_loudly_without_an_xcode_26(tmp_path):
    apps = tmp_path / "Applications"
    (apps / "Xcode_16.4.app").mkdir(parents=True)
    step = _step("Use the newest released Xcode")

    result = _run_step(
        step,
        tmp_path,
        stubs={"sudo": 'echo "sudo $*"'},
        env={"XCODE_APPS_DIR": str(apps), **step["env"]},
    )

    assert result.returncode != 0
    assert "No released Xcode 26.x" in result.stdout
    assert "xcode-select" not in result.stdout


def test_ios_ci_picks_an_iphone_on_the_newest_runtime(tmp_path):
    """Not a pinned model name: a newer Xcode ships different simulators."""
    import json

    devices = {
        "devices": {
            "com.apple.CoreSimulator.SimRuntime.iOS-18-5": [
                {"name": "iPhone 16", "udid": "OLD-IPHONE"},
            ],
            "com.apple.CoreSimulator.SimRuntime.iOS-26-2": [
                {"name": "iPad Pro 13-inch", "udid": "NEW-IPAD"},
                {"name": "iPhone 17 Pro", "udid": "NEW-IPHONE"},
            ],
            "com.apple.CoreSimulator.SimRuntime.watchOS-26-0": [
                {"name": "Apple Watch", "udid": "WATCH"},
            ],
        }
    }
    (tmp_path / "devices.json").write_text(json.dumps(devices))
    out = tmp_path / "github_output"

    result = _run_step(
        _step("Pick a simulator"),
        tmp_path,
        stubs={"xcrun": f"cat {tmp_path / 'devices.json'}"},
        env={"GITHUB_OUTPUT": str(out)},
    )

    assert result.returncode == 0, result.stderr
    assert out.read_text().strip() == "udid=NEW-IPHONE"


def test_ios_ci_fails_loudly_without_an_iphone(tmp_path):
    import json

    (tmp_path / "devices.json").write_text(json.dumps({"devices": {}}))

    result = _run_step(
        _step("Pick a simulator"),
        tmp_path,
        stubs={"xcrun": f"cat {tmp_path / 'devices.json'}"},
        env={"GITHUB_OUTPUT": str(tmp_path / "github_output")},
    )

    assert result.returncode != 0


def test_ios_ci_does_not_pin_a_simulator_model():
    test_step = next(s for s in _ios_steps() if s.get("name") == "Test")

    assert "name=iPhone" not in test_step["run"]
    assert "steps.sim.outputs.udid" in test_step["run"]


def test_backend_ci_keeps_the_code_formatted():
    """ROADMAP task 43. CLAUDE.md says "format on save", and by 2026-10-11
    88 of 124 backend files had drifted from it. The whole backend was
    formatted once; this keeps CI checking so it cannot drift again."""
    runs = [
        step.get("run", "")
        for job in _spec("backend.yml")["jobs"].values()
        for step in job["steps"]
    ]

    assert any("ruff format --check" in run for run in runs)
