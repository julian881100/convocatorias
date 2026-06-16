# Specification: Rastreador de Convocatorias

## Purpose

Automated web scraping system that crawls 50+ Colombian and international open-call sources, extracts structured records, validates vigency, deduplicates across sources, classifies by category/sector/beneficiary, and publishes a daily interactive HTML report via GitHub Pages. Replaces manual spreadsheet tracking with a zero-ops daily pipeline.

---

## 1. Source Registry (source-registry)

### Requirement: Source configuration MUST be defined in YAML

Each source SHALL be a single YAML file under `sources/{country}/{source_name}.yaml`. The system MUST support the following configuration fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Human-readable source name |
| `url` | string | yes | Entry/list URL for convocatorias |
| `country` | string | yes | ISO 3166-1 alpha-2 or "international" |
| `fetcher_type` | enum | yes | `fetcher` \| `dynamic` \| `stealthy` |
| `pagination` | object | no | Pagination strategy (page_param, next_link, infinite_scroll) |
| `selectors` | object | yes | CSS/XPath selectors for extraction |
| `custom_parser` | string | no | Adapter module path if non-generic |
| `category_default` | string | no | Fallback category when classification yields none |
| `robots_txt_obey` | bool | no | Respect robots.txt (default: true) |

#### Scenario: Valid source config loads

- GIVEN a YAML file at `sources/co/minciencias.yaml` with all required fields
- WHEN the registry loads the source
- THEN the source is available for crawling with no validation errors

#### Scenario: Source config missing required field is rejected

- GIVEN a YAML file missing the `url` field
- WHEN the registry validates the config
- THEN the source is skipped and a warning is logged

#### Scenario: Unknown fetcher_type raises error

- GIVEN a source with `fetcher_type: "magic"`
- WHEN the registry parses the config
- THEN the system SHALL raise a configuration error

---

## 2. Source Crawler (source-crawler)

### Requirement: Spider MUST support multi-session fallback

The base spider SHALL attempt extraction using the configured fetcher type. If that fails (HTTP error, timeout, empty response), it MUST fall back through `dynamic` → `stealthy`. It SHALL support three session types: `FetcherSession` (http, static), `AsyncDynamicSession` (Playwright), `AsyncStealthySession` (stealth evasion).

#### Scenario: Static source returns HTML

- GIVEN a source configured with `fetcher: fetcher` and valid selectors
- WHEN the spider crawls the URL
- THEN it returns parsed records without falling back

#### Scenario: Static source fails, dynamic fallback succeeds

- GIVEN a source with `fetcher: fetcher` that returns a 403
- WHEN the spider attempts extraction
- THEN it falls back to `dynamic` session and returns parsed records

#### Scenario: All fetcher types fail

- GIVEN a source where all three fetcher types return errors
- WHEN the spider exhausts all sessions
- THEN the source is marked as failed and the error is logged

### Requirement: Concurrency MUST be bounded

The spider SHALL use a global concurrency of 5 simultaneous requests, with at most 2 concurrent requests per domain and a minimum 1-second delay between requests to the same domain.

#### Scenario: Multiple sources share a domain

- GIVEN 3 sources all pointing to `example.com`
- WHEN the spider crawls them concurrently
- THEN at most 2 requests to `example.com` are in-flight at any time

### Requirement: Pagination MUST be supported

Three pagination strategies SHALL be supported: `page_param` (incrementing URL param), `next_link` (follow next-page anchor), `infinite_scroll` (dynamic load-more trigger).

#### Scenario: Page-param pagination reaches last page

- GIVEN a source using `pagination.type: page_param` with 5 pages total
- WHEN the spider follows pages 1 through 5
- THEN it stops at page 5 and does not attempt page 6

#### Scenario: Next-link pagination

- GIVEN a source using `pagination.type: next_link` with a "Siguiente" anchor
- WHEN the spider follows the next link
- THEN it loads the next page until no next link is found

### Requirement: robots.txt MUST be respected

When `robots_txt_obey: true`, the spider SHALL fetch and parse `robots.txt` from each domain before crawling. It MUST NOT crawl disallowed paths.

#### Scenario: robots.txt forbids path

- GIVEN a source URL matching a disallowed path in `robots.txt`
- WHEN the spider begins crawling
- THEN the source is skipped with a logged message

---

## 3. Extraction Pipeline (extraction-pipeline)

### Requirement: Raw HTML MUST be parsed into Convocatoria records

The parser SHALL extract fields using CSS/XPath selectors defined in the source YAML. Fields SHALL be mapped to a `Convocatoria` Pydantic model.

The `Convocatoria` model:

| Field | Type | Required | Validation |
|-------|------|----------|------------|
| `title` | str | yes | Non-empty, trimmed |
| `description` | str | no | HTML-to-text conversion |
| `source_url` | str | yes | Valid URL format |
| `source_name` | str | yes | From source config name |
| `country` | str | yes | ISO alpha-2 or "international" |
| `category` | str | no | Classified, or from `category_default` |
| `subcategory` | str | no | Optional finer grain |
| `beneficiary_type` | list[str] | no | From classification |
| `sector` | list[str] | no | From classification |
| `opening_date` | date | no | Parsed via python-dateutil |
| `closing_date` | date | no | Parsed via python-dateutil |
| `is_permanent` | bool | no | Default false |
| `funding_amount` | str | no | Raw string (e.g. "$50,000 USD") |
| `status` | enum | yes | `vigente` \| `requires-verification` \| `vencida` |
| `official_body` | str | no | Extracted or inherited from source |
| `funding_type` | list[str] | no | From classification |
| `tags` | list[str] | no | From classification + source defaults |
| `scraped_at` | datetime | yes | UTC timestamp of crawl |

#### Scenario: All selectors produce data

- GIVEN a source with selectors mapping title, url, opening_date, closing_date
- WHEN the parser processes the HTML
- THEN each field is populated, `scraped_at` is set

#### Scenario: Selector returns no match

- GIVEN a source whose `closing_date` selector matches nothing
- WHEN the parser extracts the record
- THEN `closing_date` is None, `status` defaults to `requires-verification`

#### Scenario: Date parsing fails

- GIVEN a source with `opening_date` text "mañana" (not parseable)
- WHEN the parser attempts date extraction
- THEN `opening_date` is None, the record is logged as parse-warning

#### Scenario: Funding amount is non-empty

- GIVEN a source whose `funding_amount` selector matches "$100.000.000 COP"
- WHEN the parser extracts the field
- THEN `funding_amount` is the raw string "$100.000.000 COP"

---

## 4. Validity Checker (validity-checker)

### Requirement: Status MUST be determined by date comparison

The cutoff date SHALL be the crawl date. Status SHALL be computed as:

- `vigente`: `closing_date` is None (permanent) OR `closing_date` >= crawl date
- `vencida`: `closing_date` < crawl date
- `requires-verification`: No closing_date info AND `is_permanent` is false

#### Scenario: Record with future closing date

- GIVEN a record with `closing_date = 2026-08-15` and crawl date `2026-06-16`
- WHEN the validator runs
- THEN status is `vigente`

#### Scenario: Record with past closing date

- GIVEN a record with `closing_date = 2026-05-01` and crawl date `2026-06-16`
- WHEN the validator runs
- THEN status is `vencida`

#### Scenario: Permanent record

- GIVEN a record with `is_permanent = true` and no closing_date
- WHEN the validator runs
- THEN status is `vigente`

#### Scenario: No dates and not permanent

- GIVEN a record with no `opening_date`, no `closing_date`, and `is_permanent = false`
- WHEN the validator runs
- THEN status is `requires-verification`

---

## 5. Deduplication Engine (deduplication-engine)

### Requirement: Duplicates MUST be detected via fuzzy title match

After validation, the system SHALL compare records pairwise. Two records SHALL be considered duplicates if `rapidfuzz.fuzz.token_sort_ratio(title_a, title_b) >= 85` AND `source_name_a == source_name_b`.

#### Scenario: Two identical titles from same source

- GIVEN records "Convocatoria Innovación 2026" and "Convocatoria Innovación 2026" from the same source
- WHEN dedup runs
- THEN only the first record (by scraped_at) is kept

#### Scenario: Near-identical titles from same source

- GIVEN records "Becas para Innovación 2026" and "Becas para Innovación 2026 - Colombia"
- WHEN dedup runs
- THEN the fuzzy score >= 85 and they are deduplicated

#### Scenario: Different titles from same source

- GIVEN records "Fondo de Becas" and "Crédito Educativo" from the same source
- WHEN dedup runs
- THEN both records are kept

### Requirement: dedup threshold MUST be configurable

The threshold of 85 SHALL be overridable via config or environment variable `DEDUP_THRESHOLD`.

#### Scenario: Lower threshold

- GIVEN `DEDUP_THRESHOLD=70` and two records with score 80
- WHEN dedup runs
- THEN they are considered duplicates

---

## 6. Classification Tagger (classification-tagger)

### Requirement: Records MUST be tagged by keyword rules

The system SHALL apply keyword pattern matching against title and description to populate `category`, `beneficiary_type`, `sector`, `funding_type`, and `tags`.

Rules SHALL be defined in `etc/classification_rules.yaml` as:

```yaml
category:
  becas:
    keywords: [beca, becario, fellowship, scholarship]
    sector: [educación]
    beneficiary_type: [individuo]
  emprendimiento:
    keywords: [emprendedor, startup, incubación, aceleración]
    sector: [economía, innovación]
    beneficiary_type: [empresa, persona_jurídica]
funding_type:
  crédito:
    keywords: [crédito, préstamo, crediticio]
  subsidio:
    keywords: [subsidio, fondo no reembolsable, grant]
```

#### Scenario: Title matches category keyword

- GIVEN a record with title "Beca para estudios de posgrado en el exterior"
- WHEN classification runs
- THEN `category` includes "becas", `beneficiary_type` includes "individuo"

#### Scenario: Description matches, title does not

- GIVEN a record with title "Convocatoria abierta" and description "Fondo para emprendedores tecnológicos"
- WHEN classification runs
- THEN the description SHALL also be matched; `category` includes "emprendimiento"

#### Scenario: No keywords match

- GIVEN a record whose title and description contain no known keywords
- WHEN classification runs
- THEN `category` falls back to `category_default` from source config, if set

---

## 7. Report Generator (report-generator)

### Requirement: Report MUST be a single-file HTML

The report SHALL embed all content in one HTML file with no build step. Chart.js SHALL be loaded from CDN. It MUST include:

- **Stat cards**: total records, Colombia count, international count, vigentes count
- **Charts**: pie chart by category, bar chart by country
- **Card grid**: filterable by country (checkboxes), category (dropdown), status (vigente/vencida/verificar), and search text
- **Data table**: sortable columns
- **Status badges**: green (vigente), yellow (requires-verification), red (vencida)
- **Responsive CSS**: works on mobile and desktop

#### Scenario: Empty dataset

- GIVEN zero records after dedup and validation
- WHEN the report generator runs
- THEN it SHALL produce a valid HTML file with stat cards showing zeros and empty charts

#### Scenario: Maximum records

- GIVEN 200+ records across 10 categories and 20 countries
- WHEN the report generator runs
- THEN all charts render, the grid filters correctly, and the single-file size is under 5 MB

### Requirement: Filters MUST interact

Country checkboxes, category dropdown, status dropdown, and search text SHALL combine as an AND filter. Changing any filter SHALL update cards and table immediately (client-side JavaScript, no page reload).

#### Scenario: Country + status filter

- GIVEN records from Colombia, Spain, and Mexico; some vigentes, some vencidas
- WHEN user checks "Colombia" and selects status "vigente"
- THEN only Colombian vigente records are shown

#### Scenario: Search text filter

- GIVEN records with titles containing "becas", "fondo", "crédito"
- WHEN user types "beca" in the search box
- THEN only records with "beca" in title, description, or tags are shown

---

## 8. GitHub Actions Workflow

### Requirement: Workflow MUST run daily on cron

A GitHub Actions workflow SHALL trigger at `0 6 * * *` daily on the `main` branch using `ubuntu-latest` and Python 3.11.

### Requirement: Workflow MUST deploy to GitHub Pages

Steps:

1. Checkout repository
2. Setup Python 3.11 with caching
3. `pip install` dependencies
4. Cache Playwright Chromium browsers
5. `scrapling install`
6. Run `python src/main.py`
7. Upload `output/` as pages artifact
8. Deploy to `github-pages` environment

#### Scenario: Successful crawl and deploy

- GIVEN all sources respond and parsing succeeds
- WHEN the workflow runs
- THEN the report is uploaded and deployed to Pages within 10 minutes

#### Scenario: Partial source failure

- GIVEN 3 of 50 sources time out
- WHEN the workflow runs
- THEN the report is still generated from the 47 successful sources and deployed

#### Scenario: All sources fail

- GIVEN all sources return errors
- WHEN the workflow runs
- THEN the workflow SHALL fail with an error and no report is deployed

---

## Acceptance Criteria

### Batch 1 (Scaffold + 6 Colombian Sources)

- `source-crawler`, `extraction-pipeline`, `validity-checker`, `deduplication-engine`, `classification-tagger`, `report-generator` are implemented
- 6 sources (Minciencias, iNNpulsa, MinTIC, SENA, Fondo Emprender, Colombia Productiva) parse correctly
- Dedup removes identical records across those sources
- HTML report generates with all sections

### Batch 2 (Remaining Colombia)

- 12+ additional Colombian sources are added (Bancóldex, Ruta N, CCB, Connect Bogotá, ProColombia, ICETEX, Fulbright Colombia, universities, gobernaciones)
- All previous tests still pass (no regressions)

### Batch 3 (International)

- 25+ international sources added (Horizon Europe, EIC, Erasmus+, EIT, BID Lab, Banco Mundial, CAF, UNESCO, OEA, CYTED, DAAD, Chevening, Fulbright, MIT Solve, Google for Startups, Microsoft for Startups, AWS Activate, GIZ, USAID, Wellcome, Gates Foundation, TWAS, CLACSO, OECD, UN)
- Each source yields at least one record

### Batch 4 (Complex Adapters + GHA)

- Custom adapters for sources with non-standard HTML structure
- GitHub Actions workflow is configured, tested, and Pages deploys successfully
- End-to-end run completes within 10 minutes
