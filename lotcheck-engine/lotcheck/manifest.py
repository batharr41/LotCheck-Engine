"""Read openFDA's download manifest (download.json) and choose partition files.

openFDA publishes every dataset as zipped JSON "partitions". The manifest
lists each file's URL, size and record count, so we can plan a download
before fetching anything.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import MANIFEST_URL

_PERIOD = re.compile(r"/(\d{4})q([1-4])/", re.IGNORECASE)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


@dataclass(frozen=True)
class Partition:
    dataset: str
    url: str
    display_name: str
    size_mb: float | None
    records: int | None
    year: int | None
    quarter: int | None

    @property
    def filename(self) -> str:
        """Unique local file name.

        Partition files reuse names across quarters (device-event-0001-of-0004.json.zip
        appears in many folders), so the parent folder is kept in the name.
        """
        parts = [p for p in self.url.split("/") if p]
        return "__".join(parts[-2:])

    @property
    def stem(self) -> str:
        return self.filename.removesuffix(".zip").removesuffix(".json")


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch_manifest(raw_dir: Path, client: httpx.Client, refresh: bool = False) -> dict:
    """Download download.json once and cache it in raw/ (use refresh=True to update)."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / "manifest.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    resp = client.get(MANIFEST_URL, timeout=60.0)
    resp.raise_for_status()
    path.write_bytes(resp.content)
    return resp.json()


def list_partitions(manifest: dict, dataset: str) -> list[Partition]:
    """All partition files for a dataset such as 'device/event'."""
    node = manifest.get("results", manifest)
    for key in dataset.split("/"):
        if not isinstance(node, dict) or key not in node:
            raise KeyError(f"dataset {dataset!r} not found in the manifest")
        node = node[key]

    partitions: list[Partition] = []
    for item in node.get("partitions", []):
        url = item.get("file") or item.get("url")
        if not url:
            continue
        name = item.get("display_name") or ""
        period = _PERIOD.search(url)
        year = int(period.group(1)) if period else None
        quarter = int(period.group(2)) if period else None
        if year is None:
            found = _YEAR.search(name)
            year = int(found.group(0)) if found else None
        partitions.append(
            Partition(
                dataset=dataset,
                url=url,
                display_name=name,
                size_mb=_to_float(item.get("size_mb")),
                records=_to_int(item.get("records")),
                year=year,
                quarter=quarter,
            )
        )
    return partitions


def select_partitions(
    partitions: list[Partition],
    since: int | None = None,
    until: int | None = None,
    include_undated: bool = False,
) -> list[Partition]:
    """Keep partitions inside [since, until]. Undated files (older bundles) are
    kept when no range is given or when include_undated is set."""
    chosen = []
    for p in partitions:
        if p.year is None:
            if include_undated or (since is None and until is None):
                chosen.append(p)
            continue
        if since is not None and p.year < since:
            continue
        if until is not None and p.year > until:
            continue
        chosen.append(p)
    return chosen


def summarize(partitions: list[Partition]) -> dict:
    years = sorted({p.year for p in partitions if p.year is not None})
    return {
        "partitions": len(partitions),
        "size_mb": round(sum(p.size_mb or 0 for p in partitions), 1),
        "records": sum(p.records or 0 for p in partitions),
        "years": f"{years[0]}-{years[-1]}" if years else "n/a",
        "undated": sum(1 for p in partitions if p.year is None),
    }
