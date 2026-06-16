"""Classifier — applies keyword-based classification tags to validated records.

``Classifier`` matches Spanish-language keyword dictionaries against each
record's ``title`` and ``description`` to populate ``category``,
``beneficiary_type``, ``sector``, ``funding_type``, and ``tags``.
"""

from __future__ import annotations

import logging
import unicodedata
from typing import Optional

from rastreador_convocatorias.models import FinalRecord, ValidatedRecord

logger = logging.getLogger(__name__)

# ── Keyword patterns ─────────────────────────────────────────────────────
# Each top-level key is the label assigned to the record.  The list values
# are keyword strings matched (case-insensitively) against title + description.

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "I+D+i": [
        "i+d+i",
        "i+d",
        "investigacion y desarrollo",
        "investigación y desarrollo",
        "ciencia",
        "tecnologia",
        "tecnología",
        "desarrollo tecnologico",
        "desarrollo tecnológico",
    ],
    "Innovación empresarial": [
        "innovacion empresarial",
        "innovación empresarial",
        "innovacion productiva",
        "innovación productiva",
        "empresa innovadora",
        "innovacion",
        "innovación",
    ],
    "Emprendimiento": [
        "emprendimiento",
        "emprendedor",
        "startup",
        "incubacion",
        "incubación",
        "aceleracion",
        "aceleración",
        "spin-off",
        "scaleup",
        "capital semilla",
        "semillero",
    ],
    "Formación": [
        "formacion",
        "formación",
        "capacitacion",
        "capacitación",
        "curso",
        "taller",
        "diplomado",
        "entrenamiento",
    ],
    "Becas": [
        "beca",
        "becario",
        "fellowship",
        "scholarship",
        "pasantia",
        "pasantía",
        "ayuda economica",
        "ayuda económica",
    ],
    "Investigación": [
        "investigacion",
        "investigación",
        "investigador",
        "proyecto de investigacion",
        "proyecto de investigación",
        "grupo de investigacion",
        "grupo de investigación",
        "ciencia basica",
        "ciencia básica",
    ],
    "Transferencia tecnológica": [
        "transferencia tecnologica",
        "transferencia tecnológica",
        "transferencia de tecnologia",
        "transferencia de tecnología",
        "licenciamiento",
        "patentamiento",
        "extension tecnolog",
        "extension tecnoló",
    ],
    "Propiedad intelectual": [
        "propiedad intelectual",
        "patente",
        "marca",
        "derecho de autor",
        "derechos de autor",
        "registro de marca",
        "know-how",
    ],
    "Cooperación internacional": [
        "cooperacion internacional",
        "cooperación internacional",
        "internacional",
        "bilateral",
        "multilateral",
        "cooperacion sur",
        "cooperación sur",
    ],
}

BENEFICIARY_KEYWORDS: dict[str, list[str]] = {
    "Startup": [
        "startup",
        "scaleup",
        "emprendimiento dinamico",
        "emprendimiento dinámico",
        "empresa de base tecnologica",
        "empresa de base tecnológica",
        "ebt",
    ],
    "PYME": [
        "pyme",
        "pequena empresa",
        "pequeña empresa",
        "mediana empresa",
        "mipyme",
        "microempresa",
        "pequeno empresario",
        "pequeño empresario",
    ],
    "Academia": [
        "universidad",
        "academia",
        "institucion educativa",
        "institución educativa",
        "centro de investigacion",
        "centro de investigación",
        "ies",
        "instituto universitario",
        "instituto tecnologico",
        "instituto tecnológico",
    ],
    "Investigador": [
        "investigador",
        "cientifico",
        "científico",
        "doctor",
        "phd",
        "grupo de investigacion",
        "grupo de investigación",
        "semillero de investigacion",
        "semillero de investigación",
    ],
    "Estudiante": [
        "estudiante",
        "alumno",
        "egresado",
        "posgrado",
        "maestria",
        "maestría",
        "doctorado",
        "pregrado",
    ],
    "Persona natural": [
        "persona natural",
        "individuo",
        "ciudadano",
        "persona juridica",
        "persona jurídica",
    ],
    "Gran empresa": [
        "gran empresa",
        "corporacion",
        "corporación",
        "empresa privada",
    ],
    "Entidad pública": [
        "entidad publica",
        "entidad pública",
        "gobierno",
        "alcaldia",
        "alcaldía",
        "gobernacion",
        "gobernación",
        "ministerio",
        "entidad territorial",
        "organismo publico",
        "organismo público",
    ],
}

SECTOR_KEYWORDS: dict[str, list[str]] = {
    "Bioeconomía": [
        "bioeconomia",
        "bioeconomía",
        "bioproducto",
        "biomaterial",
        "biocombustible",
    ],
    "Tecnologías de la información": [
        "tecnologias de la informacion",
        "tecnologías de la información",
        "ti",
        "software",
        "desarrollo de software",
        "tic",
        "transformacion digital",
        "transformación digital",
        "programacion",
        "programación",
    ],
    "Inteligencia Artificial": [
        "inteligencia artificial",
        "ia",
        "machine learning",
        "aprendizaje automatico",
        "aprendizaje automático",
        "deep learning",
        "redes neuronales",
    ],
    "Salud": [
        "salud",
        "medicina",
        "farmaceutica",
        "farmacéutica",
        "clinico",
        "clínico",
        "hospital",
        "hospitalario",
        "biomedicina",
    ],
    "Agroindustria": [
        "agroindustria",
        "agricultura",
        "agropecuario",
        "campo",
        "rural",
        "agro",
        "desarrollo rural",
        "seguridad alimentaria",
    ],
    "Energía": [
        "energia",
        "energía",
        "renovable",
        "transicion energetica",
        "transición energética",
        "eficiencia energetica",
        "eficiencia energética",
        "sostenible",
        "fotovoltaico",
        "eolico",
        "eólico",
    ],
    "Educación": [
        "educacion",
        "educación",
        "ensenanza",
        "enseñanza",
        "aprendizaje",
        "formacion academica",
        "formación académica",
        "eduacion superior",
    ],
    "Cambio climático": [
        "cambio climatico",
        "cambio climático",
        "clima",
        "ambiental",
        "medio ambiente",
        "sostenibilidad",
        "adaptacion",
        "adaptación",
        "mitigacion",
        "mitigación",
    ],
    "Biotecnología": [
        "biotecnologia",
        "biotecnología",
        "biologia molecular",
        "biología molecular",
        "genetica",
        "genética",
        "bioinformatica",
        "bioinformática",
        "bioprocesos",
    ],
    "Manufactura": [
        "manufactura",
        "industria 4.0",
        "produccion industrial",
        "producción industrial",
        "automatizacion",
        "automatización",
        "robotica",
        "robótica",
        "procesos industriales",
    ],
}

FUNDING_KEYWORDS: dict[str, list[str]] = {
    "No reembolsable": [
        "no reembolsable",
        "subsidio",
        "fondo no reembolsable",
        "cooperacion no reembolsable",
        "cooperación no reembolsable",
        "donacion",
        "donación",
    ],
    "Crédito": [
        "credito",
        "crédito",
        "prestamo",
        "préstamo",
        "crediticio",
        "linea de credito",
        "línea de crédito",
        "financiamiento",
        "fondo prestable",
    ],
    "Beca": [
        "beca",
        "becario",
        "fellowship",
        "scholarship",
        "asignacion",
        "asignación",
    ],
    "Cofinanciación": [
        "cofinanciacion",
        "cofinanciación",
        "cofinanciamiento",
        "contrapartida",
        "cofinanciado",
        "aporte",
    ],
    "Premio": [
        "premio",
        "reconocimiento",
        "concurso",
        "galardon",
        "galardón",
        "distincion",
        "distinción",
    ],
    "Inversión": [
        "inversion",
        "inversión",
        "capital",
        "venture capital",
        "capital semilla",
        "inversionista",
        "fondo de inversion",
        "fondo de inversión",
        "capital de riesgo",
    ],
}

# ── Helper functions ─────────────────────────────────────────────────────


def _strip_accents(text: str) -> str:
    """Remove Unicode accents (e.g. ``"é"`` → ``"e"``)."""
    nfkd = unicodedata.normalize("NFKD", text)
    return nfkd.encode("ascii", "ignore").decode("ascii")


def _match_keywords(
    text: str | None,
    keyword_map: dict[str, list[str]],
) -> list[str]:
    """Return labels whose keywords appear in *text* (case-insensitive).

    Parameters
    ----------
    text : str or None
        The text to search (typically ``title`` or ``description``).
    keyword_map : dict
        Mapping of label → list of keyword strings.

    Returns
    -------
    list of str
        Labels for which at least one keyword matched, in dict-insertion order.
    """
    if not text:
        return []

    # Normalise both text and keywords to ASCII so accent differences
    # (e.g. ``"tecnológica"`` vs ``"tecnologia"``) don't prevent matching.
    normalised = _strip_accents(text.lower())
    matched: list[str] = []

    for label, keywords in keyword_map.items():
        for kw in keywords:
            if _strip_accents(kw.lower()) in normalised:
                matched.append(label)
                break  # one keyword per label is enough

    return matched


def _collect_tags(
    matched_keywords: set[str],
    sector_labels: list[str],
    record: ValidatedRecord,
) -> list[str]:
    """Build a deduplicated, sorted tag list for a record."""
    tags: list[str] = []

    # Matched keyword strings (individual tokens that hit).
    for kw in sorted(matched_keywords):
        if kw not in tags:
            tags.append(kw)

    # Sector labels.
    for sl in sector_labels:
        label = sl.lower().replace(" ", "-")
        if label not in tags:
            tags.append(label)

    # Dates.
    if record.opening_date:
        tags.append(f"apertura: {record.opening_date}")
    if record.closing_date:
        tags.append(f"cierre: {record.closing_date}")

    return tags


def _extract_matched_keywords(
    text: str | None,
    keyword_map: dict[str, list[str]],
) -> set[str]:
    """Return all individual keyword strings found in *text*."""
    if not text:
        return set()

    normalised = _strip_accents(text.lower())
    found: set[str] = set()
    for _label, keywords in keyword_map.items():
        for kw in keywords:
            if _strip_accents(kw.lower()) in normalised:
                found.add(kw)
    return found


# ── Classifier ────────────────────────────────────────────────────────────


class Classifier:
    """Applies keyword-based classification to validated records.

    Parameters
    ----------
    source_defaults : dict of str → str, optional
        Mapping of ``source_name`` → ``category_default`` for records whose
        title/description match no known category keywords.
    """

    def __init__(
        self,
        source_defaults: dict[str, str] | None = None,
    ) -> None:
        self.source_defaults = source_defaults or {}

    def classify(self, records: list[ValidatedRecord]) -> list[FinalRecord]:
        """Classify a batch of validated records.

        Parameters
        ----------
        records : list of ValidatedRecord
            Records to classify.

        Returns
        -------
        list of FinalRecord
            Fully classified records.
        """
        return [self._classify_one(rec) for rec in records]

    def _classify_one(self, record: ValidatedRecord) -> FinalRecord:
        """Classify a single record."""
        title = record.title or ""
        desc = record.description or ""
        combined = f"{title} {desc}"

        # ── Category ────────────────────────────────────────────────────
        categories = _match_keywords(combined, CATEGORY_KEYWORDS)
        if not categories:
            fallback = self.source_defaults.get(record.source_name, "")
            if fallback:
                categories = [fallback]
        category_str = ", ".join(categories) if categories else ""

        # ── Beneficiary type ────────────────────────────────────────────
        beneficiary_type = _match_keywords(combined, BENEFICIARY_KEYWORDS)

        # ── Sector ──────────────────────────────────────────────────────
        sector = _match_keywords(combined, SECTOR_KEYWORDS)

        # ── Funding type ────────────────────────────────────────────────
        funding_type = _match_keywords(combined, FUNDING_KEYWORDS)

        # ── Tags ────────────────────────────────────────────────────────
        all_keywords: set[str] = set()
        for kw_map in (CATEGORY_KEYWORDS, BENEFICIARY_KEYWORDS,
                        SECTOR_KEYWORDS, FUNDING_KEYWORDS):
            all_keywords |= _extract_matched_keywords(combined, kw_map)

        tags = _collect_tags(all_keywords, sector, record)

        return FinalRecord(
            **record.model_dump(),
            category=category_str,
            beneficiary_type=beneficiary_type,
            sector=sector,
            funding_type=funding_type,
            tags=tags,
        )
