import unittest

from torrent_manager import TorrentManager

HEX = "08ada5a7a6183aae1e09d831df6748d566095a10"
# base32 of the same infohash, the other legal form in a magnet URI
BASE32 = "BCW2LJ5GDA5K4HQJ3AY56Z2I2VTASWQQ"


class InfohashTests(unittest.TestCase):
    """_infohash backs the 409 re-tag path, so it must parse both encodings
    and refuse garbage rather than send it to qBittorrent."""

    def test_hex(self):
        self.assertEqual(
            TorrentManager._infohash(f"magnet:?xt=urn:btih:{HEX}&dn=x"), HEX
        )

    def test_base32_decodes_to_the_same_hex(self):
        self.assertEqual(
            TorrentManager._infohash(f"magnet:?xt=urn:btih:{BASE32}"), HEX
        )

    def test_uppercase_hex_is_normalised(self):
        self.assertEqual(
            TorrentManager._infohash(f"magnet:?xt=urn:btih:{HEX.upper()}"), HEX
        )

    def test_rejects_non_magnet(self):
        self.assertIsNone(TorrentManager._infohash("not-a-magnet"))

    def test_rejects_wrong_length_digest(self):
        self.assertIsNone(TorrentManager._infohash("magnet:?xt=urn:btih:garbage"))


class Qbittorrent5ResponseTests(unittest.TestCase):
    """4.x returns "Ok."; 5.x returns JSON / 204. add_magnet must accept both
    and only reject when the payload genuinely reports no success."""

    def test_accepts_legacy_ok(self):
        self.assertTrue(TorrentManager._add_accepted("Ok."))
        self.assertTrue(TorrentManager._add_accepted("Ok"))

    def test_accepts_5x_json(self):
        self.assertTrue(
            TorrentManager._add_accepted(
                '{"added_torrent_ids":["08ada5a7"],"failure_count":0,'
                '"pending_count":0,"success_count":1}'
            )
        )

    def test_rejects_zero_success(self):
        self.assertFalse(
            TorrentManager._add_accepted('{"added_torrent_ids":[],"success_count":0}')
        )

    def test_rejects_unparseable(self):
        self.assertFalse(
            TorrentManager._add_accepted("<html>502 Bad Gateway</html>")
        )

    def test_rejects_empty_body(self):
        self.assertFalse(TorrentManager._add_accepted(""))


if __name__ == "__main__":
    unittest.main()