"""Every `UserSettings` field must survive the Worker's whitelist.

`worker/src/index.js` rebuilds the stored settings document from
`SETTINGS_FIELDS`, so a field missing from that map is accepted with a
200 and then silently dropped. Nothing fails, nothing logs, and the
setting simply never applies.

This is not hypothetical and it is not new:

  - `music_set` was stripped exactly this way, so the setting never once
    applied — one of the four "built and never called" bugs that
    `app/pipeline/census.py` exists to catch.
  - review of #58 caught `interests` and `followed_entities` going the
    same way before they shipped, with the extra sting that
    `_topic_centroid` would have logged "no interest topics configured"
    — indistinguishable from the user having set none.

So this is the general fix rather than the second instance of the
specific one. It is a backend test reading a JavaScript file because
that is where the coupling is, and a coupling nothing asserts on is one
that drifts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.schemas import UserSettings

WORKER = Path(__file__).resolve().parents[2] / "worker" / "src" / "index.js"

# Fields the Worker deliberately does not accept from a client. Keep this
# list SHORT and say why for each — it is the escape hatch that would let
# the next `music_set` through.
NOT_CLIENT_WRITABLE: dict[str, str] = {}


def _worker_settings_fields() -> dict[str, str]:
    source = WORKER.read_text()
    block = re.search(r"const SETTINGS_FIELDS = \{(.*?)\n\};", source, re.S)
    assert block, "SETTINGS_FIELDS is no longer parseable in the Worker"
    body = re.sub(r"//[^\n]*", "", block.group(1))
    return dict(re.findall(r'(\w+)\s*:\s*"([^"]+)"', body))


def test_the_worker_accepts_every_user_setting():
    worker_fields = _worker_settings_fields()
    missing = sorted(
        name
        for name in UserSettings.model_fields
        if name not in worker_fields and name not in NOT_CLIENT_WRITABLE
    )
    assert not missing, (
        f"{missing} exist on UserSettings and not in the Worker's "
        "SETTINGS_FIELDS whitelist, so a client can save them, get a 200, "
        "and have them silently dropped — the way `music_set` never once "
        "applied. Add them to worker/src/index.js in this same change."
    )


def test_the_worker_does_not_accept_fields_that_do_not_exist():
    """The other direction. A whitelist entry with no field behind it is
    a setting the phone can write and the pipeline will never read."""
    worker_fields = _worker_settings_fields()
    # `updated_at` is stamped by the Worker itself rather than modelled.
    extra = sorted(set(worker_fields) - set(UserSettings.model_fields) - {"updated_at"})
    assert not extra, (
        f"{extra} are accepted by the Worker and do not exist on "
        "UserSettings; nothing will ever read them"
    )


def test_the_declared_shapes_match_the_python_types():
    """A `string[]` field modelled as a scalar (or the reverse) round-trips
    as the wrong thing rather than being rejected."""
    worker_fields = _worker_settings_fields()
    wrong = []
    for name, field in UserSettings.model_fields.items():
        shape = worker_fields.get(name)
        if shape is None:
            continue
        is_list = "list[str]" in str(field.annotation)
        if is_list != (shape == "string[]"):
            wrong.append(f"{name}: python={field.annotation} worker={shape}")
    assert not wrong, wrong


def test_a_settings_document_round_trips_through_the_whitelist():
    """End to end on the data, not just the names: build settings with
    every field populated, filter them the way the Worker does, and parse
    the result back."""
    worker_fields = _worker_settings_fields()
    original = UserSettings(
        feeds=["https://example.test/rss"],
        muted_senders=["Substack"],
        interests=["war in Ukraine", "semiconductors"],
        followed_entities=["Reuters"],
        disabled_segments=["weather"],
    )
    sent = json.loads(original.model_dump_json())
    kept = {k: v for k, v in sent.items() if k in worker_fields}
    restored = UserSettings.model_validate(kept)

    assert restored.interests == original.interests
    assert restored.followed_entities == original.followed_entities
    assert restored.muted_senders == original.muted_senders
