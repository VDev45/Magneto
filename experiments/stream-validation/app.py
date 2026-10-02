from __future__ import annotations

import mimetypes
import os
import time
from pathlib import Path
from typing import Iterator

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

QBIT_URL = os.getenv("QBIT_URL", "http://127.0.0.1:8080").rstrip("/")
QBIT_USERNAME = os.getenv("QBIT_USERNAME", "admin")
QBIT_PASSWORD = os.getenv("QBIT_PASSWORD", "adminadmin")
QBIT_SAVE_PATH = Path(os.getenv("QBIT_SAVE_PATH", "/downloads"))
MAGNET_URI = os.getenv("MAGNET_URI")

app = FastAPI(title="Magneto Stream Validation")
session = requests.Session()
session.headers.update({"Referer": QBIT_URL + "/"})


def qbit_login() -> None:
    response = session.post(
        f"{QBIT_URL}/api/v2/auth/login",
        data={"username": QBIT_USERNAME, "password": QBIT_PASSWORD},
        timeout=15,
    )
    response.raise_for_status()
    if response.text.strip().lower() != "ok.":
        raise RuntimeError(f"qBittorrent login failed: {response.text}")


def qbit_get(path: str, **params):
    response = session.get(f"{QBIT_URL}{path}", params=params, timeout=15)
    response.raise_for_status()
    return response.json()


def qbit_post(path: str, **data):
    response = session.post(f"{QBIT_URL}{path}", data=data, timeout=15)
    response.raise_for_status()
    return response.text


def wait_for_torrent(timeout: int = 300) -> dict:
    if not MAGNET_URI:
        raise RuntimeError("MAGNET_URI is required")

    qbit_login()
    qbit_post(
        "/api/v2/torrents/add",
        urls=MAGNET_URI,
        savepath=str(QBIT_SAVE_PATH),
        sequentialDownload="true",
        firstLastPiecePrio="true",
    )

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        torrents = qbit_get("/api/v2/torrents/info")
        if torrents:
            # The newest torrent is sufficient for this isolated experiment.
            torrent = torrents[0]
            files = qbit_get("/api/v2/torrents/files", hash=torrent["hash"])
            videos = [
                f for f in files
                if Path(f["name"]).suffix.lower()
                in {".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".ts"}
            ]
            if videos:
                videos.sort(key=lambda f: f["size"], reverse=True)
                torrent["selected_file"] = videos[0]
                return torrent
        time.sleep(2)

    raise TimeoutError("Timed out waiting for torrent metadata")


def safe_local_path(torrent: dict) -> Path:
    file_name = Path(torrent["selected_file"]["name"])

    # qBittorrent reports torrent-relative paths. Only allow a path rooted
    # below the configured download directory.
    candidate = (QBIT_SAVE_PATH / file_name).resolve()
    root = QBIT_SAVE_PATH.resolve()

    if root != candidate and root not in candidate.parents:
        raise RuntimeError("Resolved torrent path escapes download directory")

    return candidate


def parse_range(value: str | None, size: int) -> tuple[int, int]:
    if not value:
        return 0, size - 1

    if not value.startswith("bytes="):
        raise HTTPException(status_code=416, detail="Invalid Range")

    raw = value[6:].split(",", 1)[0].strip()
    if "-" not in raw:
        raise HTTPException(status_code=416, detail="Invalid Range")

    start_text, end_text = raw.split("-", 1)

    if start_text:
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
    else:
        suffix = int(end_text)
        if suffix <= 0:
            raise HTTPException(status_code=416, detail="Invalid Range")
        start = max(0, size - suffix)
        end = size - 1

    if start < 0 or start >= size or start > end:
        raise HTTPException(status_code=416, detail="Range Not Satisfiable")

    return start, min(end, size - 1)


def iter_file(path: Path, start: int, end: int, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    remaining = end - start + 1
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining:
            chunk = handle.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@app.get("/")
def root():
    return {
        "project": "Magneto",
        "mode": "stream-validation",
        "message": "Set MAGNET_URI and start the validator.",
    }


@app.get("/probe")
def probe():
    if not MAGNET_URI:
        raise HTTPException(status_code=400, detail="MAGNET_URI is not set")

    torrent = wait_for_torrent(timeout=10)
    selected = torrent["selected_file"]
    path = safe_local_path(torrent)

    return {
        "torrent": torrent["name"],
        "hash": torrent["hash"],
        "file": selected,
        "local_path": str(path),
        "exists": path.exists(),
        "file_size_on_disk": path.stat().st_size if path.exists() else 0,
        "stream_url": "/stream",
    }


@app.get("/stream")
def stream(request: Request):
    if not MAGNET_URI:
        raise HTTPException(status_code=400, detail="MAGNET_URI is not set")

    torrent = wait_for_torrent(timeout=30)
    selected = torrent["selected_file"]
    path = safe_local_path(torrent)

    if not path.exists():
        raise HTTPException(
            status_code=425,
            detail="Video file has not been allocated yet; wait for the first piece.",
        )

    size = selected["size"]
    start, end = parse_range(request.headers.get("range"), size)
    content_length = end - start + 1
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": content_type,
        "Content-Range": f"bytes {start}-{end}/{size}",
        "Cache-Control": "no-store",
    }

    return StreamingResponse(
        iter_file(path, start, end),
        status_code=206 if request.headers.get("range") else 200,
        headers=headers,
        media_type=content_type,
    )
