from __future__ import annotations

import os
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request

from file_manager import FileManager
from models import TaskState
from stream_manager import stream_file
from task_manager import TaskManager
from torrent_manager import TorrentManager

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
        "files": [f.__dict__ for f in task.files],
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
    task = tasks.tasks.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    tasks.refresh(task)
    return {
        "id": task.id,
        "hash": task.torrent_hash,
        "name": task.name,
        "state": task.state,
        "progress": task.progress,
        "files": [f.__dict__ for f in task.files],
    }


@app.get("/tasks/{task_id}/state")
def get_state(task_id: str):
    task = tasks.tasks.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
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
    task = tasks.tasks.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    torrent_file = next(
        (f for f in task.files if f.index == file_index),
        None,
    )
    if not torrent_file:
        raise HTTPException(404, "File not found")
    if not torrent_file.is_video:
        raise HTTPException(415, "Selected file is not a video")

    task.state = TaskState.STREAMING
    return stream_file(request, task, torrent_file, files)

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
