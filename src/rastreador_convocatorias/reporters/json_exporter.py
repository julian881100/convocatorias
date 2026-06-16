"""JSON exporter for structured data output.

``JSONExporter`` writes three files under ``{output_dir}/data/``:

* ``convocatorias.json`` — all records as a JSON array
* ``stats.json`` — aggregate KPIs
* ``metadata.json`` — crawl timestamp, version, source / error counts
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from rastreador_convocatorias import __version__
from rastreador_convocatorias.models import FinalRecord

logger = logging.getLogger(__name__)


class ConvocatoriasEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles ``datetime`` and ``Enum`` types."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Enum):
            return obj.value
        return super().default(obj)


def _write_json(path: Path, data: Any) -> None:
    """Write a JSON file with consistent formatting."""
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, cls=ConvocatoriasEncoder, indent=2, ensure_ascii=False)


class JSONExporter:
    """Exports pipeline results to structured JSON files.

    Writes three files under ``{output_dir}/data/``:

    * ``convocatorias.json``
    * ``stats.json``
    * ``metadata.json``
    """

    def export(
        self,
        records: list[FinalRecord],
        stats: dict[str, Any],
        errors: list[str],
        output_dir: str | Path,
    ) -> None:
        """Export records and metadata to JSON files.

        Parameters
        ----------
        records : list of FinalRecord
            Fully processed records.
        stats : dict
            Aggregate KPIs computed by the caller.
        errors : list of str
            Source-level error messages.
        output_dir : str or Path
            Root output directory.  Files are written to
            ``{output_dir}/data/``.
        """
        data_dir = Path(output_dir) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now(UTC)

        # ── convocatorias.json ───────────────────────────────────────────
        records_data = [rec.model_dump() for rec in records]
        _write_json(data_dir / "convocatorias.json", records_data)

        # ── stats.json ───────────────────────────────────────────────────
        _write_json(data_dir / "stats.json", stats)

        # ── metadata.json ────────────────────────────────────────────────
        metadata: dict[str, Any] = {
            "generated_at": now.isoformat(),
            "version": __version__,
            "total_records": len(records),
            "source_count": stats.get("unique_sources", 0),
            "error_count": len(errors),
            "generation_time_seconds": None,
            "status_counts": stats.get("by_status", {}),
            "country_counts": stats.get("by_country", {}),
            "category_counts": stats.get("by_category", {}),
        }
        _write_json(data_dir / "metadata.json", metadata)

        logger.info(
            "JSON export complete: %d records, %d errors -> %s",
            len(records),
            len(errors),
            data_dir,
        )
