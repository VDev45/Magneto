from __future__ import annotations

import base64
import binascii
import json
import re
import time
from typing import Any

import requests
from requests import HTTPError

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
        # qBittorrent 4.x answers 200 with body "Ok."; 5.x answers 204 No
        # Content. Both mean authenticated, so only an explicit 403 is a
        # rejection -- checking the body alone broke every call on 5.x.
        if response.status_code == 403:
            raise RuntimeError(
                f"qBittorrent rejected the credentials (403): {response.text}"
            )

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
        try:
            result = self._post(
                "/api/v2/torrents/add",
                urls=task.magnet,
                savepath=save_path,
                tags=task.id,
            )
        except HTTPError as exc:
            if exc.response.status_code != 409:
                raise
            # 409: qBittorrent already has this torrent (its BT_backup lives in
            # the persistent qbit-config). Adopt it under this task's tag so
            # wait_for_hash can still find it -- otherwise the tag lookup times
            # out against a torrent tagged for an earlier task.
            infohash = self._infohash(task.magnet)
            if not infohash:
                raise RuntimeError(
                    "qBittorrent reported the magnet as already present "
                    "but its infohash could not be parsed"
                ) from exc
            self._post(
                "/api/v2/torrents/setTags", hashes=infohash, tags=task.id
            )
            return

        # qBittorrent 4.x replies "Ok."; 5.x replies JSON carrying counts.
        if not self._add_accepted(result):
            raise RuntimeError(f"qBittorrent rejected magnet: {result}")

    @staticmethod
    def _add_accepted(result: str) -> bool:
        """Did torrents/add accept the magnet? Both API generations say yes."""
        if result.strip().lower() in {"ok.", "ok"}:
            return True
        try:
            payload = json.loads(result)
        except ValueError:
            return False
        return bool(payload.get("success_count"))

    @staticmethod
    def _infohash(magnet: str) -> str | None:
        match = re.search(r"urn:btih:([0-9a-zA-Z]+)", magnet)
        if not match:
            return None
        digest = match.group(1)
        # Base32 (32 chars) and hex (40 chars) infohashes are both legal.
        if len(digest) == 32:
            try:
                digest = base64.b32decode(digest).hex()
            except (ValueError, binascii.Error):
                return None
        return digest.lower() if len(digest) == 40 else None

    def wait_for_hash(self, task: Task, timeout: int = 60) -> None:
        """Block until the torrent exists *and* its metadata has arrived.

        Waiting only for the hash is not enough: a magnet lists no files until
        qBittorrent has fetched metadata from peers, and file selection is
        impossible before then. Do not start the torrent stopped to "avoid
        downloading" -- qBittorrent then never fetches metadata at all. File
        priority (see select_files) is what actually limits the download.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            torrents = self._get("/api/v2/torrents/info", tag=task.id)
            if torrents:
                torrent = torrents[0]
                task.torrent_hash = torrent["hash"]
                task.name = torrent["name"]
                files = self._get(
                    "/api/v2/torrents/files", hash=task.torrent_hash
                )
                if files:
                    return
            time.sleep(1)
        if task.torrent_hash:
            raise TimeoutError(
                f"Metadata never arrived for {task.torrent_hash} -- no peers? "
                "Check the trackers and that outbound TCP/6881 is not blocked."
            )
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
            # qBittorrent 5.x renamed these: `hash` not `hashes`, and
            # `priority` not `prio`. The 4.x spelling returns 400.
            self._post(
                "/api/v2/torrents/filePrio",
                hash=task.torrent_hash,
                id="|".join(map(str, deselected)),
                priority=0,
            )
        if selected:
            self._post(
                "/api/v2/torrents/filePrio",
                hash=task.torrent_hash,
                id="|".join(map(str, selected)),
                priority=1,
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
