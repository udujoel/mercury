"""
Telling an expired Google token apart from a bad five minutes at Google.

`ExpiredCredentialsError` is not a message, it is a claim: the consent screen
is in Testing mode, refresh tokens expire after seven days, and the permanent
fix is to publish the app and re-mint token.json. `app.cli` prints exactly that
instruction whenever it sees the type.

So the type has to mean it. `creds.refresh()` raises `RefreshError` for an
`invalid_grant` AND for a 503, a 500 and a rate limit, and converting all of
them said "your token has permanently expired, here is a ten-minute manual
procedure" about an outage that would have cleared itself by the next
checkpoint — while the run lost the user's inbox and calendar for the morning.

google-auth already distinguishes the two: it sets `retryable` on the
exception from the OAuth error code and the HTTP status.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from google.auth.exceptions import RefreshError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from _google_auth import (  # noqa: E402
    ExpiredCredentialsError,
    get_credentials,
)


class _Creds:
    """A stored token that is present, expired, and refreshable in principle."""

    valid = False
    expired = True
    refresh_token = "refresh-me"

    def __init__(self, error: Exception | None) -> None:
        self._error = error
        self.refreshed = False

    def refresh(self, _request) -> None:
        if self._error is not None:
            raise self._error
        self.refreshed = True
        self.valid = True

    def to_json(self) -> str:
        return "{}"


@pytest.fixture
def token_at(monkeypatch, tmp_path):
    """A token.json on disk, and no OAuth client file — the Actions shape."""
    token = tmp_path / "token.json"
    token.write_text("{}")
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(token))
    monkeypatch.setenv("GOOGLE_CREDENTIALS_FILE", str(tmp_path / "absent.json"))
    return token


def _with_refresh_error(monkeypatch, error: Exception) -> _Creds:
    creds = _Creds(error)
    monkeypatch.setattr(
        "_google_auth.Credentials.from_authorized_user_file",
        classmethod(lambda cls, path, scopes: creds),
        raising=True,
    )
    return creds


def test_an_invalid_grant_is_reported_as_an_expired_token(monkeypatch, token_at):
    """The one failure with a known cause and a permanent fix."""
    _with_refresh_error(
        monkeypatch, RefreshError("invalid_grant: Token has been expired")
    )

    with pytest.raises(ExpiredCredentialsError) as caught:
        get_credentials()

    assert "Publish app" in str(caught.value)


@pytest.mark.parametrize(
    "error",
    [
        RefreshError("internal_failure", retryable=True),
        RefreshError("rate limited", retryable=True),
    ],
)
def test_a_retryable_refresh_failure_is_not_an_expired_token(
    monkeypatch, token_at, error
):
    """A 5xx or a rate limit clears itself by the next checkpoint. Labelling it
    "your token has permanently expired" sends the user through a ten-minute
    manual procedure for an outage that has already ended."""
    _with_refresh_error(monkeypatch, error)

    with pytest.raises(RefreshError) as caught:
        get_credentials()

    assert not isinstance(caught.value, ExpiredCredentialsError)
    assert caught.value.retryable is True


def test_a_successful_refresh_writes_the_token_back(monkeypatch, token_at):
    creds = _with_refresh_error(monkeypatch, None)

    assert get_credentials() is creds
    assert creds.refreshed
    assert token_at.read_text() == "{}"


# ── and the type has to survive ingestion ────────────────────


@pytest.mark.parametrize(
    ("module", "call"),
    [
        ("app.ingestion.gmail", lambda m: m._fetch_live_messages(1)),
        (
            "app.ingestion.calendar",
            lambda m: m._fetch_live(
                __import__("datetime").datetime(2026, 8, 27),
                __import__("datetime").datetime(2026, 8, 30),
            ),
        ),
    ],
)
def test_ingestion_does_not_flatten_the_type(monkeypatch, token_at, module, call):
    """Both live paths call `get_credentials` and must let its exceptions
    through untouched. Re-wrapping in a plain RuntimeError is the regression:
    `app.cli` keys its loudest, most specific error off the subtype and cannot
    do that if the type is discarded one frame after it is raised."""
    import importlib

    _with_refresh_error(
        monkeypatch, RefreshError("invalid_grant: Token has been expired")
    )

    with pytest.raises(ExpiredCredentialsError):
        call(importlib.import_module(module))
