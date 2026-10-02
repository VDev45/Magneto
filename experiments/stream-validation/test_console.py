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

    def test_page_declares_a_viewport(self):
        """Without a viewport meta tag a phone lays the page out at ~980px
        and scales it down, so every tap target lands in the wrong place.
        It is the one line that makes the rest of the mobile CSS reachable.
        """
        html = console.console()
        self.assertIn('name="viewport"', html)
        self.assertIn("width=device-width", html)

    def test_page_has_a_mobile_breakpoint(self):
        html = console.console()
        self.assertIn("@media", html)
        self.assertIn("max-width: 640px", html)

    def test_page_offers_a_download_control(self):
        self.assertIn('id="pasteMagnet"', console.console())
        js = console.console_js()
        self.assertIn("async function download(", js)


class ConsoleClipboardTests(unittest.TestCase):
    """Clipboard pickup has to degrade, not throw.

    navigator.clipboard.readText() rejects outright on an insecure origin,
    when permission is denied, and in some embedded webviews. A console that
    breaks on a refused permission prompt is worse than one that quietly
    falls back to typing.
    """

    def setUp(self):
        self.js = console.console_js()

    def test_reads_the_clipboard(self):
        self.assertIn("navigator.clipboard.readText", self.js)

    def test_handles_a_denied_permission(self):
        # The read must be wrapped: an unhandled rejection shows up as an
        # unhandled promise rejection in the console on every page load.
        self.assertIn("catch", self.js)

    def test_guards_on_browsers_without_the_async_clipboard_api(self):
        self.assertIn("!navigator.clipboard", self.js)

    def test_extracts_a_magnet_from_surrounding_text(self):
        """Magnets get shared inside page URLs and log lines, so the field
        must accept a magnet embedded in other text rather than requiring
        the whole clipboard to be one URI."""
        # Matched as a regex literal, so this checks the JS source contains
        # a magnet pattern rather than asserting on a rendered string.
        self.assertIn("/magnet:", self.js)
        self.assertIn('match(/magnet:', self.js)

    def test_has_a_paste_event_path(self):
        # A native paste event needs no permission prompt, so it is the
        # reliable route when readText() is unavailable.
        self.assertIn('addEventListener("paste"', self.js)

    def test_no_magnet_placeholder_tells_the_user_what_to_do(self):
        self.assertIn("Paste magnet", console.console())


class ConsoleScriptSyntaxTests(unittest.TestCase):
    """The console's JavaScript lives in console.js and is served verbatim.

    Every other test in this suite can pass while the page is completely
    dead: a SyntaxError makes the browser discard the whole script, leaving
    HTML that renders and an API that answers, with no polling, no file
    table and no Range probe. That is exactly what shipped once -- a bare
    "\\n\\n" inside a non-raw Python string became two real newlines inside a
    JS double-quoted literal, and the file on disk drifted from the copy
    being served until editing it did nothing at all.

    Both are now impossible by construction: there is one copy, in a real
    file, read at request time. These tests keep it that way -- they pin the
    bytes to the file on disk, and parse them for real.
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

    def test_served_script_is_the_file_on_disk(self):
        """Pins the one-copy invariant. If this fails, someone reintroduced
        an embedded copy and the two will drift apart again."""
        self.assertEqual(
            console.console_js(),
            console.CONSOLE_JS_PATH.read_text(encoding="utf-8"),
        )

    def test_script_file_exists_beside_console_py(self):
        self.assertTrue(console.CONSOLE_JS_PATH.is_file())

    def test_script_defines_the_probe(self):
        """Guards against the script silently disappearing from the page."""
        self.assertIn("async function probe()", console.console_js())

    def test_no_duplicate_script_copy_in_python(self):
        self.assertFalse(
            hasattr(console, "CONSOLE_JS"),
            "CONSOLE_JS reintroduced; console.js on disk is the only copy",
        )

    # There is deliberately no quote-balancing heuristic here any more.
    #
    # Two were tried and both were wrong: regex character classes (/[<>&"]/)
    # and comments contain unbalanced quotes, so tracking quote balance per
    # line reported failures on code node --check accepts. A node-free
    # stand-in for a real parser is not worth having when it cries wolf.
    #
    # The two exact guards above cover the original defect completely: if the
    # script is ever re-embedded in a non-raw Python string, the bytes served
    # stop matching the file and test_served_script_is_the_file_on_disk
    # fails; if the file itself has a syntax error, node --check fails.


if __name__ == "__main__":
    unittest.main()
