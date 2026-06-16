# Design: rastreador-convocatorias

## Technical Approach

Layered pipeline: YAML source config → spider orchestration (Scrapling multi-session) → extraction → validation → dedup (rapidfuzz) → classification → HTML/JSON export. Each stage is isolated; source failures never crash the pipeline. Stages communicate through Pydantic models, keeping type safety at boundaries. Daily GHA cron triggers full crawl, output deploys to `gh-pages`.

## Architecture Decisions

| Decision | Options | Choice | Rationale |
|---|---|---|---|
| Spider sessions per source | Single vs triple (http/dynamic/stealth) | Triple (all 3 initialized per spider) | Parser selects session at fetch time; same page may need different sessions for sub-fetches |
| Data models | dataclass vs dict vs Pydantic | Pydantic BaseModel | Strict type validation at pipeline boundaries; catches schema drift early |
| Dedup algorithm | exact-match vs rapidfuzz | rapidfuzz `token_sort_ratio` | Handles word-order variation across sources (e.g. "Becas Fulbright 2026" vs "Fulbright 2026 Becas") |
| Report output | Multi-file (HTML+CSS+JS) vs single HTML | Single HTML (embedded CSS + Chart.js CDN) | Zero-ops deploy; no build step, no CORS, no npm |
| Source config format | JSON vs TOML vs YAML | YAML | Comments supported; human-writable; no schema tooling required |
| Error isolation | try/except per record vs per source | Per source (entire crawl for that source) | Simpler logging; one broken page kills that source's records but not other sources |
| Storage | SQLite vs JSON vs CSV | JSON snapshot only | No history tracking (MVP); avoids schema migrations; trivial to inspect |

## Data Flow

```
sources.yaml ──→ registry.py ──→ spiders/base.py ──→ pipeline/
                     │          (3 sessions each)      validator.py
                     │                                 deduplicator.py
                     │                                 classifier.py
                     └── adapters.py                        │
                         (custom_parser=True)               ▼
                                                     reporters/ ──→ output/
                                                     html_generator.py   report.html
                                                     json_exporter.py    data.json + metadata.json
```

## Module Design — Public API

### `src/rastreador_convocatorias/main.py`
CLI entry (`python -m rastreador_convocatorias`). Orchestrates: `load_sources()` → `crawl_all()` → `pipeline.run()` → `reporters.export()`. Exit codes: 0 = success, 1 = partial (some sources failed), 2 = critical.

### `src/rastreador_convocatorias/registry.py`
`load_sources(path: Path) -> list[SourceConfig]` — reads YAML, validates with Pydantic, resolves spider class and adapter function per source. Returns typed configs ready for dispatch.

### `src/rastreador_convocatorias/spiders/base.py`
`BaseConvocatoriasSpider` — initializes 3 Scrapling sessions (FetcherSession, AsyncDynamicSession, AsyncStealthySession) on construction. `crawl(source: SourceConfig) -> list[RawRecord]`: opens session per `fetcher` field, fetches page(s) respecting pagination config (`query_param` or `custom`), parses via YAML selectors using Scrapling CSS/XPath extractors. Returns list of `RawRecord` Pydantic models.

### `src/rastreador_convocatorias/spiders/adapters.py`
Standalone `_parse_{source_name}(response, source) -> list[RawRecord]` functions for sources with `custom_parser: true`. Each handles site-specific extraction logic. Registered by convention: registry maps `source.name` → `_parse_{normalized_name}`.

### `src/rastreador_convocatorias/pipeline/validator.py`
`VigencyValidator.validate(records) -> list[ValidatedRecord]`. Parses `closing_date` with `python-dateutil` (handles "15 de junio de 2026", "2026-06-15", "15/06/2026", etc.). Tags each record: `vigente` (future), `vencida` (past), `requires-verification` (parse failed or missing). Reference date: 2026-06-16 (configurable).

### `src/rastreador_convocatorias/pipeline/deduplicator.py`
`Deduplicator.deduplicate(records) -> list[DedupedRecord]`. Normalizes titles (lowercase, strip accents via `unicodedata`), groups by `token_sort_ratio >= threshold` (default 0.85). Within each group, keeps the record with most populated fields. Reports dropped duplicates for the stats section.

### `src/rastreador_convocatorias/pipeline/classifier.py`
`Classifier.classify(records) -> list[FinalRecord]`. Keyword-pattern matching against static mapping tables (category → keywords, sector → keywords, beneficiary → keywords). Mapping tables live in `classifier.py` as module-level dicts (extractable to YAML later). Supports multiple categories per record (e.g. "Investigación + Educación").

### `src/rastreador_convocatorias/reporters/html_generator.py`
`HTMLReportGenerator.generate(records, stats, errors, output_path)`. Renders a single Jinja2 HTML template with embedded `<style>` and `<script>`. Template includes Chart.js from CDN (`<script src="https://cdn.jsdelivr.net/npm/chart.js">`). Sections: summary KPIs, category breakdown (bar chart), beneficiary pie, sector chart, source table, full record list with client-side filtering (vanilla JS), error sources section.

### `src/rastreador_convocatorias/reporters/json_exporter.py`
`JSONExporter.export(records, stats, errors, output_path)`. Writes `data.json` (all records), `stats.json` (aggregate KPIs for chart rendering), `metadata.json` (crawl timestamp, source counts, error summary). Used by the HTML template for offline-friendly data access.

## Source Config YAML Schema

```yaml
sources:
  - name: "Minciencias - Convocatorias"         # Display name
    url: "https://minciencias.gov.co/convocatorias"  # Entry page
    country: "Colombia"                          # Filter/tag
    fetcher: "stealthy"                          # session: http|dynamic|stealthy
    pagination:                                  # optional
      type: "query_param"                        # query_param|custom
      param: "page"
      start: 0
    selectors:                                   # Parsing rules
      container: "div.view-content div.views-row"
      fields:
        title: "h2 a::text"
        url: "h2 a::attr(href)"
        description: "div.field-content p::text"
        closing_date: "span.date-display-single::text"
    robots_txt_obey: true
    category_default: "Investigación"
    custom_parser: false                         # true → routes to adapters.py
```

## Spider Design

`BaseConvocatoriasSpider` creates 3 sessions in `__init__`:
- `self.http`: `FetcherSession` — fast, for simple HTML sources
- `self.dynamic`: `AsyncDynamicSession` — JS-rendered content
- `self.stealth`: `AsyncStealthySession` — anti-bot protected sites

`crawl()` flow:
1. Select session by `source.fetcher`
2. Fetch page(s) with timeout (30s http, 60s dynamic/stealthy)
3. If `paginate`, loop through pages until empty or max 50 pages
4. Select container elements, extract field values per selectors
5. If `custom_parser`, delegate to adapter function in `adapters.py`
6. Wrap each extracted item in `RawRecord` model
7. On error: log source name + exception → return empty list → continue

Pagination handler: generic for `query_param` (appends `?page=N`), `custom` delegates to adapter. Source-level timeout is enforced per fetch.

## Pipeline Design

Pipeline stages run sequentially, each with typed input/output:

```
RawRecord ──→ ValidatedRecord ──→ DedupedRecord ──→ FinalRecord
  validator.py    deduplicator.py    classifier.py
```

Each stage logs counts + stats. Pipeline can return partial results if a stage encounters non-fatal errors.

### Validator details
- Reference date: `2026-06-16` (from `datetime.now(UTC)` or env `REFERENCE_DATE`)
- `python-dateutil.parser.parse()` with `dayfirst=True` for DD/MM/YYYY formats
- Spanish month names mapped via lookup dict: `{"enero": 1, "febrero": 2, ...}`
- If closing_date is missing or unparseable → `requires-verification`
- If closing_date < ref_date → `vencida`
- If closing_date >= ref_date → `vigente`

### Deduplicator details
- Normalize: `unicodedata.normalize('NFKD').encode('ascii', 'ignore')` for accent removal
- Group: compare every pair with `fuzz.token_sort_ratio(a, b) >= 0.85`
- Winner selection: count populated fields, pick max. Tie-break: most recent closing_date
- Output stats: total before, groups merged, total after

### Classifier details
- Mapping tables in module-level dicts:
  ```python
  CATEGORY_KEYWORDS = {
      "Investigación": ["investigación", "ciencia", "i+d", "innovación"],
      "Educación": ["beca", "formación", "maestría", "doctorado", "posgrado"],
      "Emprendimiento": ["emprendimiento", "startup", "spin-off", "scaleup"],
  }
  ```
- Each record can match multiple categories
- If no match → `category_default` from source config

## Report Template Design

- Single HTML file: 1 `<style>` block, vanilla JS at bottom, Chart.js from CDN
- Layout: CSS Grid (sidebar filters + main content area)
- Sections:
  1. **Header**: crawl timestamp, total records, vigentes count
  2. **KPIs**: 4 stat cards (Total, Vigentes, Vencidas, Fuentes con error)
  3. **Charts row**: Category bar chart, Beneficiary pie, Sector doughnut
  4. **Filter controls**: checkboxes for category/beneficiary/sector/status
  5. **Error sources table**: source name + error type + timestamp
  6. **Results table**: scrollable, sortable by column click. Columns: título, fuente, categoría, sector, beneficiario, cierre, estado
- Client-side filtering: `input` event on filter checkboxes → hide/show rows via CSS class toggle
- No build step, no npm, no bundler. One file, open directly.

## Error Handling Strategy

| Failure point | Behaviour | Logging |
|---|---|---|
| Source fetch timeout (30s/60s) | Log error → skip source | `output/logs/{source_name}_timeout.log` |
| Selector not found (empty parse) | Log warning → return empty from that source | `output/logs/{source_name}_parse_warn.log` |
| Date parsing failure per record | Tag `requires-verification` → continue | No per-record log; count in stats |
| Dedup internal error | Log error → pass records through unchanged | `output/logs/dedup_error.log` |
| Report generation failure | Abort report, fall back to JSON-only export | `output/logs/report_error.log` |
| Pipeline stage total failure | Each stage catches, logs, and passes empty list | `output/logs/pipeline.log` |

GHA runner: if total run exceeds 9 min, remaining sources are skipped (logged). Next run picks them up fresh.

## GitHub Actions Design

```yaml
# .github/workflows/crawl.yml
name: Daily Crawl
on:
  schedule:
    - cron: "0 10 * * *"       # 5 AM Colombia time
  workflow_dispatch:            # manual trigger

jobs:
  crawl:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: "pip"
      - name: Cache Playwright browsers
        uses: actions/cache@v4
        with:
          path: ~/.cache/ms-playwright
          key: playwright-${{ runner.os }}
      - run: pip install -e ".[dev]"
      - run: playwright install chromium
      - name: Run crawl
        run: python -m rastreador_convocatorias
        env:
          REFERENCE_DATE: ""
          CRAWL_TIMEOUT_MINUTES: "9"
      - name: Upload report
        uses: actions/upload-pages-artifact@v3
        with:
          path: output/
  deploy:
    needs: crawl
    permissions:
      pages: write
      id-token: write
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/deploy-pages@v4
```

Caching strategy: `pip` cache via `setup-python` native; Playwright browsers via manual `actions/cache` with key `playwright-${{ runner.os }}` (browsers are ~300MB, cache hit avoids reinstall). Timeout at 9 min via env var (GHA max is 6h for free, but target <10 min).

## File Changes (All New — Greenfield)

| File | Description |
|---|---|
| `pyproject.toml` | Project metadata, Python 3.12, deps, `[project.scripts]` entry |
| `src/rastreador_convocatorias/__init__.py` | Package init |
| `src/rastreador_convocatorias/main.py` | CLI entry + orchestration |
| `src/rastreador_convocatorias/registry.py` | YAML loader + config resolution |
| `src/rastreador_convocatorias/spiders/__init__.py` | Package |
| `src/rastreador_convocatorias/spiders/base.py` | `BaseConvocatoriasSpider` |
| `src/rastreador_convocatorias/spiders/adapters.py` | Custom parser functions |
| `src/rastreador_convocatorias/pipeline/__init__.py` | Package |
| `src/rastreador_convocatorias/pipeline/validator.py` | `VigencyValidator` |
| `src/rastreador_convocatorias/pipeline/deduplicator.py` | `Deduplicator` |
| `src/rastreador_convocatorias/pipeline/classifier.py` | `Classifier` |
| `src/rastreador_convocatorias/reporters/__init__.py` | Package |
| `src/rastreador_convocatorias/reporters/html_generator.py` | `HTMLReportGenerator` |
| `src/rastreador_convocatorias/reporters/json_exporter.py` | `JSONExporter` |
| `sources.yaml` | Source definitions (50+ entries, batched) |
| `.github/workflows/crawl.yml` | Daily cron + manual dispatch + Pages deploy |
| `output/` | Runtime dir (reports + logs) — `.gitkeep` in repo |
| `tests/` | Test suite per module |

## Testing Strategy

| Layer | What | How |
|---|---|---|
| Unit | Each pipeline stage in isolation | Fixture JSON → assert output shape + counts |
| Unit | SourceConfig loading | YAML fixtures → Pydantic validation |
| Unit | Date parsing edge cases | Date strings in all expected formats → assert correct tag |
| Unit | Dedup thresholds | Known-duplicate pairs → assert merge behaviour |
| Integration | Spider → full pipeline | Mock Scrapling response object → assert end-to-end record shape |
| Smoke | Live source fetch (CI) | Single source (Minciencias) live crawl → assert non-empty output |

## Migration / Rollout

No migration — greenfield. Rollout per proposal batches:
1. **Batch 1**: 6 Colombian sources + core pipeline + basic HTML report
2. **Batch 2**: +12 Colombian sources + adapter patterns
3. **Batch 3**: +25 international sources
4. **Batch 4**: GHA workflow + Pages deploy + polish

Each batch is additive: add `sources.yaml` entries + adapters if needed. GHA workflow ships in Batch 4 but can be tested manually earlier.

## Open Questions

- [ ] Scrapling `crawldir` checkpoint: confirm it supports pause/resume across GHA runs (for timeout recovery)
- [ ] Playwright browser cache key: need exact `~/.cache/ms-playwright` path for GHA runner
