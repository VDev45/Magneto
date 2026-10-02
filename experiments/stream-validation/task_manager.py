from models import Task, TaskState
from torrent_manager import TorrentManager


class TaskManager:
    def __init__(self, torrents: TorrentManager):
        self.torrents = torrents
        self.tasks: dict[str, Task] = {}

    def create(self, task_id: str, magnet: str) -> Task:
        task = Task(id=task_id, magnet=magnet)
        self.tasks[task.id] = task
        return task

    def prepare(self, task: Task, save_path: str) -> Task:
        task.state = TaskState.METADATA
        self.torrents.add_magnet(task, save_path)
        self.torrents.wait_for_hash(task)
        self.torrents.refresh(task)
        return task

    def select_and_start(self, task: Task, selected: set[int]) -> Task:
        self.torrents.select_files(task, selected)
        self.torrents.start(task)
        task.state = TaskState.DOWNLOADING
        return task

    def refresh(self, task: Task) -> Task:
        self.torrents.refresh(task)
        return task

    def cancel(self, task: Task) -> None:
        self.torrents.stop(task)
        task.state = TaskState.CANCELLED
