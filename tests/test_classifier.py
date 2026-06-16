"""Tests for ``Classifier``.

Covers all scenarios from the spec's **Classification Tagger** section.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rastreador_convocatorias.models import Status, ValidatedRecord
from rastreador_convocatorias.pipeline.classifier import Classifier


def _make_record(
    title: str,
    description: str = "",
    source_name: str = "Minciencias",
    closing_date: str | None = "2026-08-15",
) -> ValidatedRecord:
    """Factory helper for classifier test records."""
    return ValidatedRecord(
        title=title,
        description=description,
        source_url=f"https://example.com/{title.replace(' ', '-')}",
        source_name=source_name,
        country="Colombia",
        closing_date=closing_date,
        scraped_at=datetime(2026, 6, 16, tzinfo=UTC),
        status=Status.vigente,
    )


class TestClassifierCategory:
    """Category matching scenarios."""

    def test_keyword_match_populates_category(self) -> None:
        """Scenario: Title matches category keyword."""
        classifier = Classifier()
        records = [
            _make_record("Beca para estudios de posgrado en el exterior"),
        ]
        result = classifier.classify(records)
        assert "Becas" in result[0].category

    def test_no_match_falls_back_to_category_default(self) -> None:
        """Scenario: No keywords match → category_default from source config."""
        classifier = Classifier(source_defaults={"Minciencias": "Investigación"})
        records = [
            _make_record(
                "Aviso importante sobre tramites administrativos",
                description="Procedimiento interno de la entidad",
            ),
        ]
        result = classifier.classify(records)
        assert result[0].category == "Investigación"

    def test_no_match_no_default_returns_empty(self) -> None:
        """No match and no category_default → empty category string."""
        classifier = Classifier()
        records = [
            _make_record(
                "Aviso importante sobre tramites administrativos",
            ),
        ]
        result = classifier.classify(records)
        assert result[0].category == ""

    def test_multiple_categories_matched(self) -> None:
        """Scenario: Multiple category keywords match → comma-separated."""
        classifier = Classifier()
        records = [
            _make_record(
                "Beca de investigación en emprendimiento tecnológico",
                description="Startup innovadora en el sector salud",
            ),
        ]
        result = classifier.classify(records)
        # Should match "Becas", "Investigación", "Emprendimiento", "Innovación empresarial"
        categories = result[0].category.split(", ")
        assert "Becas" in categories
        assert "Investigación" in categories
        assert "Emprendimiento" in categories

    def test_description_matched_when_title_not(self) -> None:
        """Scenario: Description contains keywords, title does not."""
        classifier = Classifier()
        records = [
            _make_record(
                "Convocatoria abierta",
                description="Programa de incubación para startups con base i+d",
            ),
        ]
        result = classifier.classify(records)
        # "incubación" should match "Emprendimiento"
        assert "Emprendimiento" in result[0].category
        # "i+d" should match "I+D+i"
        assert "I+D+i" in result[0].category


class TestClassifierBeneficiaryAndSector:
    """Beneficiary type and sector matching."""

    def test_beneficiary_type_matched(self) -> None:
        """Beneficiary type keywords populate the field."""
        classifier = Classifier()
        records = [
            _make_record(
                "Programa de apoyo a startups y pymes tecnológicas",
                description="Dirigido a pequeñas empresas innovadoras",
            ),
        ]
        result = classifier.classify(records)
        assert "Startup" in result[0].beneficiary_type
        assert "PYME" in result[0].beneficiary_type

    def test_sector_matched(self) -> None:
        """Sector keywords populate the field."""
        classifier = Classifier()
        records = [
            _make_record(
                "Convocatoria en inteligencia artificial y salud",
                description="Proyectos de machine learning para diagnóstico clínico",
            ),
        ]
        result = classifier.classify(records)
        assert "Inteligencia Artificial" in result[0].sector
        assert "Salud" in result[0].sector

    def test_funding_type_matched(self) -> None:
        """Funding type keywords populate the field."""
        classifier = Classifier()
        records = [
            _make_record(
                "Crédito educativo para formación en el exterior",
                description="Préstamo con condiciones preferenciales",
            ),
        ]
        result = classifier.classify(records)
        assert "Crédito" in result[0].funding_type


class TestClassifierTags:
    """Tag generation."""

    def test_tags_include_matched_keywords(self) -> None:
        """Tags contain the individual keyword strings that matched."""
        classifier = Classifier()
        records = [
            _make_record(
                "Beca de formación en biotecnología",
                description="Curso avanzado para investigadores",
            ),
        ]
        result = classifier.classify(records)
        tags = result[0].tags
        # Should contain matched keywords
        assert "beca" in tags or any("beca" in t for t in tags)
        assert "formacion" in tags or "formación" in tags

    def test_tags_include_sector_and_dates(self) -> None:
        """Tags include sector labels and formatted dates."""
        classifier = Classifier()
        records = [
            _make_record(
                "Formación en salud digital",
                description="Curso de tecnologías para la salud",
                closing_date="2026-09-30",
            ),
        ]
        result = classifier.classify(records)
        tags = result[0].tags
        assert "salud" in tags  # sector label
        assert any("cierre:" in t for t in tags)  # date tag

    def test_tags_deduplicated(self) -> None:
        """Tags should not contain duplicate entries."""
        classifier = Classifier()
        records = [
            _make_record(
                "Beca de investigación",
                description="Beca para investigador",
            ),
        ]
        result = classifier.classify(records)
        tags = result[0].tags
        assert len(tags) == len(set(tags)), f"Duplicated tags: {tags}"


class TestClassifierEdgeCases:
    """Edge cases for the classifier."""

    def test_empty_title_and_description(self) -> None:
        """Empty title and description → no classification."""
        classifier = Classifier()
        records = [
            _make_record("", description=""),
        ]
        result = classifier.classify(records)
        assert result[0].category == ""
        assert result[0].beneficiary_type == []
        assert result[0].sector == []

    def test_none_title(self) -> None:
        """None title should not crash."""
        classifier = Classifier()
        records = [
            ValidatedRecord(
                title=None,  # type: ignore[typeddict-item]
                description="Some text about innovación",
                source_name="Minciencias",
                source_url="https://example.com/x",
                scraped_at=datetime(2026, 6, 16, tzinfo=UTC),
                status=Status.vigente,
            ),
        ]
        result = classifier.classify(records)
        assert "Innovación empresarial" in result[0].category

    def test_batch_classification(self) -> None:
        """Multiple records are classified independently."""
        classifier = Classifier()
        records = [
            _make_record("Beca de formación en IA", ""),
            _make_record("Crédito para PYME", ""),
            _make_record("Premio de innovación", ""),
        ]
        result = classifier.classify(records)
        assert len(result) == 3
        assert "Becas" in result[0].category
        assert "Crédito" in result[2].funding_type or "Crédito" in result[1].funding_type
