"""CLI entry point for rastreador-convocatorias.

Orchestrates the full pipeline:

1. Load sources from YAML config via :func:`registry.load_sources`
2. Crawl each source via :class:`spiders.base.BaseConvocatoriasSpider`
3. Validate vigency via :class:`pipeline.validator.VigencyValidator`
4. Deduplicate via :class:`pipeline.deduplicator.Deduplicator`
5. Classify via :class:`pipeline.classifier.Classifier`
6. Compute aggregate stats
7. Export HTML report + JSON

Usage::

    python -m rastreador_convocatorias
    python -m rastreador_convocatorias --sources sources.yaml --output-dir output/
    python -m rastreador_convocatorias --log-level DEBUG --reference-date 2026-07-01

Exit codes
----------
0 — all sources succeeded
1 — partial failure (some sources errored, but data was produced)
2 — critical failure (no data produced, or config error)
130 — interrupted by user (KeyboardInterrupt)
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rastreador_convocatorias import __version__
from rastreador_convocatorias.models import FinalRecord, RawRecord, SourceConfig
from rastreador_convocatorias.registry import load_sources

logger = logging.getLogger(__name__)


# ── Argument parsing ──────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="rastreador-convocatorias",
        description=(
            "Automated web scraper and report generator for open calls "
            "(convocatorias) across 50+ sources."
        ),
    )
    parser.add_argument(
        "--sources",
        default="sources.yaml",
        help="Path to sources YAML file (default: sources.yaml)",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Output directory for reports (default: output/)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    parser.add_argument(
        "--reference-date",
        default=None,
        help=(
            "Reference date for vigency checks (ISO-8601 or DD/MM/YYYY). "
            "Defaults to current UTC time."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"rastreador-convocatorias {__version__}",
    )
    return parser


# ── Stats computation ─────────────────────────────────────────────────────


def _compute_stats(
    final_records: list[FinalRecord],
    errors: list[str],
) -> dict[str, Any]:
    """Compute aggregate KPIs from the final set of classified records.

    Parameters
    ----------
    final_records : list of FinalRecord
        Fully processed and classified records.
    errors : list of str
        Source-level error messages collected during the crawl.

    Returns
    -------
    dict
        Stats dict with keys consumed by the HTML generator, JSON exporter,
        and the spec-format aliases.
    """
    by_category: dict[str, int] = defaultdict(int)
    by_country: dict[str, int] = defaultdict(int)
    by_status: dict[str, int] = defaultdict(int)
    by_funding_type: dict[str, int] = defaultdict(int)
    unique_sources: set[str] = set()

    for rec in final_records:
        # Country
        country = rec.country or "unknown"
        by_country[country] += 1

        # Status
        status = rec.status.value if hasattr(rec.status, "value") else str(rec.status)
        by_status[status] += 1

        # Category (comma-separated)
        if rec.category:
            for cat in rec.category.split(", "):
                key = cat.strip()
                if key:
                    by_category[key] += 1

        # Funding type (list)
        for ft in rec.funding_type:
            key = ft.strip()
            if key:
                by_funding_type[key] += 1

        # Source
        if rec.source_name:
            unique_sources.add(rec.source_name)

    colombia_count = by_country.get("Colombia", 0)
    international_count = sum(
        c for country, c in by_country.items() if country.lower() != "colombia"
    )

    return {
        # Reporter-facing keys (consumed by HTMLReportGenerator & JSONExporter)
        "total": len(final_records),
        "colombia": colombia_count,
        "international": international_count,
        "vigentes": by_status.get("vigente", 0),
        "requires_verification": by_status.get("requires_verification", 0),
        "vencidas": by_status.get("vencida", 0),
        "with_errors": len(errors),
        "unique_sources": len(unique_sources),
        "by_category": dict(by_category),
        "by_country": dict(by_country),
        "by_status": dict(by_status),
        "by_funding_type": dict(by_funding_type),
        # Spec-compatible aliases (task 3.5 stats format)
        "categories": dict(by_category),
        "countries": dict(by_country),
        "statuses": dict(by_status),
        "funding_types": dict(by_funding_type),
    }


# ── Source crawl helpers ──────────────────────────────────────────────────


def _crawl_source(
    source: SourceConfig,
    spider: Any,  # BaseConvocatoriasSpider
) -> list[RawRecord]:
    """Crawl a single source and return extracted records.

    Returns an empty list on failure (exception or no records found).
    Errors are logged but never propagated.
    """
    logger.info("Crawling source: %s (%s)", source.name, source.url)
    try:
        records = spider.crawl(source)
        logger.info("  → %d record(s) from '%s'", len(records), source.name)
        return records
    except Exception:
        logger.exception("Failed to crawl source '%s'", source.name)
        return []


# ── Reference-date resolution ─────────────────────────────────────────────


def _resolve_reference_date(raw: str | None) -> datetime | None:
    """Parse the ``--reference-date`` CLI argument.

    Returns ``None`` when the flag is absent so the validator falls back
    to its own default (env var or current time).
    """
    if not raw:
        return None
    from dateutil.parser import parse as parse_date  # noqa: PLC0415

    try:
        dt = parse_date(raw.strip(), dayfirst=True)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, TypeError):
        logger.warning(
            "Cannot parse --reference-date '%s' — falling back to current time",
            raw,
        )
        return None


# ── Pipeline steps (lazy imports for fast module import) ──────────────────


def _run_validation(
    records: list[RawRecord],
    reference_date: datetime | None,
) -> list:
    """Run vigency validation on raw records."""
    from rastreador_convocatorias.pipeline.validator import (  # noqa: PLC0415
        VigencyValidator,
    )

    validator = VigencyValidator(reference_date=reference_date)
    validated = validator.validate(records)
    logger.info("Validation: %d → %d record(s)", len(records), len(validated))
    return validated


def _run_dedup(records: list) -> list:
    """Run deduplication on validated records."""
    from rastreador_convocatorias.pipeline.deduplicator import (  # noqa: PLC0415
        Deduplicator,
    )

    deduper = Deduplicator()
    deduped = deduper.deduplicate(records)
    logger.info(
        "Dedup: %d → %d (removed %d)",
        len(records),
        len(deduped),
        len(records) - len(deduped),
    )
    return deduped


def _run_classification(
    records: list,
    source_defaults: dict[str, str],
) -> list[FinalRecord]:
    """Run keyword-based classification on deduped records."""
    from rastreador_convocatorias.pipeline.classifier import (  # noqa: PLC0415
        Classifier,
    )

    classifier = Classifier(source_defaults=source_defaults)
    final = classifier.classify(records)
    logger.info("Classification: %d record(s) tagged", len(final))
    return final


def _export_results(
    records: list[FinalRecord],
    stats: dict[str, Any],
    errors: list[str],
    output_dir: Path,
) -> None:
    """Export results to HTML and JSON.

    Falls back to JSON-only if the HTML report fails.
    """
    from rastreador_convocatorias.reporters.html_generator import (  # noqa: PLC0415
        HTMLReportGenerator,
    )
    from rastreador_convocatorias.reporters.json_exporter import (  # noqa: PLC0415
        JSONExporter,
    )

    # HTML report (best-effort — fallback to JSON-only on failure)
    try:
        html_gen = HTMLReportGenerator()
        html_path = output_dir / "report.html"
        html_gen.generate(records, stats, errors, html_path)
        logger.info("HTML report → %s", html_path)
    except Exception:
        logger.exception("HTML report generation failed — falling back to JSON-only")

    # JSON export
    try:
        json_exporter = JSONExporter()
        json_exporter.export(records, stats, errors, output_dir)
        logger.info("JSON export → %s/data/", output_dir)
    except Exception:
        logger.exception("JSON export failed")


# ── Main entry point ──────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """Run the full convocatorias pipeline.

    Parameters
    ----------
    argv : list of str or None
        Command-line arguments.  ``None`` (default) reads from ``sys.argv[1:]``.

    Returns
    -------
    int
        Exit code: 0 = success, 1 = partial failure, 2 = critical error,
        130 = interrupted by user.
    """
    # ── Parse arguments and bootstrap logging ─────────────────────────
    args = _build_parser().parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("rastreador-convocatorias v%s starting", __version__)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Load sources ──────────────────────────────────────────────────
    logger.info("Loading sources from '%s' …", args.sources)
    try:
        sources = load_sources(args.sources)
    except FileNotFoundError:
        logger.exception("Sources file not found")
        return 2
    except ValueError:
        logger.exception("Source configuration error")
        return 2

    if not sources:
        logger.error("No valid sources loaded — nothing to crawl")
        return 2

    logger.info("Loaded %d source(s)", len(sources))

    # ── Reference date (optional override) ────────────────────────────
    reference_date = _resolve_reference_date(args.reference_date)
    if reference_date:
        logger.info("Reference date overridden: %s", reference_date)

    # ── Crawl phase ───────────────────────────────────────────────────
    logger.info("── Crawl phase ──────────────────────────────────")
    from rastreador_convocatorias.spiders.base import (  # noqa: PLC0415
        BaseConvocatoriasSpider,
    )

    spider = BaseConvocatoriasSpider()
    all_raw: list[RawRecord] = []
    errors: list[str] = []
    successful: int = 0

    for source in sources:
        records = _crawl_source(source, spider)
        if records:
            all_raw.extend(records)
            successful += 1
        else:
            errors.append(f"{source.name}: no records returned")

    logger.info(
        "Crawl finished: %d/%d sources successful, %d raw records",
        successful,
        len(sources),
        len(all_raw),
    )

    if not all_raw:
        logger.error("No records collected from any source — aborting")
        return 2

    # ── Validation phase ──────────────────────────────────────────────
    logger.info("── Validation phase ─────────────────────────────")
    validated = _run_validation(all_raw, reference_date)

    # ── Dedup phase ───────────────────────────────────────────────────
    logger.info("── Deduplication phase ──────────────────────────")
    deduped = _run_dedup(validated)

    # ── Classification phase ───────────────────────────────────────────
    logger.info("── Classification phase ─────────────────────────")
    source_defaults = {s.name: s.category_default for s in sources if s.category_default}
    final = _run_classification(deduped, source_defaults)

    # ── Stats ───────────────────────────────────────────────────────────
    stats = _compute_stats(final, errors)
    logger.info(
        "Stats: %d total, %d Colombia, %d international, %d vigentes",
        stats["total"],
        stats["colombia"],
        stats["international"],
        stats["vigentes"],
    )

    # ── Export ───────────────────────────────────────────────────────────
    logger.info("── Export phase ─────────────────────────────────")
    _export_results(final, stats, errors, output_dir)

    # ── Exit ─────────────────────────────────────────────────────────────
    if not errors:
        logger.info("Pipeline completed successfully — all sources OK")
        return 0

    if successful > 0:
        logger.warning(
            "Pipeline completed with %d source error(s) — partial success",
            len(errors),
        )
        return 1

    logger.error("All sources failed — critical failure")
    return 2


# ── CLI entry (handles KeyboardInterrupt) ─────────────────────────────────


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger = logging.getLogger(__name__)
        logger.warning("Interrupted by user — partial data may have been saved")
        sys.exit(130)
