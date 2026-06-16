"""Source registry — loads and validates source configurations from YAML."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

import yaml

from rastreador_convocatorias.models import FetcherType, SourceConfig

logger = logging.getLogger(__name__)


def load_sources(path: str = "sources.yaml") -> List[SourceConfig]:
    """Read YAML source definitions, validate each entry, return typed configs.

    Parameters
    ----------
    path : str
        Path to the YAML sources file (relative to CWD or absolute).

    Returns
    -------
    list[SourceConfig]
        Validated source configurations ready for dispatch.

    Raises
    ------
    ValueError
        If any source has an unknown/unsupported ``fetcher_type``.
    FileNotFoundError
        If the YAML file does not exist.
    """
    sources_path = Path(path)

    if not sources_path.is_file():
        raise FileNotFoundError(f"Sources file not found: {sources_path.resolve()}")

    with open(sources_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, dict) or "sources" not in raw:
        logger.warning("YAML file contains no 'sources' key — returning empty list")
        return []

    entries: List[dict] = raw["sources"]
    configs: List[SourceConfig] = []

    for idx, entry in enumerate(entries, start=1):
        try:
            _validate_fetcher(entry)
            source = SourceConfig(**entry)
            configs.append(source)
            logger.debug("Loaded source #%d: %s", idx, source.name)
        except ValueError as exc:
            # Unknown fetcher_type → fatal for that source
            logger.error("Source #%d: %s", idx, exc)
            raise
        except Exception as exc:
            # Validation or missing-field errors → skip with warning
            logger.warning("Source #%d skipped: %s", idx, exc)

    logger.info("Loaded %d / %d source(s)", len(configs), len(entries))
    return configs


def _validate_fetcher(entry: dict) -> None:
    """Ensure ``fetcher`` / ``fetcher_type`` field is a known value."""
    raw = entry.get("fetcher") or entry.get("fetcher_type", "")
    if not raw:
        raise ValueError(f"Missing 'fetcher' field in source entry: {entry.get('name', '<unnamed>')}")
    try:
        FetcherType(raw)
    except ValueError:
        valid = ", ".join(m.value for m in FetcherType)
        raise ValueError(
            f"Unknown fetcher_type '{raw}' in source '{entry.get('name', '<unnamed>')}'. "
            f"Valid values: {valid}"
        )
