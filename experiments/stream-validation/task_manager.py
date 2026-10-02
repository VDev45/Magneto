from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from time import time

from models import Task, TaskState
from torrent_manager import TorrentManager


class QueueState(StrEnum):
    RUNNING = "running"
    QUEUED = "queued"
    STOPPED = "stopped"


@dataclass
class TaskRecord:
    task: Task
    queue_state: QueueState = QueueState.QUEUED
    created_at: float = 0.0
    updated_at: float = 0.0


class TaskManager:
    """Orchestrates Magneto tasks; Telegram must not talk to qBittorrent directly."""

    def __init__(self, torrents: TorrentManager, max_running: int = 2):
        self.torrents = torrents
        self.max_running = max_running
        self.tasks: dict[str, TaskRecord] = {}

    def create(self, task_id: str, magnet: str) -> Task:
        if task_id in self.tasks:
            raise ValueError(f"Task already exists: {task_id}")
        now = time()
        task = Task(id=task_id, magnet=magnet)
        self.tasks[task_id] = TaskRecord(task, created_at=now, updated_at=now)
        return task

    def get(self, task_id: str) -> Task:
        record = self.tasks.get(task_id)
        if not record:
            raise KeyError(task_id)
        return record.task

    def _touch(self, task: Task) -> None:
        self.tasks[task.id].updated_at = time()

    def _running_count(self) -> int:
        return sum(
            r.queue_state == QueueState.RUNNING
            and r.task.state not in {TaskState.COMPLETED, TaskState.CANCELLED, TaskState.FAILED}
            for r in self.tasks.values()
        )

    def prepare(self, task: Task, save_path: str) -> Task:
        task.state = TaskState.METADATA
        self._touch(task)
        try:
            self.torrents.add_magnet(task, save_path)
            self.torrents.wait_for_hash(task)
            self.torrents.refresh(task)
            task.state = TaskState.QUEUED
        except Exception as exc:
            task.state = TaskState.FAILED
            task.error = str(exc)
            raise
        self._touch(task)
        return task

    def select_and_start(self, task: Task, selected: set[int]) -> Task:
        self.torrents.select_files(task, selected)

        if self._running_count() >= self.max_running:
            task.state = TaskState.QUEUED
            self.tasks[task.id].queue_state = QueueState.QUEUED
        else:
            self.torrents.start(task)
            task.state = TaskState.DOWNLOADING
            self.tasks[task.id].queue_state = QueueState.RUNNING

        self._touch(task)
        return task

    def refresh(self, task: Task) -> Task:
        try:
            self.torrents.refresh(task)
            record = self.tasks[task.id]
            if task.state == TaskState.COMPLETED:
                record.queue_state = QueueState.STOPPED
            self._touch(task)
            return task
        except Exception as exc:
            task.state = TaskState.FAILED
            task.error = str(exc)
            self.tasks[task.id].queue_state = QueueState.STOPPED
            self._touch(task)
            raise

    def cancel(self, task: Task) -> None:
        self.torrents.stop(task)
        task.state = TaskState.CANCELLED
        self.tasks[task.id].queue_state = QueueState.STOPPED
        self._touch(task)

    def remove(self, task: Task) -> None:
        self.torrents.remove(task)
        self.tasks[task.id].queue_state = QueueState.STOPPED
        self.tasks.pop(task.id, None)

    def list(self) -> list[Task]:
        return [record.task for record in self.tasks.values()]
