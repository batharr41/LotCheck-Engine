import pytest

from lotcheck.manifest import list_partitions, select_partitions, summarize

BASE = "https://download.open.fda.gov/device/event"
MANIFEST = {
    "results": {
        "device": {
            "event": {
                "total_records": 550,
                "partitions": [
                    {"display_name": "2018 Q1 (part 1 of 1)", "file": f"{BASE}/2018q1/device-event-0001-of-0001.json.zip",
                     "size_mb": "10.5", "records": 100},
                    {"display_name": "2024 Q1 (part 1 of 2)", "file": f"{BASE}/2024q1/device-event-0001-of-0002.json.zip",
                     "size_mb": "20", "records": 200},
                    {"display_name": "2024 Q2 (part 1 of 2)", "file": f"{BASE}/2024q2/device-event-0001-of-0002.json.zip",
                     "size_mb": "20", "records": 200},
                    {"display_name": "All other data", "file": f"{BASE}/all_other/device-event-0001-of-0001.json.zip",
                     "size_mb": "5", "records": 50},
                ],
            },
            "recall": {
                "total_records": 3,
                "partitions": [
                    {"display_name": "All data",
                     "file": "https://download.open.fda.gov/device/recall/device-recall-0001-of-0001.json.zip",
                     "size_mb": "1.2", "records": 3}
                ],
            },
        }
    }
}


def test_years_and_unique_filenames():
    parts = list_partitions(MANIFEST, "device/event")
    assert [p.year for p in parts] == [2018, 2024, 2024, None]
    assert len({p.filename for p in parts}) == 4  # same basename in different quarters must not collide
    assert parts[1].filename == "2024q1__device-event-0001-of-0002.json.zip"
    assert parts[1].stem == "2024q1__device-event-0001-of-0002"


def test_select_by_year():
    parts = list_partitions(MANIFEST, "device/event")
    assert [p.year for p in select_partitions(parts, since=2020)] == [2024, 2024]
    assert len(select_partitions(parts, since=2020, include_undated=True)) == 3
    assert len(select_partitions(parts)) == 4


def test_summarize():
    summary = summarize(list_partitions(MANIFEST, "device/event"))
    assert summary == {"partitions": 4, "size_mb": 55.5, "records": 550, "years": "2018-2024", "undated": 1}


def test_unknown_dataset():
    with pytest.raises(KeyError):
        list_partitions(MANIFEST, "device/nope")
