from dataclasses import dataclass, field
from enum import StrEnum


class TaskState(StrEnum):
    CREATED = "created"
    METADATA = "metadata"
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    STREAMING = "streaming"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TorrentFile:
    index: int
    name: str
    size: int
    progress: float
    priority: int
    path: str

    @property
    def is_video(self) -> bool:
        return self.name.rsplit(".", 1)[-1].lower() in {
            "mp4", "mkv", "webm", "avi", "mov", "m4v", "ts"
        } if "." in self.name else False


@dataclass
class Task:
    id: str
    magnet: str
    torrent_hash: str | None = None
    name: str | None = None
    state: TaskState = TaskState.CREATED
    progress: float = 0.0
    files: list[TorrentFile] = field(default_factory=list)
    selected_files: set[int] = field(default_factory=set)
    error: str | None = None
