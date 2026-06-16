"""Base spider for crawling convocatorias sources.

``BaseConvocatoriasSpider`` manages three Scrapling session types (http,
dynamic, stealthy) and exposes a synchronous ``crawl(source)`` method that
orchestrates fetching, pagination, and record extraction for a single source.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

import anyio
from scrapling.fetchers import (
    AsyncDynamicSession,
    AsyncStealthySession,
    FetcherSession,
)
from scrapling.spiders import SessionManager, Spider
from scrapling.spiders.request import Request
from scrapling.spiders.robotstxt import RobotsTxtManager

from rastreador_convocatorias.models import FetcherType, RawRecord, SourceConfig
from rastreador_convocatorias.spiders.adapters import ADAPTERS
from rastreador_convocatorias.spiders.pagination import (
    MAX_PAGES_DEFAULT,
    build_page_urls,
    build_scroll_page_action,
    find_next_link,
)

logger = logging.getLogger(__name__)

# Maps FetcherType enum → session ID registered in configure_sessions()
SESSION_MAP: dict[FetcherType, str] = {
    FetcherType.http: "http",
    FetcherType.dynamic: "dynamic",
    FetcherType.stealthy: "stealth",
}

# Session fallback order when the configured session fails
_FALLBACK_CHAIN: list[FetcherType] = [
    FetcherType.dynamic,
    FetcherType.stealthy,
]


class BaseConvocatoriasSpider(Spider):
    """Spider base that crawls a single convocatorias source per call.

    Three sessions are configured at construction time:
    * ``http`` — lightweight ``FetcherSession`` (curl-cffi).
    * ``dynamic`` — ``AsyncDynamicSession`` (Playwright, lazy).
    * ``stealth`` — ``AsyncStealthySession`` (Playwright + anti-bot, lazy).

    Class-level concurrency settings match the spec: 5 global, 2 per domain,
    1-second delay.
    """

    name = "convocatorias"

    concurrent_requests = 5
    concurrent_requests_per_domain = 2
    download_delay = 1.0
    robots_txt_obey = False  # Per-source; handled in crawl()

    def configure_sessions(self, manager: SessionManager) -> None:
        """Register the three session types.

        Parameters
        ----------
        manager : SessionManager
            The spider's session manager (created in ``Spider.__init__``).
        """
        manager.add("http", FetcherSession(impersonate="chrome"))

        manager.add(
            "dynamic",
            AsyncDynamicSession(headless=True, network_idle=True),
            lazy=True,
        )
        manager.add(
            "stealth",
            AsyncStealthySession(headless=True, solve_cloudflare=True),
            lazy=True,
        )

    # -- Async generator required by the abstract Spider base ---------------

    async def parse(self, response: Any) -> Any:
        """No-op parse — we don't use the standard engine-driven flow."""
        return
        yield  # pragma: no cover

    # -- Public API --------------------------------------------------------

    def crawl(self, source: SourceConfig) -> list[RawRecord]:
        """Crawl a single source and return extracted records.

        This is the synchronous entry point.  It creates a temporary asyncio
        event loop, runs the crawl, and returns the results.

        Parameters
        ----------
        source : SourceConfig
            The source configuration to crawl.

        Returns
        -------
        list of RawRecord
            Extracted records, or an empty list on failure.
        """
        return anyio.run(self._async_crawl, source)

    # -- Internal: async crawl orchestration --------------------------------

    async def _async_crawl(self, source: SourceConfig) -> list[RawRecord]:
        """Async implementation of the crawl."""
        # Build the session fallback chain
        session_order: list[FetcherType] = [source.fetcher]
        for fallback in _FALLBACK_CHAIN:
            if fallback not in session_order:
                session_order.append(fallback)

        await self._session_manager.start()

        try:
            for fetcher_type in session_order:
                sid = SESSION_MAP[fetcher_type]
                records = await self._crawl_with_session(source, sid)

                if records:
                    # Successful extraction — no need for fallbacks
                    return records

                logger.info(
                    "Session '%s' returned no records for '%s', trying next fallback",
                    sid,
                    source.name,
                )

            logger.warning("All sessions exhausted for source '%s'", source.name)
            return []
        except Exception:
            logger.exception("Unexpected error crawling source '%s'", source.name)
            return []
        finally:
            await self._session_manager.close()

    async def _crawl_with_session(
        self,
        source: SourceConfig,
        sid: str,
    ) -> list[RawRecord]:
        """Run the crawl using a specific session."""
        # -- robots.txt check --
        if source.robots_txt_obey and not await self._check_robots_txt(
            source.url, sid
        ):
            logger.warning(
                "robots.txt disallows '%s' for source '%s' — skipping",
                source.url,
                source.name,
            )
            return []

        # -- Route to pagination strategy --
        pagination = source.pagination or {}
        mode = pagination.get("type", "none")

        try:
            if mode == "query_param":
                return await self._crawl_query_param(source, sid)
            if mode == "next_link":
                return await self._crawl_next_link(source, sid)
            if mode == "infinite_scroll":
                return await self._crawl_infinite_scroll(source, sid)
            return await self._crawl_single_page(source, sid)
        except Exception:
            logger.exception(
                "Crawl with session '%s' failed for source '%s'",
                sid,
                source.name,
            )
            return []

    # -- robots.txt --------------------------------------------------------

    async def _check_robots_txt(self, url: str, sid: str) -> bool:
        """Return ``True`` if *url* is allowed by the domain's ``robots.txt``."""
        robots = RobotsTxtManager(self._robots_fetch_fn(sid))
        try:
            return await robots.can_fetch(url, sid)
        except Exception:
            logger.debug("robots.txt check failed for %s — allowing crawl", url)
            return True  # Allow on failure

    def _robots_fetch_fn(self, sid: str):
        """Return a fetch callable bound to a session ID for the robots manager."""

        async def _fetch(url: str, _sid: str) -> Any:
            req = Request(url, sid=sid)
            return await self._session_manager.fetch(req)

        return _fetch

    # -- Pagination strategies ---------------------------------------------

    async def _crawl_single_page(
        self,
        source: SourceConfig,
        sid: str,
    ) -> list[RawRecord]:
        """Fetch a single page and extract records."""
        response = await self._fetch(source.url, sid)
        if response is None:
            return []
        return self._extract_records(response, source)

    async def _crawl_query_param(
        self,
        source: SourceConfig,
        sid: str,
        *,
        consecutive_empty_limit: int = 3,
    ) -> list[RawRecord]:
        """Paginate by incrementing a query parameter.

        Stops early when *consecutive_empty_limit* consecutive pages
        return zero valid records, preventing runaway pagination on
        sources with misconfigured selectors.
        """
        urls = build_page_urls(source)
        container_selector = source.selectors.get("container", "")
        records: list[RawRecord] = []
        consecutive_empty = 0

        for url in urls:
            response = await self._fetch(url, sid)
            if response is None:
                break

            # Stop if the container selector returns nothing
            if container_selector:
                container = response.css(container_selector)
                if not container:
                    break

            page_records = self._extract_records(response, source)
            records.extend(page_records)

            # Early stop when N consecutive pages return nothing
            if not page_records:
                consecutive_empty += 1
                if consecutive_empty >= consecutive_empty_limit:
                    logger.info(
                        "Stopping pagination for '%s' after %d consecutive empty pages",
                        source.name,
                        consecutive_empty_limit,
                    )
                    break
            else:
                consecutive_empty = 0

        return records

    async def _crawl_next_link(
        self,
        source: SourceConfig,
        sid: str,
    ) -> list[RawRecord]:
        """Paginate by following "next" links."""
        max_pages = min(
            (source.pagination or {}).get("max_pages", MAX_PAGES_DEFAULT),
            MAX_PAGES_DEFAULT,
        )
        records: list[RawRecord] = []
        url: str | None = source.url

        for _ in range(max_pages):
            if url is None:
                break
            response = await self._fetch(url, sid)
            if response is None:
                break

            records.extend(self._extract_records(response, source))
            url = find_next_link(response, source)

        return records

    async def _crawl_infinite_scroll(
        self,
        source: SourceConfig,
        sid: str,
    ) -> list[RawRecord]:
        """Scroll the page to load all content, then extract."""
        page_action = build_scroll_page_action(source)

        try:
            session = self._session_manager.get(sid)
            response = await session.fetch(
                url=source.url,
                page_action=page_action,
            )
        except Exception:
            logger.exception(
                "Infinite-scroll fetch failed for '%s'",
                source.name,
            )
            return []

        if response is None:
            return []
        return self._extract_records(response, source)

    # -- Low-level fetch ---------------------------------------------------

    async def _fetch(self, url: str, sid: str) -> Any:
        """Fetch a URL and return the response, or ``None`` on error."""
        try:
            req = Request(url, sid=sid)
            return await self._session_manager.fetch(req)
        except Exception as e:
            logger.error("Fetch failed for %s (session=%s): %s", url, sid, e)
            return None

    # -- Record extraction -------------------------------------------------

    def _extract_records(
        self,
        response: Any,
        source: SourceConfig,
    ) -> list[RawRecord]:
        """Extract records from a response.

        When ``source.custom_parser`` is ``True`` and the source name is
        registered in ``ADAPTERS``, this method delegates to the custom
        adapter function.  Otherwise it falls back to the generic CSS-selector
        extraction using the source's ``selectors.container`` and
        ``selectors.fields``.

        Parameters
        ----------
        response : Response
            The page response (a ``Selector`` subclass via Scrapling).
        source : SourceConfig
            Source configuration.

        Returns
        -------
        list of RawRecord
        """
        # --- Adapter dispatch ---
        if source.custom_parser:
            adapter = ADAPTERS.get(source.name)
            if adapter is not None:
                logger.info(
                    "Using custom adapter '%s' for source '%s'",
                    adapter.__name__,
                    source.name,
                )
                return adapter(str(response.body), source)  # type: ignore[union-attr]
            logger.warning(
                "custom_parser is True but no adapter registered for '%s' — "
                "falling back to generic selectors",
                source.name,
            )

        # --- Generic CSS-selector extraction ---
        selectors = source.selectors
        container_selector = selectors.get("container", "")
        fields: dict[str, str] = selectors.get("fields", {})

        if container_selector:
            containers = response.css(container_selector)
        else:
            containers = [response]  # type: ignore[assignment]

        records: list[RawRecord] = []
        for container in containers:
            record = self._extract_single(container, fields, source)
            # Only keep records with at least a title or one meaningful field
            if record.title or record.description or record.source_url or record.closing_date:
                records.append(record)

        return records

    def _extract_single(
        self,
        container: Any,
        fields: dict[str, str],
        source: SourceConfig,
    ) -> RawRecord:
        """Extract a single ``RawRecord`` from a container element."""
        record = RawRecord(
            source_name=source.name,
            country=source.country,
            scraped_at=datetime.now(UTC),
        )

        for field_name, css_selector in fields.items():
            try:
                elements = container.css(css_selector)
                if not elements:
                    continue

                raw = str(elements[0].text).strip() if elements[0].text else ""
                # Map to the correct RawRecord attribute
                self._set_record_field(record, field_name, raw)
            except Exception:
                logger.debug(
                    "Failed to extract '%s' with '%s' for source '%s'",
                    field_name,
                    css_selector,
                    source.name,
                    exc_info=True,
                )

        # Resolve relative URLs
        if record.source_url and not record.source_url.startswith(("http://", "https://")):
            record.source_url = urljoin(source.url, record.source_url)

        return record

    @staticmethod
    def _set_record_field(record: RawRecord, field_name: str, value: str) -> None:
        """Set a field on a RawRecord, mapping common selector keys."""
        # Allow field names from sources.yaml to map to model fields
        key_map = {
            "url": "source_url",
        }
        attr = key_map.get(field_name, field_name)
        if hasattr(record, attr):
            setattr(record, attr, value)
