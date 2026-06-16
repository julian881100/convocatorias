"""Shared fixtures for pipeline unit tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rastreador_convocatorias.models import RawRecord, SourceConfig, Status, ValidatedRecord


@pytest.fixture
def ref_date() -> datetime:
    """Fixed reference date for test determinism."""
    return datetime(2026, 6, 16, tzinfo=UTC)


@pytest.fixture
def sample_raw_record() -> RawRecord:
    """A basic raw record with a future closing date."""
    return RawRecord(
        title="Convocatoria Innovación 2026",
        description="Fondo para proyectos de innovación tecnológica",
        source_url="https://minciencias.gov.co/convocatoria/1",
        source_name="Minciencias",
        country="Colombia",
        closing_date="2026-08-15",
        scraped_at=datetime(2026, 6, 16, tzinfo=UTC),
    )


@pytest.fixture
def sample_validated_record() -> ValidatedRecord:
    """A validated record with vigente status."""
    return ValidatedRecord(
        title="Convocatoria Innovación 2026",
        description="Fondo para proyectos de innovación tecnológica",
        source_url="https://minciencias.gov.co/convocatoria/1",
        source_name="Minciencias",
        country="Colombia",
        closing_date="2026-08-15",
        scraped_at=datetime(2026, 6, 16, tzinfo=UTC),
        status=Status.vigente,
    )


@pytest.fixture
def vigente_record() -> ValidatedRecord:
    """Record with a future closing date."""
    return ValidatedRecord(
        title="Beca para estudios de posgrado",
        description="Programa de formación en el exterior",
        source_url="https://example.com/beca",
        source_name="ICETEX",
        country="Colombia",
        closing_date="2026-09-30",
        opening_date="2026-06-01",
        scraped_at=datetime(2026, 6, 16, tzinfo=UTC),
        status=Status.vigente,
    )


@pytest.fixture
def vencida_record() -> ValidatedRecord:
    """Record with a past closing date."""
    return ValidatedRecord(
        title="Convocatoria cerrada 2025",
        description="Programa que ya cerró",
        source_url="https://example.com/cerrada",
        source_name="Minciencias",
        country="Colombia",
        closing_date="2025-12-31",
        scraped_at=datetime(2026, 6, 16, tzinfo=UTC),
        status=Status.vencida,
    )


@pytest.fixture
def requires_verification_record() -> ValidatedRecord:
    """Record with no closing date and not permanent."""
    return ValidatedRecord(
        title="Sin fecha de cierre",
        description="No hay información disponible",
        source_url="https://example.com/sin-fecha",
        source_name="Minciencias",
        country="Colombia",
        scraped_at=datetime(2026, 6, 16, tzinfo=UTC),
        status=Status.requires_verification,
    )


@pytest.fixture
def permanent_record() -> ValidatedRecord:
    """Permanent record with a past date (should still be vigente)."""
    return ValidatedRecord(
        title="Programa permanente de ciencia",
        description="Convocatoria abierta permanentemente",
        source_url="https://example.com/permanente",
        source_name="Minciencias",
        country="Colombia",
        closing_date="2025-01-01",
        is_permanent="true",
        scraped_at=datetime(2026, 6, 16, tzinfo=UTC),
        status=Status.vigente,
    )


# ── Mock-source helper for integration tests ──────────────────────────


def make_mock_source(
    name: str = "Mock Source",
    url: str = "https://example.com/convocatorias",
    country: str = "Colombia",
    category_default: str = "",
    custom_parser: bool = False,
) -> SourceConfig:
    """Build a ``SourceConfig`` for test use without requiring a YAML file.

    Parameters
    ----------
    name : str
        Display name for the source.
    url : str
        Entry URL.
    country : str
        Country or ``"Internacional"``.
    category_default : str
        Fallback category for records that don't match any keyword.
    custom_parser : bool
        Whether extraction should delegate to an adapter.

    Returns
    -------
    SourceConfig
        A fully valid source configuration ready for use in tests.
    """
    return SourceConfig(
        name=name,
        url=url,
        country=country,
        fetcher="http",
        selectors={"container": "", "fields": {}},
        category_default=category_default,
        custom_parser=custom_parser,
    )


def make_mock_record(
    title: str = "Test Convocatoria",
    description: str = "",
    source_url: str = "https://example.com/test",
    source_name: str = "Mock Source",
    country: str = "Colombia",
    closing_date: str | None = "2026-08-15",
    opening_date: str | None = None,
    is_permanent: str | None = None,
    funding_amount: str | None = None,
    official_body: str | None = None,
) -> RawRecord:
    """Build a ``RawRecord`` with defaults sensible for integration tests.

    Parameters
    ----------
    title, description, source_url, source_name, country :
        Standard record fields.
    closing_date, opening_date :
        Date strings (passed through to pipeline stages).
    is_permanent :
        ``"true"`` / ``"false"`` string for permanent records.
    funding_amount, official_body :
        Optional metadata fields.

    Returns
    -------
    RawRecord
        Ready for validation / dedup / classification.
    """
    return RawRecord(
        title=title,
        description=description,
        source_url=source_url,
        source_name=source_name,
        country=country,
        closing_date=closing_date,
        opening_date=opening_date,
        is_permanent=is_permanent,
        funding_amount=funding_amount,
        official_body=official_body,
        scraped_at=datetime(2026, 6, 16, tzinfo=UTC),
    )
