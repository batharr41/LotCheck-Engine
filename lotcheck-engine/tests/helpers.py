"""Builders for small, realistic openFDA-shaped test files."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

DESCRIPTION = "Description of Event or Problem"
TEMPLATE = (
    "IT WAS REPORTED THAT EARLY SENSOR EXPIRATION OCCURRED. "
    "NO PRODUCT OR DATA WAS PROVIDED FOR EVALUATION."
)


def make_zip(path: Path, records: list[dict], member: str = "data.json") -> Path:
    """Write records the way openFDA ships them: one JSON file inside a zip."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": {"results": {"total": len(records)}}, "results": records}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, json.dumps(payload))
    return path


def event(
    key: int,
    received: str,
    code: str,
    lot: str,
    maker: str = "ACME DIABETES INC.",
    brand: str = "ACME CGM 10",
    problems: tuple[str, ...] = ("Premature End-of-Life Indicator",),
    description: str = TEMPLATE,
    event_type: str = "Malfunction",
) -> dict:
    """One adverse event record with the same shape as openFDA's device/event data."""
    return {
        "mdr_report_key": str(key),
        "report_number": f"9999999-{received[:4]}-{key:06d}",
        "date_received": received,
        "event_type": event_type,
        "type_of_report": ["Initial submission"],
        "product_problems": list(problems) * 2,  # real data repeats terms; we dedupe them
        "device": [
            {
                "brand_name": brand,
                "generic_name": "CONTINUOUS GLUCOSE MONITOR",
                "manufacturer_d_name": maker,
                "model_number": "M-100",
                "lot_number": lot,
                "device_report_product_code": code,
                "openfda": {"device_name": "Integrated CGM", "device_class": "2"},
            }
        ],
        "mdr_text": [
            {"mdr_text_key": f"{key}1", "text_type_code": DESCRIPTION, "text": description},
            {"mdr_text_key": f"{key}2", "text_type_code": "Additional Manufacturer Narrative", "text": "(B)(4)."},
        ],
    }
