import unittest
from unittest import mock

import console
import main
from task_manager import TaskManager
from test_task_manager import FakeTorrentManager


class ConsoleStateTests(unittest.TestCase):
    """The 2s console poll must never raise -- a dead torrent should not
    blank the whole console."""

    def setUp(self):
        self.engine = FakeTorrentManager()
        patcher_tasks = mock.patch.object(main, "tasks", TaskManager(self.engine))
        patcher_torrents = mock.patch.object(main, "torrents", self.engine)
        self.addCleanup(patcher_tasks.stop)
        self.addCleanup(patcher_torrents.stop)
        patcher_tasks.start()
        patcher_torrents.start()

    def test_empty_snapshot(self):
        self.assertEqual(console.console_state(), {"tasks": []})

    def test_reports_state_and_files(self):
        task = main.tasks.create("abc", "magnet:?xt=one")
        task.torrent_hash = "abc"
        task.name = "Sintel"
        task.state = "downloading"
        snapshot = console.console_state()
        self.assertEqual(len(snapshot["tasks"]), 1)
        item = snapshot["tasks"][0]
        self.assertEqual(item["id"], "abc")
        self.assertEqual(item["magnet"], "magnet:?xt=one")
        self.assertEqual(item["download_speed"], 0)

    def test_engine_failure_is_reported_per_task(self):
        task = main.tasks.create("abc", "magnet:?xt=one")
        task.torrent_hash = "abc"

        def boom(_task):
            raise RuntimeError("qBittorrent unreachable")

        with mock.patch.object(self.engine, "status", boom):
            snapshot = console.console_state()
        self.assertEqual(len(snapshot["tasks"]), 1)
        self.assertIn("qBittorrent unreachable", snapshot["tasks"][0]["engine_error"])


class ConsoleHtmlTests(unittest.TestCase):
    def test_console_page_is_served(self):
        html = console.console()
        self.assertIn("<!doctype html>", html)
        self.assertIn("/console/state", html)
        # The probe is the thing that proves 206 Partial Content works.
        self.assertIn("206 Partial Content OK", html)


if __name__ == "__main__":
    unittest.main()