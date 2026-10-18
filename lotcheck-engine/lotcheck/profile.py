"""Profile the normalized tables with DuckDB and write a Markdown report.

Every number here is a candidate resume metric, and every query is timed so
you can show how fast columnar storage answers questions over millions of rows.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from .config import DataPaths

PRIMARY_TEXT = "Description of Event or Problem"


def _fmt(value, column: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value) if column.lower() == "year" else f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.1f}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def md_table(columns: list[str], rows: list[tuple]) -> str:
    if not rows:
        return "_No rows._"
    head = "| " + " | ".join(columns) + " |"
    rule = "| " + " | ".join("---" for _ in columns) + " |"
    body = ["| " + " | ".join(_fmt(v, c) for v, c in zip(row, columns)) + " |" for row in rows]
    return "\n".join([head, rule, *body])


class _Profiler:
    def __init__(self, paths: DataPaths):
        self.paths = paths
        self.con = duckdb.connect()
        self.timings: list[tuple[str, float, int]] = []

    def view(self, name: str, pattern: str, hive: bool) -> None:
        path = (self.paths.parquet / pattern).as_posix().replace("'", "''")
        opts = ", hive_partitioning = true" if hive else ""
        self.con.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{path}'{opts})")

    def query(self, title: str, sql: str) -> tuple[list[str], list[tuple]]:
        t0 = time.perf_counter()
        cur = self.con.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        self.timings.append((title, round((time.perf_counter() - t0) * 1000, 1), len(rows)))
        return cols, rows


def _has(paths: DataPaths, pattern: str) -> bool:
    return any(paths.parquet.glob(pattern))


def _ingest_stats(paths: DataPaths) -> list[tuple[str, str]]:
    """Summarize state/normalize_events.json: how much was read, kept and how fast."""
    path = paths.state / "normalize_events.json"
    if not path.exists():
        return []
    state = json.loads(path.read_text(encoding="utf-8"))
    if not state:
        return []
    scanned = sum(v.get("scanned", 0) for v in state.values())
    kept = sum(v.get("kept", 0) for v in state.values())
    seconds = sum(v.get("seconds", 0) for v in state.values())
    zip_bytes = sum(v.get("size", 0) for v in state.values())
    pq_bytes = sum(v.get("parquet_bytes", 0) for v in state.values())
    filters = sorted({v.get("filter", "?") for v in state.values()})
    return [
        ("Source files processed", f"{len(state):,}"),
        ("Records scanned (all devices)", f"{scanned:,}"),
        (f"Reports kept (filter: {', '.join(filters)})", f"{kept:,}"),
        ("Raw zip size", f"{zip_bytes / 1e6:,.1f} MB"),
        ("Parquet size (kept data)", f"{pq_bytes / 1e6:,.1f} MB"),
        ("Normalize time", f"{seconds / 60:,.1f} min"),
        ("Throughput", f"{scanned / seconds:,.0f} records/s" if seconds else "n/a"),
    ]


def run_profile(paths: DataPaths, top: int = 15) -> tuple[str, dict, Path]:
    """Run every profiling query, write profiles/profile.md + profile.json, return them."""
    if not _has(paths, "reports/**/*.parquet"):
        raise FileNotFoundError("No normalized reports found. Run `lotcheck normalize` first.")

    p = _Profiler(paths)
    p.view("reports", "reports/**/*.parquet", hive=True)
    p.view("devices", "devices/**/*.parquet", hive=True)
    has_narratives = _has(paths, "narratives/**/*.parquet")
    if has_narratives:
        p.view("narratives", "narratives/**/*.parquet", hive=True)
    has_cls = (paths.parquet / "classification" / "classification.parquet").exists()
    if has_cls:
        p.view("classification", "classification/classification.parquet", hive=False)
    has_recalls = (paths.parquet / "recalls" / "recalls.parquet").exists()
    if has_recalls:
        p.view("recalls", "recalls/recalls.parquet", hive=False)

    metrics: dict = {}
    out: list[str] = []

    def section(title: str, sql: str, note: str | None = None) -> tuple[list[str], list[tuple]]:
        cols, rows = p.query(title, sql)
        out.extend([f"## {title}", ""])
        if note:
            out.extend([note, ""])
        out.extend([md_table(cols, rows), ""])
        return cols, rows

    ingest = _ingest_stats(paths)
    if ingest:
        out.extend(["## Ingestion", "", md_table(["metric", "value"], ingest), ""])

    narr_count = "(SELECT count(*) FROM narratives)" if has_narratives else "NULL"
    _, rows = section(
        "Overview",
        f"""
        SELECT (SELECT count(*) FROM reports) AS reports,
               (SELECT count(*) FROM devices) AS device_entries,
               {narr_count} AS narratives,
               (SELECT min(date_received) FROM reports) AS first_received,
               (SELECT max(date_received) FROM reports) AS last_received
        """,
    )
    metrics.update(reports=rows[0][0], device_entries=rows[0][1], narratives=rows[0][2])

    section(
        "Reports by year received",
        """
        SELECT year, count(*) AS reports,
               count(*) FILTER (WHERE event_type = 'Malfunction') AS malfunction,
               count(*) FILTER (WHERE event_type = 'Injury') AS injury,
               count(*) FILTER (WHERE event_type = 'Death') AS death
        FROM reports GROUP BY year ORDER BY year
        """,
    )

    device_name = "any_value(c.device_name)" if has_cls else "any_value(d.device_name)"
    join = "LEFT JOIN classification c ON c.product_code = d.product_code" if has_cls else ""
    section(
        "Reports by product code",
        f"""
        SELECT d.product_code, {device_name} AS device,
               count(DISTINCT d.mdr_report_key) AS reports
        FROM devices d {join}
        GROUP BY d.product_code ORDER BY reports DESC LIMIT {top}
        """,
    )

    _, rows = section(
        "Lot number quality",
        """
        SELECT lot_status, count(*) AS device_entries,
               round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
        FROM devices GROUP BY lot_status ORDER BY device_entries DESC
        """,
        note="'blank' = empty field, 'placeholder' = values like NI, NA, ASKU, UNKNOWN.",
    )
    metrics["pct_usable_lot"] = next((float(r[2]) for r in rows if r[0] == "ok"), 0.0)

    section(
        "Lot number quality by manufacturer",
        f"""
        SELECT coalesce(manufacturer_d_name, '(blank)') AS manufacturer,
               count(*) AS device_entries,
               round(100.0 * avg(CASE WHEN lot_status = 'ok' THEN 1 ELSE 0 END), 1) AS pct_usable,
               round(100.0 * avg(CASE WHEN lot_status = 'blank' THEN 1 ELSE 0 END), 1) AS pct_blank,
               round(100.0 * avg(CASE WHEN lot_status = 'placeholder' THEN 1 ELSE 0 END), 1) AS pct_placeholder
        FROM devices GROUP BY 1 ORDER BY device_entries DESC LIMIT {top}
        """,
    )

    section(
        "Most common placeholder values",
        """
        SELECT coalesce(upper(trim(lot_number_raw)), '(empty)') AS value, count(*) AS device_entries
        FROM devices WHERE lot_status = 'placeholder'
        GROUP BY 1 ORDER BY device_entries DESC LIMIT 10
        """,
    )

    section(
        "Lot formats by manufacturer",
        f"""
        WITH shapes AS (
            SELECT coalesce(manufacturer_d_name, '(blank)') AS manufacturer, lot_shape,
                   count(*) AS device_entries, any_value(lot_number) AS example
            FROM devices WHERE lot_status = 'ok' GROUP BY 1, 2
        ), ranked AS (
            SELECT *, row_number() OVER (PARTITION BY manufacturer ORDER BY device_entries DESC) AS rn,
                   sum(device_entries) OVER (PARTITION BY manufacturer) AS maker_total
            FROM shapes
        )
        SELECT manufacturer, lot_shape, device_entries, example FROM ranked
        WHERE rn <= 3 ORDER BY maker_total DESC, manufacturer, device_entries DESC LIMIT {top * 3}
        """,
        note="Shape codes: D = digits, A = letters, with run lengths (T60002615 -> A1D8).",
    )

    section(
        "Top FDA-coded product problems",
        f"""
        SELECT problem, count(*) AS reports
        FROM (SELECT unnest(product_problems) AS problem FROM reports)
        GROUP BY problem ORDER BY reports DESC LIMIT {top}
        """,
    )

    if has_narratives:
        _, rows = section(
            "Narratives: duplication and redaction",
            """
            SELECT coalesce(text_type_code, '(none)') AS text_type, count(*) AS texts,
                   count(DISTINCT text_hash) AS unique_texts,
                   round(100.0 * (1 - count(DISTINCT text_hash)::DOUBLE / count(*)), 1) AS pct_duplicate,
                   round(100.0 * avg(CASE WHEN is_redacted THEN 1 ELSE 0 END), 1) AS pct_redacted_only
            FROM narratives GROUP BY 1 ORDER BY texts DESC
            """,
            note="Duplicate texts only need to go through the AI step once (week 3).",
        )
        metrics["pct_duplicate_primary_text"] = next((float(r[3]) for r in rows if r[0] == PRIMARY_TEXT), None)
        section(
            "Most repeated event descriptions",
            f"""
            SELECT left(any_value(text), 140) AS text_start, count(*) AS times_used
            FROM narratives WHERE text_type_code = '{PRIMARY_TEXT}'
            GROUP BY text_hash ORDER BY times_used DESC LIMIT 5
            """,
        )

    _, rows = section(
        "Most-reported lots",
        f"""
        SELECT coalesce(d.manufacturer_d_name, '(blank)') AS manufacturer, d.lot_number,
               mode(d.brand_name) AS brand,
               count(DISTINCT d.mdr_report_key) AS reports,
               min(r.date_received) AS first_report, max(r.date_received) AS last_report
        FROM devices d JOIN reports r ON r.mdr_report_key = d.mdr_report_key
        WHERE d.lot_status = 'ok'
        GROUP BY 1, 2 ORDER BY reports DESC LIMIT {top}
        """,
        note="Report counts, not failure rates: FDA data has no denominators (how many sensors each lot shipped).",
    )
    if rows:
        metrics["top_lot"] = {"manufacturer": rows[0][0], "lot": rows[0][1], "reports": rows[0][3]}

    if has_recalls:
        codes = "SELECT DISTINCT product_code FROM devices WHERE product_code IS NOT NULL"
        _, rows = section(
            "Recalls for these product codes",
            f"""
            SELECT count(*) AS recall_records, count(DISTINCT res_event_number) AS recall_events,
                   count(*) FILTER (WHERE recall_status ILIKE 'open%') AS open_records
            FROM recalls WHERE product_code IN ({codes})
            """,
        )
        metrics["recall_records"] = rows[0][0] if rows else 0
        section(
            "Latest recalls",
            f"""
            SELECT event_date_initiated, recalling_firm, product_code, recall_status,
                   left(reason_for_recall, 110) AS reason
            FROM recalls WHERE product_code IN ({codes})
            ORDER BY event_date_initiated DESC NULLS LAST LIMIT 10
            """,
        )

    total_ms = round(sum(t[1] for t in p.timings), 1)
    metrics["query_ms_total"] = total_ms
    out.extend(
        [
            "## Query timings",
            "",
            md_table(["query", "ms", "rows"], [(t, ms, n) for t, ms, n in p.timings]),
            "",
            f"Total: {total_ms:,.1f} ms across {len(p.timings)} queries.",
            "",
        ]
    )

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = [
        "# LotCheck data profile",
        "",
        f"Generated {stamp} from `{paths.parquet.as_posix()}`. Source: openFDA (FDA MAUDE). "
        "Reports are unverified and not failure rates.",
        "",
    ]
    markdown = "\n".join(header + out)

    paths.profiles.mkdir(parents=True, exist_ok=True)
    md_path = paths.profiles / "profile.md"
    md_path.write_text(markdown, encoding="utf-8")
    (paths.profiles / "profile.json").write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
    return markdown, metrics, md_path
