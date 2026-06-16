"""HTML report generator using Jinja2 templates.

``HTMLReportGenerator`` renders a standalone HTML file from pipeline records,
aggregate stats, and error messages.  The output embeds all CSS and JavaScript
inline; Chart.js is loaded from jsDelivr CDN.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from rastreador_convocatorias import __version__
from rastreador_convocatorias.models import FinalRecord

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_TEMPLATE_NAME = "report.html.j2"

_TRUTHY_VALUES = {"true", "1", "yes", "sí", "si"}


def _serialize_record(record: FinalRecord) -> dict[str, Any]:
    """Convert a ``FinalRecord`` to a plain dict safe for Jinja2 / JSON.

    * ``datetime`` → ISO-8601 string
    * ``Enum`` → value string
    * ``is_permanent`` → normalised boolean
    * ``_search`` → pre-composed lowercased search text
    """
    data = record.model_dump()

    # datetime → ISO string
    if isinstance(data.get("scraped_at"), datetime):
        data["scraped_at"] = data["scraped_at"].isoformat()

    # Enum → string value
    if record.status is not None:
        data["status"] = record.status.value

    # Normalise is_permanent → bool
    raw = data.get("is_permanent")
    if isinstance(raw, str):
        data["is_permanent"] = raw.strip().lower() in _TRUTHY_VALUES
    elif raw is None:
        data["is_permanent"] = False
    else:
        data["is_permanent"] = bool(raw)

    # Pre-compute search text (title + description + source_name + tags)
    search_parts = [
        data.get("title") or "",
        data.get("description") or "",
        data.get("source_name") or "",
        " ".join(data.get("tags") or []),
    ]
    data["_search"] = " ".join(part.lower() for part in search_parts if part)

    return data


class HTMLReportGenerator:
    """Generates a standalone HTML report from pipeline results.

    Uses Jinja2 to render a single complete HTML file with embedded CSS,
    Chart.js from CDN, and client-side filtering / sorting.
    """

    def __init__(self, template_dir: str | Path | None = None) -> None:
        """Initialise the generator with a Jinja2 environment.

        Parameters
        ----------
        template_dir : str or Path, optional
            Directory containing Jinja2 templates.  Defaults to ``templates/``
            next to this module.
        """
        self.env = Environment(
            loader=FileSystemLoader(
                str(template_dir) if template_dir else str(_TEMPLATE_DIR),
            ),
            autoescape=True,
        )

    def generate(
        self,
        records: list[FinalRecord],
        stats: dict[str, Any],
        errors: list[str],
        output_path: str | Path,
    ) -> None:
        """Render the HTML report and write it to *output_path*.

        Parameters
        ----------
        records : list of FinalRecord
            Fully processed and classified records.
        stats : dict
            Aggregate KPIs.  Expected keys:
            ``total``, ``colombia``, ``international``, ``vigentes``,
            ``requires_verification``, ``vencidas``, ``by_category``,
            ``by_country``, ``by_status``, ``by_funding_type``,
            ``unique_sources``.
        errors : list of str
            Source-level error messages.
        output_path : str or Path
            Destination file path for the generated HTML.
        """
        template = self.env.get_template(_TEMPLATE_NAME)

        now = datetime.now(UTC)
        generated_at = now.strftime("%Y-%m-%d %H:%M:%S UTC")

        serialized = [_serialize_record(r) for r in records]

        # Unique sorted countries (Colombia first for convenience).
        all_countries = sorted({r.country for r in records if r.country})
        countries: list[str] = []
        if "Colombia" in all_countries:
            countries.append("Colombia")
        countries.extend(c for c in all_countries if c != "Colombia")

        # Unique categories from stats.
        categories = sorted(stats.get("by_category", {}).keys())

        html = template.render(
            records=serialized,
            stats=stats,
            errors=errors,
            countries=countries,
            categories=categories,
            generated_at=generated_at,
            version=__version__,
            total_records=len(records),
            total_errors=len(errors),
        )

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(html, encoding="utf-8")
        logger.info(
            "HTML report written to %s (%d bytes)",
            output.resolve(),
            len(html),
        )
