"""Vigency validator — determines the status of each raw record.

``VigencyValidator`` parses the ``closing_date`` field using
``python-dateutil`` with ``dayfirst=True`` and a Spanish-month-name lookup,
then tags each record as ``vigente``, ``vencida``, or
``requires_verification``.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime

from dateutil.parser import parse as parse_date

from rastreador_convocatorias.models import RawRecord, Status, ValidatedRecord

logger = logging.getLogger(__name__)

# ── Spanish month name → number mapping ─────────────────────────────────

SPANISH_MONTHS: dict[str, int] = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

# Pattern to match "15 de junio de 2026" — captures numeric day, Spanish month
# name, and year (with optional "de" delimiters).
_SPANISH_DATE_RE = re.compile(
    r"(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+de?\s*(\d{4})",
    re.IGNORECASE,
)

# Patterns for numeric dates commonly found in international sources.
# These are tried before dateutil because dateutil can mis-parse D.M.YYYY.
_NUMERIC_DATE_RE = re.compile(
    r"(\d{1,2})[\.\/\-](\d{1,2})[\.\/\-](\d{4})"
)

# ── Helpers ──────────────────────────────────────────────────────────────

_TRUTHY_STRINGS = {"true", "1", "yes", "sí", "si"}


def _is_truthy(value: object) -> bool:
    """Check if a value should be considered ``True``."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY_STRINGS
    return bool(value)


def _parse_date_with_spanish(text: str) -> datetime | None:
    """Parse a date string that may contain Spanish month names.

    When multiple dates are present (e.g. ``"7.01.2026 | 4.03.2026 | ..."``),
    the latest parseable date is returned.  This matches the semantics of
    rolling deadlines: a call remains open until its final deadline passes.

    Parameters
    ----------
    text : str
        Raw date string (e.g. ``"15 de junio de 2026"``, ``"2026-06-15"``,
        ``"15/06/2026"``).

    Returns
    -------
    datetime or None
        Timezone-aware datetime, or ``None`` if parsing fails.
    """
    if not text or not text.strip():
        return None

    cleaned = text.strip()

    # Split on common date separators (EIC uses "|", others use ",", ";", newlines).
    parts = re.split(r"[|;,\n]+", cleaned)

    parsed_dates: list[datetime] = []

    for part in parts:
        candidate = part.strip()
        if not candidate:
            continue

        # Remove common prefixes/labels and trailing time zones.
        candidate = re.sub(
            r"(?i)(deadline dates?|closing dates?|deadline|fecha de cierre|cierre|vigencia|hasta el)\s*:?\s*",
            "",
            candidate,
        )
        candidate = re.sub(
            r"\s*\d{1,2}\.\d{2}\s*-\s*[A-Z]{2,4}$", "", candidate.strip()
        )
        candidate = candidate.strip()
        if not candidate:
            continue

        dt: datetime | None = None

        # Strategy 1: explicit numeric D[./-]M[./-]YYYY (dayfirst)
        match = _NUMERIC_DATE_RE.search(candidate)
        if match:
            day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            try:
                dt = datetime(year, month, day, tzinfo=UTC)
            except ValueError:
                dt = None

        # Strategy 2: direct parse
        if dt is None:
            try:
                dt = parse_date(candidate, dayfirst=True)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
            except (ValueError, TypeError):
                dt = None

        # Strategy 3: replace Spanish month names
        if dt is None:
            match = _SPANISH_DATE_RE.search(candidate)
            if match:
                day = int(match.group(1))
                month_name = match.group(2).lower()
                year = int(match.group(3))
                month = SPANISH_MONTHS.get(month_name)
                if month:
                    try:
                        dt = datetime(year, month, day, tzinfo=UTC)
                    except ValueError:
                        dt = None

        if dt is not None:
            parsed_dates.append(dt)

    if parsed_dates:
        return max(parsed_dates)

    # Strategy 4: brute-force month name replacement on full text
    lower = cleaned.lower()
    for name, num in SPANISH_MONTHS.items():
        if name in lower:
            patched = re.sub(
                rf"\bde\s+{name}\s+de\b",
                f"/{num}/",
                lower,
                flags=re.IGNORECASE,
            )
            try:
                dt = parse_date(patched, dayfirst=True)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt
            except (ValueError, TypeError):
                pass
            break

    logger.debug("Could not parse date text: %s", text)
    return None


# ── Reference date resolution ───────────────────────────────────────────


def _resolve_reference_date() -> datetime:
    """Return the reference date for vigency comparison.

    Priority:
    1. ``REFERENCE_DATE`` environment variable (ISO-8601 or DD/MM/YYYY).
    2. Current UTC time.

    Returns
    -------
    datetime
        Timezone-aware UTC datetime.
    """
    env_val = os.environ.get("REFERENCE_DATE")
    if env_val and env_val.strip():
        try:
            dt = parse_date(env_val.strip(), dayfirst=True)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except (ValueError, TypeError):
            logger.warning(
                "Cannot parse REFERENCE_DATE='%s', falling back to current time",
                env_val,
            )

    return datetime.now(UTC)


# ── Validator ────────────────────────────────────────────────────────────


class VigencyValidator:
    """Determines the vigency status of raw records.

    Parameters
    ----------
    reference_date : datetime, optional
        Cutoff date for vigency.  Defaults to the ``REFERENCE_DATE`` env
        var or the current UTC time.
    """

    def __init__(
        self,
        reference_date: datetime | None = None,
    ) -> None:
        self.reference_date = (
            reference_date
            if reference_date is not None
            else _resolve_reference_date()
        )

    def validate(self, records: list[RawRecord]) -> list[ValidatedRecord]:
        """Run validation on a batch of raw records.

        Each record is checked for an explicit permanent flag first,
        then its ``closing_date`` is parsed and compared against the
        reference date.

        Parameters
        ----------
        records : list of RawRecord
            Raw records to validate.

        Returns
        -------
        list of ValidatedRecord
            Records with a computed ``status`` field.
        """
        validated: list[ValidatedRecord] = []
        for record in records:
            validated.append(self._validate_one(record))
        return validated

    def _validate_one(self, record: RawRecord) -> ValidatedRecord:
        """Validate a single record and return a ``ValidatedRecord``."""
        # 1. Permanent flag overrides everything
        if _is_truthy(record.is_permanent):
            return ValidatedRecord(
                **record.model_dump(),
                status=Status.vigente,
            )

        # 2. Parse closing_date
        closing_date = self._parse_date(record.closing_date) if record.closing_date else None

        if closing_date is not None:
            status = Status.vigente if closing_date >= self.reference_date else Status.vencida
        else:
            # No explicit closing date: use opening_date as a proxy.
            # If the call has already opened (opening_date <= reference_date),
            # treat it as currently open.  Otherwise it requires verification.
            opening_date = self._parse_date(record.opening_date) if record.opening_date else None
            if opening_date is not None and opening_date <= self.reference_date:
                status = Status.vigente
            else:
                status = Status.requires_verification

        return ValidatedRecord(
            **record.model_dump(),
            status=status,
        )

    # -- Date parsing ------------------------------------------------------

    @staticmethod
    def _parse_date(text: str) -> datetime | None:
        """Parse a date string, handling Spanish formats."""
        return _parse_date_with_spanish(text)
