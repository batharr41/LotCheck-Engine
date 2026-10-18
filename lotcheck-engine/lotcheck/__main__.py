"""Allow `python -m lotcheck ...` (handy on Windows, where `make` is missing)."""

from .cli import main

raise SystemExit(main())
