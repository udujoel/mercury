"""
Briefing storage.

Local filesystem for the Phase 1 MVP — object storage (Cloudflare R2) is a
later concern per the roadmap. Per ADR-004's spirit ("every provider behind
a protocol, swapping one means one new file"), this sits behind a small
Protocol so the eventual R2 implementation is a drop-in replacement, not a
pipeline change.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings
from app.core.schemas import BriefingManifest

log = logging.getLogger(__name__)

# Formats this storage layer knows how to round-trip, in the order it
# should look for a stored file when serving audio back out. Keep in sync
# with app.synthesis.stitch's COMPRESSED_EXTENSION / FALLBACK_FORMAT.
KNOWN_AUDIO_FORMATS = ("m4a", "wav")

AUDIO_MEDIA_TYPES: dict[str, str] = {
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "wav": "audio/wav",
}

# Every object this layer writes is "{YYYY-MM-DD}.{something}". Retention reads
# the id back out of the key rather than trusting a listing to be only
# manifests, so an audio file orphaned by a half-failed run is still found and
# still cleaned up.
_BRIEFING_KEY = re.compile(r"^(\d{4}-\d{2}-\d{2})\.")


def briefing_id_from_key(key: str) -> str | None:
    """The briefing id in a storage key, or None if the key is not ours."""
    match = _BRIEFING_KEY.match(key)
    return match.group(1) if match else None


class BriefingStorage(Protocol):
    def audio_path_for(self, briefing_id: str) -> str:
        """The URL/path the API should hand back for this briefing's audio,
        independent of the format eventually written."""
        ...

    def save(
        self,
        briefing_id: str,
        audio_bytes: bytes,
        audio_format: str,
        manifest: BriefingManifest,
    ) -> None:
        ...

    def load_manifest(self, briefing_id: str) -> BriefingManifest | None:
        ...

    def load_audio(self, briefing_id: str) -> tuple[bytes, str] | None:
        """Returns (audio_bytes, media_type), or None if not found."""
        ...

    def list_briefing_ids(self) -> list[str]:
        """Every briefing id this storage holds anything for, sorted.

        Derived from the keys rather than from manifests specifically, so
        retention still finds audio left behind by a run that died between
        writing the audio and writing the manifest.
        """
        ...

    def delete_briefing(self, briefing_id: str) -> None:
        """Remove a briefing entirely — manifest and every audio format."""
        ...

    def delete_audio(self, briefing_id: str) -> None:
        """Remove every stored audio format for this briefing.

        NOT part of the regeneration path any more. `--force` used to call this
        before regenerating, and a regeneration that then failed left a
        manifest pointing at audio that no longer existed. Clearing a
        superseded format is `save`'s job, because `save` is the only caller
        that knows the replacement has actually landed.

        What is left for this to do is retention, where deleting audio whose
        manifest is going away too is exactly the intent.
        """
        ...


class LocalFilesystemStorage:
    """Writes audio + manifest JSON under `settings.audio_output_dir`."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        settings = get_settings()
        self._base_dir = Path(
            base_dir if base_dir is not None else settings.audio_output_dir
        )
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _manifest_path(self, briefing_id: str) -> Path:
        return self._base_dir / f"{briefing_id}.manifest.json"

    def _audio_path(self, briefing_id: str, audio_format: str) -> Path:
        return self._base_dir / f"{briefing_id}.{audio_format}"

    def audio_path_for(self, briefing_id: str) -> str:
        # A route the API mounts (see app/api/briefing.py), not a raw
        # filesystem path — the manifest is a client-facing artifact.
        return f"/briefing/{briefing_id}/audio"

    def save(
        self,
        briefing_id: str,
        audio_bytes: bytes,
        audio_format: str,
        manifest: BriefingManifest,
    ) -> None:
        # Remove any stale audio file left in another format from a
        # previous run (e.g. a wav fallback superseded by a later m4a run).
        for fmt in KNOWN_AUDIO_FORMATS:
            stale = self._audio_path(briefing_id, fmt)
            if fmt != audio_format and stale.exists():
                stale.unlink()

        audio_path = self._audio_path(briefing_id, audio_format)
        audio_path.write_bytes(audio_bytes)

        manifest_path = self._manifest_path(briefing_id)
        manifest_path.write_text(manifest.model_dump_json(indent=2))

        log.info(
            "stored briefing %s: %s (%d bytes), manifest %s",
            briefing_id, audio_path, len(audio_bytes), manifest_path,
        )

    def load_manifest(self, briefing_id: str) -> BriefingManifest | None:
        path = self._manifest_path(briefing_id)
        if not path.exists():
            return None
        return BriefingManifest.model_validate_json(path.read_text())

    def load_audio(self, briefing_id: str) -> tuple[bytes, str] | None:
        for fmt in KNOWN_AUDIO_FORMATS:
            path = self._audio_path(briefing_id, fmt)
            if path.exists():
                media_type = AUDIO_MEDIA_TYPES.get(fmt, "application/octet-stream")
                return path.read_bytes(), media_type
        return None

    def delete_audio(self, briefing_id: str) -> None:
        for fmt in KNOWN_AUDIO_FORMATS:
            self._audio_path(briefing_id, fmt).unlink(missing_ok=True)

    def list_briefing_ids(self) -> list[str]:
        ids = {
            briefing_id
            for path in self._base_dir.iterdir()
            if path.is_file()
            and (briefing_id := briefing_id_from_key(path.name)) is not None
        }
        return sorted(ids)

    def delete_briefing(self, briefing_id: str) -> None:
        self.delete_audio(briefing_id)
        self._manifest_path(briefing_id).unlink(missing_ok=True)


def prune_briefings(
    storage: BriefingStorage, *, retention_days: int, today_id: str
) -> list[str]:
    """Delete briefings older than the retention window. Returns what went.

    Nothing deleted these before, so R2 grew forever — roughly 3MB of audio
    plus a manifest every morning against a 10GB free tier. Not urgent, and
    unbounded, which is the kind of thing that is fine until the morning it is
    not.

    Two guards, deliberately overlapping. The arithmetic already keeps today
    (a window of at least one day cannot reach it), and there is an explicit
    `>= today_id` check as well, because "the retention job deleted the
    briefing the user was about to listen to" is not a bug worth discovering
    in production and the second guard costs one comparison. Ids are
    zero-padded YYYY-MM-DD, so lexicographic ordering is date ordering.

    Anything that is not a well-formed briefing id is left alone rather than
    guessed at. This function deletes things; it should be the least clever
    code in the module.
    """
    if retention_days < 1:
        raise ValueError(
            f"retention_days must be at least 1, got {retention_days}. A "
            "window of zero days would delete today's briefing."
        )

    try:
        cutoff = date.fromisoformat(today_id) - timedelta(days=retention_days)
    except ValueError as exc:
        raise ValueError(
            f"today_id must be YYYY-MM-DD, got {today_id!r}"
        ) from exc
    cutoff_id = cutoff.isoformat()

    deleted: list[str] = []
    for briefing_id in storage.list_briefing_ids():
        if briefing_id >= today_id:
            continue
        if briefing_id >= cutoff_id:
            continue
        try:
            storage.delete_briefing(briefing_id)
        except Exception:
            # Housekeeping must never be the reason a morning run reports
            # failure. Log it and move on to the next one.
            log.exception("could not delete old briefing %s", briefing_id)
            continue
        deleted.append(briefing_id)

    if deleted:
        log.info(
            "retention: deleted %d briefing(s) older than %s: %s",
            len(deleted), cutoff_id, ", ".join(deleted),
        )
    else:
        log.info("retention: nothing older than %s", cutoff_id)
    return deleted


def build_storage() -> BriefingStorage:
    """The storage backend this deployment is configured for.

    Fails loudly when STORAGE_BACKEND=r2 but R2 is misconfigured, rather than
    silently writing to local disk. A scheduled run that writes a briefing to
    a container's ephemeral filesystem looks exactly like success and produces
    something nobody can reach — the failure has to be visible in the Actions
    log, not discovered on a phone at 7am.
    """
    backend = get_settings().storage_backend.lower()
    if backend == "r2":
        from app.synthesis.r2_storage import R2Storage

        return R2Storage()
    if backend != "local":
        log.warning("unknown STORAGE_BACKEND %r; using local", backend)
    return LocalFilesystemStorage()
