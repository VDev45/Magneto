import os
import shutil
import subprocess
import tempfile
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
        self.assertIn('<script src="/console.js"></script>', html)
        self.assertIn('id="probeOut"', html)
        self.assertIn("206 = Partial Content", html)

    def test_console_js_contains_api_poll(self):
        js = console.console_js()
        self.assertIn("/console/state", js)
        self.assertIn("async function createTask", js)


class ConsoleScriptSyntaxTests(unittest.TestCase):
    """The console's JavaScript is served from console_js(), and it is
    embedded in a *non-raw* Python string.

    Every other test in this suite can pass while the page is completely
    dead: a SyntaxError makes the browser discard the whole script, leaving
    HTML that renders and an API that answers, with no polling, no file
    table and no Range probe. That is exactly what shipped once -- a bare
    "\\n\\n" in a non-raw Python string became two real newlines inside a JS
    double-quoted literal.

    Moving the script out of the HTML did not help on its own; the escaping
    hazard is in the Python string, not the markup. So parse what is
    actually served. Skipped where node is absent.
    """

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_served_script_parses(self):
        js = console.console_js()
        self.assertTrue(js.strip(), "console_js() served an empty script")
        with tempfile.NamedTemporaryFile(
            "w", suffix=".js", delete=False
        ) as handle:
            handle.write(js)
            path = handle.name
        self.addCleanup(os.unlink, path)

        result = subprocess.run(
            ["node", "--check", path],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"served console.js does not parse:\n{result.stderr}",
        )

    def test_script_defines_the_probe(self):
        """Guards against the script silently disappearing from the page."""
        self.assertIn("async function probe()", console.console_js())

    def test_script_is_no_raw_newline_in_a_quoted_literal(self):
        """The specific defect, caught without needing node.

        A double-quoted JS literal must not span a line break. If Python has
        already turned an escape into a real newline, this trips.
        """
        js = console.console_js()
        for number, line in enumerate(js.split("\n"), start=1):
            quote = None
            for char in line:
                if char in "\"'":
                    if quote is None:
                        quote = char
                    elif quote == char:
                        quote = None
                elif quote and char == "\\":
                    continue
            if quote is not None:
                self.fail(
                    f"line {number} leaves a {quote} literal open at end of "
                    f"line -- a \\n escape was likely expanded by Python:\n"
                    f"    {line}"
                )


if __name__ == "__main__":
    unittest.main()
