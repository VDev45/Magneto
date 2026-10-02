import unittest

from models import TaskState
from task_manager import TaskManager


class FakeTorrentManager:
    def __init__(self):
        self.started = []
        self.stopped = []
        self.removed = []

    def add_magnet(self, task, save_path):
        pass

    def wait_for_hash(self, task):
        task.torrent_hash = task.id
        task.name = task.id

    def refresh(self, task):
        task.progress = 0.0
        task.files = []

    def select_files(self, task, selected):
        task.selected_files = selected

    def start(self, task):
        self.started.append(task.id)

    def stop(self, task):
        self.stopped.append(task.id)

    def remove(self, task):
        self.removed.append(task.id)


class TaskManagerTests(unittest.TestCase):
    def test_queue_promotes_after_cancel(self):
        engine = FakeTorrentManager()
        manager = TaskManager(engine, max_running=1)
        first = manager.create("one", "magnet:one")
        second = manager.create("two", "magnet:two")
        manager.prepare(first, "/downloads")
        manager.prepare(second, "/downloads")
        manager.select_and_start(first, {0})
        manager.select_and_start(second, {0})
        self.assertEqual(engine.started, ["one"])
        self.assertEqual(second.state, TaskState.QUEUED)
        manager.cancel(first)
        self.assertEqual(engine.started, ["one", "two"])
        self.assertEqual(second.state, TaskState.DOWNLOADING)

    def test_remove_deletes_task(self):
        engine = FakeTorrentManager()
        manager = TaskManager(engine)
        task = manager.create("one", "magnet:one")
        task.torrent_hash = "hash"
        manager.remove(task)
        self.assertEqual(engine.removed, ["one"])
        with self.assertRaises(KeyError):
            manager.get("one")


if __name__ == "__main__":
    unittest.main()
