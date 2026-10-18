"""Command line: `lotcheck <command> [options]`. Run `lotcheck -h` for help."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

from . import __version__
from .config import (
    CLASSIFICATION_DATASET,
    EVENT_DATASET,
    RECALL_DATASET,
    DataPaths,
    parse_product_codes,
)
from .download import USER_AGENT, download_many
from .manifest import fetch_manifest, list_partitions, select_partitions, summarize
from .normalize import normalize_classification, normalize_events, normalize_recalls
from .profile import run_profile

DATASETS = (EVENT_DATASET, RECALL_DATASET, CLASSIFICATION_DATASET)


def _client() -> httpx.Client:
    return httpx.Client(follow_redirects=True, headers={"User-Agent": USER_AGENT})


def _raw_files(paths: DataPaths, dataset: str) -> list[Path]:
    return sorted((paths.raw / dataset.replace("/", "_")).glob("*.zip"))


def _plan(manifest: dict, dataset: str, since, until, include_undated: bool):
    parts = list_partitions(manifest, dataset)
    if dataset == EVENT_DATASET:  # reference datasets are small: always take all of them
        parts = select_partitions(parts, since, until, include_undated)
    return parts


def _download(paths, manifest, dataset, args, client, dry_run=False, force=False) -> dict:
    parts = _plan(manifest, dataset, args.since, args.until, args.include_undated)
    s = summarize(parts)
    print(f"{dataset}: {s['partitions']} files, ~{s['size_mb']:,.1f} MB zipped, "
          f"{s['records']:,} records, years {s['years']}")
    if dry_run:
        return {"planned": s, "failed": []}
    stats = download_many(parts, paths.raw, workers=args.workers, force=force, client=client)
    print(f"{dataset}: downloaded {stats['downloaded']}, already had {stats['skipped']}, "
          f"failed {len(stats['failed'])}, {stats['bytes'] / 1e9:,.2f} GB on disk")
    return stats


def cmd_manifest(args, paths: DataPaths) -> int:
    with _client() as client:
        manifest = fetch_manifest(paths.raw, client, refresh=args.refresh)
    datasets = DATASETS if args.dataset == "all" else (args.dataset,)
    for dataset in datasets:
        s = summarize(_plan(manifest, dataset, args.since, args.until, args.include_undated))
        line = f"{dataset}: {s['partitions']} files, {s['size_mb']:,.1f} MB zipped, {s['records']:,} records"
        if dataset == EVENT_DATASET:
            line += f", years {s['years']}, files without a year: {s['undated']}"
        print(line)
    return 0


def cmd_download(args, paths: DataPaths) -> int:
    with _client() as client:
        manifest = fetch_manifest(paths.raw, client, refresh=args.refresh_manifest)
        stats = _download(paths, manifest, args.dataset, args, client, dry_run=args.dry_run, force=args.force)
    return 1 if stats.get("failed") else 0


def cmd_normalize(args, paths: DataPaths) -> int:
    files = _raw_files(paths, args.dataset)
    if not files:
        print(f"No downloaded files for {args.dataset} in {paths.raw}. "
              f"Run `lotcheck download --dataset {args.dataset}` first.", file=sys.stderr)
        return 1
    if args.dataset == EVENT_DATASET:
        summary = normalize_events(files, paths, parse_product_codes(args.product_codes),
                                   force=args.force, batch_size=args.batch_size)
    elif args.dataset == RECALL_DATASET:
        summary = normalize_recalls(files, paths)
    else:
        summary = normalize_classification(files, paths)
    print(json.dumps(summary, indent=2, default=str))
    return 0


def cmd_profile(args, paths: DataPaths) -> int:
    _, metrics, md_path = run_profile(paths, top=args.top)
    print(f"Wrote {md_path}")
    for key in ("reports", "pct_usable_lot", "pct_duplicate_primary_text", "query_ms_total"):
        if metrics.get(key) is not None:
            print(f"  {key}: {metrics[key]}")
    return 0


def cmd_run(args, paths: DataPaths) -> int:
    failed = 0
    with _client() as client:
        manifest = fetch_manifest(paths.raw, client, refresh=True)
        for dataset in DATASETS:
            failed += len(_download(paths, manifest, dataset, args, client).get("failed", []))
    normalize_classification(_raw_files(paths, CLASSIFICATION_DATASET), paths)
    normalize_recalls(_raw_files(paths, RECALL_DATASET), paths)
    normalize_events(_raw_files(paths, EVENT_DATASET), paths, parse_product_codes(args.product_codes),
                     batch_size=args.batch_size)
    _, metrics, md_path = run_profile(paths)
    print(f"Done. Profile written to {md_path}")
    if failed:
        print(f"{failed} downloads failed. Rerun the same command to retry just those.")
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lotcheck",
        description="Ingest, normalize and profile FDA medical device adverse event data (openFDA).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--data-dir", type=Path, default=Path("data"),
                        help="where raw/, parquet/, state/ and profiles/ live (default: ./data)")
    sub = parser.add_subparsers(dest="command", required=True)

    def year_range(p: argparse.ArgumentParser, default_since=None) -> None:
        p.add_argument("--since", type=int, default=default_since, help="first year of event data to include")
        p.add_argument("--until", type=int, help="last year of event data to include")
        p.add_argument("--include-undated", action="store_true",
                       help="also include partition files that have no year in their name")

    m = sub.add_parser("manifest", help="show what openFDA offers (files, size, records)")
    m.add_argument("--dataset", default="all", help="device/event, device/recall, device/classification or all")
    year_range(m)
    m.add_argument("--refresh", action="store_true", help="re-download download.json")
    m.set_defaults(func=cmd_manifest)

    d = sub.add_parser("download", help="download partition zips (resumable)")
    d.add_argument("--dataset", default=EVENT_DATASET, choices=DATASETS)
    year_range(d)
    d.add_argument("--workers", type=int, default=2, help="parallel downloads (be polite: 2-4)")
    d.add_argument("--force", action="store_true", help="download again even if already finished")
    d.add_argument("--dry-run", action="store_true", help="show the plan without downloading")
    d.add_argument("--refresh-manifest", action="store_true")
    d.set_defaults(func=cmd_download)

    n = sub.add_parser("normalize", help="stream downloaded zips into Parquet tables")
    n.add_argument("--dataset", default=EVENT_DATASET, choices=DATASETS)
    n.add_argument("--product-codes", default="cgm", help="'cgm' (default), 'all', or a list like QBJ,PQF")
    n.add_argument("--batch-size", type=int, default=25_000, help="rows per Parquet file chunk")
    n.add_argument("--force", action="store_true", help="reprocess files that are already done")
    n.set_defaults(func=cmd_normalize)

    p = sub.add_parser("profile", help="profile the Parquet tables with DuckDB (writes profiles/profile.md)")
    p.add_argument("--top", type=int, default=15, help="rows per ranking table")
    p.set_defaults(func=cmd_profile)

    r = sub.add_parser("run", help="download + normalize + profile in one command")
    year_range(r, default_since=2025)
    r.add_argument("--product-codes", default="cgm")
    r.add_argument("--workers", type=int, default=2)
    r.add_argument("--batch-size", type=int, default=25_000)
    r.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = DataPaths(args.data_dir)
    try:
        return args.func(args, paths)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"Network error: {exc}. Rerun the same command to resume.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped. Rerun the same command to pick up where it left off.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
