from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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
            os.environ[key] = value

    # Cleared so the next `get_settings()` sees what was just written.
    # Without this the whole function is a no-op with a log line, which is
    # the exact failure mode it exists to prevent one level up.
    get_settings.cache_clear()


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
    tts_provider: str = "google"
    google_tts_credentials: str = "./credentials/gcp-tts.json"
    tts_voice_host_a: str = "en-US-Neural2-D"
    tts_voice_host_b: str = "en-US-Neural2-F"
    elevenlabs_api_key: str = ""
    elevenlabs_voice_a: str = ""
    elevenlabs_voice_b: str = ""

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
