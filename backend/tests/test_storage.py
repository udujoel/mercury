"""
Tests for app.synthesis.storage.LocalFilesystemStorage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.schemas import BriefingManifest
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
