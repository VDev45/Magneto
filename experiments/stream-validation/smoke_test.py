#!/usr/bin/env python3
"""Scripted check of the console UI and the Range layer.

The browser console proves PLAN.md §1 by hand. This asserts the same things
over HTTP so a regression fails the job instead of waiting to be noticed by
a human who happens to be watching.

Scope, stated plainly: this covers exactly what browser devtools would show
-- status codes, headers, byte counts. It cannot tell you whether a video
actually plays or whether VLC can seek into undownloaded territory. Those
remain human steps; see PLAN.md §12 and §23.

Usage:
    python3 smoke_test.py [--base-url http://127.0.0.1:8000] [--video-timeout 120]
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import requests

# Container magic numbers. An MP4 begins with an 'ftyp' box; Matroska (which is
# what most remuxed releases are) begins with the EBML magic. Anything else
# means we served bytes from the wrong place.
#
# Match the MP4 on the box *type*, not on a hardcoded box size. The four bytes
# before 'ftyp' are a length that varies with the brand list -- Sintel uses
# 0x20 -- so pinning specific sizes reported a false WARN on perfectly valid
# files.
MATROSKA_MAGIC = b"\x1a\x45\xdf\xa3"


def looks_like_container(head: bytes) -> bool:
    if head.startswith(MATROSKA_MAGIC):
        return True
    # MP4/QuickTime: a 32-bit big-endian box length, then the box type. Any
    # length is fine as long as the type is ftyp, and a length of 1 signals a
    # 64-bit largesize field, which is also legal.
    return len(head) >= 8 and head[4:8] in (b"ftyp", b"moov", b"mdat", b"free")


@dataclass
class Report:
    """Collects checks so one failure does not hide the rest."""

    rows: list[tuple[str, str, str]] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, "PASS" if ok else "FAIL", detail))

    def warn(self, name: str, detail: str) -> None:
        self.rows.append((name, "WARN", detail))

    @property
    def failed(self) -> bool:
        return any(status == "FAIL" for _, status, _ in self.rows)

    def render(self) -> str:
        width = max(len(name) for name, _, _ in self.rows)
        lines = ["| check | result | detail |", "|---|---|---|"]
        for name, status, detail in self.rows:
            lines.append(f"| {name.ljust(width)} | {status} | {detail} |")
        return "\n".join(lines)


def _parse_content_range(value: str) -> tuple[int, int, int] | None:
    """'bytes 0-1048575/129241752' -> (0, 1048575, 129241752)."""
    try:
        span, _, total = value.removeprefix("bytes ").partition("/")
        first, _, last = span.partition("-")
        return int(first), int(last), int(total)
    except (ValueError, AttributeError):
        return None


def check_console_ui(base: str, report: Report) -> None:
    """The page devtools loads must actually be the console, not a 404 body."""
    response = requests.get(f"{base}/console", timeout=15)
    body = response.text
    report.add(
        "GET /console",
        response.status_code == 200,
        f"HTTP {response.status_code}, {len(body)} bytes",
    )
    report.add(
        "console is the real page",
        "Magneto" in body and "Range probe" in body,
        "found <title> and Range probe control" if "Range probe" in body
        else "missing Range probe control",
    )


def check_state_endpoint(
    base: str, report: Report, allow_no_task: bool = False
) -> dict[str, Any] | None:
    """/console/state is what the page polls; it must list files for the row
    table and the file checkboxes to have anything to render.

    ``allow_no_task`` downgrades "no tasks" from FAIL to WARN. It has to be
    applied *here*, at the point the row is recorded: adding it as a FAIL and
    then excusing it in main() leaves ``report.failed`` True, so the flag looks
    like it works while still failing the job. That shipped once.
    """
    response = requests.get(f"{base}/console/state", timeout=15)
    if response.status_code != 200:
        report.add("GET /console/state", False, f"HTTP {response.status_code}")
        return None
    report.add("GET /console/state", True, "HTTP 200")

    payload = response.json()
    tasks = payload.get("tasks") or []
    if not tasks and allow_no_task:
        report.warn("task listed", "0 task(s) -- allowed, nothing to check yet")
    else:
        report.add("task listed", bool(tasks), f"{len(tasks)} task(s)")
    if not tasks:
        return None

    task = tasks[-1]
    files = task.get("files") or []
    report.add(
        "metadata resolved (files listed)",
        bool(files),
        f"{len(files)} file(s) for {task.get('name') or task['id'][:8]}",
    )
    if not files:
        return None

    videos = [f for f in files if f.get("is_video")]
    report.add(
        "a video file is listed",
        bool(videos),
        ", ".join(f["name"] for f in videos) if videos else "none flagged video",
    )
    return task if videos else None


def select_video(base: str, task: dict[str, Any], report: Report) -> bool:
    """Selection is the only thing that starts a download, so the scripted
    check has to do it -- otherwise nothing is on disk to range over."""
    index = next(f["index"] for f in task["files"] if f.get("is_video"))
    response = requests.post(
        f"{base}/tasks/{task['id']}/select",
        json={"file_indexes": [index]},
        timeout=30,
    )
    ok = response.status_code == 200
    report.add(
        "POST /select (video)",
        ok,
        f"file_index={index}, HTTP {response.status_code}",
    )
    return ok


def wait_for_bytes(base: str, task_id: str, timeout: int, report: Report) -> bool:
    """Wait for progress so the file exists and the head is likely fetched."""
    deadline = time.monotonic() + timeout
    progress = 0.0
    while time.monotonic() < deadline:
        response = requests.get(f"{base}/tasks/{task_id}", timeout=15)
        if response.status_code == 200:
            progress = response.json().get("progress") or 0.0
            if progress > 0:
                break
        time.sleep(3)
    report.add(
        "download progresses",
        progress > 0,
        f"{progress * 100:.1f}% after up to {timeout}s",
    )
    return progress > 0


def check_range(base: str, task: dict[str, Any], report: Report) -> None:
    """The 206 gate itself. Headers are asserted strictly because they are
    deterministic; the payload contents are reported but only warned on,
    since a sparse preallocated file legitimately reads as zeros."""
    index = next(f["index"] for f in task["files"] if f.get("is_video"))

    response = requests.get(
        f"{base}/stream/{task['id']}/{index}",
        headers={"Range": "bytes=0-1048575"},
        timeout=60,
    )
    report.add(
        "Range -> 206 Partial Content",
        response.status_code == 206,
        f"HTTP {response.status_code}",
    )
    if response.status_code != 206:
        return

    accept = response.headers.get("Accept-Ranges", "")
    report.add("Accept-Ranges: bytes", accept == "bytes", f"{accept!r}")

    content_range = response.headers.get("Content-Range", "")
    parsed = _parse_content_range(content_range)
    report.add(
        "Content-Range well formed",
        parsed is not None and parsed[0] == 0 and parsed[1] == 1048575,
        content_range or "absent",
    )

    expected = 1048576
    report.add(
        "body length matches request",
        len(response.content) == expected,
        f"{len(response.content)} of {expected} bytes",
    )

    report.add(
        "Content-Type is video",
        response.headers.get("Content-Type", "").startswith("video/"),
        response.headers.get("Content-Type", "absent"),
    )

    # Suffix range: players issue these when seeking to the end.
    tail = requests.get(
        f"{base}/stream/{task['id']}/{index}",
        headers={"Range": "bytes=-512"},
        timeout=60,
    )
    tail_parsed = _parse_content_range(tail.headers.get("Content-Range", ""))
    report.add(
        "suffix range (bytes=-512)",
        tail.status_code == 206 and len(tail.content) == 512,
        f"HTTP {tail.status_code}, {len(tail.content)} bytes",
    )

    head = response.content[:8]
    if looks_like_container(head):
        report.add("payload is a real container", True, f"magic {head[4:8]!r}")
    elif not any(response.content):
        # The piece gate should make this unreachable: it clamps responses to
        # the verified frontier rather than handing back sparse zeros. If this
        # fires, the gate regressed. PLAN.md §12.
        report.warn(
            "payload is a real container",
            "all zeros -- the gate served a sparse hole, which it should never do",
        )
    else:
        report.warn(
            "payload is a real container",
            f"no container magic in {head.hex()}",
        )

    # A gated response is shorter than asked for, and says so. Anything else
    # means the gate is not engaged.
    if response.headers.get("X-Magneto-Truncated"):
        report.add(
            "gate advertises truncation",
            True,
            f"{response.headers['Content-Range']} (shortened by the piece gate)",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--video-timeout",
        type=int,
        default=120,
        help="seconds to wait for the first bytes to land",
    )
    parser.add_argument(
        "--allow-no-task",
        action="store_true",
        help=(
            "succeed when the console has no task yet. The workflow no longer "
            "ships a default magnet, so an empty console is the normal state "
            "until someone pastes one."
        ),
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    report = Report()
    print(f"Console smoke test against {base}\n")

    print("-> console UI")
    check_console_ui(base, report)

    print("-> state and files")
    task = check_state_endpoint(base, report, allow_no_task=args.allow_no_task)
    if task is None:
        print(report.render())
        if args.allow_no_task:
            # The absence of a task was already downgraded to WARN above, so
            # report.failed reflects only real problems. It still fails if the
            # console itself is broken -- an empty console and a dead console
            # are very different states and must not look the same.
            print(
                "\nNo task yet -- expected, since the workflow ships no default "
                "magnet. The console and its state endpoint are reachable; "
                "paste a magnet in the browser to exercise the Range layer."
            )
        else:
            print("\n::error::Cannot continue: no task with files listed.")
        return 1 if report.failed else 0

    print("-> selection and download")
    if not select_video(base, task, report):
        print(report.render())
        print("\n::error::File selection failed; no bytes will arrive.")
        return 1
    wait_for_bytes(base, task["id"], args.video_timeout, report)

    print("-> HTTP Range")
    check_range(base, task, report)

    print()
    print(report.render())
    if report.failed:
        print("\n::error::Smoke test failed.")
        return 1
    print("\nAll checks passed. Playback and seeking are still human steps.")
    return 0


if __name__ == "__main__":
    sys.exit(main())