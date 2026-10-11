"""Research a subject against the open web, with citations.

Task 18. A newsletter is a POINTER to something someone thought was worth
your time; Mercury used to repeat what it said. This goes and finds out what
else is true about the same subject and comes back with sources.

**Why Gemini rather than a search API.** It is already this project's LLM —
`GEMINI_API_KEY` is in the environment and `GeminiProvider` is the working
implementation — and its grounding tool does the search AND the reading in
one call, returning the sources it used. A separate search API would mean a
second vendor, a second key, a second free tier to be surprised by, and
fetching pages ourselves, which is the part `net_guard` exists to make safe.

**Why REST rather than the SDK.** `google-generativeai` 0.8.3 knows only
`google_search_retrieval`, and the API now answers that with
`400 google_search_retrieval is not supported. Please use google_search tool
instead.` The installed SDK cannot express the working form. Discovered by
calling it rather than by reading the type list, which advertises
`GoogleSearchRetrieval` as though it worked.

CLAUDE.md rule 5: this takes a SUBJECT — a short noun phrase the enrichment
pass extracted — and never an email body. Nothing here can see one.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.core.config import get_settings
from app.core.providers import Findings, ResearchSource

log = logging.getLogger(__name__)

_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/" "{model}:generateContent"
)

# Grounding returns its sources as redirects through
# vertexaisearch.cloud.google.com. They resolve to the real publisher, and
# resolving them is worth one HEAD each: rule 3 asks for a source the user
# can OPEN, and an opaque Google redirect is openable but not inspectable —
# nobody can tell whether they are about to be sent to Reuters or to a
# content farm.
_REDIRECT_HOST = "vertexaisearch.cloud.google.com"


class GeminiResearchProvider:
    """Grounded search, one subject at a time. A `ResearchProvider`."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        resolve_redirects: bool = True,
    ) -> None:
        settings = get_settings()
        self._key = api_key or settings.gemini_api_key
        self._model = model or settings.llm_model
        self._client = client
        self._resolve = resolve_redirects

    async def research(self, subject: str, *, timeout: float = 30.0) -> Findings:
        """What else is true about this subject.

        Never raises. Every failure — no key, a timeout, a 429, a response
        shaped differently than expected — returns empty findings, because a
        briefing that fails because the research step failed is a worse
        product than a briefing without research (rule 7).
        """
        subject = (subject or "").strip()
        if not subject or not self._key:
            return Findings(subject=subject)

        client = self._client or httpx.AsyncClient(timeout=timeout)
        owns_client = self._client is None
        try:
            payload = await self._ask(client, subject, timeout)
            if payload is None:
                return Findings(subject=subject)
            summary, sources = payload
            if self._resolve and sources:
                sources = await self._resolved(client, sources, timeout)
            findings = Findings(subject=subject, summary=summary, sources=sources)
            if not findings.is_usable:
                # Said out loud. An answer arriving with no sources is the
                # rule-3 case, and silently dropping it would make a
                # misbehaving model look like a quiet subject.
                log.info(
                    "research: %r produced %d chars and %d source(s); discarded",
                    subject[:60],
                    len(summary),
                    len(sources),
                )
                return Findings(subject=subject)
            return findings
        except Exception:
            log.warning("research failed for %r", subject[:60], exc_info=True)
            return Findings(subject=subject)
        finally:
            if owns_client:
                await client.aclose()

    async def _ask(
        self, client: httpx.AsyncClient, subject: str, timeout: float
    ) -> tuple[str, list[ResearchSource]] | None:
        prompt = (
            "Research this subject and report what is independently "
            "established about it.\n\n"
            f"SUBJECT: {subject}\n\n"
            "Three sentences at most. Lead with the concrete: a figure, a "
            "date, a named party. State only what your sources support — if "
            "they disagree, say so rather than picking one. Do not restate "
            "the subject back, and do not pad with context the reader could "
            "have guessed. If you find nothing beyond the obvious, reply "
            "with the single word NOTHING."
        )
        response = await client.post(
            _ENDPOINT.format(model=self._model),
            # Per REQUEST, not on the client, and deliberately not in the
            # query string. `?key=` puts the credential in every proxy and
            # access log that records URLs; a header does not. And the
            # client-level alternative is worse than it looks — `_resolved`
            # drives this same client at arbitrary publisher URLs, so a key
            # set on the client would be posted to whoever grounding
            # happened to cite. Verified against the live API: the
            # `google_search` tool answers 200 to header auth.
            headers={"x-goog-api-key": self._key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "tools": [{"google_search": {}}],
            },
            timeout=timeout,
        )
        if response.status_code != 200:
            log.warning("research: HTTP %s for %r", response.status_code, subject[:60])
            return None

        data = response.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return None
        candidate = candidates[0]

        text = "".join(
            part.get("text", "")
            for part in (candidate.get("content", {}).get("parts") or [])
        ).strip()
        # The model's own way of saying a subject went nowhere, which is more
        # useful than three sentences of hedging.
        if not text or text.upper().startswith("NOTHING"):
            return None

        grounding = candidate.get("groundingMetadata") or {}
        sources: list[ResearchSource] = []
        seen: set[str] = set()
        for chunk in grounding.get("groundingChunks") or []:
            web = chunk.get("web") or {}
            uri, title = web.get("uri"), (web.get("title") or "").strip()
            # Deduplicated by URL: grounding routinely cites one page for
            # several claims, and three rows reading "wikipedia.org" is not
            # three sources.
            if not uri or uri in seen:
                continue
            seen.add(uri)
            sources.append(ResearchSource(title=title or _host_of(uri), url=uri))
        return text, sources

    async def _resolved(
        self,
        client: httpx.AsyncClient,
        sources: list[ResearchSource],
        timeout: float,
    ) -> list[ResearchSource]:
        """Turn grounding redirects into the publisher URLs they point at.

        Concurrently, and failure-tolerant: a redirect that will not resolve
        keeps its original URL, which still works. Publishers commonly answer
        a datacenter IP with 403 — that is fine, because the FINAL URL is
        what is wanted and httpx reports it whatever the status.
        """

        async def one(source: ResearchSource) -> ResearchSource:
            if _REDIRECT_HOST not in source.url:
                return source
            try:
                hop = await client.get(
                    source.url, follow_redirects=True, timeout=min(timeout, 10.0)
                )
                final = str(hop.url)
                if final and _REDIRECT_HOST not in final:
                    return ResearchSource(
                        title=source.title or _host_of(final), url=final
                    )
            except Exception:
                log.debug("could not resolve %s", source.url[:80], exc_info=True)
            return source

        return list(await asyncio.gather(*(one(s) for s in sources)))


def _host_of(url: str) -> str:
    """ "https://en.wikipedia.org/wiki/X" -> "en.wikipedia.org"."""
    try:
        from urllib.parse import urlparse

        return urlparse(url).netloc or url
    except Exception:
        return url


def build_research_provider() -> GeminiResearchProvider | None:
    """The provider, or None when research cannot run.

    None rather than a raising stub: every caller has to handle "no research
    this morning" anyway, because the network can fail, so making the absent
    case identical to the failed case leaves one path instead of two.
    """
    settings = get_settings()
    if not settings.gemini_api_key:
        log.info("research disabled: no GEMINI_API_KEY")
        return None
    return GeminiResearchProvider()
