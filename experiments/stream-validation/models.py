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
    # Byte offset of this file's first byte within the whole torrent, and the
    # torrent's piece size. Together they map a file-relative byte offset onto
    # a piece index, which is what makes waiting for real data possible.
    offset: int = 0
    piece_size: int = 0
    # Inclusive [first, last] piece span as reported by the engine. Kept
    # alongside the arithmetic so the two can be cross-checked in tests.
    piece_range: tuple[int, int] | None = None

    def piece_span(self, start: int, end: int) -> tuple[int, int] | None:
        """Inclusive piece indices covering file bytes [start, end].

        Returns None when the piece size is unknown, which happens before
        metadata resolves. Callers must treat that as "cannot reason about
        availability" and fall back to serving without waiting.
        """
        if self.piece_size <= 0:
            return None
        first = (self.offset + start) // self.piece_size
        last = (self.offset + end) // self.piece_size
        return first, last

    def piece_start_byte(self, piece: int) -> int:
        """File-relative byte offset where the given piece begins.

        Bytes before this offset within the same piece belong to an
        earlier file, so a partial wait must stop here rather than
        spilling into a neighbour's bytes.
        """
        if self.piece_size <= 0:
            return 0
        return max(0, piece * self.piece_size - self.offset)

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
            "offset": self.offset,
            "piece_size": self.piece_size,
            "piece_range": list(self.piece_range)
            if self.piece_range
            else None,
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
