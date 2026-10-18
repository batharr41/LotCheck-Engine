"""Stream openFDA partition zips into normalized Parquet tables.

Design choices (the ones worth explaining in an interview):

- Streaming. Records are parsed one at a time with ijson, so a 1 GB JSON file
  never has to fit in memory.
- Columnar output. Tables are written as zstd-compressed Parquet, partitioned
  by the year a report was received, so a query that filters on year skips
  whole folders.
- Idempotent. Every source zip owns its own output files. Reprocessing a zip
  first deletes exactly its previous output, so reruns never duplicate rows
  and everything can be rebuilt from raw/ at any time.
- Incremental. A state file remembers which zips were processed (and with
  which filter), so a rerun only touches new or changed files.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import zipfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator

import ijson
import pyarrow as pa
import pyarrow.parquet as pq

from .config import DataPaths
from .lots import lot_shape, normalize_lot

# ---------------------------------------------------------------------------
# Table schemas
# ---------------------------------------------------------------------------

REPORTS = pa.schema(
    [
        ("mdr_report_key", pa.string()),
        ("report_number", pa.string()),
        ("date_received", pa.date32()),
        ("date_of_event", pa.date32()),
        ("event_type", pa.string()),
        ("type_of_report", pa.string()),
        ("report_source_code", pa.string()),
        ("adverse_event_flag", pa.string()),
        ("product_problem_flag", pa.string()),
        ("product_problems", pa.list_(pa.string())),
        ("manufacturer_name", pa.string()),
        ("source_file", pa.string()),
    ]
)

DEVICES = pa.schema(
    [
        ("mdr_report_key", pa.string()),
        ("device_index", pa.int16()),
        ("brand_name", pa.string()),
        ("generic_name", pa.string()),
        ("manufacturer_d_name", pa.string()),
        ("model_number", pa.string()),
        ("catalog_number", pa.string()),
        ("lot_number_raw", pa.string()),
        ("lot_number", pa.string()),
        ("lot_status", pa.string()),
        ("lot_shape", pa.string()),
        ("product_code", pa.string()),
        ("device_name", pa.string()),
        ("device_class", pa.string()),
        ("udi_di", pa.string()),
    ]
)

NARRATIVES = pa.schema(
    [
        ("mdr_report_key", pa.string()),
        ("mdr_text_key", pa.string()),
        ("text_type_code", pa.string()),
        ("text", pa.string()),
        ("text_hash", pa.string()),
        ("is_redacted", pa.bool_()),
    ]
)

EVENT_TABLES = {"reports": REPORTS, "devices": DEVICES, "narratives": NARRATIVES}

RECALLS = pa.schema(
    [
        ("res_event_number", pa.string()),
        ("product_res_number", pa.string()),
        ("product_code", pa.string()),
        ("recalling_firm", pa.string()),
        ("event_date_initiated", pa.date32()),
        ("event_date_posted", pa.date32()),
        ("event_date_terminated", pa.date32()),
        ("recall_status", pa.string()),
        ("root_cause_description", pa.string()),
        ("reason_for_recall", pa.string()),
        ("product_description", pa.string()),
        ("code_info", pa.string()),
        ("code_tokens", pa.list_(pa.string())),
    ]
)

CLASSIFICATION = pa.schema(
    [
        ("product_code", pa.string()),
        ("device_name", pa.string()),
        ("device_class", pa.string()),
        ("medical_specialty_description", pa.string()),
        ("regulation_number", pa.string()),
        ("review_panel", pa.string()),
    ]
)

# ---------------------------------------------------------------------------
# Field helpers
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")
# FOIA redaction markers such as "(B)(4)." or "(B)(4). (B)(6)."
_REDACTED = re.compile(r"(?:\(B\)\s*\(\d\)\s*[.,;:]?\s*)+")
_TOKEN = re.compile(r"\b[A-Z0-9][A-Z0-9\-]{3,19}\b")


def parse_date(value) -> date | None:
    """'20250105' (events) or '2025-01-05' (recalls) -> date; anything else -> None."""
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))[:8]
    if len(digits) != 8:
        return None
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def _text(value) -> str | None:
    """openFDA mixes scalars and lists. Lists are joined with '; '; empty -> None."""
    if value is None:
        return None
    if isinstance(value, list):
        parts = [str(v).strip() for v in value if v not in (None, "")]
        return "; ".join(p for p in parts if p) or None
    text = str(value).strip()
    return text or None


def _code(value) -> str | None:
    text = _text(value)
    return text.upper() if text else None


def text_hash(text: str) -> str | None:
    """Hash of the whitespace/case-normalized text: identical templates collide on purpose."""
    norm = _WS.sub(" ", text).strip().upper()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest() if norm else None


def candidate_tokens(text: str | None) -> list[str]:
    """Lot/serial-looking tokens in recall free text (5-20 chars, at least one digit)."""
    if not text:
        return []
    seen: dict[str, None] = {}
    for token in _TOKEN.findall(text.upper()):
        token = token.strip("-")
        if 5 <= len(token) <= 20 and any(c.isdigit() for c in token):
            seen[token] = None
    return list(seen)


def iter_records(zip_path: Path) -> Iterator[dict]:
    """Yield records from an openFDA zip one at a time ({"meta":..., "results":[...]})."""
    with zipfile.ZipFile(zip_path) as z:
        member = next((n for n in z.namelist() if n.endswith(".json")), None)
        if member is None:
            raise ValueError(f"{zip_path.name}: no .json file inside")
        with z.open(member) as fh:
            yield from ijson.items(fh, "results.item", use_float=True)


def flatten_event(rec: dict, source: str) -> tuple[int, dict, list[dict], list[dict]]:
    """One adverse event record -> (year, report row, device rows, narrative rows)."""
    key = _text(rec.get("mdr_report_key")) or _text(rec.get("report_number")) or ""
    received = parse_date(rec.get("date_received"))
    problems = list(
        dict.fromkeys(str(p).strip() for p in (rec.get("product_problems") or []) if p and str(p).strip())
    )
    report = {
        "mdr_report_key": key,
        "report_number": _text(rec.get("report_number")),
        "date_received": received,
        "date_of_event": parse_date(rec.get("date_of_event")),
        "event_type": _text(rec.get("event_type")),
        "type_of_report": _text(rec.get("type_of_report")),
        "report_source_code": _text(rec.get("report_source_code")),
        "adverse_event_flag": _text(rec.get("adverse_event_flag")),
        "product_problem_flag": _text(rec.get("product_problem_flag")),
        "product_problems": problems,
        "manufacturer_name": _text(rec.get("manufacturer_name")),
        "source_file": source,
    }

    devices = []
    for i, dev in enumerate(rec.get("device") or []):
        if not isinstance(dev, dict):
            continue
        lot, status = normalize_lot(dev.get("lot_number"))
        openfda = dev.get("openfda") or {}
        devices.append(
            {
                "mdr_report_key": key,
                "device_index": i,
                "brand_name": _text(dev.get("brand_name")),
                "generic_name": _text(dev.get("generic_name")),
                "manufacturer_d_name": _text(dev.get("manufacturer_d_name")),
                "model_number": _text(dev.get("model_number")),
                "catalog_number": _text(dev.get("catalog_number")),
                "lot_number_raw": _text(dev.get("lot_number")),
                "lot_number": lot,
                "lot_status": status,
                "lot_shape": lot_shape(lot),
                "product_code": _code(dev.get("device_report_product_code")),
                "device_name": _text(openfda.get("device_name")),
                "device_class": _text(openfda.get("device_class")),
                "udi_di": _text(dev.get("udi_di")),
            }
        )

    narratives = []
    for item in rec.get("mdr_text") or []:
        if not isinstance(item, dict):
            continue
        body = (item.get("text") or "").strip()
        upper = _WS.sub(" ", body).upper()
        narratives.append(
            {
                "mdr_report_key": key,
                "mdr_text_key": _text(item.get("mdr_text_key")),
                "text_type_code": _text(item.get("text_type_code")),
                "text": body,
                "text_hash": text_hash(body),
                "is_redacted": bool(upper) and _REDACTED.fullmatch(upper) is not None,
            }
        )

    return (received.year if received else 0), report, devices, narratives


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


class _TableWriter:
    """Buffers rows per (table, year) and writes a Parquet file whenever a buffer fills."""

    def __init__(self, root: Path, stem: str, schemas: dict[str, pa.Schema], batch_size: int):
        self.root, self.stem, self.schemas, self.batch_size = root, stem, schemas, batch_size
        self._buffers: dict[tuple[str, int], list[dict]] = defaultdict(list)
        self._next_chunk: dict[str, int] = defaultdict(int)
        self.rows: dict[str, int] = defaultdict(int)
        self.bytes_written = 0

    def add(self, table: str, year: int, row: dict) -> None:
        buf = self._buffers[(table, year)]
        buf.append(row)
        if len(buf) >= self.batch_size:
            self._flush(table, year)

    def _flush(self, table: str, year: int) -> None:
        rows = self._buffers.pop((table, year), None)
        if not rows:
            return
        folder = self.root / table / f"year={year}"
        folder.mkdir(parents=True, exist_ok=True)
        chunk = self._next_chunk[table]
        self._next_chunk[table] += 1
        path = folder / f"{self.stem}-{chunk:04d}.parquet"
        pq.write_table(pa.Table.from_pylist(rows, schema=self.schemas[table]), path, compression="zstd")
        self.rows[table] += len(rows)
        self.bytes_written += path.stat().st_size

    def close(self) -> None:
        for table, year in list(self._buffers):
            self._flush(table, year)


def remove_outputs(root: Path, stem: str, tables: Iterable[str]) -> int:
    """Delete every Parquet file a given source zip produced earlier."""
    removed = 0
    for table in tables:
        for f in (root / table).glob(f"year=*/{stem}-*.parquet"):
            f.unlink()
            removed += 1
    return removed


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_run(paths: DataPaths, entry: dict) -> None:
    """Append one line of run metrics to state/runs.jsonl (your numbers log)."""
    paths.state.mkdir(parents=True, exist_ok=True)
    with open(paths.state / "runs.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, default=str) + "\n")


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def normalize_events(
    zip_paths: list[Path],
    paths: DataPaths,
    product_codes: frozenset[str] | None = None,
    force: bool = False,
    batch_size: int = 25_000,
    progress: Callable[[str], None] = print,
) -> dict:
    """Adverse event zips -> reports, devices and narratives tables.

    product_codes keeps only reports where at least one device has one of the
    codes (None keeps everything). Every scanned record is still counted.
    """
    state_path = paths.state / "normalize_events.json"
    state = _load_json(state_path)
    signature = ",".join(sorted(product_codes)) if product_codes else "all"
    totals = {
        "scanned": 0,
        "kept": 0,
        "zip_bytes": 0,
        "parquet_bytes": 0,
        "rows_reports": 0,
        "rows_devices": 0,
        "rows_narratives": 0,
    }
    processed = skipped = 0
    started = time.perf_counter()

    for zip_path in zip_paths:
        stem = zip_path.name.removesuffix(".zip").removesuffix(".json")
        size = zip_path.stat().st_size
        previous = state.get(stem)
        if not force and previous and previous.get("size") == size and previous.get("filter") == signature:
            skipped += 1
            continue

        remove_outputs(paths.parquet, stem, EVENT_TABLES)
        writer = _TableWriter(paths.parquet, stem, EVENT_TABLES, batch_size)
        scanned = kept = 0
        t0 = time.perf_counter()
        for rec in iter_records(zip_path):
            scanned += 1
            year, report, devices, narratives = flatten_event(rec, stem)
            if product_codes is not None and not any(d["product_code"] in product_codes for d in devices):
                continue
            kept += 1
            writer.add("reports", year, report)
            for row in devices:
                writer.add("devices", year, row)
            for row in narratives:
                writer.add("narratives", year, row)
        writer.close()
        seconds = time.perf_counter() - t0

        state[stem] = {
            "size": size,
            "filter": signature,
            "scanned": scanned,
            "kept": kept,
            "rows": dict(writer.rows),
            "parquet_bytes": writer.bytes_written,
            "seconds": round(seconds, 2),
            "processed_at": _now(),
        }
        _save_json(state_path, state)  # after every file: a crash loses at most one file
        processed += 1
        totals["scanned"] += scanned
        totals["kept"] += kept
        totals["zip_bytes"] += size
        totals["parquet_bytes"] += writer.bytes_written
        for table, count in writer.rows.items():
            totals[f"rows_{table}"] += count
        rate = scanned / seconds if seconds > 0 else 0
        progress(f"{zip_path.name}: scanned {scanned:,}, kept {kept:,} in {seconds:,.1f}s ({rate:,.0f} records/s)")

    elapsed = time.perf_counter() - started
    summary = {
        "dataset": "device/event",
        "filter": signature,
        "files_processed": processed,
        "files_skipped": skipped,
        **totals,
        "seconds": round(elapsed, 2),
        "records_per_second": round(totals["scanned"] / elapsed) if elapsed > 0 and totals["scanned"] else 0,
        "finished_at": _now(),
    }
    log_run(paths, summary)
    return summary


def _recall_row(rec: dict) -> dict:
    code_info = _text(rec.get("code_info"))
    return {
        "res_event_number": _text(rec.get("res_event_number")),
        "product_res_number": _text(rec.get("product_res_number")),
        "product_code": _code(rec.get("product_code")),
        "recalling_firm": _text(rec.get("recalling_firm")),
        "event_date_initiated": parse_date(rec.get("event_date_initiated")),
        "event_date_posted": parse_date(rec.get("event_date_posted")),
        "event_date_terminated": parse_date(rec.get("event_date_terminated")),
        "recall_status": _text(rec.get("recall_status")),
        "root_cause_description": _text(rec.get("root_cause_description")),
        "reason_for_recall": _text(rec.get("reason_for_recall")),
        "product_description": _text(rec.get("product_description")),
        "code_info": code_info,
        "code_tokens": candidate_tokens(code_info),
    }


def _classification_row(rec: dict) -> dict:
    return {
        "product_code": _code(rec.get("product_code")),
        "device_name": _text(rec.get("device_name")),
        "device_class": _text(rec.get("device_class")),
        "medical_specialty_description": _text(rec.get("medical_specialty_description")),
        "regulation_number": _text(rec.get("regulation_number")),
        "review_panel": _text(rec.get("review_panel")),
    }


def _normalize_small(
    zip_paths: list[Path],
    out_file: Path,
    schema: pa.Schema,
    row_fn: Callable[[dict], dict],
    paths: DataPaths,
    dataset: str,
    progress: Callable[[str], None],
) -> dict:
    """Small reference datasets fit in memory: rebuild one Parquet file each run."""
    t0 = time.perf_counter()
    rows = [row_fn(rec) for zp in zip_paths for rec in iter_records(zp)]
    out_file.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), out_file, compression="zstd")
    summary = {
        "dataset": dataset,
        "files_processed": len(zip_paths),
        "rows": len(rows),
        "seconds": round(time.perf_counter() - t0, 2),
        "finished_at": _now(),
    }
    log_run(paths, summary)
    progress(f"{dataset}: {len(rows):,} rows -> {out_file}")
    return summary


def normalize_recalls(zip_paths: list[Path], paths: DataPaths, progress: Callable[[str], None] = print) -> dict:
    out = paths.parquet / "recalls" / "recalls.parquet"
    return _normalize_small(zip_paths, out, RECALLS, _recall_row, paths, "device/recall", progress)


def normalize_classification(
    zip_paths: list[Path], paths: DataPaths, progress: Callable[[str], None] = print
) -> dict:
    out = paths.parquet / "classification" / "classification.parquet"
    return _normalize_small(zip_paths, out, CLASSIFICATION, _classification_row, paths, "device/classification", progress)
