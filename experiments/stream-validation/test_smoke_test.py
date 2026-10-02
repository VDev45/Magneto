import unittest

from smoke_test import looks_like_container


class ContainerMagicTests(unittest.TestCase):
    def test_mp4_ftyp_with_any_box_size(self):
        # The four bytes before 'ftyp' are a box length that varies with the
        # brand list. Hardcoding 0x14/0x18 made valid files report a WARN.
        for size in (0x10, 0x14, 0x18, 0x20, 0x2C):
            with self.subTest(size=size):
                head = size.to_bytes(4, "big") + b"ftypisom"
                self.assertTrue(looks_like_container(head))

    def test_mp4_64bit_largesize(self):
        head = b"\x00\x00\x00\x01ftypisom"
        self.assertTrue(looks_like_container(head))

    def test_matroska_ebml(self):
        self.assertTrue(looks_like_container(b"\x1a\x45\xdf\xa3\x01\x02"))

    def test_other_mp4_boxes_recognised(self):
        for box in (b"moov", b"mdat", b"free"):
            with self.subTest(box=box):
                head = (16).to_bytes(4, "big") + box
                self.assertTrue(looks_like_container(head))

    def test_sintel_real_header(self):
        # The exact bytes observed live from downloads/Sintel/Sintel.mp4.
        self.assertTrue(
            looks_like_container(bytes.fromhex("000000206674797069736f6d"))
        )

    def test_zeros_are_not_a_container(self):
        self.assertFalse(looks_like_container(b"\x00" * 8))

    def test_arbitrary_bytes_are_not_a_container(self):
        self.assertFalse(looks_like_container(b"\xab\xcd\xef\x01garbage"))

    def test_short_input_is_not_a_container(self):
        self.assertFalse(looks_like_container(b"\x00\x00"))
        self.assertFalse(looks_like_container(b""))


if __name__ == "__main__":
    unittest.main()
