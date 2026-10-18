import pytest

from lotcheck.lots import lot_shape, normalize_lot


@pytest.mark.parametrize(
    ("raw", "lot", "status"),
    [
        ("1725204004", "1725204004", "ok"),
        (" t60002615 ", "T60002615", "ok"),
        ("Lot# 5268240", "5268240", "ok"),
        ("LOT NUMBER: P13465867", "P13465867", "ok"),
        ("NA123", "NA123", "ok"),  # starts like a placeholder but is a real value
        ("", None, "blank"),
        (None, None, "blank"),
        ("   ", None, "blank"),
        ("NI", None, "placeholder"),
        ("na", None, "placeholder"),
        ("N/A", None, "placeholder"),
        ("ASKU", None, "placeholder"),
        ("UNKNOWN", None, "placeholder"),
        ("Not provided", None, "placeholder"),
        ("---", None, "placeholder"),
        ("0", None, "placeholder"),
        ("LOT", None, "placeholder"),
    ],
)
def test_normalize_lot(raw, lot, status):
    assert normalize_lot(raw) == (lot, status)


def test_lot_shape():
    assert lot_shape("1725204004") == "D10"
    assert lot_shape("T60002615") == "A1D8"
    assert lot_shape("AB-12") == "A2-D2"
    assert lot_shape(None) is None
