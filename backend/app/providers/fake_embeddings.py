"""
Deterministic, offline EmbeddingProvider. No network calls.

The lesson from FakeLLMProvider (see app/providers/fake.py): a fake that does
not behave like the real thing certifies nothing. A fake embedding provider
that returns random vectors would make every clustering test vacuous — two
items about the same event would land at a random cosine similarity, so a
clustering test built on it could not tell a working threshold from a broken
one.

Instead this hashes vocabulary into a fixed-dimension vector (the classic
"feature hashing" trick): each token deterministically maps to one dimension
and a sign, so texts sharing words land close together in cosine space and
texts sharing no vocabulary land near-orthogonal. It is not a real semantic
embedding — it has no notion of synonyms — but it is driven BY its input, so
clustering tests that exercise "these share wording, these do not" are
actually testing something.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter

DIMENSIONS = 256

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Small stopword list so near-universal words (which would otherwise dominate
# every vector and drag unrelated texts toward false similarity) don't
# swamp the content-bearing tokens that should actually drive clustering.
_STOPWORDS = frozenset(
    """
    a an the and or but if then else for to of in on at by with from as is
    are was were be been being this that these those it its it's you your
    we our they their he she his her i me my not no do does did has have
    had will would can could should may might about into over under after
    before between out up down off again further than so just also
    """.split()
)


_RAW_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

# Plain bag-of-words hashing alone is too noisy to be useful: two articles
# about the same event typically share only a handful of their many unique
# words, so cosine similarity stays low even when the texts obviously cover
# the same story. Real embedding models don't have this problem because
# every dimension carries a semantic contribution from the whole sentence,
# not just literal token identity.
#
# The cheapest deterministic proxy for "these texts are about the same
# thing" is named-entity overlap: two articles about the same acquisition
# both say "TechCorp" and "DataFlow"; two articles about different rocket
# launches do NOT share company/product names even if they share plenty of
# generic verbs ("launches", "test flight", "reaches orbit"). So entity-like
# tokens are boosted well above ordinary words. A token counts as
# entity-like if, in its ORIGINAL casing, it carries a signal a naive
# lowercase bag-of-words would throw away: an internal capital letter
# (brand names like "TechCorp", "DataFlow", "SpaceX"), an all-caps acronym,
# or a digit (prices, versions, dates).
ENTITY_WEIGHT = 5.0


def _is_entity_like(raw_token: str) -> bool:
    if any(c.isdigit() for c in raw_token):
        return True
    if len(raw_token) > 1 and raw_token.isupper():
        return True
    # Internal capital after the first character: camelCase / mixed-case
    # brand names ("DataFlow", "SpaceX"), not just an ordinary
    # capitalised word at the start of a sentence or headline.
    return len(raw_token) > 1 and any(c.isupper() for c in raw_token[1:])


def _hash_token(token: str) -> tuple[int, float]:
    """Deterministic (dimension index, sign) for a token.

    Two independent hash digests: one picks the dimension, the other picks
    the sign. Using the same digest for both would correlate index and sign,
    which biases the resulting vectors.
    """
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % DIMENSIONS
    sign_digest = hashlib.sha256(b"sign:" + token.encode("utf-8")).digest()
    sign = 1.0 if sign_digest[0] % 2 == 0 else -1.0
    return index, sign


def embed_text(text: str) -> list[float]:
    """Pure, deterministic text -> fixed-dimension vector, L2-normalised."""
    vector = [0.0] * DIMENSIONS
    counts: Counter[str] = Counter()
    is_entity: dict[str, bool] = {}

    for raw in _RAW_TOKEN_RE.findall(text):
        lowered = raw.lower()
        if lowered in _STOPWORDS or len(lowered) <= 1:
            continue
        counts[lowered] += 1
        if _is_entity_like(raw):
            is_entity[lowered] = True

    if not counts:
        # Empty/degenerate input: return the zero vector's normalised form,
        # i.e. a fixed all-zero vector. Cosine similarity against it is 0
        # for everything, which correctly reads as "no signal" rather than
        # raising or crashing clustering.
        return vector

    for token, count in counts.items():
        index, sign = _hash_token(token)
        # log-scaled term frequency so one repeated word can't dominate.
        weight = 1.0 + (count - 1) * 0.25
        if is_entity.get(token):
            weight *= ENTITY_WEIGHT
        vector[index] += sign * weight

    norm = sum(v * v for v in vector) ** 0.5
    if norm == 0:
        return vector
    return [v / norm for v in vector]


class FakeEmbeddingProvider:
    """Deterministic, offline EmbeddingProvider for tests and development.

    Satisfies `app.core.providers.EmbeddingProvider` structurally. Embeds
    each text independently and returns results in the same order as the
    input — same order-preservation contract as the live provider.
    """

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [embed_text(t) for t in texts]
