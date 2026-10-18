"""Download partition zips: resumable, retried, verified, and skipped when done.

Each finished file gets a small `.ok` marker next to it. A file without a
marker is treated as unfinished, so an interrupted run picks up where it
stopped (and resumes a half-downloaded file with an HTTP Range request).
"""

from __future__ import annotations

import json
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import httpx

from .manifest import Partition

_CHUNK = 1 << 20  # 1 MiB
USER_AGENT = "lotcheck-engine/0.1 (open-source portfolio project)"


def local_path(p: Partition, raw_dir: Path) -> Path:
    return raw_dir / p.dataset.replace("/", "_") / p.filename


def is_done(p: Partition, raw_dir: Path) -> bool:
    dest = local_path(p, raw_dir)
    return dest.exists() and dest.with_name(dest.name + ".ok").exists()


def _verify_zip(path: Path) -> None:
    with zipfile.ZipFile(path) as z:
        if not any(name.endswith(".json") for name in z.namelist()):
            raise zipfile.BadZipFile(f"{path.name}: no .json file inside")


def download_partition(
    p: Partition,
    raw_dir: Path,
    client: httpx.Client,
    retries: int = 4,
    force: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[Path, bool]:
    """Fetch one partition. Returns (path, downloaded_now)."""
    dest = local_path(p, raw_dir)
    marker = dest.with_name(dest.name + ".ok")
    if not force and dest.exists() and marker.exists():
        return dest, False

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    if force:
        tmp.unlink(missing_ok=True)

    for attempt in range(1, retries + 2):
        try:
            have = tmp.stat().st_size if tmp.exists() else 0
            headers = {"Range": f"bytes={have}-"} if have else {}
            timeout = httpx.Timeout(60.0, read=300.0)
            with client.stream("GET", p.url, headers=headers, timeout=timeout) as resp:
                if resp.status_code != 416:  # 416 = we already hold every byte
                    if resp.status_code not in (200, 206):
                        resp.raise_for_status()
                    mode = "ab" if (have and resp.status_code == 206) else "wb"
                    with open(tmp, mode) as fh:
                        for chunk in resp.iter_bytes(_CHUNK):
                            fh.write(chunk)
            _verify_zip(tmp)
            tmp.replace(dest)
            marker.write_text(
                json.dumps(
                    {
                        "url": p.url,
                        "bytes": dest.stat().st_size,
                        "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                ),
                encoding="utf-8",
            )
            return dest, True
        except zipfile.BadZipFile:
            tmp.unlink(missing_ok=True)  # corrupt file: start it over
            if attempt > retries:
                raise
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if 400 <= code < 500 and code not in (408, 429):
                raise  # a missing file won't appear by retrying
            if attempt > retries:
                raise
        except (httpx.HTTPError, OSError):
            if attempt > retries:
                raise
        sleep(min(60, 2**attempt))
    raise RuntimeError("unreachable")


def download_many(
    partitions: list[Partition],
    raw_dir: Path,
    workers: int = 2,
    force: bool = False,
    progress: Callable[[str], None] = print,
    client: httpx.Client | None = None,
    retries: int = 4,
) -> dict:
    """Download several partitions in parallel and report what happened."""
    stats: dict = {"downloaded": 0, "skipped": 0, "bytes": 0, "failed": []}
    own_client = client is None
    client = client or httpx.Client(follow_redirects=True, headers={"User-Agent": USER_AGENT})

    def one(p: Partition):
        try:
            path, fresh = download_partition(p, raw_dir, client, retries=retries, force=force)
            return p, path, fresh, None
        except Exception as exc:  # report and keep going; rerun to retry
            return p, None, False, exc

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [pool.submit(one, p) for p in partitions]
            for i, fut in enumerate(as_completed(futures), 1):
                p, path, fresh, err = fut.result()
                tag = f"[{i}/{len(partitions)}]"
                if err is not None:
                    stats["failed"].append({"file": p.filename, "error": str(err)})
                    progress(f"{tag} FAILED {p.filename}: {err}")
                    continue
                size = path.stat().st_size
                stats["bytes"] += size
                stats["downloaded" if fresh else "skipped"] += 1
                progress(f"{tag} {'got ' if fresh else 'have'} {p.filename} ({size / 1e6:,.1f} MB)")
    finally:
        if own_client:
            client.close()
    return stats
