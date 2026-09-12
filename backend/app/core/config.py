from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# What the environment held before any artifact overrode it, so a setting
# cleared later can be undone rather than left in place.
_ORIGINAL_ENVIRONMENT: dict[str, str | None] = {}


# Spellings of "elevenlabs" a settings artifact might carry. Mirrors
# `SettingsView.elevenLabsAliases` on the phone; the two are the same
# question asked on either side of the wire.
_ELEVENLABS_ALIASES = frozenset(
    {"elevenlabs", "eleven_labs", "eleven-labs", "11labs"}
)


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

    # WHICH voice variables the chosen voices land in depends on the
    # provider, and getting this wrong is invisible.
    #
    # `UserSettings.voice_a/voice_b` are provider-neutral by name and were
    # mapped only to `ELEVENLABS_VOICE_*`, from a time when ElevenLabs was
    # the default and Google was a fallback nobody was supposed to reach.
    # Google became the default on 2026-09-12, and `google_tts.py` reads
    # `tts_voice_host_a/_b` — which nothing could write. So the voice
    # setting would have applied to a provider that never runs while the
    # provider that does run kept its built-in voices: a picker that saves,
    # round-trips and changes nothing, which is precisely the `music_set`
    # bug wearing different clothes.
    #
    # Resolved against the same default the rest of the process uses, so an
    # artifact that leaves `tts_provider` null lands its voices in the
    # variables the server default will actually read.
    provider = (user_settings.tts_provider
                or _BLANK_DEFAULTS["tts_provider"]).strip().lower()
    voice_a_key, voice_b_key = (
        ("ELEVENLABS_VOICE_A", "ELEVENLABS_VOICE_B")
        if provider in _ELEVENLABS_ALIASES
        else ("TTS_VOICE_HOST_A", "TTS_VOICE_HOST_B")
    )

    mapping = {
        "TTS_PROVIDER": user_settings.tts_provider,
        voice_a_key: user_settings.voice_a,
        voice_b_key: user_settings.voice_b,
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

# BOTH MUST BE **PREMADE** VOICES, not library or professional ones.
#
# The first pair here paid for that lesson. "Posh - A British Woman"
# (`3Drdg7QWqr45nZmYpXRP`) is exactly the voice that was asked for, and it
# is a LIBRARY voice — so every request for it came back
#
#   402 paid_plan_required: "Free users cannot use library voices via the
#   API. Please upgrade your subscription to use this voice."
#
# while Brian, a premade voice, returned 200. The run then degraded to
# Google for that host and shipped a briefing narrated by two men, with the
# 402 sitting in the log of a workflow that reported success. Nothing in the
# audio said which half had failed.
#
# The ElevenLabs voice library shows premade and library voices side by
# side and the id looks identical; the only signal is the `category` field
# on `GET /v1/voices` ("premade" is free-tier usable, "professional" and
# library voices are not). Check it before changing either of these.

# Google's en-US Neural2 set: A, D, I and J are male; C, E, F, G and H are
# female. Matched to the host NAMES, exactly like the ElevenLabs pair — these
# were the other half of the same bug, and stayed invisible for months
# because they are only reached when ElevenLabs fails.
DEFAULT_GOOGLE_VOICE_A = "en-US-Neural2-F"
DEFAULT_GOOGLE_VOICE_B = "en-US-Neural2-D"

# Alice: British, female, clear — and premade, so the free tier can speak
# her. Host A — Mara.
DEFAULT_ELEVENLABS_VOICE_A = "Xb7hH8MSUJpSbSDYk0k2"
# Brian: deep, resonant, comforting, and premade. Host B — Theo.
DEFAULT_ELEVENLABS_VOICE_B = "nPczCjzI2devNBz1zQrb"


# What a blank environment variable falls back to. Named here rather than
# read off the model, so the fallback cannot change shape when a field's
# declaration does.
_BLANK_DEFAULTS = {
    # Google, per the decision documented on `tts_provider` below. This is
    # the value a BLANK env var falls back to, and it has to move with the
    # pydantic default or an unset `TTS_PROVIDER` secret — which is exactly
    # what production has — would still select ElevenLabs.
    "tts_provider": "google",
    "elevenlabs_voice_a": DEFAULT_ELEVENLABS_VOICE_A,
    "elevenlabs_voice_b": DEFAULT_ELEVENLABS_VOICE_B,
    # The GOOGLE pair too, and they were missing.
    #
    # `briefing.yml` does not pass these today, so pydantic's defaults apply
    # and production is correct — but that is luck, not design. The exact
    # bug this validator exists for was a secret REFERENCED in the workflow
    # and never set: `TTS_PROVIDER: ${{ secrets.TTS_PROVIDER }}` interpolates
    # to "" when unset, and an empty string beats a default. Adding
    # `TTS_VOICE_HOST_A: ${{ secrets.TTS_VOICE_HOST_A }}` to that env block
    # is the obvious thing to do when someone wants to override a voice, and
    # it would hand Google an empty voice name — a briefing read by whatever
    # voice the API picks, with nothing in the log saying why.
    "tts_voice_host_a": DEFAULT_GOOGLE_VOICE_A,
    "tts_voice_host_b": DEFAULT_GOOGLE_VOICE_B,
}


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
    #
    # **Google, decided 2026-09-12.** This defaulted to ElevenLabs on the
    # reasoning that a key on its own should be enough to switch voices, with
    # `build_tts_provider` falling back to Google when the key was missing or
    # the service unreachable. That fallback was written for a rare outage.
    # What it actually did was fire on EVERY run: the free-tier account ran
    # out of credits, so four runs a day logged `401 quota_exceeded`, failed
    # over, shipped in Google's voices, and exited 0 green. The roadmap
    # claimed a voice the product had not used in days.
    #
    # The owner has decided against a paid plan, so the honest default is the
    # provider that actually speaks. This is not a downgrade of behaviour —
    # every briefing since at least 2026-09-10 has been Google — it is the
    # configuration finally agreeing with the audio.
    #
    # ElevenLabs is NOT deleted. `ElevenLabsTTSProvider` stays, tested, and
    # `TTS_PROVIDER=elevenlabs` still selects it, because rule 6 exists so a
    # provider can be swapped back the day someone funds it. What changes is
    # which one you get by saying nothing.
    #
    # Consequence worth stating plainly: Google Neural2 returns no word
    # timings, so the transcript highlights by line rather than by word. See
    # ROADMAP task 21.
    tts_provider: str = "google"
    google_tts_credentials: str = "./credentials/gcp-tts.json"
    # Matched to the host NAMES, like the ElevenLabs pair above — and for
    # the same reason, because these were the other half of the same bug.
    # Host A is Mara and got `Neural2-D`, which is male; host B is Theo and
    # got `Neural2-F`, which is female. Swapped, and invisible for as long
    # as ElevenLabs was answering: the Google voices are only reached when
    # ElevenLabs fails, so the fallback for a woman's part was a man's
    # voice and nothing exercised it until the 402 above.
    tts_voice_host_a: str = DEFAULT_GOOGLE_VOICE_A
    tts_voice_host_b: str = DEFAULT_GOOGLE_VOICE_B
    elevenlabs_api_key: str = ""
    # Two voices that sound like a pair rather than two strangers: a deep,
    # resonant male and a poised British female. Defaults rather than
    # required, so the API key alone is enough to get the intended show —
    # overridable from Settings, and from the environment.
    elevenlabs_voice_a: str = DEFAULT_ELEVENLABS_VOICE_A
    elevenlabs_voice_b: str = DEFAULT_ELEVENLABS_VOICE_B

    @field_validator(
        "tts_provider",
        "elevenlabs_voice_a",
        "elevenlabs_voice_b",
        "tts_voice_host_a",
        "tts_voice_host_b",
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
            # An explicit map, not `model_fields[...].default`.
            #
            # That lookup works today and returns a `FieldInfo` the moment
            # any of these three gains a `default_factory` — which pydantic
            # makes an easy thing to reach for — so the "blank means
            # default" path would start handing a settings object to code
            # expecting a string, silently, with no type error until
            # something compared it to "elevenlabs" and lost.
            return _BLANK_DEFAULTS[info.field_name]
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
