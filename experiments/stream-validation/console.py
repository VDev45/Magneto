"""Browser test console for the stream-validation experiment.

Run with:  uvicorn console:app --port 8000

This reuses ``main.app`` so every lifecycle endpoint keeps working; only the
console routes are added. It is test scaffolding for the experiment, not part
of the Magneto architecture described in PLAN.md -- nothing here talks to
qBittorrent directly, it reads the same managers ``main`` owns.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import main
from fastapi.responses import HTMLResponse, PlainTextResponse

app = main.app

# The page's JavaScript lives in console.js next to this file and is served
# verbatim.
#
# It used to be a triple-quoted Python string. Two problems with that: the
# file on disk and the string in this module drifted apart until editing the
# .js did nothing at all, and a non-raw Python string silently rewrites
# escapes -- a JS "\n" became a real newline byte inside a quoted literal,
# which is a SyntaxError, so Chrome discarded the whole script and the
# console rendered as dead HTML while every HTTP test still passed.
#
# Serving the file removes both hazards by construction: there is one copy,
# and no escape is ever interpreted.
CONSOLE_JS_PATH = Path(__file__).with_name("console.js")

CONSOLE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<!-- viewport is what makes this mobile-friendly at all; without it a phone
     lays the page out at ~980px and scales it down. -->
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Magneto — stream validation console</title>
<style>
  :root { color-scheme: dark; --bg:#0f1115; --card:#171a21; --line:#262b36;
          --mut:#8b93a1; --accent:#2d5bd7; --ghost:#2a2f3a; }
  * { box-sizing: border-box; }
  html { -webkit-text-size-adjust: 100%; }
  body { margin: 0; padding: 24px; background: var(--bg); color: #e6e6e6;
         font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
  h1 { font-size: 18px; margin: 0 0 4px; }
  h2 { font-size: 15px; margin: 0 0 10px; }
  .sub { color: var(--mut); margin-bottom: 20px; }
  .grid { display: grid; gap: 16px; grid-template-columns: 1fr 1fr; align-items: start; }
  @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 16px;
          margin-bottom: 16px; }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
  button { background: var(--accent); color: #fff; border: 0; border-radius: 5px;
           padding: 9px 15px; cursor: pointer; font: inherit; min-height: 38px; }
  button.ghost { background: var(--ghost); }
  button:disabled { opacity: .45; cursor: not-allowed; }
  button.sm { padding: 6px 11px; min-height: 32px; font-size: 13px; }
  /* 16px stops iOS Safari from zooming the viewport when a field is focused. */
  input { background: var(--bg); border: 1px solid #333a48; color: #e6e6e6;
          border-radius: 5px; padding: 9px; font: inherit; width: 100%; font-size: 16px; }
  input[type=checkbox] { width: auto; min-height: 0; margin: 0; }
  /* Tables scroll sideways inside their card rather than widening the page. */
  .scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; margin: 0 -4px; padding: 0 4px; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 7px 6px; border-bottom: 1px solid #232833; font-size: 13px; }
  th { color: var(--mut); font-weight: 500; white-space: nowrap; }
  tr.sel td { background: #1c212b; }
  .wrap { word-break: break-all; max-width: 22ch; }
  .nowrap { white-space: nowrap; }
  /* Long torrent filenames are the widest cell; truncate instead of forcing
     a horizontal scroll on a narrow screen. */
  td.name { max-width: 30ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  td.actions { white-space: nowrap; }
  td.actions button + button { margin-left: 5px; }
  .prog { display: flex; gap: 8px; align-items: center; min-width: 120px; }
  .chips { display: flex; gap: 7px; flex-wrap: wrap; margin-bottom: 8px; }
  .bar { height: 7px; background: #232833; border-radius: 4px; overflow: hidden; min-width: 90px; flex: 1; }
  .bar > i { display: block; height: 100%; background: #2d9d5c; }
  video { width: 100%; background: #000; border-radius: 6px; }
  pre { background: var(--bg); border: 1px solid var(--line); border-radius: 6px;
        padding: 10px; overflow: auto; max-height: 300px; font-size: 12px; white-space: pre-wrap;
        word-break: break-word; }
  .tag { background: #232833; border-radius: 4px; padding: 1px 7px; font-size: 12px;
         white-space: nowrap; display: inline-block; }
  .v { color: #6bbf7b; } .err { color: #e06c75; } .mut { color: var(--mut); }
  label { display: flex; gap: 7px; align-items: center; cursor: pointer; }

  /* Phones: tighter padding, full-width controls, comfortable tap targets. */
  @media (max-width: 640px) {
    body { padding: 14px; }
    h1 { font-size: 17px; }
    .card { padding: 13px; margin-bottom: 13px; }
    .sub { margin-bottom: 14px; }
    .row > input, .row > button { width: 100%; }
    .row > button { justify-content: center; }
    td.name { max-width: 16ch; }
    .wrap { max-width: 14ch; }
    pre { max-height: 220px; }
    .prog { min-width: 96px; }
  }
</style>
</head>
<body>
<h1>Magneto — stream validation console</h1>
<div class="sub">
  Test harness for PLAN.md §1. Auto-refreshes every 2s.
  <span id="age" class="tag mut"></span>
</div>

<div class="grid">
  <div>
    <div class="card">
      <h2>Create task</h2>
      <div class="row">
        <input id="magnet" placeholder="magnet:?xt=urn:btih:..." autocomplete="off"
               autocapitalize="off" autocorrect="off" spellcheck="false">
      </div>
      <div class="row">
        <button id="createTask">Create</button>
        <button id="pasteMagnet" class="ghost">Paste magnet</button>
      </div>
      <div class="sub" id="createMsg">Paste a magnet, or copy one elsewhere and tap the field — it reads the clipboard.</div>
    </div>

    <div class="card">
      <h2>Tasks</h2>
      <div class="scroll"><table><thead><tr>
        <th>magnet</th><th>state</th><th>progress</th><th>speed</th><th></th>
      </tr></thead><tbody id="tasks"></tbody></table></div>
    </div>

    <div class="card">
      <h2>Files — <span id="taskLabel" class="mut"></span></h2>
      <div class="row">
        <button data-act="selectAll" onclick="selectAll(true)">All</button>
        <button data-act="selectAll" onclick="selectAll(false)">None</button>
        <button data-act="doSelect">Start / Queue</button>
        <button class="ghost" data-act="cancelTask">Cancel</button>
        <button class="ghost" data-act="removeTask">Remove</button>
      </div>
      <div class="scroll"><table><thead><tr>
        <th></th><th>#</th><th>name</th><th>size</th><th>prog</th><th>video</th><th></th>
      </tr></thead><tbody id="files"></tbody></table></div>
      <div class="sub" id="dlMsg"></div>
    </div>
  </div>

  <div>
    <div class="card">
      <h2>Pieces — <span id="pieceLabel" class="mut"></span></h2>
      <div id="pieces" class="sub">no task selected</div>
    </div>

    <div class="card">
      <h2>Range probe</h2>
      <div class="row">
        <input id="range" placeholder="bytes=0-1048575" autocomplete="off" spellcheck="false">
      </div>
      <div class="row"><button data-act="probe">Fetch</button></div>
      <pre id="probeOut" class="mut">Sends a real Range request to /stream and reports status, headers and byte count. 206 = Partial Content.</pre>
    </div>

    <div class="card">
      <h2>Player</h2>
      <video id="player" controls preload="metadata" playsinline></video>
      <div class="row" style="margin-top:10px">
        <input id="vlcUrl" readonly placeholder="stream URL — copy into VLC / MX Player">
      </div>
      <div class="row"><button class="ghost" data-act="copyVlc">Copy stream URL</button></div>
      <div class="sub">VLC/MX Player is the real gate (PLAN.md §23). Far-ahead seeking on a partial file does not work reliably in a browser.</div>
    </div>
  </div>
</div>

<script src="/console.js"></script>
</body>
</html>
"""


@app.get("/console", response_class=HTMLResponse, include_in_schema=False)
def console() -> str:
    return CONSOLE_HTML


@app.get("/console.js", response_class=PlainTextResponse, include_in_schema=False)
def console_js() -> str:
    """The page script, served byte-for-byte from console.js.

    Reading the file rather than holding it in a Python literal means a JS
    escape can never be reinterpreted on the way out. See CONSOLE_JS_PATH.
    """
    return CONSOLE_JS_PATH.read_text(encoding="utf-8")


@app.get("/console/state", include_in_schema=False)
def console_state() -> dict[str, Any]:
    """Single snapshot for the console's 2s poll.

    Reads ``main``'s managers lazily so tests that patch them still work.
    Failures are reported per task rather than failing the whole snapshot --
    one dead torrent must not blank the console.
    """
    out = []
    for record in list(main.tasks.tasks.values()):
        task = record.task
        item: dict[str, Any] = {
            "id": task.id,
            "magnet": task.magnet,
            "name": task.name,
            "state": str(task.state),
            "queue_state": str(record.queue_state),
            "progress": task.progress,
            "files": [f.to_dict() for f in task.files],
            "error": task.error,
            "download_speed": 0.0,
        }
        if task.torrent_hash:
            try:
                info = main.torrents.status(task)
                if info:
                    item["download_speed"] = info.get("dlspeed", 0)
                    item["qbit_state"] = info.get("state")
            except Exception as exc:  # engine down / torrent gone
                item["engine_error"] = str(exc)
        out.append(item)
    return {"tasks": out}