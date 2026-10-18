import io
import json
import zipfile

import httpx

from lotcheck.download import download_many, download_partition, is_done
from lotcheck.manifest import Partition

PART = Partition(
    dataset="device/event",
    url="https://example.test/device/event/2025q1/device-event-0001-of-0001.json.zip",
    display_name="2025 Q1",
    size_mb=0.1,
    records=0,
    year=2025,
    quarter=1,
)


def zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("data.json", json.dumps({"meta": {}, "results": []}))
    return buf.getvalue()


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def no_sleep(_seconds: float) -> None:
    pass


def test_download_then_skip(tmp_path):
    payload, calls = zip_bytes(), []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=payload)

    with client_for(handler) as client:
        path, fresh = download_partition(PART, tmp_path, client)
        assert fresh and path.read_bytes() == payload and is_done(PART, tmp_path)
        _, fresh_again = download_partition(PART, tmp_path, client)
    assert not fresh_again and len(calls) == 1


def test_retries_server_errors(tmp_path):
    payload, attempts = zip_bytes(), {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(503) if attempts["n"] < 3 else httpx.Response(200, content=payload)

    with client_for(handler) as client:
        _, fresh = download_partition(PART, tmp_path, client, sleep=no_sleep)
    assert fresh and attempts["n"] == 3


def test_corrupt_zip_starts_over(tmp_path):
    payload, attempts = zip_bytes(), {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(200, content=b"not a zip" if attempts["n"] == 1 else payload)

    with client_for(handler) as client:
        path, fresh = download_partition(PART, tmp_path, client, sleep=no_sleep)
    assert fresh and zipfile.is_zipfile(path)


def test_resume_uses_range_request(tmp_path):
    payload = zip_bytes()
    half = len(payload) // 2
    partial = tmp_path / "device_event" / (PART.filename + ".part")
    partial.parent.mkdir(parents=True)
    partial.write_bytes(payload[:half])
    seen = {}

    def handler(request):
        seen["range"] = request.headers.get("range")
        return httpx.Response(206, content=payload[half:])

    with client_for(handler) as client:
        path, _ = download_partition(PART, tmp_path, client)
    assert seen["range"] == f"bytes={half}-"
    assert path.read_bytes() == payload


def test_missing_file_fails_fast(tmp_path):
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(404)

    with client_for(handler) as client:
        stats = download_many([PART], tmp_path, client=client, progress=lambda *_: None)
    assert stats["downloaded"] == 0 and len(stats["failed"]) == 1
    assert attempts["n"] == 1  # 404 is not retried
