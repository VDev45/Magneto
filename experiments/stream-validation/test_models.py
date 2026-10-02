import unittest

from models import TorrentFile


def make(name: str) -> TorrentFile:
    return TorrentFile(
        index=0, name=name, size=1, progress=0.0, priority=1, path=name
    )


class SerialisationTests(unittest.TestCase):
    """The console's Range probe does files.find(f => f.is_video). If the
    serialised file lacks that key the probe reports 'no video file
    selected' for every torrent, which is what __dict__ did to it."""

    def test_is_video_is_present_in_api_shape(self):
        payload = make("Sintel.mp4").to_dict()
        self.assertIn("is_video", payload)
        self.assertIs(payload["is_video"], True)

    def test_subtitle_is_not_flagged_video(self):
        self.assertIs(make("Sintel.en.srt").to_dict()["is_video"], False)

    def test_extension_matching_is_case_insensitive(self):
        self.assertIs(make("MOVIE.MKV").to_dict()["is_video"], True)

    def test_dotless_name_is_not_video(self):
        self.assertIs(make("README").to_dict()["is_video"], False)

    def test_carries_every_field_the_table_renders(self):
        payload = make("Sintel.mp4").to_dict()
        self.assertEqual(
            set(payload),
            {"index", "name", "size", "progress", "priority", "path", "is_video"},
        )

    def test_is_serialisable_to_json(self):
        import json

        json.dumps(make("Sintel.mp4").to_dict())


if __name__ == "__main__":
    unittest.main()