"""Pagination utilities for crawling convocatorias sources.

Supports three pagination strategies:

* ``query_param`` — increment a URL query parameter (e.g. ``?page=0``),
  stop when the container selector returns empty.
* ``next_link`` — find a "next page" anchor in the response and follow it.
* ``infinite_scroll`` — scroll the page to trigger lazy-loading (for
  ``AsyncDynamicSession`` / ``AsyncStealthySession``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from rastreador_convocatorias.models import SourceConfig

if TYPE_CHECKING:
    from scrapling.engines.toolbelt.custom import Response

logger = logging.getLogger(__name__)

MAX_PAGES_DEFAULT = 10


# ── Query-param pagination ──────────────────────────────────────────────


def build_page_urls(source: SourceConfig) -> list[str]:
    """Build the full list of page URLs for ``query_param`` pagination.

    Parameters
    ----------
    source : SourceConfig
        Source whose ``pagination`` block defines the parameter name,
        start value, and (optionally) max pages.

    Returns
    -------
    list of str
        One URL per page, starting at *start* and incrementing the
        query parameter.  Always contains at least the original URL.
    """
    pagination = source.pagination or {}
    mode = pagination.get("type", "none")

    if mode != "query_param":
        return [source.url]

    param = pagination.get("param", "page")
    start = pagination.get("start", 0)
    max_pages = min(pagination.get("max_pages", MAX_PAGES_DEFAULT), MAX_PAGES_DEFAULT)

    parsed = urlparse(source.url)
    query = parse_qs(parsed.query, keep_blank_values=True)

    urls: list[str] = []
    for page_num in range(start, start + max_pages):
        query[param] = [str(page_num)]
        urls.append(urlunparse(parsed._replace(query=urlencode(query, doseq=True))))

    return urls


# ── Next-link pagination ────────────────────────────────────────────────


def find_next_link(response: Response, source: SourceConfig) -> str | None:
    """Locate a "next page" link in a response for ``next_link`` pagination.

    Uses the ``next_selector`` from the source pagination config, or a
    sensible default selector (``a[rel='next']``).

    Parameters
    ----------
    response : Response
        The page response to search in.
    source : SourceConfig
        Source config with optional ``pagination.next_selector``.

    Returns
    -------
    str or None
        Absolute URL of the next page, or ``None`` if no link found.
    """
    pagination = source.pagination or {}
    selector = pagination.get("next_selector", "a[rel='next']")
    links = response.css(selector)
    for link in links:
        href = link.attrib.get("href")
        if href:
            return urljoin(source.url, href)
    return None


# ── Infinite-scroll pagination ──────────────────────────────────────────


def build_scroll_page_action(
    source: SourceConfig,
    *,
    max_pages: int = MAX_PAGES_DEFAULT,
) -> Callable:
    """Build a ``page_action`` callable for infinite-scroll pagination.

    The returned coroutine function scrolls the page to the bottom
    repeatedly until no new content loads, or up to *max_pages*
    scrolls.

    Parameters
    ----------
    source : SourceConfig
        Source config with optional ``pagination.max_pages``.
    max_pages : int
        Hard limit on scroll iterations (default 50).

    Returns
    -------
    callable
        An async ``(page) -> None`` function suitable as the
        ``page_action`` argument for dynamic/stealthy sessions.
    """
    limit = min(
        (source.pagination or {}).get("max_pages", max_pages),
        max_pages,
    )

    async def _scroll(page) -> None:
        for i in range(limit):
            last_height = await page.evaluate("document.body.scrollHeight")
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(3000)
            new_height = await page.evaluate("document.body.scrollHeight")
            if new_height == last_height:
                logger.debug("Infinite scroll reached bottom after %d scroll(s)", i + 1)
                break

    return _scroll
