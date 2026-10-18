from __future__ import annotations

from pathlib import Path

import pytest

from helpers import event, make_zip
from lotcheck.config import DataPaths


@pytest.fixture
def sample_events() -> list[dict]:
    return [
        event(1, "20250105", "QBJ", "1725204004"),
        event(2, "20250106", "QBJ", "1725204004"),
        event(3, "20250107", "QBJ", "NI"),
        event(4, "20250108", "QBJ", ""),
        event(5, "20250109", "QBJ", "Lot# t60002615", maker="OTHER SENSOR CO", brand="OTHER CGM"),
        event(6, "20250110", "NBW", "A123", brand="METER X", problems=("Low Readings",)),  # not a CGM
        event(7, "20241231", "QLG", "ASKU", description="Sensor fell off on day 3. Adhesive did not hold."),
    ]


@pytest.fixture
def data_paths(tmp_path: Path) -> DataPaths:
    return DataPaths(tmp_path / "data")


@pytest.fixture
def event_zip(data_paths: DataPaths, sample_events: list[dict]) -> Path:
    name = "2025q1__device-event-0001-of-0001.json.zip"
    return make_zip(data_paths.raw / "device_event" / name, sample_events)
