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

    def to_dict(self) -> dict[str, object]:
        """Explicit API shape.

        Do not serialise with ``__dict__``: is_video is a property, so it is
        absent from the instance dict. The console's Range probe looks the
        video file up by that key and silently reported 'no video file
        selected' for every torrent.
        """
        return {
            "index": self.index,
            "name": self.name,
            "size": self.size,
            "progress": self.progress,
            "priority": self.priority,
            "path": self.path,
            "is_video": self.is_video,
        }


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
