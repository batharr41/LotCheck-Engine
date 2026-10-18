import duckdb

from helpers import make_zip
from lotcheck.config import parse_product_codes
from lotcheck.normalize import candidate_tokens, normalize_events, normalize_recalls, parse_date

CGM = parse_product_codes("cgm")


def quiet(*_args) -> None:
    pass


def q(sql: str):
    return duckdb.connect().execute(sql).fetchall()


def table(paths, name: str) -> str:
    glob = (paths.parquet / name / "**" / "*.parquet").as_posix()
    return f"read_parquet('{glob}', hive_partitioning = true)"


def test_filters_and_flattens(data_paths, event_zip):
    summary = normalize_events([event_zip], data_paths, CGM, batch_size=2, progress=quiet)
    assert (summary["scanned"], summary["kept"]) == (7, 6)  # the blood glucose meter is dropped
    assert q(f"select count(*) from {table(data_paths, 'reports')}")[0][0] == 6
    statuses = dict(q(f"select lot_status, count(*) from {table(data_paths, 'devices')} group by 1"))
    assert statuses == {"ok": 3, "placeholder": 2, "blank": 1}
    years = sorted(p.name for p in (data_paths.parquet / "reports").iterdir())
    assert years == ["year=2024", "year=2025"]


def test_problems_deduped_and_lots_cleaned(data_paths, event_zip):
    normalize_events([event_zip], data_paths, None, progress=quiet)  # no filter keeps all 7
    rows = q(f"select mdr_report_key, product_problems from {table(data_paths, 'reports')} order by 1")
    assert len(rows) == 7
    assert rows[0][1] == ["Premature End-of-Life Indicator"]
    lots = dict(q(f"select mdr_report_key, lot_number from {table(data_paths, 'devices')}"))
    assert lots["5"] == "T60002615"
    assert lots["3"] is None


def test_narrative_hashes_and_redaction(data_paths, event_zip):
    normalize_events([event_zip], data_paths, CGM, progress=quiet)
    rows = q(
        f"""select text_type_code, count(*), count(distinct text_hash), sum(is_redacted::int)
            from {table(data_paths, 'narratives')} group by 1"""
    )
    by_type = {r[0]: tuple(r[1:]) for r in rows}
    assert by_type["Description of Event or Problem"] == (6, 2, 0)  # five identical templates + one unique
    assert by_type["Additional Manufacturer Narrative"] == (6, 1, 6)  # all "(B)(4)." redactions


def test_rerun_is_idempotent_and_incremental(data_paths, event_zip):
    normalize_events([event_zip], data_paths, CGM, progress=quiet)
    second = normalize_events([event_zip], data_paths, CGM, progress=quiet)
    assert (second["files_processed"], second["files_skipped"]) == (0, 1)
    third = normalize_events([event_zip], data_paths, CGM, force=True, progress=quiet)
    assert third["files_processed"] == 1
    assert q(f"select count(*) from {table(data_paths, 'reports')}")[0][0] == 6  # no duplicates


def test_changing_the_filter_reprocesses(data_paths, event_zip):
    normalize_events([event_zip], data_paths, CGM, progress=quiet)
    summary = normalize_events([event_zip], data_paths, None, progress=quiet)
    assert summary["files_processed"] == 1
    assert q(f"select count(*) from {table(data_paths, 'reports')}")[0][0] == 7


def test_runs_are_logged(data_paths, event_zip):
    normalize_events([event_zip], data_paths, CGM, progress=quiet)
    lines = (data_paths.state / "runs.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1 and '"scanned": 7' in lines[0]


def test_parse_date():
    assert str(parse_date("20250105")) == "2025-01-05"
    assert str(parse_date("2025-08-28")) == "2025-08-28"
    assert parse_date("2025") is None
    assert parse_date("20251399") is None
    assert parse_date(None) is None


def test_recalls(data_paths):
    records = [
        {"res_event_number": "1", "product_code": "qbj", "recalling_firm": "ACME",
         "event_date_initiated": "2025-08-28", "recall_status": "Open, Classified",
         "reason_for_recall": "App may close unexpectedly.", "code_info": "Lot Code: UDI: SW11678 - 00386270000811"},
        {"res_event_number": "2", "product_code": "QBJ", "recalling_firm": "OTHER",
         "event_date_initiated": "2025-11-03", "recall_status": "Open, Classified",
         "reason_for_recall": "No clearance.", "code_info": "Lot/Serial Number: All Lots;"},
    ]
    z = make_zip(data_paths.raw / "device_recall" / "recall__device-recall-0001-of-0001.json.zip", records)
    assert normalize_recalls([z], data_paths, progress=quiet)["rows"] == 2
    path = (data_paths.parquet / "recalls" / "recalls.parquet").as_posix()
    rows = q(f"select product_code, event_date_initiated, code_tokens from read_parquet('{path}') order by res_event_number")
    assert rows[0][0] == "QBJ" and str(rows[0][1]) == "2025-08-28"
    assert "SW11678" in rows[0][2] and "00386270000811" in rows[0][2]
    assert rows[1][2] == []


def test_candidate_tokens():
    assert candidate_tokens("Lot numbers: 5268240, T60002615 and AB1; exp 2027") == ["5268240", "T60002615"]
