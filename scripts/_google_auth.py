"""
Shared installed-app OAuth helper for Phase 0 Google API spikes.

CLAUDE.md rule 4: "Read-only scopes only. gmail.readonly, calendar.readonly.
The app must be structurally incapable of sending, deleting or modifying
anything." SCOPES below is the enforcement point for that rule across every
spike script that imports this module — do not add scopes here.

Usage:
    from _google_auth import get_credentials
    creds = get_credentials()  # raises a clear error if credentials.json is missing

Both `credentials.json` (the OAuth client downloaded from Google Cloud
Console) and `token.json` (the cached user token) are already gitignored at
the repo root — see .gitignore. Never commit either file.
"""

from __future__ import annotations

import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# CLAUDE.md rule 4 — read-only, forever. Do not add send/modify/delete scopes.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CREDENTIALS_FILE = REPO_ROOT / "credentials.json"
DEFAULT_TOKEN_FILE = REPO_ROOT / "token.json"


class MissingCredentialsError(RuntimeError):
    """Raised when credentials.json is absent. Never invent credentials."""


def _credentials_path() -> Path:
    default = str(DEFAULT_CREDENTIALS_FILE)
    return Path(os.environ.get("GOOGLE_CREDENTIALS_FILE", default))


def _token_path() -> Path:
    return Path(os.environ.get("GOOGLE_TOKEN_FILE", str(DEFAULT_TOKEN_FILE)))


def credentials_available() -> bool:
    """True if a credentials.json is present, i.e. a --live run is possible."""
    return _credentials_path().exists()


def get_credentials() -> Credentials:
    """
    Return valid user credentials for the scopes above, running the
    installed-app OAuth flow (opens a browser) on first use and caching the
    result to token.json. Refreshes silently when the cached token expires.

    Raises MissingCredentialsError with the exact Console steps when
    credentials.json cannot be found. Never fabricates credentials.
    """
    creds_path = _credentials_path()
    token_path = _token_path()

    if not creds_path.exists():
        raise MissingCredentialsError(
            f"Missing OAuth client file: {creds_path}\n"
            "This spike needs a Google Cloud OAuth client to run against the "
            "live API. To fix:\n"
            "  1. https://console.cloud.google.com/apis/library -> enable "
            "'Gmail API' and 'Google Calendar API' for your project.\n"
            "  2. https://console.cloud.google.com/apis/credentials/consent "
            "-> configure the OAuth consent screen in Testing mode, add "
            "yourself as the sole test user.\n"
            "  3. https://console.cloud.google.com/apis/credentials -> "
            "Create Credentials -> OAuth client ID -> Application type "
            "'Desktop app'.\n"
            "  4. Download the JSON and save it as "
            f"{creds_path} (or point GOOGLE_CREDENTIALS_FILE at it).\n"
            "Run with --fixture instead to exercise the offline path."
        )

    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return creds
