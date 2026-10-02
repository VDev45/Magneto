from __future__ import annotations

import os
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request

from file_manager import FileManager
from models import TaskState
from stream_manager import stream_file
from task_manager import TaskManager
from torrent_manager import DOWNLOADED_PIECE, TorrentManager

app = FastAPI(title="Magneto Stream Validation")

QBIT_URL = os.getenv("QBIT_URL", "http://127.0.0.1:8080")
QBIT_USERNAME = os.getenv("QBIT_USERNAME", "admin")
QBIT_PASSWORD = os.getenv("QBIT_PASSWORD", "adminadmin")
SAVE_PATH = os.getenv("QBIT_REMOTE_SAVE_PATH", "/downloads")
LOCAL_PATH = os.getenv(
    "LOCAL_SAVE_PATH", "./experiments/stream-validation/downloads"
)

torrents = TorrentManager(QBIT_URL, QBIT_USERNAME, QBIT_PASSWORD)
tasks = TaskManager(torrents)
files = FileManager(LOCAL_PATH)


@app.get("/")
def root():
    return {"project": "Magneto", "mode": "architecture-validation"}


@app.post("/tasks")
def create_task(body: dict | None = None):
    # A per-request magnet is what makes multi-task and queue testing possible.
    # MAGNET_URI stays as a fallback so the README's single-magnet flow is
    # unaffected.
    magnet = (body or {}).get("magnet") or os.getenv("MAGNET_URI")
    if not magnet:
        raise HTTPException(
            400, "Provide a magnet in the request body or set MAGNET_URI"
        )

    task = tasks.create(str(uuid4()), magnet)
    torrents.login()
    tasks.prepare(task, SAVE_PATH)
    return {
        "id": task.id,
        "magnet": task.magnet,
        "hash": task.torrent_hash,
        "name": task.name,
        "state": task.state,
        "files": [f.to_dict() for f in task.files],
    }


@app.post("/tasks/{task_id}/select")
def select(task_id: str, body: dict):
    try:
        task = tasks.get(task_id)
    except KeyError:
        raise HTTPException(404, "Task not found")
    selected = {int(i) for i in body.get("file_indexes", [])}
    tasks.select_and_start(task, selected)
    return {"id": task.id, "state": task.state, "selected": sorted(selected)}


@app.get("/tasks/{task_id}")
def get_task(task_id: str):
    # tasks.tasks maps id -> TaskRecord; the managers take the Task itself.
    record = tasks.tasks.get(task_id)
    if not record:
        raise HTTPException(404, "Task not found")
    task = record.task
    tasks.refresh(task)
    return {
        "id": task.id,
        "hash": task.torrent_hash,
        "name": task.name,
        "state": task.state,
        "progress": task.progress,
        "files": [f.to_dict() for f in task.files],
    }


@app.get("/tasks/{task_id}/files/{file_index}/frontier")
def file_frontier(task_id: str, file_index: int, at: int | None = None):
    """What is actually on disk for one file, in bytes.

    This is the diagnostic behind the console's seek tester, so it has to
    describe the download as it is rather than as a linear picture suggests.

    There is no single frontier. libtorrent selects rarest-first, so at 35%
    on Sintel the 333 complete pieces sat in 217 separate runs with holes
    between them. A "frontier" therefore has to be stated as two different
    numbers, and conflating them is how the tester first came to report
    "inside the frontier" for a byte at 99% of the file:

    ``prefix_available``  the end of the unbroken run from byte 0. This is
                          what sequential playback can count on, and it is
                          exactly where stream_manager's _await_available
                          clamps: piece_start_byte(first hole) - 1.
    ``available_bytes``   given ?at=, how far playback could continue from
                          that byte before hitting the next hole. Scattered
                          pieces make this the number that matters for a
                          seek, and it is usually far below ``size``.

    The byte arithmetic lives here, not in the console, because it is the
    same arithmetic the gate uses and the two must not drift.
    """
    record = tasks.tasks.get(task_id)
    if not record:
        raise HTTPException(404, "Task not found")
    task = record.task

    torrent_file = next((f for f in task.files if f.index == file_index), None)
    if not torrent_file:
        raise HTTPException(404, "File not found")

    span = torrent_file.piece_range
    if not span:
        # Geometry unknown means metadata has not resolved. Say so rather than
        # reporting 0, which reads as "nothing downloaded".
        return {
            "index": torrent_file.index,
            "size": torrent_file.size,
            "piece_size": torrent_file.piece_size,
            "piece_range": None,
            "geometry_known": False,
            "prefix_available": 0,
            "prefix_missing_piece": None,
            "prefix_complete": False,
            "downloaded_pieces": 0,
            "total_pieces": 0,
            "at": at,
            "piece": None,
            "piece_state": None,
            "available_bytes": 0,
            "available_end": None,
            "seq_dl": None,
        }

    first, last = span
    states = torrents.piece_states(task)
    seq_dl = torrents.sequential_download(task)

    def present(piece: int) -> bool:
        # A piece the engine does not describe is not evidence it arrived.
        return 0 <= piece < len(states) and states[piece] >= DOWNLOADED_PIECE

    missing = torrents.first_missing_piece(task, first, last)
    if missing is None:
        prefix = torrent_file.size
    else:
        # Start of the missing piece, which is exactly where _await_available
        # stops: served_end = piece_start_byte(missing) - 1.
        prefix = min(torrent_file.piece_start_byte(missing), torrent_file.size)

    payload = {
        "index": torrent_file.index,
        "size": torrent_file.size,
        "piece_size": torrent_file.piece_size,
        "piece_range": [first, last],
        "geometry_known": True,
        "prefix_available": prefix,
        "prefix_missing_piece": missing,
        "prefix_complete": missing is None,
        "downloaded_pieces": sum(
            1 for piece in range(first, last + 1) if present(piece)
        ),
        "total_pieces": last - first + 1,
        # Reported so the console can explain a lagging prefix accurately
        # instead of advising the reader to enable a setting already on.
        "seq_dl": seq_dl,
        "at": at,
        "piece": None,
        "piece_state": None,
        "available_bytes": 0,
        "available_end": None,
    }
    if at is None:
        return payload

    clamped = max(0, min(at, torrent_file.size - 1))
    piece = torrent_file.piece_span(clamped, clamped)[0]
    # Walk forward over the unbroken run of complete pieces. This is the
    # number that answers "if I seek here, how much plays?".
    run_end = piece
    while run_end <= last and present(run_end):
        run_end += 1
    end_byte = min(
        torrent_file.piece_start_byte(run_end) - 1, torrent_file.size - 1
    )
    payload.update({
        "at": clamped,
        "piece": piece,
        "piece_state": states[piece] if 0 <= piece < len(states) else None,
        "available_bytes": max(0, end_byte - clamped + 1),
        "available_end": end_byte,
    })
    return payload


@app.get("/tasks/{task_id}/state")
def get_state(task_id: str):
    record = tasks.tasks.get(task_id)
    if not record:
        raise HTTPException(404, "Task not found")
    task = record.task
    tasks.refresh(task)
    pieces = torrents.piece_states(task)
    return {
        "state": task.state,
        "progress": task.progress,
        "piece_states": {
            "not_downloaded": pieces.count(0),
            "downloading": pieces.count(1),
            "downloaded": pieces.count(2),
            "total": len(pieces),
        },
    }


@app.get("/stream/{task_id}/{file_index}")
def stream(task_id: str, file_index: int, request: Request):
    record = tasks.tasks.get(task_id)
    if not record:
        raise HTTPException(404, "Task not found")
    task = record.task

    torrent_file = next(
        (f for f in task.files if f.index == file_index),
        None,
    )
    if not torrent_file:
        raise HTTPException(404, "File not found")
    if not torrent_file.is_video:
        raise HTTPException(415, "Selected file is not a video")

    task.state = TaskState.STREAMING
    # Pass the engine so the stream waits for the pieces covering the
    # requested range instead of serving sparse zeros (PLAN.md §12).
    return stream_file(request, task, torrent_file, files, engine=torrents)

@app.post("/tasks/{task_id}/cancel")
def cancel(task_id: str):
    try:
        task = tasks.get(task_id)
    except KeyError:
        raise HTTPException(404, "Task not found")
    tasks.cancel(task)
    return {"id": task.id, "state": task.state}

@app.delete("/tasks/{task_id}")
def remove(task_id: str):
    try:
        task = tasks.get(task_id)
    except KeyError:
        raise HTTPException(404, "Task not found")
    tasks.remove(task)
    return {"id": task_id, "removed": True}
