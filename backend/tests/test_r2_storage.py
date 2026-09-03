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
from app.core.schemas import (
    BriefingDigest,
    BriefingManifest,
    DigestNewsletter,
)
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

    def list_objects_v2(self, *, Bucket, ContinuationToken=None, Prefix=None):  # noqa: N803
        keys = sorted(k for k in self.objects if not Prefix or k.startswith(Prefix))
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


def test_unreadable_manifest_raises_rather_than_reading_as_absent(monkeypatch):
    """Returning None made a corrupt manifest indistinguishable from one that
    was never written, so `app.cli check` blamed GitHub's scheduler for
    dropping a run when the object was sitting right there and unparseable."""
    from app.synthesis.storage import UnreadableManifestError

    storage = _configured(monkeypatch)
    storage.__dict__["_client"].objects["2026-08-22.manifest.json"] = (
        b"{not json", "application/json"
    )
    with pytest.raises(UnreadableManifestError):
        storage.load_manifest("2026-08-22")


def test_a_corrupt_manifest_does_not_block_its_own_replacement(monkeypatch):
    """Generation is idempotent, so the probe that asks "is it already there?"
    must treat an unparseable manifest as NOT there. Otherwise the one state
    that most needs regenerating is the one that can never be regenerated."""
    from app.synthesis.storage import existing_manifest

    storage = _configured(monkeypatch)
    storage.__dict__["_client"].objects["2026-08-22.manifest.json"] = (
        b"{not json", "application/json"
    )
    assert existing_manifest(storage, "2026-08-22") is None


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


def test_a_failed_cleanup_of_a_LOWER_ranked_stale_file_is_only_a_warning(
    monkeypatch,
):
    """wav is superseded by m4a. m4a outranks wav in load_audio's order, so the
    leftover wav is never served and publishing the manifest is exactly what
    makes the pair consistent. Being unable to tidy up is worth a warning, not
    a lost briefing."""
    storage = _configured(monkeypatch)
    client = storage.__dict__["_client"]
    storage.save("2026-08-23", b"OLD", "wav", _manifest("2026-08-23"))

    def _boom(*, Bucket, Key):  # noqa: N803
        raise _ClientError(403, "AccessDenied")

    client.delete_object = _boom

    storage.save("2026-08-23", b"NEW", "m4a", _manifest("2026-08-23"))

    assert storage.load_manifest("2026-08-23") is not None
    audio = storage.load_audio("2026-08-23")
    assert audio is not None and audio[0] == b"NEW"


def test_a_failed_cleanup_of_a_HIGHER_ranked_stale_file_refuses_the_manifest(
    monkeypatch,
):
    """m4a superseded by wav, and the m4a will not delete.

    m4a outranks wav, so that leftover m4a is what `load_audio` and the Worker
    will serve. Publishing the new manifest over it hands the phone OLD audio
    against NEW turn timings — every boundary wrong, permanently, and it looks
    like a healthy briefing from the Actions log.

    The old manifest and the old m4a are still a consistent pair, so refusing
    to publish leaves the user with the briefing they already had and fails the
    run loudly. The orphaned wav is cleaned up by retention.

    The previous behaviour was a warning and a published manifest, and there
    was a test asserting that — a test that encoded the bug.
    """
    storage = _configured(monkeypatch)
    client = storage.__dict__["_client"]
    storage.save("2026-08-23", b"OLD", "m4a", _manifest("2026-08-23"))
    old_manifest_body = client.objects["2026-08-23.manifest.json"][0]

    def _boom(*, Bucket, Key):  # noqa: N803
        raise _ClientError(403, "AccessDenied")

    client.delete_object = _boom

    with pytest.raises(_ClientError):
        storage.save("2026-08-23", b"NEW", "wav", _manifest("2026-08-23"))

    # What the phone sees is unchanged, and it is still self-consistent.
    assert client.objects["2026-08-23.manifest.json"][0] == old_manifest_body
    audio = storage.load_audio("2026-08-23")
    assert audio is not None and audio[0] == b"OLD"


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


def test_delete_briefing_removes_objects_in_unknown_formats(monkeypatch):
    """Retention deleted the manifest plus m4a and wav and nothing else, so
    any other object for that briefing survived the deletion meant to reclaim
    its space — invisible, unreferenced, and counted against the 10GB."""
    storage = _configured(monkeypatch)
    client = storage.__dict__["_client"]
    storage.save("2026-08-22", b"A", "m4a", _manifest())
    client.objects["2026-08-22.mp3"] = (b"x", "audio/mpeg")
    client.objects["2026-08-22.script.json"] = (b"{}", "application/json")

    storage.delete_briefing("2026-08-22")

    assert client.objects == {}


def _digest(briefing_id: str = "2026-08-22") -> BriefingDigest:
    return BriefingDigest(
        briefing_id=briefing_id,
        generated_at=datetime.now(UTC),
        newsletters=[
            DigestNewsletter(
                id="nl_1",
                source_name="Stratechery",
                sender="ben@stratechery.com",
                subject="The Gemini Moat",
                received_at=datetime.now(UTC),
            )
        ],
    )


def test_the_digest_is_written_before_the_manifest(monkeypatch):
    """Same ordering rule as the audio, for the same reason: the manifest is
    what every reader polls to decide a briefing exists, so anything written
    after it is briefly a 404 on a briefing that already reads as ready."""
    storage = _configured(monkeypatch)
    client = storage.__dict__["_client"]

    storage.save("2026-08-22", b"AUDIO", "m4a", _manifest(), _digest())

    puts = [key for verb, key in client.calls if verb == "put"]
    assert puts.index("2026-08-22.digest.json") < puts.index(
        "2026-08-22.manifest.json"
    )
    assert storage.load_digest("2026-08-22").newsletters[0].subject == (
        "The Gemini Moat"
    )


def test_a_digest_that_will_not_upload_does_not_lose_the_briefing(monkeypatch):
    """Rule 7. The digest is the browse surface; the manifest plus the audio
    is the briefing. Refusing to publish a working briefing because a
    secondary object would not upload trades the product for one of its
    screens."""
    storage = _configured(monkeypatch)
    client = storage.__dict__["_client"]
    real_put = client.put_object

    def _fail_on_digest(*, Bucket, Key, Body, ContentType):  # noqa: N803
        if Key.endswith(".digest.json"):
            raise _ClientError(500, "InternalError")
        return real_put(Bucket=Bucket, Key=Key, Body=Body, ContentType=ContentType)

    client.put_object = _fail_on_digest

    storage.save("2026-08-22", b"AUDIO", "m4a", _manifest(), _digest())

    assert storage.load_manifest("2026-08-22") is not None
    assert storage.load_audio("2026-08-22") is not None
    assert storage.load_digest("2026-08-22") is None


def test_an_unreadable_digest_reads_as_absent(monkeypatch):
    """Unlike the manifest, which raises. No operator decision rides on
    telling corrupt apart from absent here, and the client already has to
    render an empty state for every briefing generated before the digest
    existed."""
    storage = _configured(monkeypatch)
    client = storage.__dict__["_client"]
    client.objects["2026-08-22.digest.json"] = (b"{not json", "application/json")

    assert storage.load_digest("2026-08-22") is None


def test_retention_removes_the_digest_from_the_bucket(monkeypatch):
    """A digest left behind is an orphaned record of someone's inbox — every
    newsletter that arrived, who sent it and when — sitting in R2 after the
    briefing it belonged to is gone.

    `delete_briefing` needed no change; it already sweeps every key under the
    briefing id. This exists so a refactor back to an explicit key list
    cannot quietly reintroduce it.
    """
    storage = _configured(monkeypatch)
    client = storage.__dict__["_client"]
    storage.save("2026-01-01", b"A", "m4a", _manifest("2026-01-01"),
                 _digest("2026-01-01"))
    assert "2026-01-01.digest.json" in client.objects

    storage.delete_briefing("2026-01-01")

    assert client.objects == {}

def test_a_delete_that_r2_rejects_is_not_reported_as_a_deletion(monkeypatch):
    """`prune_briefings` names what it deleted and the workflow log prints
    that list. Swallowing a 403 here made the list a claim about objects still
    sitting in the bucket — retention that reports success and reclaims
    nothing, every morning, until the free tier runs out."""
    from app.synthesis.storage import prune_briefings

    storage = _configured(monkeypatch)
    client = storage.__dict__["_client"]
    storage.save("2026-01-01", b"A", "m4a", _manifest("2026-01-01"))

    def _denied(*, Bucket, Key):  # noqa: N803
        raise _ClientError(403, "AccessDenied")

    client.delete_object = _denied

    with pytest.raises(_ClientError):
        storage.delete_briefing("2026-01-01")

    assert prune_briefings(storage, retention_days=30, today_id="2026-08-22") == []
    assert "2026-01-01.m4a" in client.objects


def test_deleting_a_key_that_is_already_gone_is_not_a_failure(monkeypatch):
    """A 404 means the object is not there, which is the state deletion was
    asking for. Only a real refusal is a failure."""
    storage = _configured(monkeypatch)
    client = storage.__dict__["_client"]
    storage.save("2026-01-01", b"A", "m4a", _manifest("2026-01-01"))

    def _missing(*, Bucket, Key):  # noqa: N803
        raise _ClientError(404, "NoSuchKey")

    client.delete_object = _missing

    storage.delete_briefing("2026-01-01")  # must not raise


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
