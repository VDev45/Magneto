from pathlib import Path

from models import Task, TorrentFile


class FileManager:
    """Maps torrent-relative paths to the local qBittorrent download tree."""

    def __init__(self, root: str):
        self.root = Path(root).resolve()

    def path_for(self, task: Task, torrent_file: TorrentFile) -> Path:
        if not task.name:
            raise RuntimeError("Torrent name is unknown")

        # qBittorrent's content is rooted at <save>/<torrent name> for
        # multi-file torrents and directly at <save>/<file> for single-file
        # torrents. This validator uses the reported relative path and
        # validates the final resolved path before opening it.
        candidates = [
            (self.root / torrent_file.path).resolve(),
            (self.root / task.name / torrent_file.path).resolve(),
        ]
        for candidate in candidates:
            if self.root == candidate or self.root in candidate.parents:
                if candidate.exists():
                    return candidate

        # Return the multi-file candidate for a file that hasn't been
        # allocated yet; the caller can report a useful not-ready state.
        return candidates[-1]
