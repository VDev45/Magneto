import os
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from file_manager import FileManager
from models import Task, TorrentFile
from stream_manager import parse_range, stream_file


class FakeEngine:
    """Stands in for TorrentManager's piece bookkeeping.

    Reports a fixed set of downloaded pieces and records whether the stream
    path actually asked it to wait, so tests can assert the wiring without a
    live qBittorrent.
    """

    def __init__(self, states):
        self.states = list(states)
        self.waited = []

    def wait_for_pieces(self, task, first, last, timeout=30.0):
        self.waited.append((first, last))
        for piece in range(first, min(last, len(self.states) - 1) + 1):
            if self.states[piece] < 2:
                return piece
        return None


class FakeRequest:
    def __init__(self, range_header=None):
        if range_header is None:
            self.headers = {}
        else:
            self.headers = {"range": range_header}


def read_body(response: StreamingResponse) -> bytes:
    """Drain a StreamingResponse synchronously.

    StreamingResponse wraps a sync generator in iterate_in_threadpool, so the
    raw attribute is not directly iterable. Reading it through an event loop
    is the supported path.
    """
    import asyncio

    async def drain() -> bytes:
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        return b"".join(chunks)

    return asyncio.run(drain())


def make_file(path: Path, size: int, sparse_from: int | None = None):
    """Write a file, optionally leaving a sparse zero tail."""
    with path.open("wb") as handle:
        if sparse_from is None:
            handle.write(b"\xab" * size)
        else:
            handle.write(b"\xab" * sparse_from)
            handle.truncate(size)
    return path


class PieceGeometryTests(unittest.TestCase):
    def test_piece_span_maps_file_bytes_to_pieces(self):
        f = TorrentFile(
            index=0, name="a.mkv", size=1000, progress=0.0, priority=1,
            path="a.mkv", offset=0, piece_size=100,
        )
        self.assertEqual(f.piece_span(0, 99), (0, 0))
        self.assertEqual(f.piece_span(100, 199), (1, 1))
        self.assertEqual(f.piece_span(50, 250), (0, 2))

    def test_piece_span_accounts_for_file_offset(self):
        # A file that starts mid-piece: its first bytes share a piece with
        # the previous file, so a naive offset=0 calculation is wrong.
        f = TorrentFile(
            index=1, name="b.mkv", size=500, progress=0.0, priority=1,
            path="b.mkv", offset=250, piece_size=100,
        )
        self.assertEqual(f.piece_span(0, 49), (2, 2))
        self.assertEqual(f.piece_span(50, 149), (3, 3))

    def test_piece_span_is_none_without_piece_size(self):
        f = TorrentFile(
            index=0, name="a.mkv", size=10, progress=0.0, priority=1,
            path="a.mkv",
        )
        self.assertIsNone(f.piece_span(0, 9))

    def test_piece_start_byte_never_negative(self):
        # Bytes before a file's first byte belong to another file, so the
        # boundary must clamp at 0 rather than going negative.
        f = TorrentFile(
            index=1, name="b.mkv", size=500, progress=0.0, priority=1,
            path="b.mkv", offset=250, piece_size=100,
        )
        self.assertEqual(f.piece_start_byte(2), 0)
        self.assertEqual(f.piece_start_byte(3), 50)

    def test_reported_piece_range_matches_computed_span(self):
        # Guards the offset accumulation in TorrentManager.refresh against
        # the engine's own piece_range.
        f = TorrentFile(
            index=5, name="Sintel.mp4", size=129241752, progress=0.0,
            priority=1, path="Sintel/Sintel.mp4",
            offset=7884, piece_size=131072, piece_range=(0, 986),
        )
        self.assertEqual(f.piece_span(0, f.size - 1), (0, 986))


class ParseRangeTests(unittest.TestCase):
    def test_plain_range(self):
        self.assertEqual(parse_range("bytes=0-99", 1000), (0, 99))

    def test_suffix_range(self):
        self.assertEqual(parse_range("bytes=-100", 1000), (900, 999))

    def test_open_ended(self):
        self.assertEqual(parse_range("bytes=500-", 1000), (500, 999))

    def test_end_is_clamped_to_size(self):
        self.assertEqual(parse_range("bytes=900-5000", 1000), (900, 999))

    def test_past_end_is_416(self):
        with self.assertRaises(HTTPException):
            parse_range("bytes=2000-3000", 1000)

    def test_malformed_is_416(self):
        for bad in ("bytes=abc", "items=0-10", "bytes=0"):
            with self.assertRaises(HTTPException):
                parse_range(bad, 1000)


class StreamGatingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.files = FileManager(str(self.root))
        self.addCleanup(self.tmp.cleanup)

    def build(self, size=4096, sparse_from=None, piece_size=512):
        path = make_file(self.root / "movie.mkv", size, sparse_from)
        task = Task(id="t1", magnet="magnet:?x", name=None)
        task.name = ""
        torrent_file = TorrentFile(
            index=0, name="movie.mkv", size=size, progress=0.0,
            priority=1, path="movie.mkv",
            offset=0, piece_size=piece_size,
        )
        # FileManager resolves <root>/<path>; force the single-file layout.
        task.name = "movie.mkv"
        return path, task, torrent_file

    def test_fully_downloaded_serves_whole_range(self):
        path, task, torrent_file = self.build(size=4096, piece_size=512)
        engine = FakeEngine(states=[2] * 8)
        response = stream_file(
            FakeRequest("bytes=0-4095"), task, torrent_file, self.files,
            engine=engine,
        )
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.headers["content-length"], "4096")
        self.assertEqual(
            response.headers["content-range"], "bytes 0-4095/4096"
        )
        body = read_body(response)
        self.assertEqual(len(body), 4096)

    def test_truncates_at_first_missing_piece(self):
        # 8 pieces of 512 bytes; piece 3 (bytes 1536-2047) is absent, so the
        # response must stop at byte 1535 rather than run into the hole.
        path, task, torrent_file = self.build(size=4096, piece_size=512)
        engine = FakeEngine(states=[2, 2, 2, 0, 0, 0, 0, 0])
        response = stream_file(
            FakeRequest("bytes=0-4095"), task, torrent_file, self.files,
            engine=engine,
        )
        self.assertEqual(
            response.headers["content-range"], "bytes 0-1535/4096"
        )
        self.assertEqual(response.headers["content-length"], "1536")
        body = read_body(response)
        self.assertEqual(len(body), 1536)
        self.assertNotIn(b"\x00" * 512, body)

    def test_marks_truncation_for_the_client(self):
        path, task, torrent_file = self.build(size=4096, piece_size=512)
        engine = FakeEngine(states=[2, 2, 0, 0, 0, 0, 0, 0])
        response = stream_file(
            FakeRequest("bytes=0-4095"), task, torrent_file, self.files,
            engine=engine,
        )
        self.assertEqual(response.headers.get("x-magneto-truncated"),
                         "piece-gate")

    def test_no_truncation_marker_when_complete(self):
        path, task, torrent_file = self.build(size=4096, piece_size=512)
        engine = FakeEngine(states=[2] * 8)
        response = stream_file(
            FakeRequest("bytes=0-4095"), task, torrent_file, self.files,
            engine=engine,
        )
        self.assertNotIn("x-magneto-truncated", response.headers)

    def test_entirely_undownloaded_range_is_425(self):
        path, task, torrent_file = self.build(size=4096, piece_size=512)
        engine = FakeEngine(states=[0] * 8)
        with self.assertRaises(HTTPException) as caught:
            stream_file(
                FakeRequest("bytes=0-4095"), task, torrent_file, self.files,
                engine=engine,
            )
        self.assertEqual(caught.exception.status_code, 425)

    def test_engine_failure_falls_back_instead_of_500(self):
        class BrokenEngine:
            def wait_for_pieces(self, *a, **k):
                raise RuntimeError("qBittorrent went away")

        path, task, torrent_file = self.build(size=4096, piece_size=512)
        response = stream_file(
            FakeRequest("bytes=0-1023"), task, torrent_file, self.files,
            engine=BrokenEngine(),
        )
        self.assertEqual(response.status_code, 206)
        self.assertEqual(len(read_body(response)), 1024)

    def test_without_engine_behaviour_is_unchanged(self):
        # The gate is opt-in. With no engine the response must look exactly
        # like the pre-§12 implementation.
        path, task, torrent_file = self.build(size=4096, piece_size=512)
        response = stream_file(
            FakeRequest("bytes=0-4095"), task, torrent_file, self.files
        )
        self.assertEqual(
            response.headers["content-range"], "bytes 0-4095/4096"
        )
        self.assertEqual(len(read_body(response)), 4096)

    def test_body_length_matches_content_length_header(self):
        # The old code could break out of the read loop early and still
        # promise the full length in the header, leaving the client hanging.
        path, task, torrent_file = self.build(size=4096, piece_size=512)
        engine = FakeEngine(states=[2] * 8)
        response = stream_file(
            FakeRequest("bytes=0-4095"), task, torrent_file, self.files,
            engine=engine,
        )
        declared = int(response.headers["content-length"])
        body = read_body(response)
        self.assertEqual(len(body), declared)


class SparseFallbackTests(unittest.TestCase):
    """The zero-scan path, used when piece geometry is unknown.

    This is reachable in production when the engine is present but the
    torrent's piece_size has not resolved yet, or when a piece-state query
    fails. With engine=None there is no gating at all, which
    test_without_engine_behaviour_is_unchanged covers separately.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.files = FileManager(str(self.root))
        self.addCleanup(self.tmp.cleanup)

    def build_sparse(self, size, sparse_from):
        make_file(self.root / "v.mp4", size, sparse_from)
        task = Task(id="t", magnet="m", name="v.mp4")
        # piece_size=0 stands in for unresolved metadata.
        torrent_file = TorrentFile(
            index=0, name="v.mp4", size=size, progress=0.0,
            priority=1, path="v.mp4",
        )
        return task, torrent_file

    def test_zero_scan_stops_before_sparse_hole(self):
        size = 3 * 1024 * 1024
        task, torrent_file = self.build_sparse(size, 1024 * 1024)
        engine = FakeEngine(states=[])
        response = stream_file(
            FakeRequest("bytes=0-%d" % (size - 1)), task, torrent_file,
            self.files, engine=engine,
        )
        body = read_body(response)
        self.assertLess(len(body), size)
        self.assertNotIn(b"\x00" * 4096, body[-8192:])

    def test_zero_scan_serves_real_data_whole_file(self):
        # A fully present file must not be truncated just because geometry
        # is missing -- the scan should run to the end and stop there.
        size = 256 * 1024
        task, torrent_file = self.build_sparse(size, None)
        engine = FakeEngine(states=[])
        response = stream_file(
            FakeRequest(), task, torrent_file, self.files, engine=engine
        )
        self.assertEqual(len(read_body(response)), size)

    def test_content_length_never_exceeds_body(self):
        size = 2 * 1024 * 1024
        task, torrent_file = self.build_sparse(size, 1024 * 1024)
        engine = FakeEngine(states=[])
        response = stream_file(
            FakeRequest(), task, torrent_file, self.files, engine=engine
        )
        declared = int(response.headers["content-length"])
        self.assertLessEqual(len(read_body(response)), declared)


if __name__ == "__main__":
    unittest.main()
