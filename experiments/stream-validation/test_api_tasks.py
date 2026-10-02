import os
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

import main
from models import TorrentFile
from stream_manager import _await_available
from task_manager import TaskManager
from test_task_manager import FakeTorrentManager
from torrent_manager import TorrentManager


def make_file(index=5, name="Sintel.mp4", size=129_241_752,
              offset=0, piece_size=131_072, span=(0, 999)):
    """Piece indices line up with array indices when offset is 0, which keeps
    the fixture readable. The offset case gets its own file."""
    return TorrentFile(
        index=index, name=name, size=size, progress=0.0, priority=1,
        path=name, offset=offset, piece_size=piece_size, piece_range=span,
    )


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


class FrontierTests(unittest.TestCase):
    """GET /tasks/{id}/files/{index}/frontier -- the diagnostic behind the
    console's seek tester, and the number that makes "seeking is broken"
    checkable.

    The endpoint is useless if it disagrees with the gate in stream_manager:
    a frontier the player is told it can reach but the server refuses would
    point the investigation at the wrong layer. Both sides read the same
    piece states but convert to bytes differently, so pin them together here
    rather than trusting the two to stay in step.

    A real TorrentManager with _get stubbed, per the repo rule that unit
    tests never touch a live container.
    """

    def setUp(self):
        self.states = [2] * 1000
        self.seq_dl = True
        engine = TorrentManager("http://qbit", "admin", "pw")

        def fake_get(path, **params):
            if path.endswith("pieceStates"):
                return list(self.states)
            if path.endswith("torrents/info"):
                return [{"hash": "t1", "f_l_piece_prio": True,
                         "seq_dl": self.seq_dl}]
            raise AssertionError("unexpected GET " + path)

        engine._get = fake_get
        self.engine = engine

        self.manager = TaskManager(FakeTorrentManager())
        self.task = self.manager.create("t1", "magnet:?xt=frontier")
        self.task.torrent_hash = "abc123"
        self.file = make_file()
        self.task.files = [self.file]

        for name, value in (("tasks", self.manager), ("torrents", engine)):
            patcher = mock.patch.object(main, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def frontier(self, index=5):
        return main.file_frontier("t1", index)

    def test_missing_piece_becomes_the_frontier(self):
        self.states = [2] * 12 + [0] + [2] * 987
        result = self.frontier()
        self.assertEqual(result["prefix_missing_piece"], 12)
        # The frontier is the first byte of the missing piece: byte 11 is the
        # last servable byte, byte 12 is not.
        self.assertEqual(
            result["prefix_available"], self.file.piece_start_byte(12)
        )
        self.assertFalse(result["prefix_complete"])

    def test_in_progress_piece_is_still_a_frontier(self):
        # State 1 is a piece mid-download. Reporting it as available is the
        # exact bug the piece gate exists to prevent.
        self.states = [2] * 7 + [1] + [2] * 992
        result = self.frontier()
        self.assertEqual(result["prefix_missing_piece"], 7)

    def test_complete_file_frontier_is_its_size(self):
        self.states = [2] * 1000
        self.seq_dl = True
        result = self.frontier()
        self.assertIsNone(result["prefix_missing_piece"])
        self.assertEqual(result["prefix_available"], self.file.size)
        self.assertTrue(result["prefix_complete"])

    def test_frontier_is_the_first_byte_the_gate_would_refuse(self):
        """The byte at the frontier and the byte before it must land in
        different pieces, and the latter must be a downloaded one. If this
        fails the seek tester is pointing at the wrong offset."""
        self.states = [2] * 12 + [0] + [2] * 987
        frontier = self.frontier()["prefix_available"]
        before = self.file.piece_span(frontier - 1, frontier - 1)
        at = self.file.piece_span(frontier, frontier)
        self.assertNotEqual(before, at)
        self.assertEqual(self.states[before[0]], 2)
        self.assertLess(self.states[at[0]], 2)

    def test_frontier_accounts_for_the_files_offset(self):
        """A file that does not start on a piece boundary: the frontier is not
        piece * piece_size. Here the file starts 4096 bytes into piece 5, so
        piece 5 covers file bytes 0..126975 and piece 6 begins at 126976."""
        self.file = make_file(offset=5 * 131_072 + 4096, span=(0, 999))
        self.task.files = [self.file]
        self.states = [2] * 6 + [0] + [2] * 993
        result = self.frontier()
        self.assertEqual(result["prefix_missing_piece"], 6)
        self.assertEqual(result["prefix_available"], 131_072 - 4096)
        # The naive piece * piece_size would be a whole piece too high.
        self.assertNotEqual(result["prefix_available"], 6 * 131_072)

    def test_frontier_starts_inside_a_missing_piece_when_offset_says_so(self):
        """If the first piece of the file is itself missing, the frontier is 0:
        no byte of this file can be served. Reporting piece_start_byte(1)
        without the offset would put it a whole piece too high."""
        self.file = make_file(offset=5 * 131_072 + 4096, span=(5, 999))
        self.task.files = [self.file]
        self.states = [0] * 8
        self.assertEqual(self.frontier()["prefix_available"], 0)

    def test_frontier_never_passes_the_end_of_the_file(self):
        """piece_start_byte(missing + 1) overshoots when the file ends
        mid-piece. A frontier past EOF would send the seek tester to request
        bytes that cannot exist."""
        self.file = make_file(size=1000, offset=0, piece_size=131_072,
                              span=(0, 0))
        self.task.files = [self.file]
        self.states = [0] * 10  # piece 0 missing, file ends inside it
        result = self.frontier()
        self.assertLessEqual(result["prefix_available"], result["size"])

    def test_span_reaching_past_the_reported_array_is_missing(self):
        """The engine not describing a piece is not evidence it arrived."""
        self.states = [2] * 5  # file claims pieces 10..995
        result = self.frontier()
        self.assertEqual(result["prefix_missing_piece"], 5)

    def test_unknown_geometry_is_reported_not_guessed(self):
        """Before metadata resolves piece_size is 0. Reporting
        first_available 0 would be indistinguishable from 'nothing
        downloaded', which reads as a broken download."""
        self.task.files = [make_file(piece_size=0, span=None)]
        result = self.frontier()
        self.assertFalse(result["geometry_known"])
        self.assertIsNone(result["piece_range"])
        self.assertIsNone(result["prefix_missing_piece"])

    def test_unknown_task_is_404(self):
        with self.assertRaises(HTTPException) as ctx:
            main.file_frontier("nope", 5)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_unknown_file_is_404(self):
        with self.assertRaises(HTTPException) as ctx:
            main.file_frontier("t1", 99)
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()


class FrontierMatchesTheGateTests(unittest.TestCase):
    """The endpoint must not drift from stream_manager.

    _await_available clamps a range to piece_start_byte(missing) - 1. The
    frontier endpoint reports piece_start_byte(missing). If either moves, the
    seek tester starts lying about what the server will serve, and the only
    symptom is a tester that contradicts the player. Driving both from one
    fixture makes a divergence a test failure rather than an afternoon of
    debugging.
    """

    def setUp(self):
        self.states = [2] * 1000
        self.seq_dl = True
        engine = TorrentManager("http://qbit", "admin", "pw")

        def fake_get(path, **params):
            if path.endswith("pieceStates"):
                return list(self.states)
            if path.endswith("torrents/info"):
                return [{"hash": "t1", "f_l_piece_prio": True,
                         "seq_dl": self.seq_dl}]
            raise AssertionError("unexpected GET " + path)

        engine._get = fake_get
        self.engine = engine
        self.manager = TaskManager(FakeTorrentManager())
        self.task = self.manager.create("t1", "magnet:?xt=x")
        self.task.torrent_hash = "abc123"
        self.file = make_file()
        self.task.files = [self.file]
        for name, value in (("tasks", self.manager), ("torrents", engine)):
            patcher = mock.patch.object(main, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def gate_serves_through(self, start, end):
        """The highest byte stream_manager will serve for this range."""
        served_end, _ = _await_available(
            self.engine, self.task, self.file, start, end,
            Path("/nonexistent"), wait_seconds=0,
        )
        return served_end

    def test_a_full_file_request_stops_one_byte_below_the_frontier(self):
        for missing in (0, 1, 7, 500, 999):
            with self.subTest(missing=missing):
                self.states = [2] * missing + [0] + [2] * (999 - missing)
                frontier = main.file_frontier("t1", 5)["prefix_available"]
                # Ask for the whole file, from byte 0.
                served = self.gate_serves_through(0, self.file.size - 1)
                self.assertEqual(served, frontier - 1)

    def test_a_range_entirely_before_the_frontier_is_served_in_full(self):
        self.states = [2] * 100 + [0] + [2] * 899
        frontier = main.file_frontier("t1", 5)["prefix_available"]
        self.assertEqual(
            self.gate_serves_through(frontier - 65536, frontier - 1),
            frontier - 1,
        )

    def test_a_range_straddling_the_frontier_is_truncated_there(self):
        self.states = [2] * 100 + [0] + [2] * 899
        frontier = main.file_frontier("t1", 5)["prefix_available"]
        self.assertEqual(
            self.gate_serves_through(frontier - 1000, frontier + 9999),
            frontier - 1,
        )

    def test_a_range_past_the_frontier_serves_nothing(self):
        self.states = [2] * 100 + [0] + [2] * 899
        frontier = main.file_frontier("t1", 5)["prefix_available"]
        # served_end is start - 1 here, which is how stream_manager signals
        # "no bytes at all" to its caller.
        self.assertEqual(
            self.gate_serves_through(frontier + 10, frontier + 9999),
            frontier + 9,
        )


class ScatteredPieceTests(unittest.TestCase):
    """libtorrent selects rarest-first, so complete pieces are NOT contiguous.

    Measured on Sintel at 35%: 333 of 987 pieces complete, spread over 217
    separate runs. A single "frontier" therefore lies about the rest of the
    file -- the first version of this endpoint reported a byte at 99% as
    "inside the frontier" when the frontier was at 6%. These tests pin the
    two-number model that replaced it.
    """

    def setUp(self):
        self.states = [2] * 1000
        self.seq_dl = True
        engine = TorrentManager("http://qbit", "admin", "pw")

        def fake_get(path, **params):
            if path.endswith("pieceStates"):
                return list(self.states)
            if path.endswith("torrents/info"):
                return [{"hash": "t1", "f_l_piece_prio": True,
                         "seq_dl": self.seq_dl}]
            raise AssertionError("unexpected GET " + path)

        engine._get = fake_get
        self.manager = TaskManager(FakeTorrentManager())
        self.task = self.manager.create("t1", "magnet:?xt=x")
        self.task.torrent_hash = "abc"
        self.file = make_file()
        self.task.files = [self.file]
        for name, value in (("tasks", self.manager), ("torrents", engine)):
            patcher = mock.patch.object(main, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def at(self, offset):
        return main.file_frontier("t1", 5, at=offset)

    def test_prefix_is_the_first_hole_not_the_last_complete_piece(self):
        # Pieces 0-49 and 600-999 complete: the prefix stops at 50 even
        # though 40% of the file is present further along.
        self.states = [2] * 50 + [0] * 550 + [2] * 400
        result = self.at(0)
        self.assertEqual(result["prefix_available"], 50 * 131_072)
        self.assertEqual(result["prefix_missing_piece"], 50)
        self.assertEqual(result["downloaded_pieces"], 450)

    def test_a_later_run_is_reachable_from_its_own_offset(self):
        self.states = [2] * 50 + [0] * 550 + [2] * 400
        result = self.at(700 * 131_072)
        self.assertEqual(result["piece"], 700)
        self.assertEqual(result["piece_state"], 2)
        # Runs from piece 700 to 999, then clamps at the file's last byte.
        self.assertEqual(result["available_end"], self.file.size - 1)
        self.assertEqual(
            result["available_bytes"], self.file.size - 700 * 131_072
        )

    def test_a_hole_reports_no_bytes_available(self):
        self.states = [2] * 50 + [0] * 550 + [2] * 400
        result = self.at(100 * 131_072)
        self.assertEqual(result["piece_state"], 0)
        self.assertEqual(result["available_bytes"], 0)

    def test_available_run_stops_at_the_next_hole(self):
        # Pieces 10-14 complete, 15 a hole: 5 pieces, not the whole file.
        self.states = [0] * 10 + [2] * 5 + [0] * 985
        result = self.at(10 * 131_072)
        self.assertEqual(result["available_bytes"], 5 * 131_072)
        self.assertEqual(result["available_end"], 15 * 131_072 - 1)

    def test_in_progress_piece_is_not_available(self):
        self.states = [2] * 10 + [1] + [2] * 989
        self.assertEqual(self.at(10 * 131_072)["available_bytes"], 0)

    def test_available_run_never_reports_past_the_end_of_the_file(self):
        """A run reaching past the last piece must clamp to the file's size,
        not piece_start_byte(piece) of a piece this file does not occupy."""
        self.states = [2] * 1000
        self.seq_dl = True
        result = self.at(self.file.size - 1)
        self.assertEqual(result["available_bytes"], 1)
        self.assertEqual(result["available_end"], self.file.size - 1)

    def test_offset_past_the_end_is_clamped_not_rejected(self):
        result = self.at(self.file.size + 5_000_000)
        self.assertEqual(result["at"], self.file.size - 1)
        self.assertLessEqual(result["available_end"], self.file.size - 1)

    def test_negative_offset_is_clamped_to_zero(self):
        self.assertEqual(self.at(-99)["at"], 0)

    def test_omitting_at_omits_the_per_offset_fields(self):
        result = main.file_frontier("t1", 5)
        self.assertIsNone(result["piece"])
        self.assertIsNone(result["piece_state"])
        self.assertEqual(result["available_bytes"], 0)

    def test_a_span_the_engine_does_not_describe_is_not_available(self):
        self.states = [2] * 10  # file claims pieces 0..999
        self.assertEqual(self.at(500 * 131_072)["available_bytes"], 0)

    def test_counted_pieces_are_clamped_to_this_files_span(self):
        # Subtitle files share piece 0 with the video, so counting whole-array
        # state 2s would credit pieces belonging to other files.
        self.states = [2] * 1000
        self.seq_dl = True
        self.file = make_file(span=(100, 199))
        self.task.files = [self.file]
        self.assertEqual(
            main.file_frontier("t1", 5)["downloaded_pieces"], 100
        )


class SequentialFlagTests(unittest.TestCase):
    """The frontier payload carries seq_dl so the console can explain a
    lagging prefix without advising the reader to enable a setting already on.

    Measured: a prefix 34 points behind overall progress is rarest-first and
    worth fixing; 3 points behind with sequential on is just the last-piece
    pull for moov. Telling someone to turn on sequential download they already
    enabled is worse than saying nothing.
    """

    def build(self, info):
        engine = TorrentManager("http://qbit", "admin", "pw")

        def fake_get(path, **params):
            if path.endswith("pieceStates"):
                return [2] * 1000
            if path.endswith("torrents/info"):
                return info
            raise AssertionError("unexpected GET " + path)

        engine._get = fake_get
        manager = TaskManager(FakeTorrentManager())
        task = manager.create("t1", "magnet:?xt=x")
        task.torrent_hash = "t1"
        task.files = [make_file()]
        for name, value in (("tasks", manager), ("torrents", engine)):
            patcher = mock.patch.object(main, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return task

    def test_reports_sequential_on(self):
        self.build([{"hash": "t1", "seq_dl": True}])
        self.assertIs(main.file_frontier("t1", 5)["seq_dl"], True)

    def test_reports_sequential_off(self):
        self.build([{"hash": "t1", "seq_dl": False}])
        self.assertIs(main.file_frontier("t1", 5)["seq_dl"], False)

    def test_an_absent_flag_is_false_not_an_error(self):
        self.build([{"hash": "t1"}])
        self.assertIs(main.file_frontier("t1", 5)["seq_dl"], False)

    def test_an_unknown_torrent_is_false(self):
        """No row for our hash must not raise, and must not read as "on"."""
        self.build([{"hash": "other", "seq_dl": True}])
        self.assertIs(main.file_frontier("t1", 5)["seq_dl"], False)

    def test_unknown_geometry_reports_no_flag_rather_than_guessing(self):
        task = self.build([{"hash": "t1", "seq_dl": True}])
        task.files = [make_file(piece_size=0, span=None)]
        self.assertIsNone(main.file_frontier("t1", 5)["seq_dl"])
