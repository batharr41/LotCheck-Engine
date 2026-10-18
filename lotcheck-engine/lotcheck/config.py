"""Shared settings: where data lives and which devices we focus on."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MANIFEST_URL = "https://api.fda.gov/download.json"

# Datasets this project uses, named the way openFDA's download.json names them.
EVENT_DATASET = "device/event"
RECALL_DATASET = "device/recall"
CLASSIFICATION_DATASET = "device/classification"

# Continuous glucose monitor product codes. Names come from FDA's device
# classification database (checked October 2026).
CGM_PRODUCT_CODES: dict[str, str] = {
    "QBJ": "Integrated CGM, factory calibrated (iCGM)",
    "QLG": "Integrated CGM, factory calibrated, not for automated insulin delivery",
    "PQF": "Glucose sensor, invasive, non-adjunctive",
    "PZE": "Glucose sensor, invasive, non-adjunctive, factory-calibrated, user-initiated",
    "MDS": "Glucose sensor, invasive",
}


@dataclass(frozen=True)
class DataPaths:
    """Folder layout under the data directory (default ./data)."""

    root: Path

    @property
    def raw(self) -> Path:
        """Downloads exactly as FDA serves them. Never edited: the raw layer."""
        return self.root / "raw"

    @property
    def parquet(self) -> Path:
        """Normalized, columnar tables built from raw/."""
        return self.root / "parquet"

    @property
    def state(self) -> Path:
        """Bookkeeping: which files were processed, plus a log of run metrics."""
        return self.root / "state"

    @property
    def profiles(self) -> Path:
        """Generated reports (profile.md, profile.json)."""
        return self.root / "profiles"


def parse_product_codes(value: str | None) -> frozenset[str] | None:
    """'cgm' -> the CGM codes, 'all' or empty -> no filter, 'QBJ,PQF' -> those codes."""
    if value is None or value.strip().lower() in {"", "all"}:
        return None
    if value.strip().lower() == "cgm":
        return frozenset(CGM_PRODUCT_CODES)
    codes = {c.strip().upper() for c in value.split(",") if c.strip()}
    return frozenset(codes) or None
