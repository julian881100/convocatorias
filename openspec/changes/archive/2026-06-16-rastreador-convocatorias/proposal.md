# Proposal: rastreador-convocatorias

## Intent

Build an automated web scraping system that tracks open calls (convocatorias) from 50+ Colombian and international sources. The system extracts structured data, validates active status, deduplicates, classifies by category/sector/beneficiary, and publishes a daily interactive HTML report via GitHub Pages. Replaces manual spreadsheet tracking with a zero-ops, daily-refreshed pipeline.

## Scope

### In Scope
- Python project scaffold (pyproject.toml, src/ layout, pytest, testing deps)
- Source config YAML defining 50+ sources with per-site adapter config
- Base spider engine using Scrapling (Fetcher, DynamicFetcher, StealthyFetcher, Spider)
- Extraction pipeline: parse → validate → deduplicate (rapidfuzz) → classify
- Batch 1: First 6 Colombian sources (Minciencias, iNNpulsa, MinTIC, SENA, Fondo Emprender, Colombia Productiva)
- Batch 2: Remaining 12+ Colombian sources (Bancóldex, Ruta N, Cámara de Comercio Bogotá, Connect Bogotá, ProColombia, ICETEX, Fulbright Colombia, universities, gobernaciones)
- Batch 3: International sources (Horizon Europe, EIC, Erasmus+, EIT, BID Lab, Banco Mundial, CAF, UNESCO, OEA, CYTED, DAAD, Chevening, Fulbright, MIT Solve, Google for Startups, Microsoft for Startups, AWS Activate, GIZ, USAID, Wellcome, Gates Foundation, TWAS, CLACSO, OECD, UN)
- Batch 4: Custom adapters for complex sources + GitHub Actions workflow + Pages deploy
- Jinja2 + Chart.js interactive HTML dashboard with filtered views
- GitHub Actions cron (daily) + Pages deployment

### Out of Scope
- Authentication/login-based sources (require user credentials)
- Real-time alerts or push notifications (daily batch only)
- Historical tracking across dates (current snapshot only)
- Multi-language reports (Spanish only MVP)
- Admin UI or manual review interface

## Capabilities

### New Capabilities
- `source-crawler`: Crawl configurable sources via Scrapling with fallback strategies (static → dynamic → stealthy + multi-session)
- `extraction-pipeline`: Parse raw HTML into structured records (title, description, dates, funding, source)
- `validity-checker`: Validate each convocatoria against published dates, tagging as vigente / requires-verification / vencida
- `deduplication-engine`: Fuzzy dedup across sources using rapidfuzz with configurable threshold
- `classification-tagger`: Tag by category, sector, beneficiary type via keyword/pattern rules
- `report-generator`: Build Jinja2 + Chart.js HTML dashboard with summary stats and filtered views
- `source-registry`: YAML-driven source config with per-site adapter, schedule, and parser hints

## Approach

Layered architecture: YAML config → spider layer (Scrapling Spider with multi-session rotation) → extraction pipeline (Pydantic models + date validators) → dedup + classification → report generator. Each source adapter implements a common `_parse(response) -> list[RawRecord]` protocol. Pipeline stages are isolated functions operating on pandas DataFrames. GitHub Actions orchestrates daily, deploying to `gh-pages`.

## Affected Areas

All new — greenfield project under `src/rastreador_convocatorias/`. No existing code modified.

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Source site structure changes | Medium | Isolated per-source adapters; manual validation cadence |
| Rate limiting / IP blocking | Medium | Scrapling multi-session rotation; StealthyFetcher for protected sites |
| GHA run exceeds 10 min | Medium | Batch crawling with timeouts; parallel job splitting if needed |
| False positive dedup | Low | Configurable threshold; manual review sample in report |

## Rollback Plan

Revert `gh-pages` branch to previous commit restores last good report. Source/adapter changes are per-file — revert individual commits. Build is dependency-only (no Docker), so no image rollback needed.

## Dependencies

- Python 3.12, Scrapling, Playwright + Chromium browser
- GitHub Actions with cron trigger + Pages deployment
- Public internet access from GHA runners
- rapidfuzz, pandas, python-dateutil, jinja2, pyyaml

## Success Criteria

- [ ] All configured sources crawl without errors (200 OK or graceful fallback)
- [ ] Each convocatoria correctly tagged: vigente / requires-verification / vencida
- [ ] >90% duplicate removal without false positives (manual sample check)
- [ ] HTML report renders all sections with functioning Chart.js charts
- [ ] GHA run completes within 10 minutes
- [ ] Report accessible at GitHub Pages URL
