"""
Tests for app.synthesis.storage.LocalFilesystemStorage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.schemas import BriefingDigest, BriefingManifest, DigestNewsletter
from app.synthesis.storage import AUDIO_MEDIA_TYPES, LocalFilesystemStorage


def _manifest(
    briefing_id: str = "2026-08-21", audio_url: str = "/briefing/x/audio"
) -> BriefingManifest:
    return BriefingManifest(
        briefing_id=briefing_id,
        generated_at=datetime.now(UTC),
        duration_ms=1000,
        audio_url=audio_url,
        segments=[{"id": "cold_open", "label": "Good morning", "start_ms": 0}],
        turns=[],
        sources=[],
    )


def test_save_and_load_manifest_round_trips(tmp_path):
    storage = LocalFilesystemStorage(base_dir=tmp_path)
    manifest = _manifest()

    storage.save("2026-08-21", b"RIFF....fake wav bytes", "wav", manifest)

    loaded = storage.load_manifest("2026-08-21")
    assert loaded is not None
    assert loaded.briefing_id == "2026-08-21"
    assert loaded.duration_ms == 1000


def test_load_manifest_missing_returns_none(tmp_path):
    storage = LocalFilesystemStorage(base_dir=tmp_path)
    assert storage.load_manifest("does-not-exist") is None


def test_save_and_load_audio_round_trips_with_correct_media_type(tmp_path):
    storage = LocalFilesystemStorage(base_dir=tmp_path)
    manifest = _manifest()
    audio_bytes = b"some wav bytes"

    storage.save("2026-08-21", audio_bytes, "wav", manifest)

    result = storage.load_audio("2026-08-21")
    assert result is not None
    loaded_bytes, media_type = result
    assert loaded_bytes == audio_bytes
    assert media_type == AUDIO_MEDIA_TYPES["wav"]


def test_load_audio_missing_returns_none(tmp_path):
    storage = LocalFilesystemStorage(base_dir=tmp_path)
    assert storage.load_audio("does-not-exist") is None


def test_save_removes_stale_audio_from_a_previous_format(tmp_path):
    storage = LocalFilesystemStorage(base_dir=tmp_path)
    manifest = _manifest()

    storage.save("2026-08-21", b"wav bytes", "wav", manifest)
    assert (tmp_path / "2026-08-21.wav").exists()

    storage.save("2026-08-21", b"m4a bytes", "m4a", manifest)
    assert (tmp_path / "2026-08-21.m4a").exists()
    assert not (tmp_path / "2026-08-21.wav").exists()

    result = storage.load_audio("2026-08-21")
    assert result is not None
    audio_bytes, media_type = result
    assert audio_bytes == b"m4a bytes"
    assert media_type == AUDIO_MEDIA_TYPES["m4a"]


def test_the_superseded_format_is_deleted_only_after_the_new_audio_lands(
    tmp_path, monkeypatch
):
    """Same defect the R2 backend was already fixed for, still here in local
    storage: the stale format was unlinked BEFORE the new bytes were written,
    so a write that failed left the old manifest pointing at audio that no
    longer existed — 200 on the manifest, 404 on the audio, and that is the
    state of the day."""
    storage = LocalFilesystemStorage(base_dir=tmp_path)
    manifest = _manifest()
    storage.save("2026-08-21", b"m4a bytes", "m4a", manifest)

    def _fails(_self, _data):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", _fails, raising=True)
    with pytest.raises(OSError):
        storage.save("2026-08-21", b"wav bytes", "wav", manifest)

    assert (tmp_path / "2026-08-21.m4a").read_bytes() == b"m4a bytes"
    assert storage.load_audio("2026-08-21") is not None


def test_delete_briefing_removes_objects_in_unknown_formats(tmp_path):
    storage = LocalFilesystemStorage(base_dir=tmp_path)
    storage.save("2026-08-21", b"wav bytes", "wav", _manifest())
    (tmp_path / "2026-08-21.mp3").write_bytes(b"some other format")

    storage.delete_briefing("2026-08-21")

    assert list(tmp_path.iterdir()) == []


def test_audio_path_for_is_a_stable_api_route(tmp_path):
    storage = LocalFilesystemStorage(base_dir=tmp_path)
    assert storage.audio_path_for("2026-08-21") == "/briefing/2026-08-21/audio"


def test_base_dir_created_if_missing(tmp_path):
    target = tmp_path / "nested" / "out"
    assert not target.exists()
    LocalFilesystemStorage(base_dir=target)
    assert target.exists()


def test_a_storage_outage_is_not_a_corrupt_manifest(tmp_path, monkeypatch):
    """A disk that will not answer must not read as a missing briefing.

    `existing_manifest` treats UnreadableManifestError as "no usable
    briefing", so folding PermissionError and transient OSError into it made
    a thirty-second storage blip trigger a full regeneration — real Gemini
    and TTS spend to replace a file that was sitting there intact. Corrupt
    JSON is a statement about the briefing; an unreadable disk is a
    statement about the disk.
    """
    from app.synthesis.storage import LocalFilesystemStorage, UnreadableManifestError

    storage = LocalFilesystemStorage(base_dir=tmp_path)
    path = tmp_path / "2026-08-30.manifest.json"
    path.write_text("{}")

    def _explode(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_text", _explode)

    with pytest.raises(PermissionError):
        storage.load_manifest("2026-08-30")

    # ...while genuinely invalid JSON still reports as unreadable.
    monkeypatch.undo()
    path.write_text("not json at all")
    with pytest.raises(UnreadableManifestError):
        storage.load_manifest("2026-08-30")


# ──────────────────────────────────────────────────────────────
# The digest and the manifest are published as one generation
#
# The invariant: what `load_digest` returns was built from what
# `load_manifest` returns, or there is no digest. `--force` reuses the
# briefing id, so a digest surviving from an earlier run for the same day
# carries `turn_index` values addressing THAT run's turn list — a browse
# screen whose taps land in audio the bucket no longer holds, wrong in a way
# that looks like it worked.
# ──────────────────────────────────────────────────────────────


def _digest(briefing_id: str = "2026-08-21", subject: str = "Today") -> BriefingDigest:
    return BriefingDigest(
        briefing_id=briefing_id,
        generated_at=datetime.now(UTC),
        newsletters=[
            DigestNewsletter(
                id="nl_1",
                source_name="Stratechery",
                sender="ben@stratechery.com",
                subject=subject,
                received_at=datetime.now(UTC),
            )
        ],
    )


def test_a_digest_that_will_not_write_does_not_lose_the_briefing(tmp_path, monkeypatch):
    """Rule 7, which this side did not apply. `R2Storage.save` has caught a
    failed digest upload since the digest landed; here the exception
    propagated out of `save`, so a full disk cost the whole briefing — the
    audio was already written and orphaned — rather than one screen."""
    storage = LocalFilesystemStorage(base_dir=tmp_path)
    real_write_text = Path.write_text

    def _fail_on_digest(self, data, *args, **kwargs):
        if ".digest.json" in self.name:
            raise OSError(28, "No space left on device")
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _fail_on_digest)

    storage.save("2026-08-21", b"AUDIO", "m4a", _manifest(), _digest())

    monkeypatch.undo()
    assert storage.load_manifest("2026-08-21") is not None
    assert storage.load_audio("2026-08-21") is not None
    assert storage.load_digest("2026-08-21") is None


def test_a_regeneration_with_no_digest_withdraws_the_previous_one(tmp_path):
    """An enrichment failure, or a morning with nothing browsable, produces
    no digest at all — and that is exactly a morning on which yesterday's
    would otherwise stay on the screen labelled as today's."""
    storage = LocalFilesystemStorage(base_dir=tmp_path)
    storage.save("2026-08-21", b"AUDIO", "m4a", _manifest(), _digest())

    storage.save("2026-08-21", b"AUDIO2", "m4a", _manifest(), None)

    assert storage.load_digest("2026-08-21") is None


def test_a_digest_that_will_not_write_does_not_leave_the_last_one_behind(
    tmp_path, monkeypatch
):
    """The failure that survives the rule 7 catch. Shipping the briefing
    without its digest is right; shipping it with the PREVIOUS run's is not,
    and one `except` that only logs does both."""
    storage = LocalFilesystemStorage(base_dir=tmp_path)
    storage.save("2026-08-21", b"AUDIO", "m4a", _manifest(), _digest(subject="Old"))
    real_write_text = Path.write_text

    def _fail_on_digest(self, data, *args, **kwargs):
        if ".digest.json" in self.name:
            raise OSError(28, "No space left on device")
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _fail_on_digest)

    storage.save("2026-08-21", b"AUDIO", "m4a", _manifest(), _digest(subject="New"))

    monkeypatch.undo()
    assert storage.load_manifest("2026-08-21") is not None
    assert storage.load_digest("2026-08-21") is None


def test_a_manifest_that_will_not_write_takes_its_digest_with_it(tmp_path, monkeypatch):
    """The digest goes down first, so a manifest write that then fails leaves
    the PREVIOUS manifest being served beside the NEW digest — every deep-link
    addressing a turn list nobody can fetch."""
    storage = LocalFilesystemStorage(base_dir=tmp_path)
    storage.save("2026-08-21", b"AUDIO", "m4a", _manifest(), _digest(subject="Old"))
    real_write_text = Path.write_text

    def _fail_on_manifest(self, data, *args, **kwargs):
        if ".manifest.json" in self.name:
            raise OSError(28, "No space left on device")
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _fail_on_manifest)

    with pytest.raises(OSError):
        storage.save("2026-08-21", b"AUDIO", "m4a", _manifest(), _digest(subject="New"))

    monkeypatch.undo()
    assert storage.load_manifest("2026-08-21") is not None
    assert storage.load_digest("2026-08-21") is None


def test_a_reader_never_sees_the_digest_half_written(tmp_path, monkeypatch):
    """`write_text` truncates the live file before it writes it.

    The API and the generator share this directory, so a `--force`
    regeneration made `GET /briefing/{id}/digest` answer 404 — `load_digest`
    reading invalid JSON and reporting absent — for a briefing that was
    sitting there complete, for as long as the write took. The observation
    below is taken from inside the write, which is the only place the window
    exists.
    """
    storage = LocalFilesystemStorage(base_dir=tmp_path)
    storage.save("2026-08-21", b"AUDIO", "m4a", _manifest(), _digest(subject="Old"))
    seen = []

    def _observed_write_text(self, data, *args, **kwargs):
        if ".digest.json" not in self.name:
            return Path(self).open("w").write(data)
        with open(self, "w") as handle:  # truncate, as write_text does
            seen.append(storage.load_digest("2026-08-21"))
            handle.write(data)
        return len(data)

    monkeypatch.setattr(Path, "write_text", _observed_write_text)

    storage.save("2026-08-21", b"AUDIO", "m4a", _manifest(), _digest(subject="New"))

    monkeypatch.undo()
    assert [d and d.newsletters[0].subject for d in seen] == ["Old"]
    assert storage.load_digest("2026-08-21").newsletters[0].subject == "New"
