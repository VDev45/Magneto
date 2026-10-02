from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Iterator

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from file_manager import FileManager
from models import Task, TorrentFile


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


def stream_file(
    request: Request,
    task: Task,
    torrent_file: TorrentFile,
    files: FileManager,
) -> StreamingResponse:
    path = files.path_for(task, torrent_file)
    if not path.exists():
        raise HTTPException(425, "Selected file is not available yet")

    start, end = parse_range(request.headers.get("range"), torrent_file.size)
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    def body() -> Iterator[bytes]:
        remaining = end - start + 1
        with path.open("rb") as handle:
            handle.seek(start)
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        body(),
        status_code=206 if request.headers.get("range") else 200,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
            "Content-Range": f"bytes {start}-{end}/{torrent_file.size}",
            "Cache-Control": "no-store",
        },
        media_type=content_type,
    )
