# Tasks: Rastreador de Convocatorias

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~2000 |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (Foundation) → PR 2 (Core Pipeline) → PR 3 (Reporting + Sources) → PR 4 (CI/CD + Intl) |
| Delivery strategy | auto-forecast |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes (resolved)
Chain strategy: stacked-to-main
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Base | Est. Lines |
|------|------|-----------|------|------------|
| 1 | Project scaffold, models, config, registry, setup | PR 1 | main | ~350 |
| 2 | Spider base, pipeline stages (validator, dedup, classifier), unit tests | PR 2 | main | ~650 |
| 3 | Report generators, templates, adapters, main.py, +12 CO sources | PR 3 | main | ~550 |
| 4 | CI/CD workflow, +25 intl sources, custom adapters, smoke test | PR 4 | main | ~450 |

> **Note**: PR 2 (~650 lines) exceeds the 400-line review budget. Consider splitting PR 2 into 2a (spider base + validator) and 2b (dedup + classifier + tests) if strict 400-line adherence is required.

---

## Phase 1: Project Scaffold + Core Infrastructure

- [x] **1.1** Create `pyproject.toml` at `/home/julian/convocatorias/pyproject.toml` — project metadata, Python 3.12+, `[project.scripts]` entry, deps (scrapling, playwright, pandas, python-dateutil, rapidfuzz, jinja2, pyyaml). *Complexity: S. Dep: none. Verifiable: `pip install -e ".[dev]"` succeeds, `python -m rastreador_convocatorias --help` resolves.*
- [x] **1.2** Create package structure — `/home/julian/convocatorias/src/rastreador_convocatorias/__init__.py`, `tests/__init__.py`, `output/.gitkeep`. *Complexity: S. Dep: none. Verifiable: `import rastreador_convocatorias` succeeds.*
- [x] **1.3** Create models at `/home/julian/convocatorias/src/rastreador_convocatorias/models.py` — Pydantic `SourceConfig`, `RawRecord`, `ValidatedRecord`, `DedupedRecord`, `FinalRecord`, enums `FetcherType` (http/dynamic/stealthy), `Status` (vigente/vencida/requires-verification), `PaginationType`. *Complexity: M. Dep: 1.1. Verifiable: model instantiation validates required fields and rejects invalid enums.*
- [x] **1.4** Create `sources.yaml` at `/home/julian/convocatorias/sources.yaml` — 6 Colombian sources (Minciencias, iNNpulsa, MinTIC, SENA, Fondo Emprender, Colombia Productiva) with selectors, pagination, fetcher type, and category_default. *Complexity: M. Dep: 1.3. Verifiable: `registry.load_sources()` returns 6 valid `SourceConfig` objects.*
- [x] **1.5** Create registry at `/home/julian/convocatorias/src/rastreador_convocatorias/registry.py` — `load_sources(path)` reads YAML, validates with Pydantic, returns `list[SourceConfig]`. Logs warnings for invalid configs, raises error on unknown fetcher_type. *Complexity: M. Dep: 1.3, 1.4. Verifiable: loads valid config, skips invalid config with log, raises on bad fetcher_type.*
- [x] **1.6** Create setup script at `/home/julian/convocatorias/setup.sh` — venv creation, `pip install -e ".[dev]"`, `playwright install chromium`. *Complexity: S. Dep: 1.1. Verifiable: script runs to completion.*

## Phase 2: Spider + Pipeline Core

- [x] **2.1** Create spider base at `/home/julian/convocatorias/src/rastreador_convocatorias/spiders/base.py` — `BaseConvocatoriasSpider` with 3 Scrapling sessions (FetcherSession, AsyncDynamicSession, AsyncStealthySession), global concurrency of 5, max 2/domain, 1s domain delay. `crawl(source)` selects session by fetcher_type, fetches with timeout (30s http / 60s dynamic+stealthy), returns `list[RawRecord]`. *Complexity: XL. Dep: 1.3, 1.5. Verifiable: session selection matches fetcher_type, concurrency limits are respected, errors per source don't crash pipeline.*
- [x] **2.2** Add pagination support in spider — `page_param` (increment param), `next_link` (follow anchor), `infinite_scroll` (trigger load-more). Max 50 pages. *Complexity: M. Dep: 2.1. Verifiable: page_param stops after last page, next_link stops when no next anchor found.*
- [x] **2.3** Implement robots.txt check in spider — fetch and parse `robots.txt` per domain when `robots_txt_obey: true`, skip disallowed paths with log message. *Complexity: M. Dep: 2.1. Verifiable: source with disallowed path is skipped, allowed path proceeds.*
- [x] **2.4** Create validator at `/home/julian/convocatorias/src/rastreador_convocatorias/pipeline/validator.py` — `VigencyValidator.validate(records)` parses closing_date via python-dateutil (dayfirst=True, Spanish month lookup dict), compares to reference date (env `REFERENCE_DATE` or `datetime.now(UTC)`). Tags: vigente (future/permanent), vencida (past), requires-verification (unparseable/missing). *Complexity: M. Dep: 1.3. Verifiable: future date → vigente, past date → vencida, missing+not permanent → requires-verification, permanent → vigente.*
- [x] **2.5** Create deduplicator at `/home/julian/convocatorias/src/rastreador_convocatorias/pipeline/deduplicator.py` — `Deduplicator.deduplicate(records)` normalizes titles (lowercase, NFKD accent removal via unicodedata), pairwise compares with `rapidfuzz.fuzz.token_sort_ratio >= threshold` (env `DEDUP_THRESHOLD`, default 0.85), keeps winner (most populated fields, tie-break: latest closing_date). *Complexity: M. Dep: 1.3. Verifiable: identical titles merged, different titles kept, threshold configurable via env var.*
- [x] **2.6** Create classifier at `/home/julian/convocatorias/src/rastreador_convocatorias/pipeline/classifier.py` — `Classifier.classify(records)` matches module-level keyword dicts against title+description to populate category, sector, beneficiary_type, funding_type, tags. Falls back to `category_default` from source config if no match. Supports multiple categories per record. *Complexity: M. Dep: 1.3. Verifiable: keyword match populates category+beneficiary, no match falls back to default, multiple categories supported.*
- [x] **2.7** Write unit tests at `/home/julian/convocatorias/tests/` — `test_validator.py` (all date formats, Spanish months, missing dates, permanent), `test_deduplicator.py` (threshold, tie-breaking, accent normalization), `test_classifier.py` (keyword match, fallback, no match, multiple categories). *Complexity: M. Dep: 2.4, 2.5, 2.6. Verifiable: all spec scenarios pass.*

## Phase 3: Reporting + Main + Colombian Sources

- [x] **3.1** Create HTML generator at `/home/julian/convocatorias/src/rastreador_convocatorias/reporters/html_generator.py` — `HTMLReportGenerator.generate(records, stats, errors)` renders Jinja2 template with embedded CSS, Chart.js CDN, sections: stat cards (total/vigentes/vencidas/errores), charts (category bar, beneficiary pie, sector doughnut), filter controls, sortable results table, error sources table. *Complexity: L. Dep: 2.6. Verifiable: generates valid HTML file, renders with zero records gracefully, stat cards show correct counts.*
- [x] **3.2** Create JSON exporter at `/home/julian/convocatorias/src/rastreador_convocatorias/reporters/json_exporter.py` — `JSONExporter.export(records, stats, errors)` writes `data.json` (all records), `stats.json` (aggregate KPIs), `metadata.json` (crawl timestamp, source counts, error summary). *Complexity: S. Dep: 1.3. Verifiable: JSON files are valid, contain expected fields, serialize all records.*
- [x] **3.3** Create Jinja2 templates at `/home/julian/convocatorias/src/rastreador_convocatorias/reporters/templates/` — single `report.html.j2` template with client-side filtering (AND filter: country checkboxes, category/status dropdown, search text), responsive CSS grid, status badges (green vigente / yellow requires-verification / red vencida). *Complexity: M. Dep: 3.1. Verifiable: filters combine correctly, responsive breakpoints work, all status badges render.*
- [x] **3.4** Create adapters at `/home/julian/convocatorias/src/rastreador_convocatorias/spiders/adapters.py` — standalone `_parse_{source_name}` functions for custom sources, registered by convention (registry maps source.name → `_parse_{normalized_name}`). *Complexity: M. Dep: 2.1. Verifiable: adapter function is called when custom_parser=true, returns list[RawRecord].*
- [x] **3.5** Create entry point at `/home/julian/convocatorias/src/rastreador_convocatorias/main.py` — orchestration: `load_sources()` → `crawl_all()` → `pipeline.run()` → `reporters.export()`. Exit codes: 0 success, 1 partial failure, 2 critical. *Complexity: L. Dep: 2.1, 2.4, 2.5, 2.6, 3.1, 3.2. Verifiable: `python -m rastreador_convocatorias` exits 0 on success, generates output/ files, reports partial failures.*
- [x] **3.6** Add 12+ Colombian sources to `sources.yaml` — Bancóldex, Ruta N, CCB, Connect Bogotá, ProColombia, ICETEX, Fulbright Colombia, universities, gobernaciones (each with selectors and fetcher_type). *Complexity: M. Dep: 1.4, 2.1. Verifiable: `registry.load_sources()` returns all 18+ sources, each parses at least one record on crawl.*

## Phase 4: CI/CD + International Sources

- [x] **4.1** Create GitHub Actions workflow at `/home/julian/convocatorias/.github/workflows/crawl.yml` — daily cron `0 10 * * *`, workflow_dispatch, Python 3.12 with pip cache, Playwright browser cache, `python -m rastreador_convocatorias` with 9-min timeout (env `CRAWL_TIMEOUT_MINUTES`), upload-pages-artifact, deploy-pages. *Complexity: M. Dep: 3.5. Verifiable: workflow syntax validates, manual trigger runs crawl and deploys.*
- [x] **4.2** Add 25+ international sources to `sources.yaml` — Horizon Europe, EIC, Erasmus+, EIT, BID Lab, Banco Mundial, CAF, UNESCO, OEA, CYTED, DAAD, Chevening, Fulbright, MIT Solve, Google/MS/AWS for Startups, GIZ, USAID, Wellcome, Gates Foundation, TWAS, CLACSO, OECD, UN. *Complexity: M. Dep: 1.4, 2.1. Verifiable: all sources load without validation errors, each yields at least one record.*
- [x] **4.3** Implement custom adapters for non-standard sources — sources with unique HTML structures that can't use generic selector-based parsing. *Complexity: M. Dep: 3.4. Verifiable: each custom source produces records matching Convocatoria schema.*
- [x] **4.4** Write integration smoke test at `/home/julian/convocatorias/tests/test_smoke.py` — live crawl of 1 source (Minciencias) with mocked response or real HTTP, assert non-empty `RawRecord` list, verify end-to-end pipeline produces valid report. *Complexity: M. Dep: 3.5. Verifiable: test passes with mock fixture, reports valid JSON/HTML output.*
