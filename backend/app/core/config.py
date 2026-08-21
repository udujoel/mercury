from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # LLM
    gemini_api_key: str = ""
    llm_model: str = "gemini-2.5-flash"
    embedding_model: str = "text-embedding-004"

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
    user_timezone: str = "Europe/Lisbon"
    user_lat: float = 40.6405
    user_lon: float = -8.6538
    wake_time: str = "07:00"
    briefing_length: str = "standard"
    host_a_name: str = "Mara"
    host_b_name: str = "Theo"

    # Runtime
    database_url: str = "sqlite+aiosqlite:///./mercury.db"
    audio_output_dir: str = "./out"
    log_level: str = "INFO"

    # Clustering — tune this against your own data. Start conservative.
    cluster_similarity_threshold: float = 0.82

    # Ranking weights
    w_personal: float = 0.35
    w_urgency: float = 0.25
    w_interest: float = 0.20
    w_cluster: float = 0.12
    w_recency: float = 0.08


@lru_cache
def get_settings() -> Settings:
    return Settings()
