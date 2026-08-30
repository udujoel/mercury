"""
Cloudflare R2 storage.

ADR-008: generation runs as a GitHub Actions cron and the phone reads the
finished briefing straight from object storage, so there is no backend to host
and nothing that depends on a laptop being awake.

R2 rather than S3 for one reason that dominates at this scale: **zero egress
fees**. The phone downloads roughly 4MB every morning, and egress is what makes
object storage expensive elsewhere. 10GB of storage is free, and a 7-day
retention of ~4MB briefings uses about 30MB of it.

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
from app.core.schemas import BriefingManifest
from app.synthesis.storage import AUDIO_MEDIA_TYPES, KNOWN_AUDIO_FORMATS

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
        for fmt in KNOWN_AUDIO_FORMATS:
            if fmt == audio_format:
                continue
            stale = self._audio_key(briefing_id, fmt)
            try:
                self._client.delete_object(Bucket=self._bucket, Key=stale)
            except Exception:
                log.warning(
                    "could not remove stale audio %s; it may be served instead "
                    "of the file just written", stale, exc_info=True,
                )

        # Manifest LAST, deliberately. It is what the client polls for, so
        # writing it only after the audio is durably stored means the phone
        # can never see a briefing whose audio is still uploading.
        self._client.put_object(
            Bucket=self._bucket,
            Key=self._manifest_key(briefing_id),
            Body=manifest.model_dump_json(indent=2).encode(),
            ContentType="application/json",
        )
        log.info(
            "stored briefing %s in R2: %s (%d bytes) + manifest",
            briefing_id, audio_key, len(audio_bytes),
        )

    def load_manifest(self, briefing_id: str) -> BriefingManifest | None:
        raw = self._get(self._manifest_key(briefing_id))
        if raw is None:
            return None
        try:
            return BriefingManifest.model_validate(json.loads(raw))
        except Exception:
            log.exception("manifest for %s in R2 is unreadable", briefing_id)
            return None

    def load_audio(self, briefing_id: str) -> tuple[bytes, str] | None:
        for fmt in KNOWN_AUDIO_FORMATS:
            data = self._get(self._audio_key(briefing_id, fmt))
            if data is not None:
                return data, AUDIO_MEDIA_TYPES.get(fmt, "application/octet-stream")
        return None

    def delete_audio(self, briefing_id: str) -> None:
        for fmt in KNOWN_AUDIO_FORMATS:
            key = self._audio_key(briefing_id, fmt)
            try:
                self._client.delete_object(Bucket=self._bucket, Key=key)
            except Exception:
                # Deleting a key that is not there is fine; anything else is
                # worth knowing about but must not stop a regeneration.
                log.debug("R2 delete of %s did nothing", key, exc_info=True)

    # ── internals ─────────────────────────────────────────────

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
