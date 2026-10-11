"""
Tests for `python -m app.cli generate`, and specifically for what --force is
allowed to destroy.

Generation is idempotent, so --force exists to replace a briefing that already
exists — a run that shipped degraded, or one with no audio in it. The danger is
that "replace" was implemented as "delete, then try to make a new one", which
is only equivalent when the second half succeeds.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.cli import MAX_DEGRADED_ATTEMPTS, main
from app.core.schemas import BriefingManifest, Turn
from app.synthesis.stitch import EmptyBriefingError
from app.synthesis.storage import LocalFilesystemStorage

# The real ExpiredCredentialsError, not a look-alike. app.cli identifies it by
# type name, and a stand-in defined here would pass whether or not the two
# names still agree — which is the whole thing being asserted.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _google_auth import ExpiredCredentialsError  # noqa: E402


def _manifest(briefing_id: str = "2026-08-27") -> BriefingManifest:
    return BriefingManifest(
        briefing_id=briefing_id,
        generated_at=datetime.now(UTC),
        duration_ms=1000,
        audio_url=f"/briefing/{briefing_id}/audio",
        segments=[{"id": "cold_open", "label": "Good morning", "start_ms": 0}],
        turns=[Turn(speaker="A", segment="cold_open", text="Morning.")],
        sources=[],
    )


@pytest.fixture
def cli(monkeypatch, tmp_path):
    """The CLI wired to temp storage and stub providers.

    Providers are stubbed rather than configured: constructing the real ones
    raises without credentials, and every test here fails or succeeds before
    a provider is ever called.
    """
    storage = LocalFilesystemStorage(base_dir=tmp_path)
    monkeypatch.setattr(
        "app.synthesis.storage.build_storage", lambda: storage, raising=True
    )
    monkeypatch.setattr(
        "app.providers.gemini.GeminiProvider", lambda: object(), raising=True
    )
    monkeypatch.setattr(
        "app.providers.google_tts.GoogleTTSProvider", lambda: object(), raising=True
    )
    return storage


def _fails_with(monkeypatch, exc: Exception) -> None:
    async def _boom(**_kwargs):
        raise exc

    monkeypatch.setattr(
        "app.pipeline.generate.generate_briefing", _boom, raising=True
    )


def test_force_keeps_the_old_briefing_when_regeneration_fails(
    cli, monkeypatch
):
    """The finding. --force used to delete the audio BEFORE regenerating, so a
    failed retry stranded a manifest pointing at audio that no longer existed:
    the phone got 200 on the manifest and 404 on the audio, and that was the
    state of the day. A failed replacement must leave the previous briefing
    exactly as it was — stale audio beats no audio."""
    cli.save("2026-08-27", b"OLD AUDIO", "wav", _manifest())
    _fails_with(monkeypatch, RuntimeError("Gemini 503"))

    assert main(["generate", "--force", "--briefing-id", "2026-08-27"]) == 1

    assert cli.load_manifest("2026-08-27") is not None
    audio = cli.load_audio("2026-08-27")
    assert audio is not None
    assert audio[0] == b"OLD AUDIO"


def test_force_keeps_the_old_briefing_when_every_turn_fails_to_synthesize(
    cli, monkeypatch
):
    """Same guarantee for the total-TTS-failure path, which is the most likely
    reason anyone reaches for --force in the first place."""
    cli.save("2026-08-27", b"OLD AUDIO", "wav", _manifest())
    _fails_with(monkeypatch, EmptyBriefingError("no audible turns"))

    assert main(["generate", "--force", "--briefing-id", "2026-08-27"]) == 1

    audio = cli.load_audio("2026-08-27")
    assert audio is not None
    assert audio[0] == b"OLD AUDIO"


def test_without_force_an_existing_briefing_is_a_no_op(cli, monkeypatch):
    """The property the whole multi-trigger schedule depends on: a later run
    that finds a briefing already there costs one read and exits 0."""
    cli.save("2026-08-27", b"OLD AUDIO", "wav", _manifest())

    def _never(**_kwargs):
        raise AssertionError("generation must not run when a briefing exists")

    monkeypatch.setattr(
        "app.pipeline.generate.generate_briefing", _never, raising=True
    )

    assert main(["generate", "--briefing-id", "2026-08-27"]) == 0


def test_an_empty_briefing_failure_is_reported_distinctly(
    cli, monkeypatch, capsys
):
    """Nothing was stored, so the operator must not be told to re-run with
    --force — the action is to go and look at the TTS provider."""
    _fails_with(monkeypatch, EmptyBriefingError("no audible turns"))

    assert main(["generate", "--briefing-id", "2026-08-27"]) == 1
    out = capsys.readouterr().out
    assert "NO AUDIBLE TURNS" in out
    assert "no --force" in out


def _succeeds_but_degraded(monkeypatch, storage, failures):
    """A generation that stores a real briefing while some personal source
    failed — which is exactly what CLAUDE.md rule 7 asks the pipeline to do,
    and exactly the run that used to exit 0 and tell nobody."""
    from app.pipeline import generate as generate_module

    async def _ok(*, briefing_id, **_kwargs):
        manifest = _manifest(briefing_id)
        storage.save(briefing_id, b"REAL AUDIO", "wav", manifest)
        generate_module._record_failed_sources(failures)
        return manifest

    monkeypatch.setattr(
        "app.pipeline.generate.generate_briefing", _ok, raising=True
    )


def test_a_degraded_briefing_fails_the_run_but_still_ships(
    cli, monkeypatch, capsys
):
    """The 2026-08-27 lesson. A briefing that shipped without the user's inbox
    exited 0, so Actions went green and no email was sent, and it was found
    days later by reading `gh run list`. The audio must still be stored — rule
    7 — but the run must stop claiming success."""
    _succeeds_but_degraded(monkeypatch, cli, {"email": RuntimeError("Gmail 500")})

    assert main(["generate", "--briefing-id", "2026-08-27"]) == 3

    out = capsys.readouterr().out
    assert "::warning::" in out
    assert "::error::" in out
    assert "DEGRADED" in out

    # Shipped anyway: the phone has something to play.
    audio = cli.load_audio("2026-08-27")
    assert audio is not None and audio[0] == b"REAL AUDIO"


def test_allow_degraded_opts_back_out(cli, monkeypatch):
    _succeeds_but_degraded(monkeypatch, cli, {"calendar": RuntimeError("boom")})

    assert (
        main(["generate", "--allow-degraded", "--briefing-id", "2026-08-27"]) == 0
    )


def test_a_healthy_briefing_is_still_a_clean_zero(cli, monkeypatch, capsys):
    """The guard against the opposite mistake: turning every run red teaches
    the user to ignore the email, which is the same outcome as sending none."""
    _succeeds_but_degraded(monkeypatch, cli, {})

    assert main(["generate", "--briefing-id", "2026-08-27"]) == 0
    out = capsys.readouterr().out
    assert "::warning::" not in out
    assert "::error::" not in out


def test_an_expired_google_token_says_so_and_names_the_permanent_fix(
    cli, monkeypatch, capsys
):
    """The most likely production failure in this deployment, and it arrives
    as a bare invalid_grant that says nothing. While the consent screen is in
    Testing mode Google expires refresh tokens after 7 days, so this recurs
    weekly until it is fixed — the error has to be recognisable on sight."""
    _succeeds_but_degraded(
        monkeypatch,
        cli,
        {
            "email": ExpiredCredentialsError("token could not be refreshed"),
            "calendar": ExpiredCredentialsError("token could not be refreshed"),
        },
    )

    assert main(["generate", "--briefing-id", "2026-08-27"]) == 3

    out = capsys.readouterr().out
    assert "EXPIRED" in out
    assert "Testing mode" in out
    assert "Publish app" in out
    assert "GOOGLE_OAUTH_TOKEN_JSON" in out


def test_a_transient_failure_is_not_reported_as_an_expired_token(
    cli, monkeypatch, capsys
):
    """Identified by exception TYPE, not by message text. A Gmail 500 is not a
    reason to go and republish an OAuth consent screen."""
    _succeeds_but_degraded(monkeypatch, cli, {"email": RuntimeError("Gmail 500")})

    assert main(["generate", "--briefing-id", "2026-08-27"]) == 3
    assert "Testing mode" not in capsys.readouterr().out


# ──────────────────────────────────────────────────────────────
# A degraded briefing must not block its own replacement.
#
# Observed on production for three consecutive days (runs 33383999697,
# 33491842708, 33610322516): the Google refresh token expired, the first
# checkpoint each morning stored a briefing with no inbox and no calendar
# and exited 3, and every later checkpoint that day reported SUCCESS —
# because the degraded briefing was already there. The retry existed and
# could not reach the one state it was for. From the outside the day looked
# like a failure followed by a recovery; nothing had recovered.
# ──────────────────────────────────────────────────────────────


def _degraded_manifest(briefing_id: str = "2026-08-27") -> BriefingManifest:
    m = _manifest(briefing_id)
    return m.model_copy(update={"degraded_sources": ["email", "calendar"]})


def test_a_degraded_briefing_is_regenerated_rather_than_reported_as_done(
    cli, monkeypatch, caplog
):
    cli.save("2026-08-27", b"OLD AUDIO", "wav", _degraded_manifest())
    generated: list[str] = []

    async def _records(**kwargs):
        generated.append(kwargs["briefing_id"])
        # The replacement run is the healthy one — the token came back. The
        # stub bypasses generate_briefing, so its module-level record of
        # which sources failed has to be cleared explicitly or the CLI
        # reports the PREVIOUS run's degradation and exits 3.
        monkeypatch.setattr(
            "app.pipeline.generate._failed_personal_sources", [], raising=True
        )
        return _manifest()

    monkeypatch.setattr(
        "app.pipeline.generate.generate_briefing", _records, raising=True
    )
    with caplog.at_level("WARNING"):
        code = main(["generate", "--briefing-id", "2026-08-27"])

    assert code == 0
    assert generated == ["2026-08-27"], (
        "a briefing missing its whole personal half is the one most worth "
        "replacing, and idempotency made it the one that never could be"
    )
    assert any("shipped without email, calendar" in r.getMessage()
               for r in caplog.records)


def test_a_healthy_briefing_is_still_left_alone(cli, monkeypatch):
    """The control, and the reason this cannot simply always regenerate:
    the daily schedule fires several checkpoints and each costs real Gemini
    and TTS spend. Only a degraded briefing is worth the second run."""
    cli.save("2026-08-27", b"GOOD AUDIO", "wav", _manifest())

    def _never(**_kwargs):
        raise AssertionError("a healthy briefing must stay a no-op")

    monkeypatch.setattr(
        "app.pipeline.generate.generate_briefing", _never, raising=True
    )

    assert main(["generate", "--briefing-id", "2026-08-27"]) == 0


def test_a_refused_segment_is_reported_not_regenerated(cli, monkeypatch, caplog):
    """A content refusal is deterministic, so a retry pays a full LLM and
    TTS bill to be refused again. There was no test of this path at all."""
    m = _manifest().model_copy(update={"degraded_sources": ["world"]})
    cli.save("2026-08-27", b"AUDIO", "wav", m)

    def _never(**_kwargs):
        raise AssertionError("a refusal must not be regenerated")

    monkeypatch.setattr(
        "app.pipeline.generate.generate_briefing", _never, raising=True
    )
    with caplog.at_level("WARNING"):
        assert main(["generate", "--briefing-id", "2026-08-27"]) == 0
    assert any("model refused" in r.getMessage() for r in caplog.records)


def test_a_calendar_that_failed_alone_is_retried_not_filed_as_a_refusal(
    cli, monkeypatch, caplog
):
    """`calendar` is a source label AND a `Segment` value. Classified
    against the whole enum, a calendar fetch that failed on its own read as
    a model refusal: exit 0, logged as "the model refused to write
    calendar", and never retried — for a briefing missing the user's day.
    The ladder cannot give up the calendar segment, so this can only ever
    be the source."""
    m = _manifest().model_copy(update={"degraded_sources": ["calendar"]})
    cli.save("2026-08-27", b"AUDIO", "wav", m)
    generated: list[str] = []

    async def _records(**kwargs):
        generated.append(kwargs["briefing_id"])
        monkeypatch.setattr(
            "app.pipeline.generate._failed_personal_sources", [], raising=True
        )
        return _manifest()

    monkeypatch.setattr(
        "app.pipeline.generate.generate_briefing", _records, raising=True
    )
    with caplog.at_level("WARNING"):
        assert main(["generate", "--briefing-id", "2026-08-27"]) == 0

    assert generated == ["2026-08-27"]
    assert not any("model refused" in r.getMessage() for r in caplog.records)


def test_a_source_label_is_never_also_a_refusable_segment():
    """The two vocabularies in `degraded_sources` must not overlap.

    They did, through `calendar`, while a docstring said they could not.
    Both sets now live side by side in `schemas.py`; this is what stops a
    new source or a new ladder rung re-creating the collision."""
    from app.core.schemas import INGESTION_SOURCE_LABELS, REFUSABLE_SEGMENTS

    refusable = {segment.value for segment in REFUSABLE_SEGMENTS}
    assert not refusable & set(INGESTION_SOURCE_LABELS), (
        "a label that is both a source and a refusable segment cannot be "
        "classified, and cli.py will either retry a refusal or never retry "
        "a failure"
    )


def test_a_fallback_voice_is_not_a_reason_to_regenerate(cli, monkeypatch):
    """Task 22. A voice quota that is out for the month does not come back
    by 06:00, so a regeneration would only buy the same fallback voice
    again, three times a day. That is why `narrator_fallback` is its own
    field rather than an entry in `degraded_sources`, and this pins it."""
    m = _manifest().model_copy(update={"narrator_fallback": True})
    cli.save("2026-08-27", b"AUDIO", "wav", m)

    def _never(**_kwargs):
        raise AssertionError("a fallback voice must not trigger a regeneration")

    monkeypatch.setattr(
        "app.pipeline.generate.generate_briefing", _never, raising=True
    )
    assert main(["generate", "--briefing-id", "2026-08-27"]) == 0


def test_the_manifest_records_which_sources_were_missing():
    """The signal has to survive the round trip through storage, or the
    existence check on the next run cannot see it."""
    m = _degraded_manifest()
    restored = BriefingManifest.model_validate_json(m.model_dump_json())
    assert restored.degraded_sources == ["email", "calendar"]
    assert _manifest().degraded_sources == []


def _degraded_manifest_at(attempts: int, briefing_id: str = "2026-08-27"):
    m = _manifest(briefing_id)
    return m.model_copy(update={
        "degraded_sources": ["email", "calendar"],
        "degraded_attempts": attempts,
    })


def test_the_retry_gives_up_after_the_cap(cli, monkeypatch, caplog):
    """Unbounded self-healing is a bill, not a feature.

    What degrades a briefing is almost always a credential a human has to
    replace, and it stays broken until they do. Eight checkpoints fire on a
    broken day, so an unbounded retry runs eight full generations for a
    briefing that cannot improve — against a TTS free tier of a million
    characters a month, and a briefing of roughly six thousand.
    """
    cli.save("2026-08-27", b"OLD", "wav", _degraded_manifest_at(MAX_DEGRADED_ATTEMPTS))

    def _never(**_kwargs):
        raise AssertionError("the cap must stop regeneration")

    monkeypatch.setattr(
        "app.pipeline.generate.generate_briefing", _never, raising=True
    )

    with caplog.at_level("ERROR"):
        code = main(["generate", "--briefing-id", "2026-08-27"])

    # Exit 3, not 0: giving up is still a degraded day, and the schedule
    # must not report success for it.
    assert code == 3
    assert any("needs a person" in r.getMessage() for r in caplog.records)


def test_the_cap_allows_three_generations_in_total(cli, monkeypatch):
    """The arithmetic, spelled out, because the first version of this cap
    said 'two retries' and delivered one — the stored count includes the
    initial run, so `MAX = 2` blocked the third generation rather than the
    fourth. A constant named for what it permits rather than what it counts
    is how that hid."""
    attempts_that_regenerate = []
    for stored in range(0, MAX_DEGRADED_ATTEMPTS + 2):
        blocked = stored >= MAX_DEGRADED_ATTEMPTS
        if not blocked:
            attempts_that_regenerate.append(stored)
    assert MAX_DEGRADED_ATTEMPTS == 3
    assert attempts_that_regenerate == [0, 1, 2], (
        "three generations in total: the first, plus two retries"
    )


def test_below_the_cap_it_still_retries(cli, monkeypatch):
    """The control. Giving up too early is the failure this whole PR is
    about — a source that recovers at 06:00 must heal the same morning."""
    cli.save("2026-08-27", b"OLD", "wav",
             _degraded_manifest_at(MAX_DEGRADED_ATTEMPTS - 1))
    generated: list[int] = []

    async def _records(**kwargs):
        generated.append(kwargs.get("previous_degraded_attempts"))
        monkeypatch.setattr(
            "app.pipeline.generate._failed_personal_sources", [], raising=True
        )
        return _manifest()

    monkeypatch.setattr(
        "app.pipeline.generate.generate_briefing", _records, raising=True
    )

    assert main(["generate", "--briefing-id", "2026-08-27"]) == 0
    assert generated == [MAX_DEGRADED_ATTEMPTS - 1], (
        "the attempt count must be carried into the run, or the next "
        "manifest resets it and the cap never binds"
    )


def test_force_overrides_the_cap(cli, monkeypatch):
    """--force is the operator saying they have fixed the cause."""
    cli.save("2026-08-27", b"OLD", "wav", _degraded_manifest_at(99))
    ran: list[str] = []

    async def _records(**kwargs):
        ran.append(kwargs["briefing_id"])
        monkeypatch.setattr(
            "app.pipeline.generate._failed_personal_sources", [], raising=True
        )
        return _manifest()

    monkeypatch.setattr(
        "app.pipeline.generate.generate_briefing", _records, raising=True
    )

    assert main(["generate", "--force", "--briefing-id", "2026-08-27"]) == 0
    assert ran == ["2026-08-27"]
