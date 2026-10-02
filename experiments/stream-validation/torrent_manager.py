from __future__ import annotations

import time
from typing import Any

import requests

from models import Task, TaskState, TorrentFile


class TorrentManager:
    """qBittorrent adapter. Nothing outside this class talks to its API."""

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Referer"] = self.base_url + "/"
        self.username = username
        self.password = password

    def login(self) -> None:
        response = self.session.post(
            f"{self.base_url}/api/v2/auth/login",
            data={"username": self.username, "password": self.password},
            timeout=15,
        )
        response.raise_for_status()
        if response.text.strip().lower() not in {"ok.", "ok"}:
            raise RuntimeError(f"qBittorrent login failed: {response.text}")

    def _get(self, path: str, **params: Any):
        response = self.session.get(
            f"{self.base_url}{path}", params=params, timeout=15
        )
        response.raise_for_status()
        return response.json()

    def _post(self, path: str, **data: Any) -> str:
        response = self.session.post(
            f"{self.base_url}{path}", data=data, timeout=15
        )
        response.raise_for_status()
        return response.text

    def add_magnet(self, task: Task, save_path: str) -> None:
        result = self._post(
            "/api/v2/torrents/add",
            urls=task.magnet,
            savepath=save_path,
            stopped="true",
            tags=task.id,
        )
        if result.strip() not in {"Ok.", "Ok"}:
            raise RuntimeError(f"qBittorrent rejected magnet: {result}")

    def wait_for_hash(self, task: Task, timeout: int = 60) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            torrents = self._get("/api/v2/torrents/info", tag=task.id)
            if torrents:
                torrent = torrents[0]
                task.torrent_hash = torrent["hash"]
                task.name = torrent["name"]
                return
            time.sleep(1)
        raise TimeoutError("Torrent did not appear in qBittorrent")

    def refresh(self, task: Task) -> None:
        if not task.torrent_hash:
            raise RuntimeError("Task has no torrent hash")
        torrents = self._get(
            "/api/v2/torrents/info", hashes=task.torrent_hash
        )
        if not torrents:
            raise RuntimeError("Torrent no longer exists")
        torrent = torrents[0]
        task.name = torrent["name"]
        task.progress = torrent["progress"]
        qbit_state = torrent.get("state", "")
        if torrent["progress"] >= 1:
            task.state = TaskState.COMPLETED
        elif qbit_state in {"pausedDL", "queuedDL"}:
            task.state = TaskState.QUEUED
        elif qbit_state in {"metaDL", "checkingDL"}:
            task.state = TaskState.METADATA
        else:
            task.state = TaskState.DOWNLOADING

        files = self._get(
            "/api/v2/torrents/files", hash=task.torrent_hash
        )
        task.files = [
            TorrentFile(
                index=f["index"],
                name=f["name"].rsplit("/", 1)[-1],
                size=f["size"],
                progress=f["progress"],
                priority=f["priority"],
                path=f["name"],
            )
            for f in files
        ]

    def select_files(self, task: Task, selected: set[int]) -> None:
        if not task.torrent_hash:
            raise RuntimeError("Task has no torrent hash")
        all_files = {f.index for f in task.files}
        invalid = selected - all_files
        if invalid:
            raise ValueError(f"Unknown file indexes: {sorted(invalid)}")

        deselected = all_files - selected
        if deselected:
            self._post(
                "/api/v2/torrents/filePrio",
                hash=task.torrent_hash,
                id="|".join(map(str, deselected)),
                prio=0,
            )
        if selected:
            self._post(
                "/api/v2/torrents/filePrio",
                hash=task.torrent_hash,
                id="|".join(map(str, selected)),
                prio=1,
            )
        task.selected_files = selected

    def start(self, task: Task) -> None:
        if not task.torrent_hash:
            raise RuntimeError("Task has no torrent hash")
        self._post("/api/v2/torrents/start", hashes=task.torrent_hash)

    def stop(self, task: Task) -> None:
        if task.torrent_hash:
            self._post("/api/v2/torrents/stop", hashes=task.torrent_hash)

    def piece_states(self, task: Task) -> list[int]:
        if not task.torrent_hash:
            raise RuntimeError("Task has no torrent hash")
        return self._get(
            "/api/v2/torrents/pieceStates", hash=task.torrent_hash
        )

    def status(self, task: Task) -> dict[str, Any] | None:
        """Raw engine info for one task, or None if the torrent is gone.

        Reporting stays behind this class so nothing else reaches the
        qBittorrent API directly (PLAN.md §9: TorrentManager owns "status").
        """
        if not task.torrent_hash:
            return None
        torrents = self._get(
            "/api/v2/torrents/info", hashes=task.torrent_hash
        )
        return torrents[0] if torrents else None

    def remove(self, task: Task, delete_files: bool = False) -> None:
        if not task.torrent_hash:
            return
        self._post(
            "/api/v2/torrents/delete",
            hashes=task.torrent_hash,
            deleteFiles=str(delete_files).lower(),
        )
