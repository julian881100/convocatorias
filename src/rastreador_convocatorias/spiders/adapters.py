"""Adapter functions for sources requiring custom parsing beyond generic CSS selectors.

Each adapter is a standalone function with this signature::

    def parse_{source_name_normalized}(html: str, source: SourceConfig) -> list[RawRecord]:
        ...

Adapters are registered in the ``ADAPTERS`` dict at module bottom.
``BaseConvocatoriasSpider`` checks ``source.name`` against the registry before
falling back to generic CSS-selector extraction.

Sources with ``custom_parser: true`` in their YAML config are routed here.

Currently implemented adapters
-------------------------------

* ``parse_horizon_europe`` — Horizon Europe Funding & Tender Opportunities portal.
  The Angular-based UI loads topic cards via API; this adapter parses the
  static fallback HTML structure that the stealthy fetcher receives after JS
  execution.

* ``parse_erasmus_plus`` — Erasmus+ opportunities listing.  The site uses a
  Drupal-based card grid with teaser text, deadline dates, and action links.
  The generic selectors work for basic extraction, but the adapter provides
  more robust date-text joining and URL resolution.

* ``parse_mit_solve`` — MIT Solve challenges page.  A React-heavy app where
  challenge cards are rendered via client-side JavaScript.  After the dynamic
  fetcher finishes, this adapter extracts the card DOM with prize amounts and
  challenge-specific metadata.

* ``parse_daad`` — DAAD scholarship listing (country portal).  The DAAD site
  uses a custom CMS with scholarship cards that include funding amounts and
  application periods.  This adapter handles the specific card structure and
  enriches records with scholarship-level metadata.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

from scrapling.parser import Selector

from rastreador_convocatorias.models import RawRecord, SourceConfig

logger = logging.getLogger(__name__)

# ── Helpers ────────────────────────────────────────────────────────────


def _extract_text(el: Any, selector: str) -> str:
    """Return the trimmed text of the first element matching *selector*, or ``""``."""
    try:
        matched = el.css(selector)
        if matched and matched[0] is not None:
            txt = matched[0].text
            return str(txt).strip() if txt else ""
    except Exception:
        logger.debug("Failed to extract text with selector '%s'", selector, exc_info=True)
    return ""


def _extract_attr(el: Any, selector: str, attr: str = "href") -> str:
    """Return an attribute value from the first element matching *selector*, or ``""``."""
    try:
        matched = el.css(selector)
        if matched and matched[0] is not None:
            return str(matched[0].attrib.get(attr, "")).strip()
    except Exception:
        logger.debug(
            "Failed to extract attr '%s' with selector '%s'", attr, selector, exc_info=True
        )
    return ""


def _build_record(
    source: SourceConfig,
    *,
    title: str = "",
    url: str = "",
    description: str = "",
    closing_date: str = "",
    opening_date: str = "",
    funding_amount: str = "",
    official_body: str = "",
) -> RawRecord:
    """Build a single ``RawRecord`` from extracted fields.

    The *url* is resolved against ``source.url`` when it looks relative.
    """
    source_url = url
    if source_url and not source_url.startswith(("http://", "https://")):
        source_url = urljoin(source.url, source_url)

    return RawRecord(
        title=title,
        description=description,
        source_url=source_url,
        source_name=source.name,
        country=source.country,
        opening_date=opening_date,
        closing_date=closing_date,
        funding_amount=funding_amount,
        official_body=official_body,
        scraped_at=datetime.now(UTC),
    )


# ── Adapter: Horizon Europe ────────────────────────────────────────────


def parse_horizon_europe(response_text: str, source: SourceConfig) -> list[RawRecord]:
    """Parse Horizon Europe's Funding & Tender Opportunities topic-search page.

    The Horizon Europe portal uses Angular.  After the stealthy fetcher
    finishes JS rendering, the page contains ``<div class="topic-search-result">``
    cards with a title-link, description, deadlines, and programme metadata.

    Expected HTML structure (after JS rendering)::

        <div class="topic-search-result">
            <a class="topic-title" href="/topic/HORIZON-CL1-2026-01">
                AI for Health Equity
            </a>
            <p class="topic-description">
                Research proposals for AI-driven healthcare solutions.
            </p>
            <span class="closing-date">15 September 2026</span>
            <span class="opening-date">1 March 2026</span>
            <span class="programme-name">Horizon Europe – Cluster 1</span>
        </div>
    """
    logger.info("Parsing Horizon Europe topics from '%s' …", source.name)
    page = Selector(response_text)
    containers = page.css("div.topic-search-result")
    records: list[RawRecord] = []

    if not containers:
        logger.warning("No topic-search-result elements found for '%s'", source.name)
        return []

    for container in containers:
        title = _extract_text(container, "a.topic-title")
        url = _extract_attr(container, "a.topic-title")
        desc = _extract_text(container, "p.topic-description")
        closing = _extract_text(container, "span.closing-date")
        opening = _extract_text(container, "span.opening-date")
        body = _extract_text(container, "span.programme-name")

        if not title:
            continue

        records.append(
            _build_record(
                source,
                title=title,
                url=url,
                description=desc,
                closing_date=closing,
                opening_date=opening,
                official_body=body,
            )
        )

    logger.info("  → %d record(s) from Horizon Europe adapter", len(records))
    return records


# ── Adapter: Erasmus+ ──────────────────────────────────────────────────


def parse_erasmus_plus(response_text: str, source: SourceConfig) -> list[RawRecord]:
    """Parse the Erasmus+ opportunities listing page.

    The Erasmus+ site uses a Drupal-based card layout.  Cards
    (``div.opportunity-item``) contain a title link, teaser paragraph,
    deadline, and optional start date.

    Expected HTML structure::

        <div class="opportunity-item">
            <h3>
                <a href="/opportunities/ka1-learning-mobility">
                    KA1 Learning Mobility for Individuals
                </a>
            </h3>
            <p class="teaser">
                Funding for study, traineeship, or staff mobility abroad.
            </p>
            <span class="deadline">15 October 2026</span>
            <span class="start-date">1 March 2026</span>
        </div>
    """
    logger.info("Parsing Erasmus+ opportunities from '%s' …", source.name)
    page = Selector(response_text)
    containers = page.css("div.opportunity-item")
    records: list[RawRecord] = []

    if not containers:
        logger.warning("No opportunity-item elements found for '%s'", source.name)
        return []

    for container in containers:
        title = _extract_text(container, "h3 a")
        url = _extract_attr(container, "h3 a")
        desc = _extract_text(container, "p.teaser")
        closing = _extract_text(container, "span.deadline")
        opening = _extract_text(container, "span.start-date")

        if not title:
            continue

        records.append(
            _build_record(
                source,
                title=title,
                url=url,
                description=desc,
                closing_date=closing,
                opening_date=opening,
            )
        )

    logger.info("  → %d record(s) from Erasmus+ adapter", len(records))
    return records


# ── Adapter: MIT Solve ─────────────────────────────────────────────────


def parse_mit_solve(response_text: str, source: SourceConfig) -> list[RawRecord]:
    """Parse MIT Solve's challenges listing page.

    MIT Solve is a React-heavy application.  After the dynamic fetcher
    completes client-side rendering, challenge cards are present in the DOM
    as ``div.challenge-card`` elements containing title, teaser, deadline,
    and prize amount.

    Expected HTML structure (after JS rendering)::

        <div class="challenge-card">
            <h3 class="card-title">
                <a class="card-link" href="/challenges/ai-health-equity">
                    AI for Health Equity
                </a>
            </h3>
            <p class="challenge-teaser">
                Develop AI-powered solutions for underserved communities.
            </p>
            <span class="deadline">1 August 2026</span>
            <span class="prize">$1,000,000</span>
        </div>
    """
    logger.info("Parsing MIT Solve challenges from '%s' …", source.name)
    page = Selector(response_text)
    containers = page.css("div.challenge-card")
    records: list[RawRecord] = []

    if not containers:
        logger.warning("No challenge-card elements found for '%s'", source.name)
        return []

    for container in containers:
        title = _extract_text(container, "h3.card-title a")
        url = _extract_attr(container, "a.card-link") or _extract_attr(container, "h3.card-title a")
        desc = _extract_text(container, "p.challenge-teaser")
        closing = _extract_text(container, "span.deadline")
        funding = _extract_text(container, "span.prize")

        if not title:
            continue

        records.append(
            _build_record(
                source,
                title=title,
                url=url,
                description=desc,
                closing_date=closing,
                funding_amount=funding,
            )
        )

    logger.info("  → %d record(s) from MIT Solve adapter", len(records))
    return records


# ── Adapter: DAAD ──────────────────────────────────────────────────────


def parse_daad(response_text: str, source: SourceConfig) -> list[RawRecord]:
    """Parse the DAAD scholarship listing page (country portal).

    The DAAD website uses a custom CMS that renders scholarship cards as
    ``div.scholarship-item`` elements.  Each card contains the scholarship
    title (linked to the details page), a description, application deadline,
    and funding-amount information.

    Expected HTML structure::

        <div class="scholarship-item">
            <h3>
                <a href="/becas/daad-scholarship-2026">
                    DAAD Scholarship for Developing Countries 2026
                </a>
            </h3>
            <p class="description">
                Full funding for Master's and PhD programmes in Germany.
            </p>
            <span class="deadline">31 December 2026</span>
            <div class="funding-amount">€1,000 / month</div>
        </div>
    """
    logger.info("Parsing DAAD scholarships from '%s' …", source.name)
    page = Selector(response_text)
    containers = page.css("div.scholarship-item")
    records: list[RawRecord] = []

    if not containers:
        logger.warning("No scholarship-item elements found for '%s'", source.name)
        return []

    for container in containers:
        title = _extract_text(container, "h3 a")
        url = _extract_attr(container, "h3 a")
        desc = _extract_text(container, "p.description")
        closing = _extract_text(container, "span.deadline")
        funding = _extract_text(container, "div.funding-amount")

        if not title:
            continue

        records.append(
            _build_record(
                source,
                title=title,
                url=url,
                description=desc,
                closing_date=closing,
                funding_amount=funding,
            )
        )

    logger.info("  → %d record(s) from DAAD adapter", len(records))
    return records


# ── Adapter Registry ───────────────────────────────────────────────────
#
# Keys are source *names* (as defined in ``sources.yaml``).  Values are
# callables matching the ``(html: str, source: SourceConfig) -> list[RawRecord]``
# signature.
#
# ``BaseConvocatoriasSpider._extract_records`` checks this dict before
# falling back to generic CSS-selector extraction.

ADAPTERS: dict[str, Callable[..., list[RawRecord]]] = {
    "Horizon Europe - Funding & Tender Opportunities": parse_horizon_europe,
    "Erasmus+ - Opportunities": parse_erasmus_plus,
    "MIT Solve - Challenges": parse_mit_solve,
    "DAAD - Becas": parse_daad,
}
