# The 6-week plan

**Goal:** a public, fast explorer of FDA device reports for continuous glucose monitors, with per-lot signals, built like production software.

**Rule:** every week ends with something that runs and at least one number in the metrics log below. Those numbers become your resume bullets.

## Week 1: Ingest and profile (this starter)

- Run `lotcheck manifest --since 2018` to see the download size before committing.
- Run `lotcheck run --since 2025` (quick), then the full CGM history: `lotcheck run --since 2006 --include-undated`.
- Read every module in `lotcheck/` until you can explain why it streams, why Parquet, why it's idempotent per file, and how resume works.
- Investigate: how do follow-up reports show up (`type_of_report`)? Do they repeat `report_number`? Write your dedupe rule into `docs/data-notes.md`.
- Add one profile query of your own.

**Done when** `data/profiles/profile.md` exists for the full CGM history and the week 1 rows of the log are filled.

## Week 2: Serving database and API

- Postgres in Docker. Tables: reports (partitioned by year), devices, narratives, recalls.
- Loader: Parquet to Postgres with `COPY`, idempotent on `mdr_report_key`.
- Indexes: `devices(lot_number)`, `devices(product_code)`, `reports(date_received)`, GIN full-text on narratives.
- FastAPI: `GET /lots/{lot}`, `GET /search?q=&code=&from=&to=`, `GET /reports/{key}`, `GET /recalls?code=`.
- Benchmark each endpoint (p50/p95) before and after indexing; keep the `EXPLAIN ANALYZE` output.
- Compact the small Parquet files into fewer, larger ones.

## Week 3: AI extraction with an eval set

- Send each unique narrative once (dedupe on `text_hash`).
- Hand-label 300 random CGM reports: failure mode, day of wear, any lot number written in the text.
- Regex baseline first, then an LLM with a JSON schema (batch API, cached by hash).
- Measure precision and recall per field against your labels, and compare with FDA's own `product_problems` codes.

## Week 4: Lot statistics and nightly updates

- Per lot: reports over time, failure-mode mix, first and last seen, matching recalls (lot tokens from recall `code_info`).
- Flag lots that stand out from sibling lots (same maker, same period). Show counts and mix, never "failure rates".
- Nightly job: refresh the manifest, download only new or changed files, normalize, load, recompute.
- Data checks that fail the job: row counts never drop, keys stay unique, dates stay in range.

## Week 5: Web app

- Next.js, server-rendered so lot pages show up in search.
- Pages: search, lot, device model, recall banner. Charts: reports by week, failure-mode mix, lot vs siblings.
- Plain-language caveats on every page.

## Week 6: Ship it

- Deploy the database, API and web app. CI already runs the tests on every push; add API tests.
- README: architecture diagram, benchmark table, eval table, screenshots.
- Fill in the resume bullets below with real numbers and post a short write-up.

## Metrics log

| week | metric | value | where it comes from |
| --- | --- | --- | --- |
| 1 | records scanned (all devices) | | profile.md, Ingestion |
| 1 | CGM reports kept | | profile.md, Overview |
| 1 | raw download size | | profile.md, Ingestion |
| 1 | normalize throughput (records/s) | | profile.md, Ingestion |
| 1 | usable lot numbers (%) | | profile.md, Lot number quality |
| 1 | duplicate event descriptions (%) | | profile.md, Narratives |
| 2 | API p95 before and after indexing | | load test |
| 3 | extraction accuracy: lot, mode, day | | eval script |
| 3 | cost per 1,000 reports | | API usage |
| 4 | nightly update time vs full backfill | | job logs |

## Resume bullets (use real numbers; delete what you didn't build)

- Built a resumable Python pipeline that streams [N]M FDA device reports ([X] GB) from openFDA into a year-partitioned Parquet lake at [Y]k records/s, with idempotent reprocessing per source file.
- Found [Z]% of CGM reports lack a usable lot number; extracted lots and failure modes from narratives with an LLM at [A]% accuracy on a 300-report hand-labeled set, cutting LLM calls [B]% by deduplicating templated text.
- Served per-lot signals through FastAPI and Postgres (p95 [C] ms after indexing and partitioning) and a server-rendered Next.js site.
