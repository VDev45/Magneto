"""Tests for the container-index piece prioritisation in TorrentManager.

An MP4 with ``moov`` at the end cannot be demuxed until that index has been
downloaded, which is why playback stalled on a partial file. The fix is to
ask qBittorrent for the trailing pieces first.

No live qBittorrent: _get and _post are stubbed, per the repo rule that unit
tests never require a running container.
"""

import unittest
from unittest import mock

from requests import HTTPError

from models import Task, TorrentFile
from torrent_manager import TorrentManager


class ContainerIndexPriorityTests(unittest.TestCase):
    def build(self, *, info=None, post_error=None, video=True):
        """A manager with a two-file task: one video, one subtitle."""
        manager = TorrentManager("http://qbit", "admin", "pw")
        task = Task(id="t1", magnet="magnet:?x")
        task.torrent_hash = "abc123"

        names = ["Sintel.mp4", "Sintel.en.srt"] if video else ["a.srt", "b.srt"]
        task.files = [
            TorrentFile(
                index=i,
                name=names[i],
                size=1000,
                progress=0.0,
                priority=1,
                path=names[i],
            )
            for i in range(len(names))
        ]

        self.gets: list[str] = []
        self.posts: list[tuple[str, dict]] = []

        def fake_get(path, **params):
            self.gets.append(path)
            if path.endswith("torrents/info"):
                return info if info is not None else [
                    {"hash": "abc123", "f_l_piece_prio": False, "seq_dl": False}
                ]
            raise AssertionError("unexpected GET " + path)

        def fake_post(path, **data):
            self.posts.append((path, data))
            if post_error and path.endswith(post_error[0]):
                raise HTTPError(post_error[1])
            return ""

        manager._get = fake_get
        manager._post = fake_post
        return manager, task

    # ---- the happy path -------------------------------------------------

    def test_enables_priority_when_selecting_a_video(self):
        manager, task = self.build()
        manager.select_files(task, {0, 1})
        toggles = [p for p in self.posts if p[0].endswith("toggleFirstLastPiecePrio")]
        self.assertEqual(len(toggles), 1, "expected exactly one toggle")

    def test_toggle_sends_the_torrent_hash(self):
        manager, task = self.build()
        manager.select_files(task, {0})
        path, data = next(p for p in self.posts if p[0].endswith("toggleFirstLastPiecePrio"))
        self.assertEqual(data["hashes"], "abc123")

    # ---- the toggle trap ------------------------------------------------

    def test_does_not_toggle_when_already_enabled(self):
        """The endpoint is a TOGGLE. A second call turns the setting back off,
        so an already-enabled torrent must not be toggled again -- e.g. when
        the user changes their file selection mid-download."""
        manager, task = self.build(info=[{"hash": "abc123", "f_l_piece_prio": True}])
        manager.select_files(task, {0})
        toggles = [p for p in self.posts if p[0].endswith("toggleFirstLastPiecePrio")]
        self.assertEqual(toggles, [], "toggled an already-enabled torrent back off")

    def test_reads_the_5x_field_name(self):
        """4.x called it first_last_prio_pieces; 5.x calls it f_l_piece_prio.
        Reading the old name yields None, which would look like 'off'."""
        manager, task = self.build(info=[{"hash": "abc123", "f_l_piece_prio": True}])
        self.assertTrue(manager._first_last_prio(task))

    def test_missing_field_reads_as_disabled(self):
        manager, task = self.build(info=[{"hash": "abc123"}])
        self.assertFalse(manager._first_last_prio(task))

    def test_torrent_absent_from_info_reads_as_disabled(self):
        manager, task = self.build(info=[])
        self.assertFalse(manager._first_last_prio(task))

    def test_matches_the_right_torrent_when_several_are_listed(self):
        manager, task = self.build(
            info=[
                {"hash": "other", "f_l_piece_prio": False},
                {"hash": "abc123", "f_l_piece_prio": True},
            ]
        )
        self.assertTrue(manager._first_last_prio(task))

    # ---- scope ----------------------------------------------------------

    def test_skipped_when_only_non_video_files_are_selected(self):
        """A subtitle has no index to chase, and bumping its pieces would slow
        down the file the user actually wants."""
        manager, task = self.build(video=False)
        manager.select_files(task, {0, 1})
        toggles = [p for p in self.posts if p[0].endswith("toggleFirstLastPiecePrio")]
        self.assertEqual(toggles, [])

    def test_applies_when_video_is_selected_alongside_others(self):
        manager, task = self.build()  # Sintel.mp4 + Sintel.en.srt
        manager.select_files(task, {0, 1})
        toggles = [p for p in self.posts if p[0].endswith("toggleFirstLastPiecePrio")]
        self.assertEqual(len(toggles), 1)

    def test_selection_still_happens_when_priority_fails(self):
        """Prioritisation is an optimisation. Losing it must not lose the
        user's file selection -- the fallback is just the old behaviour."""
        manager, task = self.build(post_error=("toggleFirstLastPiecePrio", "404"))
        manager.select_files(task, {0})
        self.assertEqual(task.selected_files, {0})
        prios = [p for p in self.posts if p[0].endswith("filePrio")]
        self.assertTrue(prios, "file priorities were never applied")

    def test_engine_failure_is_swallowed(self):
        manager, task = self.build(post_error=("toggleFirstLastPiecePrio", "500"))
        manager.select_files(task, {0})  # must not raise
        self.assertEqual(task.selected_files, {0})

    def test_rejects_unknown_indexes_before_touching_the_engine(self):
        manager, task = self.build()
        with self.assertRaises(ValueError):
            manager.select_files(task, {99})
        self.assertEqual(self.posts, [])

    def test_requires_a_torrent_hash(self):
        manager, task = self.build()
        task.torrent_hash = None
        with self.assertRaises(RuntimeError):
            manager.select_files(task, {0})


class SequentialDownloadTests(unittest.TestCase):
    """Sequential download is the biggest lever on how much plays early.

    libtorrent defaults to rarest-first, so complete pieces scatter and the
    playable prefix -- the unbroken run from byte 0, the only part a player
    starting at the beginning can reach -- stalls far behind overall progress.
    Measured on Sintel at 35% complete: 333 of 987 pieces in 217 separate
    runs, with the prefix at 1-2%. With sequential on, the prefix tracked
    progress (9.1%, 13.2%, 15.4%, 16.3% as the same download advanced).

    Same bytes on disk, an order of magnitude more of them reachable.
    """

    def build(self, *, info, post_error=None, video=True):
        manager = TorrentManager("http://qbit", "admin", "pw")
        task = Task(id="t1", magnet="magnet:?x")
        task.torrent_hash = "abc123"
        names = ["Sintel.mp4", "a.srt"] if video else ["a.srt", "b.srt"]
        task.files = [
            TorrentFile(index=i, name=names[i], size=1000, progress=0.0,
                        priority=1, path=names[i])
            for i in range(len(names))
        ]
        self.posts: list[tuple[str, dict]] = []

        def fake_get(path, **params):
            if path.endswith("torrents/info"):
                return info
            raise AssertionError("unexpected GET " + path)

        def fake_post(path, **data):
            self.posts.append((path, data))
            if post_error and path.endswith(post_error[0]):
                raise HTTPError(post_error[1])
            return ""

        manager._get = fake_get
        manager._post = fake_post
        return manager, task

    def off(self, **extra):
        base = {"hash": "abc123", "f_l_piece_prio": True, "seq_dl": False}
        base.update(extra)
        return [base]

    def toggles(self):
        return [p for p in self.posts if "toggle" in p[0]]

    def test_sequential_download_is_enabled_on_select(self):
        manager, task = self.build(info=self.off())
        manager.select_files(task, {0})
        self.assertTrue(
            any(p[0].endswith("toggleSequentialDownload") for p in self.posts),
            "sequential download was never enabled",
        )

    def test_never_posts_the_toggle_when_already_on(self):
        """toggleSequentialDownload is a TOGGLE, not a setter. Posting it to a
        torrent that already has it on turns it back off -- which would silently
        undo this exact optimisation and stall the prefix with no visible
        reason."""
        manager, task = self.build(info=self.off(seq_dl=True))
        manager.select_files(task, {0})
        self.assertFalse(
            any(p[0].endswith("toggleSequentialDownload") for p in self.posts),
            "re-posted the toggle to an already-sequential torrent",
        )

    def test_reads_the_5x_field_name(self):
        """5.x calls it seq_dl; 4.x called it sequential_download. Reading the
        old name yields None, reads as off, and re-posts the toggle."""
        manager, task = self.build(
            info=[{"hash": "abc123", "f_l_piece_prio": True,
                   "seq_dl": True, "sequential_download": False}]
        )
        manager.select_files(task, {0})
        self.assertFalse(
            any(p[0].endswith("toggleSequentialDownload") for p in self.posts),
            "used the 4.x field name; the toggle got re-posted",
        )

    def test_a_missing_flag_reads_as_off(self):
        """Some builds omit seq_dl entirely. That has to post the toggle, not
        raise -- and definitely not read as 'on' and skip it."""
        manager, task = self.build(
            info=[{"hash": "abc123", "f_l_piece_prio": True}]
        )
        manager.select_files(task, {0})
        self.assertTrue(
            any(p[0].endswith("toggleSequentialDownload") for p in self.posts)
        )

    def test_not_applied_when_only_subtitles_are_selected(self):
        """A subtitle has no index to chase and no timeline to play, and
        prioritising pieces for one slows the file the user wants."""
        manager, task = self.build(info=self.off(), video=False)
        manager.select_files(task, {0})
        self.assertEqual(self.toggles(), [])

    def test_the_index_toggle_is_also_idempotent(self):
        """Both toggles now go through the same read-before-post helper, so
        both have to hold when already on."""
        manager, task = self.build(
            info=self.off(seq_dl=True, f_l_piece_prio=True)
        )
        manager.select_files(task, {0})
        self.assertEqual(self.toggles(), [])

    def test_a_failing_sequential_toggle_does_not_lose_the_selection(self):
        manager, task = self.build(
            info=self.off(), post_error=("toggleSequentialDownload", "404")
        )
        manager.select_files(task, {0})
        self.assertEqual(task.selected_files, {0})
        self.assertTrue(
            any(p[0].endswith("filePrio") for p in self.posts),
            "file priorities were never applied",
        )

    def test_a_failing_info_query_does_not_lose_the_selection(self):
        manager, task = self.build(
            info=[], post_error=("toggleSequentialDownload", "500")
        )
        manager.select_files(task, {0})
        self.assertEqual(task.selected_files, {0})

    def test_a_missing_torrent_entry_is_not_treated_as_on(self):
        """info returns rows for other torrents when the hash filter is
        ignored. Not finding our own row must fall through to posting."""
        manager, task = self.build(
            info=[{"hash": "other", "f_l_piece_prio": True, "seq_dl": True}]
        )
        manager.select_files(task, {0})
        self.assertTrue(
            any(p[0].endswith("toggleSequentialDownload") for p in self.posts)
        )


if __name__ == "__main__":
    unittest.main()
