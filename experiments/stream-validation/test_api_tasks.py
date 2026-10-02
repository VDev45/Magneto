import os
import unittest
from unittest import mock

from fastapi import HTTPException

import main
from task_manager import TaskManager
from test_task_manager import FakeTorrentManager


class CreateTaskTests(unittest.TestCase):
    """POST /tasks magnet resolution: body wins, env is the fallback."""

    def setUp(self):
        self.engine = FakeTorrentManager()
        # main.py builds real managers at import; swap in stubs so no
        # qBittorrent instance is ever contacted.
        patcher_tasks = mock.patch.object(
            main, "tasks", TaskManager(self.engine)
        )
        patcher_torrents = mock.patch.object(main, "torrents", self.engine)
        self.addCleanup(patcher_tasks.stop)
        self.addCleanup(patcher_torrents.stop)
        patcher_tasks.start()
        patcher_torrents.start()

    def test_magnet_comes_from_request_body(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MAGNET_URI", None)
            result = main.create_task({"magnet": "magnet:?xt=body"})
        self.assertEqual(result["magnet"], "magnet:?xt=body")
        self.assertEqual(self.engine.logged_in, 1)

    def test_env_magnet_is_a_fallback(self):
        with mock.patch.dict(os.environ, {"MAGNET_URI": "magnet:?xt=env"}):
            result = main.create_task()
        self.assertEqual(result["magnet"], "magnet:?xt=env")

    def test_body_magnet_overrides_env(self):
        with mock.patch.dict(os.environ, {"MAGNET_URI": "magnet:?xt=env"}):
            result = main.create_task({"magnet": "magnet:?xt=body"})
        self.assertEqual(result["magnet"], "magnet:?xt=body")

    def test_missing_magnet_is_rejected(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MAGNET_URI", None)
            with self.assertRaises(HTTPException) as ctx:
                main.create_task()
        self.assertEqual(ctx.exception.status_code, 400)

    def test_each_call_creates_a_distinct_task(self):
        """Distinct magnets must not collapse onto one task (queue needs >1)."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MAGNET_URI", None)
            first = main.create_task({"magnet": "magnet:?xt=one"})
            second = main.create_task({"magnet": "magnet:?xt=two"})
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(main.tasks.tasks), 2)


if __name__ == "__main__":
    unittest.main()