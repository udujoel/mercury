from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Runtime
    database_url: str = "sqlite+aiosqlite:///./mercury.db"
    audio_output_dir: str = "./out"
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
