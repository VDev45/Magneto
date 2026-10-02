from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from file_manager import FileManager
from models import Task, TorrentFile

if TYPE_CHECKING:
    from torrent_manager import TorrentManager as EngineGate

# How long a single request may block waiting for pieces before we answer
# with what we have. A media player holding a request open forever is worse
# than an honest short response: it can retry, but it cannot wait on a
# socket that never speaks.
PIECE_WAIT_SECONDS = float(os.getenv("STREAM_PIECE_WAIT", "30"))

# Bytes read per iteration. Also the granularity at which we re-check that
# the download is still making progress.
READ_CHUNK = 1024 * 1024


def parse_range(value: str | None, size: int) -> tuple[int, int]:
    if not value:
        return 0, size - 1

    if not value.startswith("bytes="):
        raise HTTPException(416, "Invalid Range")

    raw = value[6:].split(",", 1)[0].strip()
    if "-" not in raw:
        raise HTTPException(416, "Invalid Range")

    start_text, end_text = raw.split("-", 1)
    if start_text:
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
    else:
        suffix = int(end_text)
        if suffix <= 0:
            raise HTTPException(416, "Invalid Range")
        start = max(0, size - suffix)
        end = size - 1

    if start < 0 or start >= size or start > end:
        raise HTTPException(416, "Range Not Satisfiable")
    return start, min(end, size - 1)


def _await_available(
    engine: "EngineGate",
    task: Task,
    torrent_file: TorrentFile,
    start: int,
    end: int,
    path: Path,
    wait_seconds: float,
) -> tuple[int, bool]:
    """Narrow [start, end] to the bytes we can actually serve.

    Returns (served_end, gated). Without piece geometry -- before metadata
    resolves, or if the engine cannot report it -- we fall back to clamping
    against the on-disk size and report gated=False, which preserves the
    pre-existing behaviour exactly.
    """
    span = torrent_file.piece_span(start, end)
    if span is None:
        return min(end, _downloaded_prefix(path, start, end)), False

    first, last = span
    try:
        missing = engine.wait_for_pieces(
            task, first, last, timeout=wait_seconds
        )
    except Exception:
        # Never let a piece-state query failure turn into a bad response.
        # Serving the old behaviour beats a 500 mid-playback.
        return min(end, _downloaded_prefix(path, start, end)), False

    if missing is None:
        return end, True

    # Everything from the start of the missing piece onward is unverified.
    boundary = torrent_file.piece_start_byte(missing)
    served_end = min(end, max(start - 1, boundary - 1))
    return served_end, True


def _downloaded_prefix(path: Path, start: int, end: int) -> int:
    """Last byte before a run of zeros in [start, end], or start - 1.

    A fallback for when piece geometry is unavailable. Scans for a long
    zero run rather than a single zero byte, because real media legitimately
    contains zero bytes.
    """
    limit = end - start + 1
    if limit <= 0:
        return start - 1
    window = min(limit, 8 * 1024 * 1024)
    with path.open("rb") as handle:
        handle.seek(start)
        blob = handle.read(window)
    zeros = 0
    last_real = -1
    for index, byte in enumerate(blob):
        if byte == 0:
            zeros += 1
            if zeros >= READ_CHUNK and index > 0:
                return start + last_real
        else:
            zeros = 0
            last_real = index
    return end if len(blob) >= limit else start + last_real


def stream_file(
    request: Request,
    task: Task,
    torrent_file: TorrentFile,
    files: FileManager,
    engine: "EngineGate | None" = None,
    wait_seconds: float = PIECE_WAIT_SECONDS,
) -> StreamingResponse:
    path = files.path_for(task, torrent_file)
    if not path.exists():
        raise HTTPException(425, "Selected file is not available yet")

    start, end = parse_range(request.headers.get("range"), torrent_file.size)
    content_type = (
        mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    )

    # Clamp to what is actually on disk. qBittorrent preallocates, so the
    # file is full size from the first second; reading past the downloaded
    # frontier returns sparse zeros, which a decoder renders as garbage or
    # silently drops. PLAN.md §12.
    served_end = end
    gated = False
    if engine is not None:
        served_end, gated = _await_available(
            engine, task, torrent_file, start, end, path, wait_seconds
        )

    length = served_end - start + 1
    if length <= 0:
        raise HTTPException(
            425,
            "Requested range is not downloaded yet",
            headers={"Accept-Ranges": "bytes"},
        )

    def body() -> Iterator[bytes]:
        remaining = length
        with path.open("rb") as handle:
            handle.seek(start)
            while remaining:
                chunk = handle.read(min(READ_CHUNK, remaining))
                if not chunk:
                    # Sparse hole or truncated file. Stop cleanly rather than
                    # padding out to Content-Length, which would leave the
                    # client waiting on bytes that never arrive.
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Range": f"bytes {start}-{served_end}/{torrent_file.size}",
        "Cache-Control": "no-store",
    }
    if gated and served_end < end:
        # Tell the client the body is deliberately shorter than the range it
        # asked for, so it knows to come back for the rest instead of
        # treating this as a truncated transfer.
        headers["X-Magneto-Truncated"] = "piece-gate"

    return StreamingResponse(
        body(),
        status_code=206 if request.headers.get("range") else 200,
        headers=headers,
        media_type=content_type,
    )
