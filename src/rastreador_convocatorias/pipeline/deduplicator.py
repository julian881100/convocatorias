"""Deduplicator — removes near-duplicate convocatorias using fuzzy title matching.

``Deduplicator`` normalizes titles (lowercase, strip common prefixes, remove
accents), then compares records pairwise using ``rapidfuzz.fuzz.token_sort_ratio``.
Only records from the same source are compared.  When duplicates are found the
record with the most populated fields is kept; ties are broken by most-recent
``closing_date``.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata

from dateutil.parser import parse as parse_date
from rapidfuzz import fuzz

from rastreador_convocatorias.models import ValidatedRecord

logger = logging.getLogger(__name__)

# ── Common words stripped before comparison ──────────────────────────────
# These high-frequency words are removed wherever they appear as whole words.
# This normalises titles like "Convocatoria Innovación 2026" vs
# "Innovación Convocatoria 2026" so that ``token_sort_ratio`` doesn't inflate
# scores from repeated stop-words.

_COMMON_WORDS = [
    r"\bconvocatoria\b",
    r"\bprograma\b",
    r"\bbeca\b",
    r"\bbecas\b",
    r"\bfondo\b",
    r"\bconcurso\b",
]

_COMMON_WORDS_RE = re.compile(
    "|".join(_COMMON_WORDS),
    re.IGNORECASE,
)

# ── Normalisation helpers ────────────────────────────────────────────────


def _normalize_title(title: str) -> str:
    """Normalise a title for fuzzy comparison.

    Steps
    -----
    1. Strip leading/trailing whitespace.
    2. Lowercase.
    3. Remove common Spanish stop-words (e.g. ``convocatoria``, ``programa``).
    4. Collapse multiple whitespace characters into one.
    """
    text = title.strip().lower()
    text = _COMMON_WORDS_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_accents(text: str) -> str:
    """Remove Unicode accents (e.g. ``"é"`` → ``"e"``)."""
    nfkd = unicodedata.normalize("NFKD", text)
    return nfkd.encode("ascii", "ignore").decode("ascii")


# ── Threshold resolution ─────────────────────────────────────────────────


def _resolve_threshold() -> float:
    """Return the dedup threshold from ``DEDUP_THRESHOLD`` env or ``0.85``."""
    env_val = os.environ.get("DEDUP_THRESHOLD")
    if env_val and env_val.strip():
        try:
            return float(env_val.strip())
        except ValueError:
            logger.warning(
                "Invalid DEDUP_THRESHOLD='%s', falling back to 0.85", env_val
            )
    return 0.85


# ── Deduplicator ─────────────────────────────────────────────────────────


class Deduplicator:
    """Removes near-duplicate ``ValidatedRecord`` instances by fuzzy title matching.

    Parameters
    ----------
    threshold : float, optional
        Minimum ``token_sort_ratio`` (0–1) to consider two records duplicates.
        Defaults to the ``DEDUP_THRESHOLD`` env var, or ``0.85`` if unset.
    """

    def __init__(self, threshold: float | None = None) -> None:
        self.threshold = threshold if threshold is not None else _resolve_threshold()

    # ── Public API ──────────────────────────────────────────────────────

    def deduplicate(self, records: list[ValidatedRecord]) -> list[ValidatedRecord]:
        """Remove near-duplicate records, keeping the best one per group.

        Parameters
        ----------
        records : list of ValidatedRecord
            Input records to deduplicate.

        Returns
        -------
        list of ValidatedRecord
            Deduplicated records in (approximately) original insertion order.
        """
        if not records:
            return []

        # Group by source_name so cross-source records are never compared.
        by_source: dict[str, list[tuple[int, ValidatedRecord]]] = {}
        for idx, rec in enumerate(records):
            by_source.setdefault(rec.source_name, []).append((idx, rec))

        kept: list[tuple[int, ValidatedRecord]] = []

        for _source_name, indexed in by_source.items():
            if len(indexed) == 1:
                kept.append(indexed[0])
                continue

            # Sort by original index to keep insertion order as much as possible.
            indexed.sort(key=lambda x: x[0])
            groups = self._group_duplicates(indexed)
            for group in groups:
                winner = self._select_winner([r for _, r in group])
                orig_idx = next(idx for idx, r in group if r is winner)
                kept.append((orig_idx, winner))

        # Restore original insertion order.
        kept.sort(key=lambda x: x[0])
        return [r for _, r in kept]

    # ── Internals ───────────────────────────────────────────────────────

    def _group_duplicates(
        self,
        indexed: list[tuple[int, ValidatedRecord]],
    ) -> list[list[tuple[int, ValidatedRecord]]]:
        """Group near-duplicate records together by fuzzy title match."""
        groups: list[list[tuple[int, ValidatedRecord]]] = []
        used = [False] * len(indexed)

        for i, (idx_a, rec_a) in enumerate(indexed):
            if used[i]:
                continue
            group = [(idx_a, rec_a)]
            used[i] = True
            norm_a = _strip_accents(_normalize_title(rec_a.title or ""))

            for j, (idx_b, rec_b) in enumerate(indexed):
                if used[j]:
                    continue
                norm_b = _strip_accents(_normalize_title(rec_b.title or ""))
                score = fuzz.token_sort_ratio(norm_a, norm_b)
                if self._is_duplicate(score):
                    group.append((idx_b, rec_b))
                    used[j] = True

            groups.append(group)

        return groups

    def _is_duplicate(self, score: int) -> bool:
        """Return ``True`` when a fuzzy-match score meets the threshold.

        ``rapidfuzz`` returns integer scores in the 0–100 range, so we
        multiply the internal 0–1 threshold by 100 for comparison.
        """
        return score >= self.threshold * 100

    @staticmethod
    def _select_winner(group: list[ValidatedRecord]) -> ValidatedRecord:
        """Pick the best record from a group of duplicates.

        1. Most populated fields wins.
        2. Tie-break: most recent ``closing_date``.
        """
        best = group[0]
        best_count = best.populated_field_count()

        for candidate in group[1:]:
            count = candidate.populated_field_count()
            if count > best_count:
                best = candidate
                best_count = count
            elif count == best_count:
                # Tie-break: most recent closing_date.
                winner = _pick_most_recent(best, candidate)
                if winner is not best:
                    best = winner
                    best_count = count

        return best


# ── Tie-break helper ─────────────────────────────────────────────────────


def _pick_most_recent(
    a: ValidatedRecord,
    b: ValidatedRecord,
) -> ValidatedRecord:
    """Return the record with the more-recent ``closing_date``.

    If neither or both have no parseable date, *a* is returned (first-wins).
    """
    a_raw = a.closing_date
    b_raw = b.closing_date
    if a_raw and b_raw:
        try:
            a_dt = parse_date(a_raw, dayfirst=True)
            b_dt = parse_date(b_raw, dayfirst=True)
            return a if a_dt >= b_dt else b
        except (ValueError, TypeError):
            pass
    return a
