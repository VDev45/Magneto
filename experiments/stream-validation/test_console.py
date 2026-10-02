import os
import re
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


SEEK_SNIPPET = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");

// Pull the two pure helpers out of the served script and evaluate them. They
// touch no DOM, so they can be tested without a browser; everything that does
// touch the DOM is exercised by the manual seek probe instead.
function extract(name) {
  const start = src.search(new RegExp("^(async )?function " + name + "\\(", "m"));
  if (start < 0) throw new Error("not found: " + name);
  let i = src.indexOf("{", start), depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}") { depth--; if (depth === 0) return src.slice(start, j + 1); }
  }
  throw new Error("unbalanced braces in " + name);
}

const parseSeekTarget = eval("(" + extract("parseSeekTarget") + ")");
const countLeadingZeros = eval("(" + extract("countLeadingZeros") + ")");

const D = 888.0, SIZE = 129241752;
const out = [];
function check(label, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  out.push((ok ? "ok   " : "FAIL ") + label + " got=" + JSON.stringify(actual)
    + (ok ? "" : " want=" + JSON.stringify(expected)));
}

// A bare integer is a byte offset. There is no way to tell "900" (offset)
// from "900s" (time) other than the suffix, so integers stay bytes.
check("bare integer is bytes", parseSeekTarget("900", D, SIZE), 900);
check("thousands separator-ish plain digits", parseSeekTarget("129241752", D, SIZE), 129241752);

// Seconds -> bytes, linearly across the file. Note "90s" is a TIME, not
// the byte offset 90: returning 90 there probed the first 90 bytes of the
// file and reported a confident verdict about the wrong region.
check("90s of 888s", parseSeekTarget("90s", D, SIZE), Math.floor(90 / D * SIZE));
check("90s without duration is refused", parseSeekTarget("90s", 0, SIZE), null);
check("fractional seconds", parseSeekTarget("1.5s", D, SIZE), Math.floor(1.5 / D * SIZE));

// Colon forms.
check("1:30", parseSeekTarget("1:30", D, SIZE), Math.floor(90 / D * SIZE));
check("1:02:03", parseSeekTarget("1:02:03", D, SIZE), Math.floor(3723 / D * SIZE));
check("uppercase S", parseSeekTarget("90S", D, SIZE), Math.floor(90 / D * SIZE));
check("whitespace trimmed", parseSeekTarget("  90s  ", D, SIZE), Math.floor(90 / D * SIZE));

// A time without metadata is refused rather than guessed against size 0,
// which would report a verdict about a byte range nobody asked for.
check("time needs duration", parseSeekTarget("1:30", 0, SIZE), null);
check("time needs duration NaN", parseSeekTarget("1:30", NaN, SIZE), null);
check("time needs size", parseSeekTarget("1:30", D, 0), null);

// Garbage in, null out -- never NaN, which would produce "bytes NaN-undefined".
for (const bad of ["", "   ", "abc", "-5", "1:2:3:4", "90x", "1:90", "magnet:?x", "1.5"]) {
  check("rejects " + JSON.stringify(bad), parseSeekTarget(bad, D, SIZE), null);
}

// countLeadingZeros is the sparse-hole detector: the gate exists to stop
// zeros, so a leading zero run in a 206 body is a defect worth reporting.
check("no leading zeros", countLeadingZeros(new Uint8Array([1, 0, 0])), 0);
check("all zeros", countLeadingZeros(new Uint8Array([0, 0, 0, 0])), 4);
check("some leading zeros", countLeadingZeros(new Uint8Array([0, 0, 7, 1])), 2);
check("empty body", countLeadingZeros(new Uint8Array([])), 0);
check("starts non-zero", countLeadingZeros(new Uint8Array([255, 0])), 0);

console.log(out.join("\n"));
process.exit(out.some(l => l.startsWith("FAIL")) ? 1 : 0);
"""


class ConsoleSeekTests(unittest.TestCase):
    """The seek tester's two pure helpers.

    These are the parts of the seek tester that can be wrong in a way that
    still looks fine: a mis-parsed target probes the wrong bytes and reports
    a confident, wrong verdict, and a broken zero-counter silently stops
    detecting the sparse-hole signature the gate exists to prevent.
    """

    def run_node(self, script_name, body):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        with tempfile.NamedTemporaryFile(
            "w", suffix=".js", delete=False
        ) as handle:
            handle.write(body)
            path = handle.name
        self.addCleanup(os.unlink, path)
        result = subprocess.run(
            [node, path, str(console.CONSOLE_JS_PATH)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode, 0,
            f"{script_name} failed:\n{result.stdout}\n{result.stderr}",
        )
        return result.stdout

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_seek_helpers_behave(self):
        self.run_node("seek helpers", SEEK_SNIPPET)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_a_helpers_value_test_actually_asserts(self):
        """Guard against the harness silently passing. If extract() stops
        finding a function, the eval throws and node exits non-zero, which the
        assertion above catches -- but if the assertion list itself empties out,
        the script would pass while testing nothing."""
        output = self.run_node("seek helpers", SEEK_SNIPPET)
        self.assertIn("FAIL", SEEK_SNIPPET)  # the checks are in the file
        self.assertGreaterEqual(output.count("ok   "), 20)
        self.assertNotIn("FAIL", output)


class ConsoleSeekMarkupTests(unittest.TestCase):
    """The page has to actually offer the seek tester, and the controls must
    be wired to real functions -- data-act resolves through window, so a
    button pointing at a missing name fails silently on click."""

    def setUp(self):
        self.js = console.console_js()
        self.page = console.console()

    def test_page_has_a_seek_tester(self):
        for marker in ("seekOut", "seekOffset", "Seek tester"):
            self.assertIn(marker, self.page)

    def test_every_seek_button_resolves_to_a_declared_function(self):
        """data-act dispatches through window[act]. A button naming a
        function that was renamed or removed fails silently on click -- the
        button looks fine and does nothing.

        Uses re.search with MULTILINE rather than assertRegex(flags=...),
        which only exists from 3.13 and this project targets 3.11+.
        """
        actions = re.findall(r'data-act="(seek[^"]*)"', self.page)
        self.assertGreaterEqual(len(actions), 3, "no seek controls found")
        for action in actions:
            declared = re.search(
                rf"^(?:async )?function {re.escape(action)}\(",
                self.js,
                re.MULTILINE,
            )
            self.assertIsNotNone(
                declared,
                f'data-act="{action}" has no matching function; '
                "window[act] would be undefined and the click would do nothing",
            )

    def test_probe_reports_the_three_outcomes(self):
        """A seek probe that cannot distinguish 206 / truncated / 425 cannot
        answer the question PLAN.md §1 asks."""
        self.assertIn("x-magneto-truncated", self.js)
        self.assertIn("425", self.js)
        self.assertIn("206", self.js)
        self.assertIn("/frontier", self.js)



SEEK_PRELUDE = r"""
// Minimal DOM + fetch stubs, then the real console.js, then assertions.
// This drives seekProbeAt for real rather than grepping its source. The whole
// job of the verdict logic is deciding whether a given response is healthy or
// a defect, and a grep cannot observe that: the first version of these tests
// counted occurrences of "/frontier?at=" and passed while the code judged
// every response against a stale pre-request snapshot.
const SIZE = 129241752;

function makeEl() {
  return {
    innerHTML: "", textContent: "", value: "", dataset: {},
    addEventListener() {}, getAttribute() { return null; },
    classList: { add() {}, remove() {} },
  };
}
const els = {};
global.document = {
  getElementById(id) { return els[id] || (els[id] = makeEl()); },
  querySelectorAll() { return []; },
  addEventListener() {},
};
global.window = global;

// A clock the harness controls, so a scenario can simulate a request that sat
// for eight seconds waiting for a piece to land.
let clock = 0;
global.performance = { now: () => clock };
global.advanceClock = ms => { clock += ms; };

global.navigator = { clipboard: null };
global.setInterval = () => 0;

let nextFetch = null;
const seen = [];
global.fetch = async (url, opts) => { seen.push({ url, opts }); return nextFetch(url, opts); };
"""


SEEK_SCENARIOS = r"""
files = [{ index: 5, name: "Sintel.mp4", size: SIZE, is_video: true,
           piece_size: 131072, piece_range: [0, 986] }];
current = "t1";

const out = [];
function verdict() { return els.seekOut.innerHTML; }
function check(label, cond, extra) {
  out.push((cond ? "ok   " : "FAIL ") + label + (cond ? "" : "  " + (extra || "")));
}

function body(n, fill) { return new Uint8Array(n).fill(fill); }
function resp(status, bytes, headers) {
  return {
    ok: status < 400, status,
    headers: {
      get: k => (headers || {})[k.toLowerCase()] ?? null,
      entries: () => Object.entries(headers || {}),
    },
    arrayBuffer: async () => bytes.buffer.slice(
      bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
  };
}
function jsonResp(value) {
  return { ...resp(200, body(0), {}), text: async () => JSON.stringify(value) };
}
function frontierJson(pieceState, availableBytes, prefixPiece, extra) {
  return Object.assign({
    index: 5, size: SIZE, piece_size: 131072, piece_range: [0, 986],
    geometry_known: true,
    prefix_available: prefixPiece * 131072,
    prefix_missing_piece: prefixPiece,
    prefix_complete: false,
    downloaded_pieces: 234, total_pieces: 987,
    at: 1000, piece: 10, piece_state: pieceState,
    available_bytes: availableBytes,
    available_end: 1000 + availableBytes - 1,
    seq_dl: true,
  }, extra || {});
}
const RANGE = { "content-range": "bytes 1000-66535/" + SIZE };

// 1. A 206 whose piece was in progress at request time and has landed since.
//    The gate waited and it worked. Reporting this as a DEFECT would send
//    people to hunt a bug in the gate that is not there.
nextFetch = async (url) => {
  if (url.includes("/frontier")) {
    const firstCall = seen.filter(s => s.url.includes("/frontier")).length === 1;
    return jsonResp(frontierJson(firstCall ? 1 : 2, firstCall ? 0 : 65536, 10));
  }
  advanceClock(8000);
  return resp(206, body(65536, 0x41), RANGE);
};
await seekProbeAt(1000);
check("a wait that landed is success, not DEFECT",
      verdict().indexOf("DEFECT") < 0, verdict().slice(0, 160));
check("the wait is mentioned", /had to wait/.test(verdict()), verdict().slice(0, 200));
check("the piece transition is reported",
      /arrived during the wait/.test(verdict()), verdict().slice(0, 300));

// 2. A 206 where the piece is still incomplete afterwards. That is the one
//    outcome that means bytes were returned that cannot be verified.
nextFetch = async (url) => url.includes("/frontier")
  ? jsonResp(frontierJson(1, 0, 10))
  : resp(206, body(65536, 0x41), RANGE);
await seekProbeAt(1000);
check("unverifiable bytes are flagged DEFECT",
      verdict().indexOf("DEFECT") >= 0, verdict().slice(0, 200));

// 3. 425 with nothing on disk is the design working, not an error.
nextFetch = async (url) => url.includes("/frontier")
  ? jsonResp(frontierJson(0, 0, 10))
  : resp(425, body(50, 0x7b), {});
await seekProbeAt(1000);
check("425 on missing data is not an error",
      verdict().indexOf('class="err"') < 0 && /refused/.test(verdict()),
      verdict().slice(0, 200));

// 4. 425 while the engine claims bytes ARE there: gate and engine disagree.
nextFetch = async (url) => url.includes("/frontier")
  ? jsonResp(frontierJson(2, 65536, 10))
  : resp(425, body(50, 0x7b), {});
await seekProbeAt(1000);
check("425 against a claimed-available piece is MISMATCH",
      /MISMATCH/.test(verdict()), verdict().slice(0, 200));

// 5. A long zero run at a non-zero offset is the sparse-hole signature.
nextFetch = async (url) => url.includes("/frontier")
  ? jsonResp(frontierJson(2, 65536, 10))
  : resp(206, body(65536, 0x00), RANGE);
await seekProbeAt(5000);
check("a sparse hole is reported", /sparse-hole signature/.test(verdict()),
      verdict().slice(0, 300));

// 6. Sintel's own first bytes: 00 00 00 20 then 'ftyp'. At offset 0 the
//    leading zeros are a box length, not a hole. An earlier version flagged
//    this and reported the file's own header as corruption.
nextFetch = async (url) => {
  if (url.includes("/frontier")) return jsonResp(frontierJson(2, 65536, 0));
  const bytes = new Uint8Array(65536).fill(0x41);
  bytes.set([0, 0, 0, 0x20, 0x66, 0x74, 0x79, 0x70], 0);
  return resp(206, bytes, { "content-range": "bytes 0-65535/" + SIZE });
};
await seekProbeAt(0);
check("ftyp at offset 0 is not a sparse hole",
      verdict().indexOf("sparse-hole signature") < 0, verdict().slice(0, 400));

// 6b. A container that genuinely opens with more than the threshold's worth
//     of padding -- an mdat-first file with a large free box. The length
//     threshold alone would call that a sparse hole; only the offset-0
//     exemption saves it, so this is what makes that guard load-bearing.
nextFetch = async (url) => {
  if (url.includes("/frontier")) return jsonResp(frontierJson(2, 65536, 0));
  // 8 KB of padding, then the first real byte. The run is deliberately far
  // past the threshold, so only the offset-0 exemption can spare it.
  const bytes = new Uint8Array(65536).fill(0x00);
  bytes.set([0x66, 0x74, 0x79, 0x70], 8192);
  return resp(206, bytes, { "content-range": "bytes 0-65535/" + SIZE });
};
await seekProbeAt(0);
check("leading padding at offset 0 is not a sparse hole",
      verdict().indexOf("sparse-hole signature") < 0, verdict().slice(0, 400));

// 7. Clamped at a hole: report the clamp, not success.
nextFetch = async (url) => url.includes("/frontier")
  ? jsonResp(frontierJson(2, 4096, 10))
  : resp(206, body(4096, 0x41), {
      "content-range": "bytes 1000-5095/" + SIZE,
      "x-magneto-truncated": "piece-gate" });
await seekProbeAt(1000);
check("a clamped range reports the clamp, not success",
      /clamped at the next hole/.test(verdict()), verdict().slice(0, 250));

// 8. Unknown geometry must refuse to reason rather than guess.
nextFetch = async (url) => url.includes("/frontier")
  ? jsonResp({ index: 5, size: SIZE, piece_size: 0, piece_range: null,
               geometry_known: false })
  : resp(206, body(16, 0x41), {});
await seekProbeAt(1000);
check("unknown geometry refuses to judge",
      /Cannot reason/.test(verdict()), verdict().slice(0, 250));

// 9. A prefix lagging far behind overall progress, with the tail partly
//    complete. The explanation depends on whether sequential download is on,
//    and telling someone to enable a setting they already enabled is worse
//    than saying nothing at all.
nextFetch = async (url) => url.includes("/frontier")
  ? jsonResp(frontierJson(2, 0, 149, { downloaded_pieces: 179 }))
  : resp(425, body(50, 0x7b), {});
await seekProbeAt(Math.floor(SIZE * 0.95));
// "not rarest-first selection" contains "rarest-first selection", so assert
// on the actionable half: only the seq_dl-off branch should tell the reader
// to enable anything.
check("a lagging prefix with sequential on gives no enable advice",
      !/enabling/.test(verdict())
      && /last-piece pull/.test(verdict()), verdict().slice(-460));

nextFetch = async (url) => url.includes("/frontier")
  ? jsonResp(frontierJson(2, 0, 149, { downloaded_pieces: 179, seq_dl: false }))
  : resp(425, body(50, 0x7b), {});
await seekProbeAt(Math.floor(SIZE * 0.95));
check("with sequential off the reader is told to enable it",
      /enabling sequential download/.test(verdict()), verdict().slice(-460));

console.log(out.join("\n"));
process.exit(out.some(l => l.startsWith("FAIL")) ? 1 : 0);
"""


class ConsoleSeekVerdictTests(unittest.TestCase):
    """Drives seekProbeAt against stubbed fetch/DOM and asserts on what it
    reports, because the verdict logic's only job is telling a healthy
    response apart from a defect -- and that cannot be checked by grepping
    the source.

    The harness runs the real console.js, so these break if the probe starts
    reading the availability snapshot only once (which is exactly the bug
    that made a healthy 10-second wait report "DEFECT").
    """

    def setUp(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        script = console.console_js()
        marker = "/* ---------- wiring ---------- */"
        self.assertIn(marker, script, "wiring marker moved; the harness would "
                                     "strip the wrong block")
        # Drop the wiring: it calls refresh() on load, which would repopulate
        # `files` from a stubbed /console/state. The harness seeds state itself.
        script = script[: script.index(marker)]
        combined = (
            SEEK_PRELUDE + script + "(async () => {\n" + SEEK_SCENARIOS + "\n})();"
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".js", delete=False
        ) as handle:
            handle.write(combined)
            self.path = handle.name
        self.addCleanup(os.unlink, self.path)

    def run_scenarios(self):
        result = subprocess.run(
            ["node", self.path], capture_output=True, text=True
        )
        self.assertEqual(
            result.returncode, 0,
            f"seek verdict scenarios failed:\n{result.stdout}\n{result.stderr}",
        )
        return result.stdout

    def test_every_scenario_passes(self):
        output = self.run_scenarios()
        self.assertEqual(output.count("ok   "), 13)
        self.assertNotIn("FAIL", output)

    def test_the_harness_really_exercises_the_probe(self):
        """If the harness silently stopped running the scenarios it would
        pass vacuously. Corrupting the script must turn it red."""
        with open(self.path) as handle:
            original = handle.read()
        with open(self.path, "w") as handle:
            handle.write(original.replace('await seekProbeAt(1000);', '', 1))
        result = subprocess.run(
            ["node", self.path], capture_output=True, text=True
        )
        self.assertNotEqual(
            result.returncode, 0,
            "removing a scenario left the harness green",
        )
