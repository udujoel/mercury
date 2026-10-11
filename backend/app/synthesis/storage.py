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

from pydantic import ValidationError

from app.core.config import get_settings
from app.core.schemas import (
    SETTINGS_KEY,
    BriefingDigest,
    BriefingManifest,
    UserSettings,
)

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

# A briefing that is present but silent is not a briefing. A real one is ~3MB
# and a bare WAV header is 44 bytes, so a kilobyte is three orders of magnitude
# below anything genuine and cannot reject a real file — while still catching
# the zero-byte and truncated objects an upload that died mid-flight leaves.
# Kept in sync with MIN_PLAYABLE_AUDIO_BYTES in worker/src/index.js.
MIN_PLAYABLE_AUDIO_BYTES = 1024

# Every object this layer writes is "{YYYY-MM-DD}.{something}". Retention reads
# the id back out of the key rather than trusting a listing to be only
# manifests, so an audio file orphaned by a half-failed run is still found and
# still cleaned up.
# `YYYY-MM-DD`, or `YYYY-MM-DD-HHMM` once a day can hold more than one.
#
# The time suffix is what lets several briefings share a day instead of
# overwriting each other, and its shape is chosen so nothing downstream has
# to be told about it: dashes rather than dots, because the storage key is
# `<id>.<artifact>` and a dot in an id would make the two unparseable; fixed
# width, because ids are compared as strings in the archive and in R2's
# listing, and lexicographic order is then chronological order for free.
#
# The DATE prefix is load-bearing and stays exactly where it was. Retention
# ages briefings by parsing it, the archive groups by it, and every id
# already in R2 is one — so old ids remain valid ids rather than needing a
# migration, which for fourteen live briefings is the difference between a
# format change and an outage.
_BRIEFING_KEY = re.compile(r"^(\d{4}-\d{2}-\d{2}(?:-\d{4})?)\.")

# The same shape, anchored at BOTH ends, for validating an id on its own.
#
# `is_briefing_id` used to ask `_BRIEFING_KEY` about `f"{value}."`, and
# `match` only anchors the start — so `2026-09-07-0412.5` matched the group
# and the trailing `.5` was simply ignored. It was accepted as an id, and
# storage keys are `<id>.<artifact>`, so it would have been written and then
# read back truncated at the first dot. An id that does not survive its own
# storage key is the worst kind of valid.
_BRIEFING_ID = re.compile(r"^\d{4}-\d{2}-\d{2}(?:-\d{4})?$")

#: How long a `YYYY-MM-DD` day prefix is, wherever an id is grouped by day.
DAY_PREFIX_LENGTH = 10


def briefing_day(value: str) -> str:
    """The day an id belongs to, whether or not it carries a time."""
    return value[:DAY_PREFIX_LENGTH]


# The local time before which a briefing was made OVERNIGHT, as `HHMM`.
#
# Must agree with `MORNING_FLOOR_HHMM` in worker/src/index.js. Ids carry the
# user's local time, and a run stamped before this was made for the night:
# GitHub's 00:12 cron files its briefing under the NEW day, and counting it
# as the morning's let every checkpoint and both watchdogs stand down over a
# briefing written before the inbox had a morning in it (ROADMAP task 31).
MORNING_FLOOR_HHMM = "0300"


def covers_morning(briefing_id: str) -> bool:
    """Was this briefing made for the morning, rather than overnight?

    A legacy day-only id has no time to judge by, and counts: those date
    from when the one run a day was the morning run.
    """
    suffix = briefing_id[DAY_PREFIX_LENGTH + 1:]
    return not suffix or suffix >= MORNING_FLOOR_HHMM


def is_briefing_id(value: str) -> bool:
    r"""Is this a real day, optionally at a real time of day?

    The single place this question is answered. It used to be answered by a
    bare `\d{4}-\d{2}-\d{2}` regex in three places — here, `app.cli` and the
    Worker — and all three agreed that `2026-01-99` was a date. Retention
    parses ids as dates to decide what is old, so a date-SHAPED id sorted
    before every cutoff and got deleted; `app.cli generate` would have stored
    a briefing under a key retention could then never age out.

    The same trap applies to the new suffix: `2026-01-02-2599` matches the
    regex and is not a time, so the hour and minute are range-checked here
    rather than trusted.
    """
    if not _BRIEFING_ID.match(value):
        return False
    try:
        date.fromisoformat(briefing_day(value))
    except ValueError:
        return False

    suffix = value[DAY_PREFIX_LENGTH:]
    if not suffix:
        return True
    hour, minute = int(suffix[1:3]), int(suffix[3:5])
    return 0 <= hour <= 23 and 0 <= minute <= 59


def briefing_id_from_key(key: str) -> str | None:
    """The briefing id in a storage key, or None if the key is not ours."""
    match = _BRIEFING_KEY.match(key)
    if match is None:
        return None
    briefing_id = match.group(1)
    return briefing_id if is_briefing_id(briefing_id) else None


def validate_retention_days(retention_days: int) -> None:
    """Raise if this window is not one retention is allowed to run with.

    Its own function so `--dry-run` cannot preview a command the real run
    would reject: a preview of something that cannot happen is worse than no
    preview, because it reads as a description of what would happen.
    """
    if retention_days < 1:
        raise ValueError(
            f"retention_days must be at least 1, got {retention_days}. A "
            "window of zero days would delete today's briefing."
        )


class UnreadableManifestError(RuntimeError):
    """The manifest object is there and cannot be parsed.

    Its own type because "corrupt" and "absent" need different words said to
    the operator, and returning None for both said the wrong one: `app.cli
    check` blamed GitHub's scheduler for dropping a run while the object sat
    in the bucket, unparseable. Callers that ask "is a briefing already there?"
    should use `existing_manifest`, which treats corrupt as absent so the one
    state that most needs regenerating is not the one that can never be.
    """


def existing_manifest(
    storage: BriefingStorage, briefing_id: str
) -> BriefingManifest | None:
    """The stored manifest, or None if there is nothing worth keeping.

    For the idempotency probes — the callers asking "has today already been
    generated?". A manifest that will not parse is not a briefing worth
    preserving, and generation is idempotent, so treating it as absent is what
    lets the next run replace it.
    """
    try:
        return storage.load_manifest(briefing_id)
    except UnreadableManifestError:
        log.warning(
            "manifest for %s is unreadable; treating it as absent so it can "
            "be regenerated", briefing_id,
        )
        return None


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
        digest: BriefingDigest | None = None,
    ) -> None:
        ...

    def load_digest(self, briefing_id: str) -> BriefingDigest | None:
        """The stored digest, or None if this briefing has none.

        None is an ORDINARY answer, not an error. Every briefing generated
        before the digest existed has no digest object, and a run whose
        enrichment failed still ships one — so the read surface degrades to
        an empty state rather than a failure. Unlike `load_manifest` an
        unparseable digest also returns None: nothing depends on it to play
        audio, so there is no operator decision riding on telling "corrupt"
        apart from "absent".
        """
        ...

    def load_manifest(self, briefing_id: str) -> BriefingManifest | None:
        """The stored manifest, or None if nothing was ever written.

        Raises UnreadableManifestError if the object is there and will not
        parse. None means absent and nothing else.
        """
        ...

    def load_audio(self, briefing_id: str) -> tuple[bytes, str] | None:
        """Returns (audio_bytes, media_type), or None if not found."""
        ...

    def load_settings(self) -> UserSettings | None:
        """The user's saved settings, or None when none have been written.

        None is the state every deployment starts in and stays in until the
        app saves something, so it must be the easy path rather than an
        error — the caller falls back to the environment.
        """
        ...

    def save_settings(self, settings: UserSettings) -> None:
        """Persist settings. Overwrites; there is only ever one."""
        ...

    def audio_exists(self, briefing_id: str) -> bool:
        """Whether there is PLAYABLE audio for this briefing.

        Separate from `load_audio` because the archive index asks this of
        every briefing it lists, and loading thirty three-megabyte files to
        find out whether they exist is not a check, it is a download.

        Size, not existence: a zero-byte object is an upload that died
        mid-flight, and it read as playable for a whole day the last time
        that mattered.
        """
        ...

    def list_briefing_ids(self) -> list[str]:
        """Every briefing id this storage holds anything for, sorted.

        Derived from the keys rather than from manifests specifically, so
        retention still finds audio left behind by a run that died between
        writing the audio and writing the manifest.
        """
        ...

    def delete_briefing(self, briefing_id: str) -> None:
        """Remove a briefing entirely — every object stored under its id.

        Every object, not "the manifest plus the formats this module knows
        about". Retention exists to reclaim space, and an object in any other
        format survived the deletion meant to reclaim it: unreferenced,
        invisible, and still counted against the 10GB.

        Failures PROPAGATE. `prune_briefings` reports what it deleted and the
        workflow log prints that list; a delete that swallows its own errors
        turns that list into a claim about objects still sitting in the
        bucket. A key that is already gone is not a failure — that is the
        state deletion was asking for.
        """
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

    def _digest_path(self, briefing_id: str) -> Path:
        return self._base_dir / f"{briefing_id}.digest.json"

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
        digest: BriefingDigest | None = None,
    ) -> None:
        audio_path = self._audio_path(briefing_id, audio_format)
        audio_path.write_bytes(audio_bytes)

        # Only NOW remove audio left behind in a different format by an
        # earlier run (a wav fallback superseded by a later m4a, or the
        # reverse). load_audio returns the first format it finds in
        # KNOWN_AUDIO_FORMATS order, so a stale m4a would outrank a fresh wav
        # and the phone would play old audio against new timings.
        #
        # The ordering is the point, and it is the same ordering R2Storage
        # already uses. This ran BEFORE the write, so a write that failed —
        # disk full, container killed — left the old manifest pointing at
        # audio that no longer existed: 200 on the manifest, 404 on the audio,
        # and that was the state of the day. Nothing is deleted on the
        # strength of a write that has not happened yet.
        for fmt in KNOWN_AUDIO_FORMATS:
            stale = self._audio_path(briefing_id, fmt)
            if fmt != audio_format and stale.exists():
                stale.unlink()

        # BEFORE the manifest, for the same reason the audio is: the manifest
        # is what every reader polls to decide a briefing exists, so anything
        # written after it is briefly visible as a 404 on a briefing that
        # already reads as ready.
        #
        # The invariant the three branches below hold is that the stored
        # digest is the one built from the stored MANIFEST, or nothing.
        # `--force` reuses the briefing id, so any digest left over from an
        # earlier run for this day carries `turn_index` values addressing
        # that run's turn list — deep-links into audio that no longer
        # exists, which is worse than the documented empty browse state
        # because it looks like it worked. See `_publish_digest`.
        self._publish_digest(briefing_id, digest)

        manifest_path = self._manifest_path(briefing_id)
        try:
            manifest_path.write_text(manifest.model_dump_json(indent=2))
        except Exception:
            # The other half of the same invariant. The digest just written
            # describes turns in a manifest that never landed, and the
            # manifest still being served is the PREVIOUS run's — so leaving
            # it would pair old audio with new deep-links. Removing it costs
            # the browse screen and keeps the pair the user already had.
            self._digest_path(briefing_id).unlink(missing_ok=True)
            raise

        log.info(
            "stored briefing %s: %s (%d bytes), manifest %s",
            briefing_id, audio_path, len(audio_bytes), manifest_path,
        )

    def _publish_digest(
        self, briefing_id: str, digest: BriefingDigest | None
    ) -> None:
        """Replace the stored digest, or remove it. Never leaves a stale one.

        A failure to WRITE is not fatal: the digest is the browse surface,
        and the manifest plus the audio is the briefing. Refusing to publish
        a working briefing because a secondary object would not write trades
        the product for one of its screens (rule 7). `R2Storage.save` has
        said so since the digest landed; this side raised instead, so a full
        disk lost the briefing rather than the screen.

        A failure to REMOVE what is there already is fatal, and for the same
        reason the stale-audio branch in `R2Storage.save` is: publishing the
        manifest over a digest belonging to an earlier run hands the phone
        deep-links into audio it no longer has. The old manifest and the old
        digest are a consistent pair, so the run fails and leaves the user
        the briefing they already had.

        Written through a temporary file and renamed. `write_text` truncates
        the live file before it writes, and `load_digest` reads whatever is
        there — so a regeneration made `GET /briefing/{id}/digest` answer 404
        for a briefing that was sitting there complete, for as long as the
        write took.
        """
        path = self._digest_path(briefing_id)
        if digest is None:
            path.unlink(missing_ok=True)
            return

        temp = path.with_name(f"{path.name}.tmp")
        try:
            temp.write_text(digest.model_dump_json(indent=2))
            temp.replace(path)
        except Exception:
            log.exception(
                "could not store the digest for %s; publishing the briefing "
                "without it (the app shows an empty browse state, the audio "
                "is unaffected)", briefing_id,
            )
            temp.unlink(missing_ok=True)
            path.unlink(missing_ok=True)

    def load_manifest(self, briefing_id: str) -> BriefingManifest | None:
        path = self._manifest_path(briefing_id)
        if not path.exists():
            return None
        # Read OUTSIDE the parse handler. Inside it, a PermissionError or a
        # transient OSError became UnreadableManifestError, and
        # `existing_manifest` reads that as "no usable briefing" — so a
        # storage outage triggered a full regeneration of a briefing that was
        # sitting there intact, spending real Gemini and TTS money to replace
        # a file nobody could read for thirty seconds. Corrupt JSON is a
        # statement about the briefing; an unreadable disk is a statement
        # about the disk, and only the first one is this exception's job.
        raw = path.read_text()
        try:
            return BriefingManifest.model_validate_json(raw)
        except Exception as exc:
            raise UnreadableManifestError(
                f"the manifest at {path} is not a readable BriefingManifest"
            ) from exc

    def load_digest(self, briefing_id: str) -> BriefingDigest | None:
        path = self._digest_path(briefing_id)
        if not path.exists():
            return None
        try:
            return BriefingDigest.model_validate_json(path.read_text())
        except Exception:
            log.warning(
                "the digest at %s is unreadable; serving no digest for %s",
                path, briefing_id, exc_info=True,
            )
            return None

    def load_audio(self, briefing_id: str) -> tuple[bytes, str] | None:
        for fmt in KNOWN_AUDIO_FORMATS:
            path = self._audio_path(briefing_id, fmt)
            if path.exists():
                media_type = AUDIO_MEDIA_TYPES.get(fmt, "application/octet-stream")
                return path.read_bytes(), media_type
        return None

    def load_settings(self) -> UserSettings | None:
        path = self._base_dir / SETTINGS_KEY
        try:
            raw = path.read_text()
        except FileNotFoundError:
            # Never saved. The state every deployment starts in, and the only
            # one that is silent.
            return None
        except (OSError, UnicodeError):
            # An existing file that cannot be read: a permission problem, a
            # half-written file, invalid UTF-8. Distinguished from absent
            # because the operator needs to know their saved preferences were
            # ignored rather than never existed — but still not fatal, since
            # a preferences file must not be able to break the morning.
            log.error(
                "settings at %s exist but could not be read; using the "
                "environment. Saved preferences are NOT being applied.",
                path, exc_info=True,
            )
            return None
        try:
            return UserSettings.model_validate_json(raw)
        except ValidationError:
            # Corrupt or written by an incompatible client. The environment
            # is a working configuration; a half-parsed one is not, and
            # failing the run over a settings file would make a preferences
            # screen able to break the morning.
            log.error(
                "settings at %s will not parse; using the environment", path
            )
            return None

    def save_settings(self, settings: UserSettings) -> None:
        """Written to a temporary file and renamed over the live one.

        `os.replace` is atomic within a filesystem, so a reader either sees
        the whole previous file or the whole new one. A direct overwrite
        interrupted mid-write leaves a truncated file, which parses as
        corrupt, which silently falls back to the environment — the user's
        settings gone because a process was killed at the wrong moment.
        """
        import os
        import tempfile

        target = self._base_dir / SETTINGS_KEY
        self._base_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._base_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(settings.model_dump_json(indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        except BaseException:
            # Including KeyboardInterrupt: the point is not to leave the
            # temporary file behind either.
            os.unlink(tmp) if os.path.exists(tmp) else None
            raise

    def audio_exists(self, briefing_id: str) -> bool:
        for fmt in KNOWN_AUDIO_FORMATS:
            try:
                size = self._audio_path(briefing_id, fmt).stat().st_size
            except OSError:
                # Absent, or deleted between the check and the stat — the
                # retention sweep runs against this same directory, so the
                # race is real rather than theoretical. Either way this
                # format is not playable; it must not 500 the archive.
                continue
            if size >= MIN_PLAYABLE_AUDIO_BYTES:
                return True
        return False

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
        for path in self._base_dir.iterdir():
            if path.is_file() and briefing_id_from_key(path.name) == briefing_id:
                path.unlink(missing_ok=True)


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

    Compared by DAY, not by id. An id may now carry a `-HHMM` suffix so
    several briefings can share a date, and `date.fromisoformat` raises on
    one — which would have turned the housekeeping step into the thing that
    failed the run. Comparing the day prefix also states the intent
    directly: the window is a number of DAYS, so a morning is kept or
    dropped as a whole rather than four briefings from one day straddling a
    cutoff on the strength of their minute.

    Anything that is not a well-formed briefing id is left alone rather than
    guessed at. This function deletes things; it should be the least clever
    code in the module.
    """
    validate_retention_days(retention_days)

    today_day = briefing_day(today_id)
    try:
        cutoff = date.fromisoformat(today_day) - timedelta(days=retention_days)
    except ValueError as exc:
        raise ValueError(
            f"today_id must start with YYYY-MM-DD, got {today_id!r}"
        ) from exc
    cutoff_id = cutoff.isoformat()

    deleted: list[str] = []
    for briefing_id in storage.list_briefing_ids():
        day = briefing_day(briefing_id)
        if day >= today_day:
            continue
        if day >= cutoff_id:
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
