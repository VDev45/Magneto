import unittest
from unittest import mock

import smoke_test
from smoke_test import Report, check_state_endpoint, looks_like_container


class AllowNoTaskTests(unittest.TestCase):
    """--allow-no-task has to actually allow the run to pass.

    It shipped as a no-op: "task listed" was recorded as a FAIL, and main()
    then returned ``1 if report.failed else 0`` with failed already True. The
    message said "that is expected" while the job still failed, which is the
    worst kind of bug -- it looks handled.
    """

    def call(self, *, tasks, allow):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"tasks": tasks}
        report = Report()
        with mock.patch.object(smoke_test.requests, "get", return_value=response):
            result = check_state_endpoint(report=report, base="http://x",
                                          allow_no_task=allow)
        return result, report

    def test_empty_console_passes_when_allowed(self):
        _, report = self.call(tasks=[], allow=True)
        self.assertFalse(report.failed)
        self.assertEqual(report.rows[-1][1], "WARN")

    def test_empty_console_fails_when_not_allowed(self):
        _, report = self.call(tasks=[], allow=False)
        self.assertTrue(report.failed)
        self.assertEqual(report.rows[-1][1], "FAIL")

    def test_returns_none_when_empty(self):
        result, _ = self.call(tasks=[], allow=True)
        self.assertIsNone(result)

    def test_a_task_is_a_pass_either_way(self):
        task = {
            "id": "abc12345",
            "name": "Sintel",
            "files": [{"index": 0, "name": "Sintel.mp4", "is_video": True}],
        }
        result, report = self.call(tasks=[task], allow=True)
        self.assertIsNotNone(result)
        self.assertFalse(report.failed)
        # "task listed" must be a real PASS, not a WARN the allowance produced.
        self.assertEqual(report.rows[-1][1], "PASS")

    def test_allowing_no_task_does_not_mask_a_broken_console(self):
        """An empty console and a dead console are different states. A 500
        must still fail even with the allowance, or the smoke test goes green
        while the server is down."""
        response = mock.Mock(status_code=500)
        report = Report()
        with mock.patch.object(smoke_test.requests, "get", return_value=response):
            check_state_endpoint(base="http://x", report=report, allow_no_task=True)
        self.assertTrue(report.failed)

    def test_warn_rows_never_count_as_failure(self):
        report = Report()
        report.warn("something", "detail")
        self.assertFalse(report.failed)
        self.assertIn("WARN", report.render())


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
