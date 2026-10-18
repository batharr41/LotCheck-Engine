import pytest

from helpers import make_zip
from lotcheck.cli import main
from lotcheck.config import parse_product_codes
from lotcheck.normalize import normalize_classification, normalize_events
from lotcheck.profile import run_profile


def quiet(*_args) -> None:
    pass


def test_profile_end_to_end(data_paths, event_zip):
    normalize_events([event_zip], data_paths, parse_product_codes("cgm"), progress=quiet)
    cls = make_zip(
        data_paths.raw / "device_classification" / "classification__device-classification-0001-of-0001.json.zip",
        [{"product_code": "QBJ", "device_name": "Integrated Continuous Glucose Monitoring System, Factory Calibrated",
          "device_class": "2"}],
    )
    normalize_classification([cls], data_paths, progress=quiet)

    md, metrics, path = run_profile(data_paths, top=10)

    assert path.exists() and (data_paths.profiles / "profile.json").exists()
    assert metrics["reports"] == 6
    assert metrics["pct_usable_lot"] == 50.0
    assert metrics["top_lot"] == {"manufacturer": "ACME DIABETES INC.", "lot": "1725204004", "reports": 2}
    assert metrics["pct_duplicate_primary_text"] == 66.7
    assert "Integrated Continuous Glucose Monitoring System" in md
    assert "Premature End-of-Life Indicator" in md
    assert "## Query timings" in md


def test_profile_needs_data(data_paths):
    with pytest.raises(FileNotFoundError):
        run_profile(data_paths)


def test_cli_normalize_then_profile(data_paths, event_zip, capsys):
    root = str(data_paths.root)
    assert main(["--data-dir", root, "normalize"]) == 0
    assert main(["--data-dir", root, "profile", "--top", "5"]) == 0
    assert "profile.md" in capsys.readouterr().out
    assert (data_paths.profiles / "profile.md").exists()


def test_cli_normalize_without_downloads(tmp_path):
    assert main(["--data-dir", str(tmp_path / "empty"), "normalize"]) == 1
