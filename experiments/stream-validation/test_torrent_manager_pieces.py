"""Tests for the piece-wait logic in TorrentManager.

No live qBittorrent: _get is stubbed so these stay offline, per the
repo rule that unit tests never require a running container.
"""

import unittest

from models import Task
from torrent_manager import TorrentManager


class PieceWaitTests(unittest.TestCase):
    def build(self, states, wait_calls=None):
        manager = TorrentManager("http://qbit", "admin", "pw")
        task = Task(id="t1", magnet="magnet:?x")
        task.torrent_hash = "abc123"

        def fake_get(path, **params):
            if path.endswith("pieceStates"):
                return list(states)
            raise AssertionError("unexpected GET " + path)

        manager._get = fake_get
        return manager, task

    def test_complete_span_reports_no_missing_piece(self):
        manager, task = self.build([2, 2, 2, 2])
        self.assertIsNone(manager.first_missing_piece(task, 0, 3))

    def test_finds_lowest_missing_piece(self):
        manager, task = self.build([2, 2, 0, 0])
        self.assertEqual(manager.first_missing_piece(task, 0, 3), 2)

    def test_in_progress_piece_counts_as_missing(self):
        # State 1 means a piece is being fetched right now. Treating it as
        # available is exactly the bug that served zeros.
        manager, task = self.build([2, 1, 2])
        self.assertEqual(manager.first_missing_piece(task, 0, 2), 1)

    def test_empty_states_reports_first_piece_missing(self):
        manager, task = self.build([])
        self.assertEqual(manager.first_missing_piece(task, 4, 8), 4)

    def test_span_beyond_known_pieces_is_missing(self):
        manager, task = self.build([2, 2])
        self.assertEqual(manager.first_missing_piece(task, 0, 9), 2)

    def test_inverted_span_is_not_missing(self):
        manager, task = self.build([0, 0, 0])
        self.assertIsNone(manager.first_missing_piece(task, 5, 1))

    def test_wait_returns_none_when_already_complete(self):
        manager, task = self.build([2, 2, 2])
        self.assertIsNone(
            manager.wait_for_pieces(task, 0, 2, timeout=0.1)
        )

    def test_wait_times_out_with_missing_piece(self):
        manager, task = self.build([2, 0, 0])
        missing = manager.wait_for_pieces(
            task, 0, 2, timeout=0.2, poll_interval=0.01
        )
        self.assertEqual(missing, 1)

    def test_wait_returns_promptly_when_complete(self):
        manager, task = self.build([2] * 10)
        missing = manager.wait_for_pieces(
            task, 0, 9, timeout=5.0, poll_interval=0.01
        )
        self.assertIsNone(missing)

    def test_wait_requires_hash(self):
        manager, task = self.build([2, 2])
        task.torrent_hash = None
        with self.assertRaises(RuntimeError):
            manager.wait_for_pieces(task, 0, 1, timeout=0.1)

    def test_wait_polls_until_piece_lands(self):
        # Simulates a piece arriving between polls: the first lookup misses,
        # the second succeeds. Without this, a one-shot check would return
        # "missing" for data that was already on its way.
        manager, task = self.build([2, 0, 0])
        calls = {"n": 0}

        def flaky_get(path, **params):
            if path.endswith("pieceStates"):
                calls["n"] += 1
                return [2, 2, 2] if calls["n"] > 1 else [2, 0, 0]
            raise AssertionError("unexpected GET " + path)

        manager._get = flaky_get
        missing = manager.wait_for_pieces(
            task, 0, 2, timeout=2.0, poll_interval=0.01
        )
        self.assertIsNone(missing)
        self.assertGreater(calls["n"], 1)


class FileGeometryTests(unittest.TestCase):
    """refresh() must derive per-file offsets and piece spans correctly."""

    def build_manager(self, files, piece_size):
        manager = TorrentManager("http://qbit", "admin", "pw")
        task = Task(id="t1", magnet="magnet:?x")
        task.torrent_hash = "abc123"

        def fake_get(path, **params):
            if path.endswith("torrents/info"):
                return [{
                    "name": "Sintel", "progress": 1.0, "state": "stalledUP",
                }]
            if path.endswith("torrents/files"):
                return files
            if path.endswith("torrents/properties"):
                return {"piece_size": piece_size}
            raise AssertionError("unexpected GET " + path)

        manager._get = fake_get
        return manager, task

    def test_offsets_accumulate_in_index_order(self):
        files = [
            {"index": 0, "name": "Sintel/a.srt", "size": 100,
             "progress": 1.0, "priority": 1, "piece_range": [0, 0]},
            {"index": 1, "name": "Sintel/b.mkv", "size": 300,
             "progress": 0.5, "priority": 1, "piece_range": [0, 2]},
            {"index": 2, "name": "Sintel/c.srt", "size": 50,
             "progress": 1.0, "priority": 1, "piece_range": [3, 3]},
        ]
        manager, task = self.build_manager(files, 128)
        manager.refresh(task)
        self.assertEqual([f.offset for f in task.files], [0, 100, 400])
        self.assertEqual([f.piece_size for f in task.files], [128] * 3)

    def test_computed_span_matches_reported_piece_range(self):
        # The derived offset must reproduce the engine's own piece_range.
        # If these ever diverge, the gate would wait on the wrong pieces.
        files = [
            {"index": 0, "name": "Sintel/a.srt", "size": 700,
             "progress": 1.0, "priority": 1, "piece_range": [0, 5]},
            {"index": 1, "name": "Sintel/b.mkv", "size": 900,
             "progress": 0.5, "priority": 1, "piece_range": [5, 12]},
        ]
        manager, task = self.build_manager(files, 128)
        manager.refresh(task)
        for f in task.files:
            computed = f.piece_span(0, f.size - 1)
            self.assertEqual(computed, f.piece_range, f.name)

    def test_missing_piece_range_tolerated(self):
        # Some engines/versions may omit piece_range. Refresh must not crash.
        files = [
            {"index": 0, "name": "only.mkv", "size": 1000,
             "progress": 0.0, "priority": 1},
        ]
        manager, task = self.build_manager(files, 512)
        manager.refresh(task)
        self.assertIsNone(task.files[0].piece_range)
        self.assertEqual(task.files[0].piece_span(0, 999), (0, 1))

    def test_missing_piece_size_tolerated(self):
        files = [
            {"index": 0, "name": "only.mkv", "size": 1000,
             "progress": 0.0, "priority": 1, "piece_range": [0, 1]},
        ]
        manager, task = self.build_manager(files, 0)
        manager.refresh(task)
        self.assertEqual(task.files[0].piece_size, 0)
        self.assertIsNone(task.files[0].piece_span(0, 999))

    def test_basename_split_from_relative_path(self):
        files = [
            {"index": 0, "name": "Sintel/sub dir/Sintel.mp4", "size": 10,
             "progress": 1.0, "priority": 1, "piece_range": [0, 0]},
        ]
        manager, task = self.build_manager(files, 128)
        manager.refresh(task)
        self.assertEqual(task.files[0].name, "Sintel.mp4")
        self.assertEqual(task.files[0].path, "Sintel/sub dir/Sintel.mp4")


if __name__ == "__main__":
    unittest.main()
