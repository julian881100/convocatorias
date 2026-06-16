"""Tests for ``VigencyValidator``.

Covers all scenarios from the spec's **Validity Checker** section.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rastreador_convocatorias.models import RawRecord, Status
from rastreador_convocatorias.pipeline.validator import VigencyValidator


class TestVigencyValidator:
    """Suite of validator scenarios."""

    def test_future_date_is_vigente(self, ref_date: datetime) -> None:
        """Scenario: Record with future closing date → vigente."""
        validator = VigencyValidator(reference_date=ref_date)
        record = RawRecord(
            title="Test future",
            closing_date="2026-08-15",
        )
        result = validator.validate([record])
        assert result[0].status == Status.vigente

    def test_past_date_is_vencida(self, ref_date: datetime) -> None:
        """Scenario: Record with past closing date → vencida."""
        validator = VigencyValidator(reference_date=ref_date)
        record = RawRecord(
            title="Test past",
            closing_date="2026-05-01",
        )
        result = validator.validate([record])
        assert result[0].status == Status.vencida

    def test_spanish_date_format(self) -> None:
        """Spanish format '15 de junio de 2026' is parsed correctly."""
        validator = VigencyValidator(
            reference_date=datetime(2026, 7, 1, tzinfo=UTC),
        )
        record = RawRecord(
            title="Test spanish date",
            closing_date="15 de junio de 2026",
        )
        result = validator.validate([record])
        # June 15 is before July 1 → vencida
        assert result[0].status == Status.vencida

    def test_permanent_record_is_vigente(self, ref_date: datetime) -> None:
        """Scenario: Permanent record → vigente even with past date."""
        validator = VigencyValidator(reference_date=ref_date)
        record = RawRecord(
            title="Test permanent",
            closing_date="2025-01-01",
            is_permanent="true",
        )
        result = validator.validate([record])
        assert result[0].status == Status.vigente

    def test_missing_closing_date_not_permanent(self, ref_date: datetime) -> None:
        """Scenario: No dates and not permanent → requires_verification."""
        validator = VigencyValidator(reference_date=ref_date)
        record = RawRecord(title="Test no date")
        result = validator.validate([record])
        assert result[0].status == Status.requires_verification

    def test_ref_date_today_boundary_same_day_vigente(
        self, ref_date: datetime,
    ) -> None:
        """Closing date on the reference date → vigente (>=)."""
        validator = VigencyValidator(reference_date=ref_date)
        record = RawRecord(
            title="Same day",
            closing_date="2026-06-16",
        )
        result = validator.validate([record])
        assert result[0].status == Status.vigente

    def test_env_reference_date_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """REFERENCE_DATE env var overrides the default (DD/MM/YYYY)."""
        monkeypatch.setenv("REFERENCE_DATE", "01/07/2026")
        validator = VigencyValidator()  # no explicit ref_date → env fallback
        record = RawRecord(
            title="Test env override",
            closing_date="2026-06-15",
        )
        result = validator.validate([record])
        assert result[0].status == Status.vencida

    def test_dd_mm_yyyy_format(self, ref_date: datetime) -> None:
        """DD/MM/YYYY format is parsed correctly with dayfirst=True."""
        validator = VigencyValidator(reference_date=ref_date)
        record = RawRecord(
            title="Test dd/mm",
            closing_date="15/08/2026",
        )
        result = validator.validate([record])
        assert result[0].status == Status.vigente

    def test_dot_separated_date_format(self, ref_date: datetime) -> None:
        """DD.MM.YYYY format (EIC) is parsed correctly."""
        validator = VigencyValidator(reference_date=ref_date)
        record = RawRecord(
            title="Test dots",
            closing_date="8.07.2026",
        )
        result = validator.validate([record])
        assert result[0].status == Status.vigente

    def test_multiple_dates_use_latest(self, ref_date: datetime) -> None:
        """Pipe-separated deadlines use the latest date (rolling deadlines)."""
        validator = VigencyValidator(reference_date=ref_date)
        record = RawRecord(
            title="Test multiple",
            closing_date="Deadline dates: 7.01.2026 | 4.03.2026 | 8.07.2026 | 4.11.2026",
        )
        result = validator.validate([record])
        # Latest deadline is 4 Nov 2026 → vigente
        assert result[0].status == Status.vigente

    def test_opening_date_without_closing_date_is_vigente(
        self, ref_date: datetime,
    ) -> None:
        """Record that has already opened and has no closing date → vigente."""
        validator = VigencyValidator(reference_date=ref_date)
        record = RawRecord(
            title="Already opened",
            opening_date="2026-01-15",
        )
        result = validator.validate([record])
        assert result[0].status == Status.vigente

    def test_future_opening_date_requires_verification(
        self, ref_date: datetime,
    ) -> None:
        """Record that opens in the future → requires_verification."""
        validator = VigencyValidator(reference_date=ref_date)
        record = RawRecord(
            title="Future opening",
            opening_date="01/12/2026",
        )
        result = validator.validate([record])
        assert result[0].status == Status.requires_verification


class TestValidatorEdgeCases:
    """Edge cases that might trip up the parser."""

    def test_empty_closing_date_string_counts_as_missing(
        self, ref_date: datetime,
    ) -> None:
        """Empty string closing_date → treated as missing."""
        validator = VigencyValidator(reference_date=ref_date)
        record = RawRecord(title="Empty date", closing_date="")
        result = validator.validate([record])
        assert result[0].status == Status.requires_verification

    def test_invalid_date_string(self, ref_date: datetime) -> None:
        """Unparseable date string → requires_verification."""
        validator = VigencyValidator(reference_date=ref_date)
        record = RawRecord(title="Gibberish date", closing_date="not-a-date")
        result = validator.validate([record])
        assert result[0].status == Status.requires_verification

    def test_validated_record_keeps_original_fields(
        self, ref_date: datetime,
    ) -> None:
        """Validated records preserve all original RawRecord fields."""
        validator = VigencyValidator(reference_date=ref_date)
        record = RawRecord(
            title="Preserve test",
            description="Check fields survive",
            source_url="https://example.com/preserve",
            source_name="TestSource",
            country="Colombia",
            closing_date="2026-09-01",
        )
        result = validator.validate([record])
        validated = result[0]
        assert validated.title == "Preserve test"
        assert validated.description == "Check fields survive"
        assert validated.source_url == "https://example.com/preserve"
        assert validated.source_name == "TestSource"
        assert validated.country == "Colombia"
