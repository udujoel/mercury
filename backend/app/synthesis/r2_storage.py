"""
Cloudflare R2 storage.

ADR-008: generation runs as a GitHub Actions cron and the phone reads the
finished briefing straight from object storage, so there is no backend to host
and nothing that depends on a laptop being awake.

R2 rather than S3 for one reason that dominates at this scale: **zero egress
fees**. The phone downloads roughly 4MB every morning, and egress is what makes
object storage expensive elsewhere. 10GB of storage is free.

That last number used to be followed by "and a 7-day retention of ~4MB
briefings uses about 30MB of it". There was no 7-day retention. Nothing deleted
anything, here or in the workflow or in any configuration — the sentence
described a policy that was never implemented, and it read as reassurance every
time anyone looked. Retention is real now (`prune_briefings`, 30 days by
default, `RETENTION_DAYS`), which is what makes the arithmetic true rather than
aspirational: ~3MB a day for 30 days is about 90MB of the 10GB.

R2 speaks the S3 API, so this is boto3 against a custom endpoint. Satisfies the
same `BriefingStorage` protocol as `LocalFilesystemStorage`, so nothing in the
pipeline knows or cares which one it is writing to — that is ADR-004's
swappable-provider rule applied to storage.
"""

from __future__ import annotations

import json
import logging
from functools import cached_property

from app.core.config import get_settings
from app.core.schemas import BriefingDigest, BriefingManifest
from app.synthesis.storage import (
    AUDIO_MEDIA_TYPES,
    KNOWN_AUDIO_FORMATS,
    MIN_PLAYABLE_AUDIO_BYTES,
    UnreadableManifestError,
    briefing_id_from_key,
)

log = logging.getLogger(__name__)


class R2ConfigurationError(RuntimeError):
    """R2 settings are missing. Never silently fall back to local disk — a
    scheduled run that writes to a container's ephemeral filesystem looks
    like success and produces a briefing nobody can reach."""


class R2Storage:
    """BriefingStorage backed by a Cloudflare R2 bucket."""

    def __init__(
        self,
        *,
        account_id: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        bucket: str | None = None,
    ) -> None:
        s = get_settings()
        self._account_id = account_id or s.r2_account_id
        self._access_key_id = access_key_id or s.r2_access_key_id
        self._secret_access_key = secret_access_key or s.r2_secret_access_key
        self._bucket = bucket or s.r2_bucket

        missing = [
            name
            for name, value in (
                ("R2_ACCOUNT_ID", self._account_id),
                ("R2_ACCESS_KEY_ID", self._access_key_id),
                ("R2_SECRET_ACCESS_KEY", self._secret_access_key),
                ("R2_BUCKET", self._bucket),
            )
            if not value
        ]
        if missing:
            raise R2ConfigurationError(
                f"R2 storage is not configured — missing {', '.join(missing)}. "
                "Set them in backend/.env locally, or as repository secrets for "
                "the GitHub Actions workflow. See docs/DEPLOYMENT.md."
            )

    @cached_property
    def _client(self):
        # Imported lazily so a machine without boto3, or without R2 configured,
        # can still import the module and run the local-storage path.
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=f"https://{self._account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=self._access_key_id,
            aws_secret_access_key=self._secret_access_key,
            region_name="auto",  # R2 ignores region but boto3 requires one
        )

    # ── keys ──────────────────────────────────────────────────

    @staticmethod
    def _manifest_key(briefing_id: str) -> str:
        return f"{briefing_id}.manifest.json"

    @staticmethod
    def _digest_key(briefing_id: str) -> str:
        # Must stay "{id}.digest.json": `briefing_id_from_key` reads the id
        # back off the prefix, and that is what makes retention find and
        # delete this object without knowing it exists.
        return f"{briefing_id}.digest.json"

    @staticmethod
    def _audio_key(briefing_id: str, audio_format: str) -> str:
        return f"{briefing_id}.{audio_format}"

    def audio_path_for(self, briefing_id: str) -> str:
        return f"/briefing/{briefing_id}/audio"

    # ── BriefingStorage ───────────────────────────────────────

    def save(
        self,
        briefing_id: str,
        audio_bytes: bytes,
        audio_format: str,
        manifest: BriefingManifest,
        digest: BriefingDigest | None = None,
    ) -> None:
        audio_key = self._audio_key(briefing_id, audio_format)
        self._client.put_object(
            Bucket=self._bucket,
            Key=audio_key,
            Body=audio_bytes,
            ContentType=AUDIO_MEDIA_TYPES.get(audio_format, "application/octet-stream"),
        )

        # Clear any audio left behind in a DIFFERENT format, now that the
        # replacement is durably stored. load_audio returns the first format it
        # finds in KNOWN_AUDIO_FORMATS order, so an m4a from an earlier run
        # would outrank a freshly written wav and the phone would play the old
        # audio against the new manifest, putting every turn boundary wrong.
        #
        # Ordering is the whole point. `--force` used to do this BEFORE
        # regenerating, which meant a failed regeneration left the old manifest
        # pointing at an object that no longer existed — 200 on the manifest,
        # 404 on the audio. Doing it here means nothing is deleted until its
        # replacement exists, and the old manifest is still the one being
        # served throughout, so the pair stays consistent the whole way.
        #
        # A cleanup that FAILS is treated differently depending on which way
        # round the two formats rank, because the consequences are opposite.
        #
        #   stale outranks the new file (an m4a superseded by a wav): the
        #   leftover is what load_audio and the Worker will serve. Publishing
        #   the new manifest over it hands the phone OLD audio against NEW turn
        #   timings — every boundary wrong, permanently, and indistinguishable
        #   from success in the Actions log. The old manifest and that old m4a
        #   are still a consistent pair, so the run fails and leaves the user
        #   the briefing they already had. Retention clears the orphan.
        #
        #   the new file outranks the stale one (a wav superseded by an m4a):
        #   the leftover is never served, and publishing the manifest is
        #   exactly what makes the pair consistent. A warning, not a failure —
        #   a briefing must not be lost over a tidying-up problem.
        rank = {fmt: i for i, fmt in enumerate(KNOWN_AUDIO_FORMATS)}
        for fmt in KNOWN_AUDIO_FORMATS:
            if fmt == audio_format:
                continue
            stale = self._audio_key(briefing_id, fmt)
            try:
                self._delete(stale)
            except Exception:
                if rank.get(fmt, len(rank)) < rank.get(audio_format, len(rank)):
                    log.error(
                        "could not remove stale audio %s, which outranks the "
                        "%s just written; refusing to publish a manifest that "
                        "would describe audio the phone will not be served",
                        stale, audio_format,
                    )
                    raise
                log.warning(
                    "could not remove stale audio %s; it is outranked by the "
                    "%s just written, so nothing will serve it",
                    stale, audio_format, exc_info=True,
                )

        # Also before the manifest. The invariant is that the stored digest
        # is the one built from the stored MANIFEST, or nothing at all —
        # see `_publish_digest`.
        self._publish_digest(briefing_id, digest)

        # Manifest LAST, deliberately. It is what the client polls for, so
        # writing it only after the audio is durably stored means the phone
        # can never see a briefing whose audio is still uploading.
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=self._manifest_key(briefing_id),
                Body=manifest.model_dump_json(indent=2).encode(),
                ContentType="application/json",
            )
        except Exception:
            # The other half of the same invariant. The digest just uploaded
            # describes turns in a manifest that never landed, and the
            # manifest still being served is the PREVIOUS run's — so leaving
            # it would pair old audio with new deep-links. Removing it costs
            # the browse screen and keeps the pair the user already had.
            if digest is not None:
                try:
                    self._delete(self._digest_key(briefing_id))
                except Exception:
                    log.exception(
                        "the manifest for %s failed to publish and its "
                        "digest could not be withdrawn; the browse screen "
                        "will deep-link into the previous run's audio",
                        briefing_id,
                    )
            raise
        log.info(
            "stored briefing %s in R2: %s (%d bytes) + manifest",
            briefing_id, audio_key, len(audio_bytes),
        )

    def _publish_digest(
        self, briefing_id: str, digest: BriefingDigest | None
    ) -> None:
        """Replace the stored digest, or remove it. Never leaves a stale one.

        A failure to UPLOAD is not fatal. The digest is the browse surface;
        the manifest plus the audio is the briefing, and refusing to publish
        a working briefing because a secondary object would not upload
        trades the product for one of its screens (rule 7).

        A failure to REMOVE what is there already IS fatal, for the same
        reason the stale-audio branch above is. `--force` reuses the
        briefing id, so a digest left over from an earlier run for this day
        carries `turn_index` values addressing that run's turn list;
        publishing a new manifest over it hands the phone deep-links into
        audio that no longer exists — wrong in a way that looks like it
        worked, and permanent until the next run. The old manifest and that
        old digest are a consistent pair, so the run fails and leaves the
        user the briefing they already had.

        `digest is None` takes the same removal path, and not as a nicety:
        an enrichment failure or a run with nothing browsable produces no
        digest at all, and that is exactly a morning where yesterday's would
        otherwise stay on the screen labelled as today's.

        Nothing is deleted, and so nothing can fail, unless there is an
        object under the key. Most runs with no digest are a first run or a
        day the enrichment failed twice, and issuing a delete on a key that
        was never written would turn an under-scoped token into a lost
        briefing over an object that does not exist.
        """
        key = self._digest_key(briefing_id)
        if digest is not None:
            try:
                self._client.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=digest.model_dump_json(indent=2).encode(),
                    ContentType="application/json",
                )
                return
            except Exception:
                log.exception(
                    "could not store the digest for %s; publishing the "
                    "briefing without it (the app shows an empty browse "
                    "state, the audio is unaffected)", briefing_id,
                )

        if self._get(key) is None:
            return
        try:
            self._delete(key)
        except Exception:
            log.error(
                "could not remove the previous digest for %s; refusing to "
                "publish a manifest whose deep-links it would answer with "
                "another run's turns", briefing_id,
            )
            raise

    def load_manifest(self, briefing_id: str) -> BriefingManifest | None:
        raw = self._get(self._manifest_key(briefing_id))
        if raw is None:
            return None
        try:
            return BriefingManifest.model_validate(json.loads(raw))
        except Exception as exc:
            # Raise rather than return None. None means "never written", and
            # `app.cli check` turns that into "the scheduler dropped the run,
            # go and re-run the workflow" — the wrong cause, pointing the
            # operator at GitHub while the broken object sits in the bucket.
            log.exception("manifest for %s in R2 is unreadable", briefing_id)
            raise UnreadableManifestError(
                f"the manifest object for {briefing_id} in R2 is not a "
                "readable BriefingManifest"
            ) from exc

    def load_digest(self, briefing_id: str) -> BriefingDigest | None:
        raw = self._get(self._digest_key(briefing_id))
        if raw is None:
            return None
        try:
            return BriefingDigest.model_validate(json.loads(raw))
        except Exception:
            # Unlike the manifest, this does NOT raise. Nothing depends on
            # the digest to play a briefing, so there is no operator decision
            # riding on telling corrupt apart from absent — and the client
            # already has to render "no digest for this briefing" for every
            # day generated before the digest existed.
            log.exception("the digest for %s in R2 is unreadable", briefing_id)
            return None

    def load_audio(self, briefing_id: str) -> tuple[bytes, str] | None:
        for fmt in KNOWN_AUDIO_FORMATS:
            data = self._get(self._audio_key(briefing_id, fmt))
            if data is not None:
                return data, AUDIO_MEDIA_TYPES.get(fmt, "application/octet-stream")
        return None

    def audio_exists(self, briefing_id: str) -> bool:
        """HEAD, not GET.

        The archive index asks this of every briefing it lists, and `_get`
        would download each one — thirty three-megabyte files to answer a
        yes/no question.

        Size rather than presence, matching the Worker and the local
        backend: `head_object` succeeds for a zero-byte key, which is what
        an upload that died mid-flight leaves behind.
        """
        from botocore.exceptions import ClientError

        for fmt in KNOWN_AUDIO_FORMATS:
            try:
                head = self._client.head_object(
                    Bucket=self._bucket, Key=self._audio_key(briefing_id, fmt)
                )
            except ClientError as exc:
                # ONLY a 404 means absent. Swallowing every ClientError made a
                # rotated or under-scoped credential look like an empty
                # bucket, so the archive would come back empty and say so
                # confidently — the same class of lie as `_delete` reporting
                # success on a 403, which this module already documents.
                status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                if status == 404 or exc.response.get("Error", {}).get("Code") in {
                    "404", "NoSuchKey", "NotFound",
                }:
                    continue
                raise
            if head.get("ContentLength", 0) >= MIN_PLAYABLE_AUDIO_BYTES:
                return True
        return False

    def list_briefing_ids(self) -> list[str]:
        ids = {
            briefing_id
            for key in self._iter_keys()
            if (briefing_id := briefing_id_from_key(key)) is not None
        }
        return sorted(ids)

    def delete_briefing(self, briefing_id: str) -> None:
        # Every object under this id, not just the manifest and the two audio
        # formats this module writes. Retention exists to reclaim space, and
        # anything else stored for the briefing survived the deletion meant to
        # reclaim it — unreferenced, invisible, still counted against the
        # 10GB.
        # The listing is materialised before anything is deleted: paginating
        # a listing while mutating what it lists skips whatever slides into
        # the page boundary.
        keys = [
            key
            for key in self._iter_keys(prefix=f"{briefing_id}.")
            if briefing_id_from_key(key) == briefing_id
        ]
        for key in keys:
            self._delete(key)

    def delete_audio(self, briefing_id: str) -> None:
        for fmt in KNOWN_AUDIO_FORMATS:
            self._delete(self._audio_key(briefing_id, fmt))

    # ── internals ─────────────────────────────────────────────

    def _iter_keys(self, prefix: str | None = None):
        """Every key in the bucket, or every key under a prefix.

        Paginates properly. A bucket holding more than 1000 objects would
        otherwise silently return only the first page, and retention would
        quietly stop working at exactly the point it starts to matter.
        """
        token: str | None = None
        while True:
            kwargs: dict = {"Bucket": self._bucket}
            if prefix:
                kwargs["Prefix"] = prefix
            if token:
                kwargs["ContinuationToken"] = token
            response = self._client.list_objects_v2(**kwargs)
            for item in response.get("Contents", []):
                yield item["Key"]
            if not response.get("IsTruncated"):
                return
            token = response.get("NextContinuationToken")
            if not token:
                return

    def _delete(self, key: str) -> None:
        """Delete a key. A 404 is success; anything else is a real failure.

        It used to swallow everything, so `prune_briefings` reported briefings
        as deleted while a 403 from a rotated or under-scoped token left every
        object exactly where it was — retention that claims success and
        reclaims nothing, every morning, until the free tier runs out.
        """
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            if self._status_code(exc) == 404 or type(exc).__name__ == "NoSuchKey":
                log.debug("R2 key %s was already gone", key)
                return
            raise

    @staticmethod
    def _status_code(exc: Exception) -> int | None:
        """HTTP status from a botocore ClientError, if there is one."""
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            meta = response.get("ResponseMetadata") or {}
            code = meta.get("HTTPStatusCode")
            if isinstance(code, int):
                return code
        return None

    def _get(self, key: str) -> bytes | None:
        """Fetch a key, or None if it genuinely does not exist.

        Only a real 404 counts as "not there". Treating every ClientError as
        a miss meant a 403 from a rotated or under-scoped token was reported
        as "this briefing was never generated" — so the CLI would rerun the
        whole 127-second pipeline, spending real Gemini and TTS money, every
        single day, with nothing in the log saying "permission denied".
        Anything that is not a 404 is raised so the run fails visibly.
        """
        try:
            return self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        except Exception as exc:
            status = self._status_code(exc)
            if status == 404 or type(exc).__name__ == "NoSuchKey":
                log.debug("R2 key %s not found", key)
                return None
            log.error("R2 read failed for %s (HTTP %s): %r", key, status, exc)
            raise
