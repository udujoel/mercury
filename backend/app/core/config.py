from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# What the environment held before any artifact overrode it, so a setting
# cleared later can be undone rather than left in place.
_ORIGINAL_ENVIRONMENT: dict[str, str | None] = {}


def apply_to_environment(user_settings) -> None:
    """Write saved settings into the process environment, then rebuild.

    Not elegant, and deliberate. `get_settings()` is already the single
    place every component asks about configuration — providers, feeds, the
    script chain — and it is `lru_cache`d. Threading a `UserSettings` object
    through all of them would create a SECOND source of truth for the same
    questions, and the two would drift the first time one call site was
    missed. Writing the values where `Settings` already looks keeps one.

    Only non-empty values are applied: a null field means "use the
    environment", which is what the artifact is missing entirely.
    """
    import os

    mapping = {
        "TTS_PROVIDER": user_settings.tts_provider,
        "ELEVENLABS_VOICE_A": user_settings.voice_a,
        "ELEVENLABS_VOICE_B": user_settings.voice_b,
    }
    for key, value in mapping.items():
        if value:
            _ORIGINAL_ENVIRONMENT.setdefault(key, os.environ.get(key))
            os.environ[key] = value
        elif key in _ORIGINAL_ENVIRONMENT:
            # Cleared in the artifact after having been set. Null means "use
            # the environment", so the environment has to come back — in a
            # long-lived process (the API, the scheduler) a stale override
            # would outlive the setting that created it and null would
            # quietly mean "keep the last value".
            #
            # The record is READ, not popped. Popping it made this work
            # exactly once: a second call with the same field still null
            # found nothing to restore and left the first call's override in
            # place, so "null means the environment" quietly stopped being
            # true after one round trip. The entry is only meaningful while
            # the process lives, and keeping it costs nothing.
            previous = _ORIGINAL_ENVIRONMENT[key]
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous

    # Cleared so the next `get_settings()` sees what was just written.
    # Without this the whole function is a no-op with a log line, which is
    # the exact failure mode it exists to prevent one level up.
    get_settings.cache_clear()


# Matched to the HOST NAMES below, not to the order they were suggested in.
#
# `host_a_name` is "Mara" and `host_b_name` is "Theo", so host A takes the
# British woman and host B takes Brian. Assigning them the other way round
# gave Mara a deep male voice and Theo a woman's — a gender-swapped
# conversation that reads as a bug in every single briefing, and one nobody
# would think to look for in a config file.
#
# If the host names change, these move with them.

# A poised British woman. Host A — Mara.
DEFAULT_ELEVENLABS_VOICE_A = "3Drdg7QWqr45nZmYpXRP"
# Brian: deep, resonant, comforting. Host B — Theo.
DEFAULT_ELEVENLABS_VOICE_B = "nPczCjzI2devNBz1zQrb"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # LLM
    gemini_api_key: str = ""
    llm_model: str = "gemini-2.5-flash"

    # text-embedding-004 is RETIRED — it 404s against v1beta as of Aug 2026,
    # despite being what the PRD and the original scaffold specified.
    # gemini-embedding-001 is the live replacement (3072 dimensions, up from
    # 768, which is fine at personal scale but is a real memory difference if
    # this ever holds a large corpus in one numpy matrix).
    embedding_model: str = "gemini-embedding-001"

    # TTS
    # ElevenLabs by default. `build_tts_provider` falls back to Google and
    # logs loudly when the key is missing or ElevenLabs is unreachable, so a
    # deployment with no ElevenLabs key still ships a briefing — it just
    # ships in the old voice. Defaulting the other way meant a key on its
    # own did nothing, and the difference was invisible.
    tts_provider: str = "elevenlabs"
    google_tts_credentials: str = "./credentials/gcp-tts.json"
    tts_voice_host_a: str = "en-US-Neural2-D"
    tts_voice_host_b: str = "en-US-Neural2-F"
    elevenlabs_api_key: str = ""
    # Two voices that sound like a pair rather than two strangers: a deep,
    # resonant male and a poised British female. Defaults rather than
    # required, so the API key alone is enough to get the intended show —
    # overridable from Settings, and from the environment.
    elevenlabs_voice_a: str = DEFAULT_ELEVENLABS_VOICE_A
    elevenlabs_voice_b: str = DEFAULT_ELEVENLABS_VOICE_B

    @field_validator(
        "tts_provider", "elevenlabs_voice_a", "elevenlabs_voice_b",
        mode="before",
    )
    @classmethod
    def _blank_means_unset(cls, value, info):
        """An empty environment variable means "not set", not "empty".

        This is the bug that made a configured ElevenLabs key do nothing.
        `briefing.yml` passes `TTS_PROVIDER: ${{ secrets.TTS_PROVIDER }}`,
        and an unset secret interpolates to the EMPTY STRING rather than
        being omitted — so the variable is present, pydantic takes it over
        the field default, and `tts_provider` became "" on every run. The
        defaults added specifically so the API key alone would be enough
        were overridden by their own absence.

        Fixed here rather than in the workflow. A `|| 'elevenlabs'` in YAML
        would put the default in two places and only cover the one caller
        that remembered it; this covers the workflow, a local `.env` with a
        blank line, and an exported shell variable alike.
        """
        if value is None or (isinstance(value, str) and not value.strip()):
            return cls.model_fields[info.field_name].default
        return value.strip() if isinstance(value, str) else value

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"

    # Optional sources
    slack_client_id: str = ""
    slack_client_secret: str = ""
    reddit_user_agent: str = "mercury/0.1"

    # User
    user_name: str = "there"
    # The Google account the OAuth token is issued for. Used to decide
    # whether the user is a direct "To" recipient when scoring email
    # priority. Empty means that signal is simply unavailable.
    user_email: str = ""
    user_timezone: str = "Europe/Lisbon"
    user_lat: float = 40.6405
    user_lon: float = -8.6538
    wake_time: str = "07:00"
    briefing_length: str = "standard"
    host_a_name: str = "Mara"
    host_b_name: str = "Theo"

    # API auth — REQUIRED. The briefing routes refuse to serve without it;
    # see app/core/auth.py for why this fails closed rather than open.
    # Generate: python -c "import secrets; print(secrets.token_urlsafe(32))"
    mercury_api_token: str = ""

    # APNs — silent push "briefing is ready" hint. See app/notifications/apns.py.
    # Path to the .p8 auth key downloaded from the Apple Developer portal.
    apns_key_path: str = ""
    # The 10-character Key ID shown next to the key in the Apple Developer portal.
    apns_key_id: str = ""
    # The 10-character Team ID for the Apple Developer account.
    apns_team_id: str = ""
    # The app's bundle identifier — the APNs "topic" the push is addressed to.
    apns_topic: str = ""
    # True for development/TestFlight builds (api.sandbox.push.apple.com),
    # False for production App Store builds (api.push.apple.com).
    apns_use_sandbox: bool = True

    # Scheduler — the server-side cron that runs generate_briefing daily.
    # Off by default so tests and local dev never schedule real generation;
    # see app/scheduler/cron.py.
    scheduler_enabled: bool = False
    # Minutes before wake_time the cron fires generation, so the briefing is
    # ready (not merely started) by the time the user wakes up.
    generation_lead_minutes: int = 30

    # Runtime
    database_url: str = "sqlite+aiosqlite:///./mercury.db"
    audio_output_dir: str = "./out"

    # Storage backend: "local" or "r2". ADR-008 — the deployed path writes to
    # Cloudflare R2 and the phone reads straight from it, so there is no
    # backend to host.
    storage_backend: str = "local"
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""

    # How many days of briefings to keep. Nothing deleted anything before this
    # existed, so storage grew forever — ~3MB of audio plus a manifest every
    # morning against a 10GB free tier. 30 days is about 90MB, which is both
    # comfortably inside the tier and long enough to go back and listen to
    # something. Must be at least 1; prune_briefings refuses 0 rather than
    # interpreting it as "delete today".
    retention_days: int = 30

    log_level: str = "INFO"

    # Clustering — tuned against a real threshold sweep, not guessed.
    #
    # At 0.82 the canonical ADR-003 case does NOT fully collapse: an event
    # covered by Reuters, Stratechery, Benedict Evans and Platformer splits,
    # with Reuters breaking off at 0.7885 similarity while the other three
    # merge. That is the exact failure the clustering exists to prevent.
    #
    # 0.78 sits inside the verified-correct band [0.70, 0.80] and still keeps
    # 0.089 of margin above the highest similarity between two stories that
    # must NOT merge (a SpaceX / Rocket Lab pair at 0.6908). ADR-003 says
    # merging unrelated stories is far worse than leaving a duplicate, so the
    # margin matters more than the collapse.
    cluster_similarity_threshold: float = 0.78

    # Ranking weights
    w_personal: float = 0.35
    w_urgency: float = 0.25
    w_interest: float = 0.20
    w_cluster: float = 0.12
    w_recency: float = 0.08


@lru_cache
def get_settings() -> Settings:
    return Settings()
