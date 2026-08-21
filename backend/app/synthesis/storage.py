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
