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

from google.auth.exceptions import RefreshError
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


class ExpiredCredentialsError(MissingCredentialsError):
    """The stored token exists but can no longer be refreshed.

    A separate type, not just a different message, because this is the one
    credential failure with a KNOWN cause and a permanent fix, and callers need
    to be able to say so without pattern-matching on prose. While the OAuth
    consent screen is in Testing mode Google expires refresh tokens after seven
    days, so unattended generation stops every week — and CLAUDE.md rule 7
    means a failed source is dropped rather than raised, so the only symptom
    the user ever sees is a briefing that quietly stopped mentioning their
    inbox. `app.cli` keys its loudest error off this type.

    Still a MissingCredentialsError so every existing handler keeps working.
    """


def _credentials_path() -> Path:
    default = str(DEFAULT_CREDENTIALS_FILE)
    return Path(os.environ.get("GOOGLE_CREDENTIALS_FILE", default))


def _token_path() -> Path:
    return Path(os.environ.get("GOOGLE_TOKEN_FILE", str(DEFAULT_TOKEN_FILE)))


def credentials_available() -> bool:
    """True if a live run is possible at all.

    A cached token.json is sufficient on its own — see get_credentials. This
    used to check only for credentials.json, which meant that on a box
    provisioned the way the Actions runner is (token present, client file
    absent — now the supported deployment shape) the spikes silently fell back
    to fixtures and printed fixture data that looked like a real inbox.
    """
    return _token_path().exists() or _credentials_path().exists()


def get_credentials() -> Credentials:
    """
    Return valid user credentials for the scopes above.

    A cached token.json is enough on its own: `InstalledAppFlow` writes the
    client_id and client_secret into it, so refreshing needs no other file.
    credentials.json is required ONLY to run the interactive browser flow for
    the first authorisation.

    That ordering is the whole point. Demanding credentials.json up front made
    the GitHub Actions run fail both Gmail and Calendar ingestion with
    "Missing OAuth client file" while holding a perfectly valid token — and
    because rule 7 drops a failed source rather than failing the run, the
    briefing shipped with no email and no calendar in it. A green tick over a
    briefing missing the entire personal half of the product.

    An interactive flow can never run unattended anyway, so requiring the file
    that only the interactive flow needs was backwards.
    """
    creds_path = _credentials_path()
    token_path = _token_path()

    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            # A 5xx, a timeout or a rate limit from Google's token endpoint is
            # NOT an expired token. Both arrive as RefreshError, and converting
            # both told the user "your token has permanently expired, here is a
            # ten-minute manual procedure" about an outage that would have
            # cleared itself by the next checkpoint — while the run lost the
            # inbox and calendar for the morning. google-auth already draws the
            # line, from the OAuth error code and the HTTP status; take its
            # answer and let a retryable failure keep its own type.
            if getattr(exc, "retryable", False):
                raise

            # What is left is the single most likely production failure in this
            # project, and it arrives as a bare "invalid_grant" that says
            # nothing. While the OAuth consent screen is in Testing mode Google
            # expires refresh tokens after SEVEN DAYS, so an unattended
            # schedule stops weekly. Name the cause and the permanent fix, or
            # the same breakage looks like a different bug every time.
            raise ExpiredCredentialsError(
                f"The stored Google token at {token_path} could not be "
                f"refreshed: {exc}\n"
                "The usual cause is the OAuth consent screen still being in "
                "Testing mode, where Google expires refresh tokens after 7 "
                "days. Fix it permanently: Google Cloud Console -> Google "
                "Auth Platform -> Audience -> Publish app. For a single user "
                "on read-only scopes this needs no verification review.\n"
                "Then re-mint token.json locally and update the "
                "GOOGLE_OAUTH_TOKEN_JSON secret."
            ) from exc
        token_path.write_text(creds.to_json())
        return creds

    # Only now is the interactive flow unavoidable, and only now does the
    # client file actually matter.
    if not creds_path.exists():
        raise MissingCredentialsError(
            f"No usable token at {token_path}, and no OAuth client file at "
            f"{creds_path} to authorise a new one.\n"
            "A cached token.json alone is enough for unattended runs — it "
            "carries its own client_id and client_secret. You only need "
            "credentials.json to create that token in the first place:\n"
            "  1. https://console.cloud.google.com/apis/library -> enable "
            "'Gmail API' and 'Google Calendar API'.\n"
            "  2. https://console.cloud.google.com/apis/credentials/consent "
            "-> OAuth consent screen, add yourself as a test user.\n"
            "  3. https://console.cloud.google.com/apis/credentials -> "
            "Create Credentials -> OAuth client ID -> 'Desktop app'.\n"
            f"  4. Save the JSON as {creds_path} and run a spike locally "
            "once to mint token.json.\n"
            "Run with --fixture instead to exercise the offline path."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json())
    return creds
