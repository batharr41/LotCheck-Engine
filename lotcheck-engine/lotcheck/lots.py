"""Clean up lot numbers.

In the real data about 1 in 3 CGM device entries has no usable lot: the field
is blank or holds a placeholder such as "NI" (no information), "NA" or
"ASKU" (asked, unknown). Those must count as missing, not as a lot called
"NI" with 200,000 reports.
"""

from __future__ import annotations

import re

# HL7-style null flavors and common hand-typed equivalents.
NULL_FLAVORS = {
    "", "NI", "NA", "N/A", "N.A.", "N.A", "UNK", "UNKN", "UNKNOWN", "UNKOWN", "UKN",
    "ASKU", "NASK", "MSK", "NAV", "NONE", "NULL", "NIL", "?", "-", "--", "---",
    "0", "00", "000", "NOT PROVIDED", "NOT AVAILABLE", "NOT KNOWN", "NOT APPLICABLE",
    "UNAVAILABLE", "NO LOT", "NO LOT NUMBER",
}

_PLACEHOLDER_PATTERNS = [
    re.compile(p)
    for p in (
        r"^UNKNOWN",
        r"^NOT\s+(PROVIDED|AVAILABLE|KNOWN|APPLICABLE|REPORTED|GIVEN)",
        r"^N/?A\b",
        r"^(UN)?AVAILABLE",
        r"^SEE\s",
        r"^VARIOUS",
        r"^MULTIPLE",
        r"^ALL\s+LOTS?",
    )
]

# "LOT# 123", "LOT NO. 123", "LOT NUMBER: 123", "L/N 123" -> "123"
_PREFIX = re.compile(r"^(?:LOT\s*(?:NUMBER|NUM|NO\.?|#)?|L/N)\s*[:#.\-]?\s*")
_WS = re.compile(r"\s+")


def normalize_lot(raw) -> tuple[str | None, str]:
    """Return (normalized_lot, status), status being 'ok', 'blank' or 'placeholder'."""
    if raw is None:
        return None, "blank"
    text = _WS.sub(" ", str(raw).strip().upper())
    if not text:
        return None, "blank"
    if text in NULL_FLAVORS or any(p.search(text) for p in _PLACEHOLDER_PATTERNS):
        return None, "placeholder"
    text = _PREFIX.sub("", text).replace(" ", "")
    if text in NULL_FLAVORS or not re.search(r"[0-9A-Z]", text):
        return None, "placeholder"
    return text, "ok"


def lot_shape(lot: str | None) -> str | None:
    """Coarse format signature: '1725204004' -> 'D10', 'T60002615' -> 'A1D8'.

    Different makers use different shapes, which helps spot typos and tells
    the extraction step what a valid lot looks like for each manufacturer.
    """
    if not lot:
        return None
    out: list[str] = []
    prev_kind, run = "", 0
    for ch in lot:
        kind = "D" if ch.isdigit() else "A" if ch.isalpha() else ch
        if kind == prev_kind and kind in "AD":
            run += 1
            continue
        if prev_kind:
            out.append(f"{prev_kind}{run}" if prev_kind in "AD" else prev_kind)
        prev_kind, run = kind, 1
    out.append(f"{prev_kind}{run}" if prev_kind in "AD" else prev_kind)
    return "".join(out)
