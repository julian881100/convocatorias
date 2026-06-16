"""Pydantic models for the convocatorias pipeline."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────


class FetcherType(StrEnum):
    """Session type used to fetch a source's HTML."""

    http = "http"
    dynamic = "dynamic"
    stealthy = "stealthy"


class Status(StrEnum):
    """Vigency status of a convocatoria."""

    vigente = "vigente"
    vencida = "vencida"
    requires_verification = "requires_verification"


class PaginationType(StrEnum):
    """Strategy used to paginate through a source's listing."""

    query_param = "query_param"
    next_link = "next_link"
    infinite_scroll = "infinite_scroll"
    none = "none"


# ── Configuration ──────────────────────────────────────────────────────


class SourceConfig(BaseModel):
    """Configuration for a single convocatorias source."""

    name: str
    url: str
    country: str = Field(
        default="Colombia",
        description="ISO 3166-1 alpha-2 or country name",
    )
    fetcher: FetcherType
    pagination: Optional[dict] = None
    selectors: dict  # container + fields mapping
    robots_txt_obey: bool = True
    category_default: str = ""
    custom_parser: bool = False


# ── Pipeline Records ───────────────────────────────────────────────────


class RawRecord(BaseModel):
    """Record extracted directly from a source, before validation."""

    title: Optional[str] = None
    description: Optional[str] = None
    source_url: Optional[str] = None
    opening_date: Optional[str] = None
    closing_date: Optional[str] = None
    is_permanent: Optional[str] = None
    funding_amount: Optional[str] = None
    official_body: Optional[str] = None
    source_name: str = ""
    country: str = ""
    scraped_at: datetime = Field(default_factory=lambda: datetime.now())

    def populated_field_count(self) -> int:
        """Count non-None, non-empty fields (excluding metadata)."""
        skip = {"source_name", "country", "scraped_at"}
        return sum(
            1
            for val in self.model_dump().values()
            if val is not None and val != "" and val not in skip  # type: ignore[comparison-overlap]
        )


class ValidatedRecord(RawRecord):
    """Raw record with a computed vigency status."""

    status: Status = Status.requires_verification


class FinalRecord(ValidatedRecord):
    """Fully processed record with classification tags."""

    category: str = ""
    subcategory: str = ""
    beneficiary_type: list[str] = Field(default_factory=list)
    sector: list[str] = Field(default_factory=list)
    funding_type: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
