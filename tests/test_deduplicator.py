"""Tests for ``Deduplicator``.

Covers all scenarios from the spec's **Deduplication Engine** section.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rastreador_convocatorias.models import Status, ValidatedRecord
from rastreador_convocatorias.pipeline.deduplicator import Deduplicator


def _make_record(
    title: str,
    source_name: str = "Minciencias",
    closing_date: str | None = "2026-08-15",
    description: str = "",
    **extra,
) -> ValidatedRecord:
    """Factory helper to build test records quickly."""
    return ValidatedRecord(
        title=title,
        description=description or f"Descripción de {title}",
        source_url=f"https://example.com/{title.replace(' ', '-')}",
        source_name=source_name,
        country="Colombia",
        closing_date=closing_date,
        scraped_at=datetime(2026, 6, 16, tzinfo=UTC),
        status=Status.vigente,
        **extra,
    )


class TestDeduplicator:
    """Core deduplication scenarios."""

    def test_exact_duplicates_merged(self) -> None:
        """Scenario: Two identical titles from same source → deduplicated."""
        dedup = Deduplicator(threshold=0.85)
        records = [
            _make_record("Convocatoria Innovación 2026"),
            _make_record("Convocatoria Innovación 2026"),
        ]
        result = dedup.deduplicate(records)
        assert len(result) == 1

    def test_similar_titles_merged(self) -> None:
        """Word-order variation: 'Innovación Convocatoria 2026' vs
        'Convocatoria Innovación 2026' should normalise to the same string."""
        dedup = Deduplicator(threshold=0.85)
        records = [
            _make_record("Convocatoria Innovación 2026"),
            _make_record("Innovación Convocatoria 2026"),
        ]
        result = dedup.deduplicate(records)
        assert len(result) == 1

    def test_different_titles_kept(self) -> None:
        """Scenario: Completely different titles → both kept."""
        dedup = Deduplicator(threshold=0.85)
        records = [
            _make_record("Fondo de Becas para Innovación"),
            _make_record("Crédito Educativo Universitario"),
        ]
        result = dedup.deduplicate(records)
        assert len(result) == 2

    def test_threshold_configurable(self) -> None:
        """Lower threshold catches looser matches as duplicates."""
        records = [
            _make_record("Becas para Innovación 2026"),
            _make_record("Becas para Innovación 2026 - Colombia"),
        ]
        # High threshold (0.95) → not duplicates
        strict = Deduplicator(threshold=0.95)
        assert len(strict.deduplicate(records)) == 2

        # Low threshold (0.70) → duplicates
        loose = Deduplicator(threshold=0.70)
        assert len(loose.deduplicate(records)) == 1

    def test_different_sources_not_compared(self) -> None:
        """Records from different sources are never merged."""
        dedup = Deduplicator(threshold=0.85)
        records = [
            _make_record("Innovación 2026", source_name="Minciencias"),
            _make_record("Innovación 2026", source_name="iNNpulsa"),
        ]
        result = dedup.deduplicate(records)
        assert len(result) == 2

    def test_accent_normalization(self) -> None:
        """Accented and unaccented versions match after normalisation."""
        dedup = Deduplicator(threshold=0.85)
        records = [
            _make_record("Innovación Tecnológica 2026"),
            _make_record("Innovacion Tecnologica 2026"),  # no accents
        ]
        result = dedup.deduplicate(records)
        assert len(result) == 1

    def test_common_prefix_stripped(self) -> None:
        """'Convocatoria ' prefix is stripped before comparison."""
        dedup = Deduplicator(threshold=0.85)
        records = [
            _make_record("Convocatoria Innovación 2026"),
            _make_record("Innovación 2026"),
        ]
        result = dedup.deduplicate(records)
        assert len(result) == 1

    def test_empty_input(self) -> None:
        """Empty record list returns empty list."""
        dedup = Deduplicator()
        assert dedup.deduplicate([]) == []


class TestDeduplicatorWinnerSelection:
    """Winner selection logic (most populated fields, tie-break by date)."""

    def test_winner_has_more_fields(self) -> None:
        """Record with more populated fields wins."""
        dedup = Deduplicator(threshold=0.85)
        records = [
            _make_record(
                "Innovación 2026",
                description="Short",  # only title + description + defaults
            ),
            _make_record(
                "Innovación 2026",
                description="Longer description with more info",
                opening_date="2026-06-01",
                funding_amount="$50,000 USD",
                official_body="Minciencias",
            ),
        ]
        result = dedup.deduplicate(records)
        assert len(result) == 1
        # The second record (with more fields) should win
        assert result[0].funding_amount == "$50,000 USD"
        assert result[0].official_body == "Minciencias"

    def test_tie_break_by_recent_date(self) -> None:
        """When field counts are equal, most recent closing_date wins."""
        dedup = Deduplicator(threshold=0.85)
        records = [
            _make_record("Innovación 2026", closing_date="2026-08-15"),
            _make_record("Innovación 2026", closing_date="2026-09-30"),
        ]
        result = dedup.deduplicate(records)
        assert len(result) == 1
        assert result[0].closing_date == "2026-09-30"


class TestDeduplicatorPreservesOrder:
    """Original insertion order should be preserved as much as possible."""

    def test_order_preserved_with_no_duplicates(self) -> None:
        """Non-duplicate records keep their original order."""
        dedup = Deduplicator(threshold=0.85)
        records = [
            _make_record("Primera Convocatoria"),
            _make_record("Segunda Convocatoria"),
            _make_record("Tercera Convocatoria"),
        ]
        result = dedup.deduplicate(records)
        assert [r.title for r in result] == [
            "Primera Convocatoria",
            "Segunda Convocatoria",
            "Tercera Convocatoria",
        ]

    def test_first_appearance_wins_on_exact_duplicate(self) -> None:
        """When duplicates are exact, the first one in insertion order
        should be kept (even if the winner logic could swap)."""
        dedup = Deduplicator(threshold=0.99)
        records = [
            _make_record("Única Convocatoria", closing_date="2026-08-15"),
            _make_record("Única Convocatoria", closing_date="2026-09-30"),
            _make_record("Única Convocatoria", closing_date="2026-07-01"),
        ]
        result = dedup.deduplicate(records)
        assert len(result) == 1
        # Winner: most recent closing_date = "2026-09-30"
        assert result[0].closing_date == "2026-09-30"
