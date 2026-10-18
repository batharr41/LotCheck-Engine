PY ?= python3
VENV = .venv
BIN = $(VENV)/bin

.PHONY: setup test quickstart backfill profile clean-data

setup:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -e ".[dev]"

test:
	$(BIN)/pytest -q

quickstart:  ## one recent year of CGM reports, end to end
	$(BIN)/lotcheck run --since 2025

backfill:  ## the whole CGM history (large download: run `lotcheck manifest --since 2006` first)
	$(BIN)/lotcheck run --since 2006 --include-undated

profile:
	$(BIN)/lotcheck profile

clean-data:  ## delete derived data, keep the raw downloads
	rm -rf data/parquet data/state data/profiles
