"""
R2 storage and the storage selector.

Fully offline — a fake S3 client stands in for boto3, so no test ever touches
a real bucket or needs credentials.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.core.config import get_settings
from app.core.schemas import BriefingManifest
from app.synthesis.r2_storage import R2ConfigurationError, R2Storage
from app.synthesis.storage import LocalFilesystemStorage, build_storage

R2_VARS = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")


class _ClientError(Exception):
    """Stands in for botocore.exceptions.ClientError, which carries its HTTP
    status in a `response` dict rather than in the exception type."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }


class _FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        # Ordering is load-bearing in this module twice over, so record it.
        self.calls: list[tuple[str, str]] = []

    def put_object(self, *, Bucket, Key, Body, ContentType):  # noqa: N803
        self.objects[Key] = (Body, ContentType)
        self.calls.append(("put", Key))

    def delete_object(self, *, Bucket, Key):  # noqa: N803
        self.objects.pop(Key, None)
        self.calls.append(("delete", Key))

    # Paginates two keys at a time. Real R2 returns at most 1000 per page, and
    # a listing that silently stops at the first page would make retention
    # quietly stop working at exactly the point it starts to matter.
    PAGE_SIZE = 2

    def list_objects_v2(self, *, Bucket, ContinuationToken=None):  # noqa: N803
        keys = sorted(self.objects)
        start = int(ContinuationToken or 0)
        page = keys[start : start + self.PAGE_SIZE]
        end = start + len(page)
        truncated = end < len(keys)
        response = {
            "Contents": [{"Key": key} for key in page],
            "IsTruncated": truncated,
        }
        if truncated:
            response["NextContinuationToken"] = str(end)
        return response

    def get_object(self, *, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            # Shaped like a real botocore ClientError, because the whole
            # point of the code under test is telling 404 apart from 403.
            raise _ClientError(404, "NoSuchKey")

        class _Body:
            def __init__(self, data): self._data = data
            def read(self): return self._data

        return {"Body": _Body(self.objects[Key][0])}


def _configured(monkeypatch) -> R2Storage:
    for var, val in (
        ("R2_ACCOUNT_ID", "acct"), ("R2_ACCESS_KEY_ID", "key"),
        ("R2_SECRET_ACCESS_KEY", "secret"), ("R2_BUCKET", "mercury"),
    ):
        monkeypatch.setenv(var, val)
    get_settings.cache_clear()
    storage = R2Storage()
    storage.__dict__["_client"] = _FakeS3()  # bypass the cached_property
    return storage


def _manifest(briefing_id: str = "2026-08-22") -> BriefingManifest:
    return BriefingManifest(
        briefing_id=briefing_id,
        generated_at=datetime(2026, 8, 22, 5, 0, tzinfo=UTC),
        duration_ms=1000,
        audio_url=f"/briefing/{briefing_id}/audio",
        segments=[], turns=[], sources=[],
    )


def test_missing_config_raises_naming_every_missing_var(monkeypatch):
    """Never fall back to local disk silently. A scheduled run writing to a
    container's ephemeral filesystem looks like success and produces a
    briefing nobody can reach."""
    for var in R2_VARS:
        monkeypatch.setenv(var, "")
    get_settings.cache_clear()

    with pytest.raises(R2ConfigurationError) as exc:
        R2Storage()
    message = str(exc.value)
    for var in R2_VARS:
        assert var in message, "the error must name what to set"


def test_save_then_load_round_trips(monkeypatch):
    storage = _configured(monkeypatch)
    storage.save("2026-08-22", b"AUDIOBYTES", "m4a", _manifest())

    loaded = storage.load_manifest("2026-08-22")
    assert loaded is not None
    assert loaded.briefing_id == "2026-08-22"

    audio = storage.load_audio("2026-08-22")
    assert audio is not None
    data, media_type = audio
    assert data == b"AUDIOBYTES"
    assert media_type == "audio/mp4"


def test_manifest_is_written_after_the_audio(monkeypatch):
    """Ordering is load-bearing: the client polls for the manifest, so writing
    it first would let the phone see a briefing whose audio is still
    uploading."""
    storage = _configured(monkeypatch)
    storage.save("2026-08-22", b"A", "m4a", _manifest())

    keys = list(storage.__dict__["_client"].objects)
    assert keys.index("2026-08-22.m4a") < keys.index("2026-08-22.manifest.json")


def test_missing_briefing_returns_none_not_an_error(monkeypatch):
    storage = _configured(monkeypatch)
    assert storage.load_manifest("never-generated") is None
    assert storage.load_audio("never-generated") is None


def test_unreadable_manifest_returns_none(monkeypatch):
    storage = _configured(monkeypatch)
    storage.__dict__["_client"].objects["bad.manifest.json"] = (
        b"{not json", "application/json"
    )
    assert storage.load_manifest("bad") is None


def test_build_storage_selects_local_by_default(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    get_settings.cache_clear()
    assert isinstance(build_storage(), LocalFilesystemStorage)


def test_build_storage_raises_when_r2_selected_but_unconfigured(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "r2")
    for var in R2_VARS:
        monkeypatch.setenv(var, "")
    get_settings.cache_clear()

    with pytest.raises(R2ConfigurationError):
        build_storage()


def test_unknown_backend_falls_back_to_local(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "gopher")
    get_settings.cache_clear()
    assert isinstance(build_storage(), LocalFilesystemStorage)


def test_manifest_json_is_valid_and_reparsable(monkeypatch):
    storage = _configured(monkeypatch)
    storage.save("2026-08-22", b"A", "m4a", _manifest())
    raw, content_type = storage.__dict__["_client"].objects["2026-08-22.manifest.json"]
    assert content_type == "application/json"
    assert json.loads(raw)["briefing_id"] == "2026-08-22"


def test_permission_error_raises_rather_than_looking_like_a_miss(monkeypatch):
    """A 403 must NOT be reported as "this briefing does not exist".

    Treating every ClientError as a miss meant a rotated or under-scoped R2
    token made the CLI conclude the briefing was never generated — so it
    reran the whole 127-second pipeline, spending real Gemini and TTS money,
    every day, with nothing in the log saying "permission denied".
    """
    storage = _configured(monkeypatch)

    class _Forbidden:
        def get_object(self, *, Bucket, Key):  # noqa: N803
            raise _ClientError(403, "AccessDenied")

    storage.__dict__["_client"] = _Forbidden()

    with pytest.raises(_ClientError) as exc:
        storage.load_audio("2026-08-22")
    assert "AccessDenied" in str(exc.value)


def test_permission_error_on_manifest_also_propagates(monkeypatch):
    storage = _configured(monkeypatch)

    class _Forbidden:
        def get_object(self, *, Bucket, Key):  # noqa: N803
            raise _ClientError(403, "AccessDenied")

    storage.__dict__["_client"] = _Forbidden()

    with pytest.raises(_ClientError):
        storage.load_manifest("2026-08-22")


def test_delete_audio_removes_every_format(monkeypatch):
    """Forced regeneration must not leave a stale file in the other format.

    `load_audio` returns the FIRST format it finds in KNOWN_AUDIO_FORMATS
    order, so an m4a left behind by an earlier run outranks a freshly written
    wav — the phone would play the old audio against the new manifest and
    every turn boundary would be wrong.
    """
    storage = _configured(monkeypatch)
    client = storage.__dict__["_client"]

    client.objects["2026-08-23.m4a"] = (b"OLD", "audio/mp4")
    client.objects["2026-08-23.wav"] = (b"NEW", "audio/wav")

    storage.delete_audio("2026-08-23")

    assert "2026-08-23.m4a" not in client.objects
    assert "2026-08-23.wav" not in client.objects


def test_save_clears_audio_left_in_the_other_format(monkeypatch):
    """A wav fallback superseding an earlier m4a, or the reverse.

    `load_audio` returns the FIRST format it finds in KNOWN_AUDIO_FORMATS
    order, so an m4a from an earlier run outranks a freshly written wav — the
    phone plays the old audio against the new manifest and every turn boundary
    is wrong. LocalFilesystemStorage.save has always done this; R2 did not, and
    `--force` was papering over it from the CLI.
    """
    storage = _configured(monkeypatch)
    client = storage.__dict__["_client"]

    storage.save("2026-08-23", b"OLD", "m4a", _manifest("2026-08-23"))
    storage.save("2026-08-23", b"NEW", "wav", _manifest("2026-08-23"))

    assert "2026-08-23.m4a" not in client.objects
    audio = storage.load_audio("2026-08-23")
    assert audio is not None
    assert audio[0] == b"NEW"


def test_the_superseded_format_is_deleted_only_after_the_new_audio_lands(
    monkeypatch,
):
    """The ordering finding. `--force` used to delete the old audio BEFORE
    regenerating, so a regeneration that then failed left a manifest pointing
    at an object that no longer existed — 200 on the manifest, 404 on the
    audio, and that was the state of the day. Nothing may be deleted on the
    strength of a write that has not happened yet."""
    storage = _configured(monkeypatch)
    client = storage.__dict__["_client"]

    storage.save("2026-08-23", b"OLD", "m4a", _manifest("2026-08-23"))
    client.calls.clear()
    storage.save("2026-08-23", b"NEW", "wav", _manifest("2026-08-23"))

    put_new = client.calls.index(("put", "2026-08-23.wav"))
    delete_old = client.calls.index(("delete", "2026-08-23.m4a"))
    put_manifest = client.calls.index(("put", "2026-08-23.manifest.json"))

    assert put_new < delete_old, "old audio deleted before the new audio landed"
    assert delete_old < put_manifest, "manifest published before the stale file went"


def test_save_survives_a_failed_cleanup_of_the_stale_format(monkeypatch):
    """Being unable to tidy up the superseded file is worth a warning, not a
    lost briefing: the new audio and its manifest are already correct."""
    storage = _configured(monkeypatch)
    client = storage.__dict__["_client"]
    storage.save("2026-08-23", b"OLD", "m4a", _manifest("2026-08-23"))

    def _boom(*, Bucket, Key):  # noqa: N803
        raise _ClientError(403, "AccessDenied")

    client.delete_object = _boom

    storage.save("2026-08-23", b"NEW", "wav", _manifest("2026-08-23"))

    assert storage.load_manifest("2026-08-23") is not None
    assert client.objects["2026-08-23.wav"][0] == b"NEW"


def test_list_briefing_ids_reads_every_page(monkeypatch):
    """R2 returns at most 1000 objects per page. Retention that read only the
    first page would work fine on a small bucket and stop working on the
    bucket that actually needed it."""
    storage = _configured(monkeypatch)
    for day in ("2026-08-20", "2026-08-21", "2026-08-22", "2026-08-23"):
        storage.save(day, b"A", "m4a", _manifest(day))

    # Eight objects, two per page, so this is four round trips.
    assert storage.list_briefing_ids() == [
        "2026-08-20", "2026-08-21", "2026-08-22", "2026-08-23",
    ]


def test_list_briefing_ids_ignores_keys_that_are_not_briefings(monkeypatch):
    storage = _configured(monkeypatch)
    client = storage.__dict__["_client"]
    storage.save("2026-08-22", b"A", "m4a", _manifest())
    client.objects["notes.txt"] = (b"x", "text/plain")
    client.objects["backup/2026-08-01.m4a"] = (b"x", "audio/mp4")

    assert storage.list_briefing_ids() == ["2026-08-22"]


def test_delete_briefing_removes_the_manifest_and_every_audio_format(monkeypatch):
    """Retention has to reclaim the audio, which is ~3MB of the ~3MB. A
    manifest-only delete would leave the growth exactly where it was."""
    storage = _configured(monkeypatch)
    client = storage.__dict__["_client"]
    storage.save("2026-08-22", b"A", "m4a", _manifest())
    client.objects["2026-08-22.wav"] = (b"OLD", "audio/wav")

    storage.delete_briefing("2026-08-22")

    assert client.objects == {}
    assert storage.list_briefing_ids() == []


def test_retention_against_r2_keeps_today(monkeypatch):
    """The same safety property as the local backend, through the backend that
    actually runs in production."""
    from app.synthesis.storage import prune_briefings

    storage = _configured(monkeypatch)
    for day in ("2026-01-01", "2026-08-22"):
        storage.save(day, b"A", "m4a", _manifest(day))

    deleted = prune_briefings(
        storage, retention_days=30, today_id="2026-08-22"
    )

    assert deleted == ["2026-01-01"]
    assert storage.load_manifest("2026-08-22") is not None
