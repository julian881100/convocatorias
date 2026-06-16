"""Integration smoke test for the full rastreador-convocatorias pipeline.

Tests the end-to-end flow using mocked HTML responses (static strings) so no
network access is required.  All stages are tested in sequence:

* Adapter extraction from known HTML structures
* Vigency validation
* Deduplication
* Classification
* HTML report generation
* JSON export
* CLI entry point
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from rastreador_convocatorias.main import main
from rastreador_convocatorias.models import (
    FinalRecord,
    RawRecord,
    SourceConfig,
    Status,
)
from rastreador_convocatorias.pipeline.classifier import Classifier
from rastreador_convocatorias.pipeline.deduplicator import Deduplicator
from rastreador_convocatorias.pipeline.validator import VigencyValidator
from rastreador_convocatorias.reporters.html_generator import HTMLReportGenerator
from rastreador_convocatorias.reporters.json_exporter import JSONExporter

# ── Helpers ────────────────────────────────────────────────────────────


def _make_source(
    name: str = "Test Source",
    url: str = "https://example.com/test",
    country: str = "Internacional",
    category_default: str = "Investigación",
    custom_parser: bool = False,
) -> SourceConfig:
    """Create a minimal ``SourceConfig`` for testing."""
    return SourceConfig(
        name=name,
        url=url,
        country=country,
        fetcher="http",
        selectors={"container": "", "fields": {}},
        category_default=category_default,
        custom_parser=custom_parser,
    )


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def mock_horizon_html() -> str:
    """Mock HTML matching the Horizon Europe topic-search result structure."""
    return """<!DOCTYPE html>
<html><body>
<div class="topic-search-result">
    <a class="topic-title" href="/topic/HORIZON-CL1-2026-01">
        AI for Health Equity
    </a>
    <p class="topic-description">
        Research proposals for AI-driven healthcare solutions.
    </p>
    <span class="closing-date">15 September 2026</span>
    <span class="opening-date">1 March 2026</span>
    <span class="programme-name">Horizon Europe \u2013 Cluster 1</span>
</div>
<div class="topic-search-result">
    <a class="topic-title" href="/topic/HORIZON-CL4-2026-02">
        Digital Europe Advanced Skills
    </a>
    <p class="topic-description">
        Building advanced digital skills across the EU.
    </p>
    <span class="closing-date">30 November 2026</span>
    <span class="opening-date">1 June 2026</span>
    <span class="programme-name">Horizon Europe \u2013 Cluster 4</span>
</div>
<div class="topic-search-result">
    <a class="topic-title" href="/topic/HORIZON-MSCA-2026-01">
        MSCA Postdoctoral Fellowships 2026
    </a>
    <p class="topic-description">
        Support for postdoctoral researchers across all disciplines.
    </p>
    <span class="closing-date">15 October 2026</span>
    <span class="opening-date">1 April 2026</span>
    <span class="programme-name">Horizon Europe \u2013 MSCA</span>
</div>
</body></html>"""


@pytest.fixture
def mock_erasmus_html() -> str:
    """Mock HTML matching the Erasmus+ opportunity-item card structure."""
    return """<!DOCTYPE html>
<html><body>
<div class="opportunity-item">
    <h3><a href="/opportunities/ka1-learning-mobility">
        KA1 Learning Mobility for Individuals</a></h3>
    <p class="teaser">
        Funding for study, traineeship, or staff mobility abroad.
    </p>
    <span class="deadline">15 October 2026</span>
    <span class="start-date">1 March 2026</span>
</div>
<div class="opportunity-item">
    <h3><a href="/opportunities/ka2-partnerships">
        KA2 Cooperation Partnerships</a></h3>
    <p class="teaser">
        Support for cross-border cooperation between organisations.
    </p>
    <span class="deadline">31 December 2026</span>
</div>
</body></html>"""


@pytest.fixture
def mock_daad_html() -> str:
    """Mock HTML matching the DAAD scholarship-item card structure."""
    return """<!DOCTYPE html>
<html><body>
<div class="scholarship-item">
    <h3><a href="/becas/daad-scholarship-2026">
        DAAD Scholarship for Developing Countries 2026</a></h3>
    <p class="description">
        Full funding for Master's and PhD programmes in Germany.
    </p>
    <span class="deadline">31 December 2026</span>
    <div class="funding-amount">&euro;1,000 / month</div>
</div>
<div class="scholarship-item">
    <h3><a href="/becas/helmut-schmidt">
        Helmut Schmidt Programme 2027</a></h3>
    <p class="description">
        Master's scholarships in public policy and governance.
    </p>
    <span class="deadline">30 June 2027</span>
    <div class="funding-amount">&euro;992 / month</div>
</div>
</body></html>"""


@pytest.fixture
def mock_mit_html() -> str:
    """Mock HTML matching the MIT Solve challenge-card structure."""
    return """<!DOCTYPE html>
<html><body>
<div class="challenge-card">
    <h3 class="card-title">
        <a class="card-link" href="/challenges/ai-health-equity">
            AI for Health Equity</a></h3>
    <p class="challenge-teaser">
        Develop AI-powered solutions for underserved communities.
    </p>
    <span class="deadline">1 August 2026</span>
    <span class="prize">$1,000,000</span>
</div>
</body></html>"""


@pytest.fixture
def empty_html() -> str:
    """Empty HTML with no relevant elements — tests graceful handling."""
    return "<html><body><p>No convocatorias here.</p></body></html>"


@pytest.fixture
def tmp_output(tmp_path: Path) -> Path:
    """Temporary output directory for report/JSON export tests."""
    out = tmp_path / "output"
    out.mkdir()
    return out


@pytest.fixture
def sample_final_records() -> list[FinalRecord]:
    """Small set of classified records for export tests."""
    now = datetime(2026, 6, 16, tzinfo=UTC)
    return [
        FinalRecord(
            title="AI for Health Equity",
            source_url="https://example.com/ai-health",
            source_name="TestSource",
            country="Internacional",
            closing_date="2026-09-15",
            scraped_at=now,
            status=Status.vigente,
            category="I+D+i",
            sector=["Inteligencia Artificial", "Salud"],
            beneficiary_type=["Investigador", "Academia"],
            funding_type=["No reembolsable"],
            tags=["ai", "health-equity", "apertura: 2026-03-01"],
        ),
        FinalRecord(
            title="Digital Europe Advanced Skills",
            source_url="https://example.com/digital-skills",
            source_name="TestSource",
            country="Internacional",
            closing_date="2026-11-30",
            scraped_at=now,
            status=Status.vigente,
            category="Formación",
            sector=["Tecnologías de la información", "Educación"],
            beneficiary_type=["Estudiante", "Academia"],
            funding_type=["Beca"],
            tags=["digital", "skills", "formation"],
        ),
        FinalRecord(
            title="Expired Grant 2025",
            source_url="https://example.com/expired",
            source_name="OtherSource",
            country="Colombia",
            closing_date="2025-12-31",
            scraped_at=now,
            status=Status.vencida,
            category="Investigación",
            sector=["Bioeconomía"],
            beneficiary_type=["Investigador"],
            funding_type=["No reembolsable"],
            tags=["expired", "grant"],
        ),
        FinalRecord(
            title="No Closing Date Entry",
            source_url="https://example.com/no-close",
            source_name="OtherSource",
            country="Colombia",
            scraped_at=now,
            status=Status.requires_verification,
        ),
    ]


# ── Adapter Tests ─────────────────────────────────────────────────────


class TestAdapters:
    """Verify each adapter parses its mock HTML structure correctly."""

    def test_horizon_europe_adapter(self, mock_horizon_html: str) -> None:
        """Horizon Europe adapter returns records from mock HTML."""
        from rastreador_convocatorias.spiders.adapters import parse_horizon_europe

        source = _make_source(name="Horizon Europe - Funding & Tender Opportunities")
        records = parse_horizon_europe(mock_horizon_html, source)

        assert len(records) == 3
        assert records[0].title == "AI for Health Equity"
        assert records[0].source_url.endswith("/topic/HORIZON-CL1-2026-01")
        assert records[2].title == "MSCA Postdoctoral Fellowships 2026"

    def test_erasmus_plus_adapter(self, mock_erasmus_html: str) -> None:
        """Erasmus+ adapter returns records from mock HTML."""
        from rastreador_convocatorias.spiders.adapters import parse_erasmus_plus

        source = _make_source(name="Erasmus+ - Opportunities")
        records = parse_erasmus_plus(mock_erasmus_html, source)

        assert len(records) == 2
        assert records[0].title == "KA1 Learning Mobility for Individuals"
        assert records[1].description == "Support for cross-border cooperation between organisations."

    def test_daad_adapter(self, mock_daad_html: str) -> None:
        """DAAD adapter returns records from mock HTML."""
        from rastreador_convocatorias.spiders.adapters import parse_daad

        source = _make_source(name="DAAD - Becas")
        records = parse_daad(mock_daad_html, source)

        assert len(records) == 2
        assert "DAAD Scholarship" in records[0].title
        assert records[0].funding_amount
        assert records[1].title == "Helmut Schmidt Programme 2027"

    def test_mit_solve_adapter(self, mock_mit_html: str) -> None:
        """MIT Solve adapter returns records from mock HTML."""
        from rastreador_convocatorias.spiders.adapters import parse_mit_solve

        source = _make_source(name="MIT Solve - Challenges")
        records = parse_mit_solve(mock_mit_html, source)

        assert len(records) == 1
        assert records[0].title == "AI for Health Equity"
        assert records[0].funding_amount == "$1,000,000"

    def test_empty_html_returns_empty(self, empty_html: str) -> None:
        """All adapters handle empty HTML gracefully (return empty list)."""
        from rastreador_convocatorias.spiders.adapters import (
            parse_daad,
            parse_erasmus_plus,
            parse_horizon_europe,
            parse_mit_solve,
        )

        source = _make_source()
        for adapter in (parse_horizon_europe, parse_erasmus_plus, parse_mit_solve, parse_daad):
            records = adapter(empty_html, source)
            assert records == [], f"{adapter.__name__} should return [] for empty HTML"


# ── Adapter Registry Test ─────────────────────────────────────────────


def test_adapters_dict_registered() -> None:
    """All expected adapters are registered in the ADAPTERS dict."""
    from rastreador_convocatorias.spiders.adapters import ADAPTERS

    expected_keys = (
        "Horizon Europe - Funding & Tender Opportunities",
        "Erasmus+ - Opportunities",
        "MIT Solve - Challenges",
        "DAAD - Becas",
    )
    for key in expected_keys:
        assert key in ADAPTERS, f"Missing adapter registration for '{key}'"
        assert callable(ADAPTERS[key]), f"Adapter for '{key}' is not callable"


# ── Pipeline Tests ────────────────────────────────────────────────────


class TestPipeline:
    """End-to-end pipeline: validate → dedup → classify."""

    def test_full_pipeline_from_adapter_records(
        self, mock_horizon_html: str
    ) -> None:
        """Raw records from an adapter survive the full pipeline."""
        from rastreador_convocatorias.spiders.adapters import parse_horizon_europe

        source = _make_source(name="Horizon Europe - Funding & Tender Opportunities")
        raw = parse_horizon_europe(mock_horizon_html, source)
        assert len(raw) == 3

        # Validate
        ref_date = datetime(2026, 6, 16, tzinfo=UTC)
        validator = VigencyValidator(reference_date=ref_date)
        validated = validator.validate(raw)
        assert len(validated) == 3
        assert all(r.status == Status.vigente for r in validated)

        # Dedup
        deduper = Deduplicator(threshold=0.85)
        deduped = deduper.deduplicate(validated)
        assert len(deduped) == 3  # All different titles, no dedup

        # Classify
        classifier = Classifier(source_defaults={source.name: source.category_default})
        final = classifier.classify(deduped)
        assert len(final) == 3
        assert all(isinstance(r, FinalRecord) for r in final)

        # Each record has the basic fields
        for record in final:
            assert record.title
            assert record.source_url
            assert record.scraped_at
            assert record.status == Status.vigente

    def test_validation_classification_dedup_integration(
        self,
        vigente_record: Any,
        vencida_record: Any,
        requires_verification_record: Any,
    ) -> None:
        """Pipeline processes mixed-status records correctly."""
        # Use fixtures from conftest (injected via conftest.py)
        records = [vigente_record, vencida_record, requires_verification_record]

        # All enter as ValidatedRecord — pass through dedup
        deduper = Deduplicator(threshold=0.85)
        deduped = deduper.deduplicate(records)
        assert len(deduped) == 3

        # Classify
        classifier = Classifier()
        final = classifier.classify(deduped)
        assert len(final) == 3
        assert final[0].status == vigente_record.status
        assert final[1].status == vencida_record.status
        assert final[2].status == requires_verification_record.status

    def test_empty_pipeline(self) -> None:
        """Pipeline handles empty input gracefully."""
        validator = VigencyValidator()
        assert validator.validate([]) == []

        deduper = Deduplicator()
        assert deduper.deduplicate([]) == []

        classifier = Classifier()
        assert classifier.classify([]) == []


# ── Report Export Tests ───────────────────────────────────────────────


class TestExports:
    """HTML report and JSON export correctness."""

    def test_html_report_generates(
        self,
        sample_final_records: list[FinalRecord],
        tmp_output: Path,
    ) -> None:
        """HTML report renders without errors and produces a valid HTML file."""
        stats = {
            "total": len(sample_final_records),
            "colombia": 2,
            "international": 2,
            "vigentes": 2,
            "requires_verification": 1,
            "vencidas": 1,
            "with_errors": 0,
            "unique_sources": 2,
            "by_category": {"I+D+i": 1, "Formación": 1, "Investigación": 1},
            "by_country": {"Internacional": 2, "Colombia": 2},
            "by_status": {"vigente": 2, "vencida": 1, "requires_verification": 1},
            "by_funding_type": {"No reembolsable": 2, "Beca": 1},
        }
        errors: list[str] = []

        html_path = tmp_output / "report.html"
        generator = HTMLReportGenerator()
        generator.generate(sample_final_records, stats, errors, html_path)

        assert html_path.exists()
        content = html_path.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content or "<html" in content
        assert "AI for Health Equity" in content
        assert "Expired Grant 2025" in content
        assert "No Closing Date Entry" in content

    def test_html_empty_dataset(self, tmp_output: Path) -> None:
        """HTML report renders gracefully with zero records."""
        stats = {
            "total": 0,
            "colombia": 0,
            "international": 0,
            "vigentes": 0,
            "requires_verification": 0,
            "vencidas": 0,
            "with_errors": 0,
            "unique_sources": 0,
            "by_category": {},
            "by_country": {},
            "by_status": {},
            "by_funding_type": {},
        }
        html_path = tmp_output / "report.html"
        generator = HTMLReportGenerator()
        generator.generate([], stats, [], html_path)

        assert html_path.exists()
        content = html_path.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content or "<html" in content

    def test_json_exporter_writes_files(
        self,
        sample_final_records: list[FinalRecord],
        tmp_output: Path,
    ) -> None:
        """JSON exporter writes convocatorias.json, stats.json, metadata.json."""
        stats = {
            "total": len(sample_final_records),
            "colombia": 2,
            "international": 2,
            "vigentes": 2,
            "requires_verification": 1,
            "vencidas": 1,
            "with_errors": 0,
            "unique_sources": 2,
            "by_category": {"I+D+i": 1, "Formación": 1, "Investigación": 1},
            "by_country": {"Internacional": 2, "Colombia": 2},
            "by_status": {"vigente": 2, "vencida": 1, "requires_verification": 1},
            "by_funding_type": {"No reembolsable": 2, "Beca": 1},
        }
        errors: list[str] = []

        exporter = JSONExporter()
        exporter.export(sample_final_records, stats, errors, tmp_output)

        data_path = tmp_output / "data" / "convocatorias.json"
        stats_path = tmp_output / "data" / "stats.json"
        meta_path = tmp_output / "data" / "metadata.json"

        assert data_path.exists(), "convocatorias.json not written"
        assert stats_path.exists(), "stats.json not written"
        assert meta_path.exists(), "metadata.json not written"

        # Validate JSON content
        with data_path.open(encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, list)
        assert len(data) == 4

        with stats_path.open(encoding="utf-8") as f:
            s = json.load(f)
        assert s["total"] == 4

        with meta_path.open(encoding="utf-8") as f:
            meta = json.load(f)
        assert "version" in meta
        assert "generated_at" in meta
        assert meta["total_records"] == 4


# ── CLI Tests ─────────────────────────────────────────────────────────


class TestCLI:
    """CLI entry-point smoke tests (no network requests)."""

    def test_help_exits_zero(self) -> None:
        """``--help`` prints usage and exits with code 0."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0

    def test_version_exits_zero(self) -> None:
        """``--version`` prints version and exits with code 0."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])
        assert exc_info.value.code == 0

    def test_missing_sources_file_exits_critical(self, tmp_path: Path) -> None:
        """Non-existent sources file leads to exit code 2."""
        fake = tmp_path / "nonexistent.yaml"
        exit_code = main(["--sources", str(fake)])
        assert exit_code == 2

    def test_empty_sources_file_exits_critical(self, tmp_path: Path) -> None:
        """No valid sources leads to exit code 2."""
        yaml_path = tmp_path / "empty.yaml"
        yaml_path.write_text("sources: []", encoding="utf-8")
        exit_code = main(["--sources", str(yaml_path)])
        assert exit_code == 2

    def test_creates_output_dir(self, tmp_path: Path) -> None:
        """CLI creates the output directory when it doesn't exist."""
        out_dir = tmp_path / "custom-output"
        yaml_path = tmp_path / "test_sources.yaml"
        # Write a minimal valid source that won't actually fetch
        yaml_path.write_text(
            "sources:\n"
            "  - name: TestSource\n"
            "    url: https://example.com/convocatorias\n"
            "    country: Colombia\n"
            "    fetcher: http\n"
            "    selectors:\n"
            '      container: "div.test"\n'
            "      fields: {}\n"
            "    robots_txt_obey: false\n",
            encoding="utf-8",
        )
        # Since no real network, this will get no records → exit 2
        exit_code = main(["--sources", str(yaml_path), "--output-dir", str(out_dir)])
        assert exit_code == 2  # No records → critical
        # But the output dir should still exist
        assert out_dir.exists()


# ── Error Handling Tests ──────────────────────────────────────────────


class TestErrorHandling:
    """Graceful degradation on bad input."""

    def test_empty_html_adapter_error_handling(self, empty_html: str) -> None:
        """Adapters never crash on empty/unparseable HTML."""
        from rastreador_convocatorias.spiders.adapters import ADAPTERS

        source = _make_source()
        for name, adapter in ADAPTERS.items():
            records = adapter(empty_html, source)
            assert isinstance(records, list)
            # If the adapter's selectors don't match, we get an empty list
            # (not a crash). Some adapters might also crash inside Selector
            # on truly broken HTML, but they're wrapped in try/except.

    def test_missing_source_name_in_adapters(self) -> None:
        """Unknown source name in ADAPTERS lookup returns None."""
        from rastreador_convocatorias.spiders.adapters import ADAPTERS

        result = ADAPTERS.get("Nonexistent Source")
        assert result is None

    def test_adapter_without_url_resolution(self) -> None:
        """Adapter returns records even when source has no resolvable URL."""
        from rastreador_convocatorias.spiders.adapters import parse_daad

        html = """<html><body>
        <div class="scholarship-item">
            <h3><a href="/becas/local">Local Scholarship</a></h3>
            <p class="description">Local opportunity</p>
            <span class="deadline">31 Dec 2026</span>
        </div>
        </body></html>"""
        # Source with a custom URL to test relative resolution
        source = _make_source(url="https://daad.de/becas")
        records = parse_daad(html, source)
        assert len(records) == 1
        assert records[0].source_url == "https://daad.de/becas/local"
