"""
Tests for app/providers/fake_embeddings.py and app/providers/gemini_embeddings.py.

The core property under test for the fake: it must be driven BY its input,
not random and not canned. Texts sharing vocabulary should land close in
cosine space; texts sharing no vocabulary should not. See
app/providers/fake_embeddings.py's module docstring for why — a fake that
does not behave like the real thing certifies nothing (the FakeLLMProvider
lesson from Phase 1).
"""

from __future__ import annotations

import math

import pytest

from app.providers.fake_embeddings import DIMENSIONS, FakeEmbeddingProvider, embed_text


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ──────────────────────────────────────────────────────────────
# FakeEmbeddingProvider / embed_text
# ──────────────────────────────────────────────────────────────


def test_embed_text_is_deterministic():
    text = "TechCorp acquires DataFlow for $2.3 billion"
    assert embed_text(text) == embed_text(text)


def test_embed_text_has_fixed_dimension():
    assert len(embed_text("short")) == DIMENSIONS
    assert len(embed_text("a much longer piece of text " * 20)) == DIMENSIONS


def test_embed_text_is_unit_normalised():
    vector = embed_text("TechCorp acquires DataFlow for a large sum of money")
    norm = math.sqrt(sum(v * v for v in vector))
    assert norm == pytest.approx(1.0, abs=1e-6)


def test_shared_vocabulary_lands_close():
    a = embed_text(
        "TechCorp to acquire DataFlow for $2.3 billion in largest AI deal"
    )
    b = embed_text(
        "TechCorp buys DataFlow: notes on the $2.3bn acquisition and distribution"
    )
    unrelated = embed_text(
        "The 12 best new restaurants to open in Lisbon this summer"
    )
    sim_related = cosine(a, b)
    sim_unrelated = cosine(a, unrelated)
    assert sim_related > sim_unrelated
    # Not just "greater than" — related texts should be clearly close and
    # unrelated texts should be clearly not, or the fake is too noisy to be
    # useful for clustering tests.
    assert sim_related > 0.3
    assert sim_unrelated < 0.15


def test_disjoint_vocabulary_is_near_orthogonal():
    a = embed_text("quarterly earnings report revenue growth margin")
    b = embed_text("Olympic swimming schedule finals controversy coaches")
    assert cosine(a, b) < 0.1


async def test_fake_provider_embed_preserves_order():
    provider = FakeEmbeddingProvider()
    texts = ["first item text", "second item text", "third item text"]
    result = await provider.embed(texts)
    assert len(result) == len(texts)
    assert result[0] == embed_text(texts[0])
    assert result[1] == embed_text(texts[1])
    assert result[2] == embed_text(texts[2])


async def test_fake_provider_embed_empty_list():
    provider = FakeEmbeddingProvider()
    assert await provider.embed([]) == []


# ──────────────────────────────────────────────────────────────
# GeminiEmbeddingProvider — offline-safe behaviour only. conftest.py blanks
# GEMINI_API_KEY for every test, so this only exercises the "unset key"
# guard, never a live network call.
# ──────────────────────────────────────────────────────────────


def test_gemini_embedding_provider_raises_clear_error_without_key():
    from app.providers.gemini_embeddings import GeminiEmbeddingProvider

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        GeminiEmbeddingProvider(api_key="")
