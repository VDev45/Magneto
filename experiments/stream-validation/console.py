"""Browser test console for the stream-validation experiment.

Run with:  uvicorn console:app --port 8000

This reuses ``main.app`` so every lifecycle endpoint keeps working; only the
console routes are added. It is test scaffolding for the experiment, not part
of the Magneto architecture described in PLAN.md -- nothing here talks to
qBittorrent directly, it reads the same managers ``main`` owns.
"""

from __future__ import annotations

from typing import Any

import main
from fastapi.responses import HTMLResponse

app = main.app

CONSOLE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Magneto — stream validation console</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 24px; background: #0f1115; color: #e6e6e6;
         font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
  h1 { font-size: 18px; margin: 0 0 4px; }
  h2 { font-size: 15px; margin: 0 0 10px; }
  .sub { color: #8b93a1; margin-bottom: 20px; }
  .grid { display: grid; gap: 16px; grid-template-columns: 1fr 1fr; align-items: start; }
  @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
  .card { background: #171a21; border: 1px solid #262b36; border-radius: 8px; padding: 16px; }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
  button { background: #2d5bd7; color: #fff; border: 0; border-radius: 5px;
           padding: 7px 13px; cursor: pointer; font: inherit; }
  button.ghost { background: #2a2f3a; }
  button:disabled { opacity: .45; cursor: not-allowed; }
  input { background: #0f1115; border: 1px solid #333a48; color: #e6e6e6;
          border-radius: 5px; padding: 7px 9px; font: inherit; width: 100%; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 5px 6px; border-bottom: 1px solid #232833; font-size: 13px; }
  th { color: #8b93a1; font-weight: 500; }
  .bar { height: 7px; background: #232833; border-radius: 4px; overflow: hidden; min-width: 90px; }
  .bar > i { display: block; height: 100%; background: #2d9d5c; }
  video { width: 100%; background: #000; border-radius: 6px; }
  pre { background: #0f1115; border: 1px solid #262b36; border-radius: 6px;
        padding: 10px; overflow: auto; max-height: 300px; font-size: 12px; white-space: pre-wrap; }
  .tag { background: #232833; border-radius: 4px; padding: 1px 7px; font-size: 12px; }
  .v { color: #6bbf7b; } .err { color: #e06c75; } .mut { color: #8b93a1; }
  label { display: flex; gap: 7px; align-items: center; cursor: pointer; }
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
    <div class="card" style="margin-bottom:16px">
      <h2>Create task</h2>
      <div class="row">
        <input id="magnet" placeholder="magnet:?xt=urn:btih:... (blank = MAGNET_URI fallback)">
        <button onclick="createTask()">Create</button>
      </div>
      <div class="sub" id="createMsg"></div>
    </div>

    <div class="card" style="margin-bottom:16px">
      <h2>Tasks</h2>
      <table><thead><tr>
        <th>magnet</th><th>state</th><th>progress</th><th>speed</th><th></th>
      </tr></thead><tbody id="tasks"></tbody></table>
    </div>

    <div class="card">
      <h2>Files — <span id="taskLabel" class="mut"></span></h2>
      <div class="row">
        <button onclick="selectAll(true)">All</button>
        <button onclick="selectAll(false)">None</button>
        <button onclick="doSelect()">Start / Queue</button>
        <button class="ghost" onclick="cancelTask()">Cancel</button>
        <button class="ghost" onclick="removeTask()">Remove</button>
      </div>
      <table><thead><tr>
        <th></th><th>#</th><th>name</th><th>size</th><th>prog</th><th>video</th><th>play</th>
      </tr></thead><tbody id="files"></tbody></table>
    </div>
  </div>

  <div>
    <div class="card" style="margin-bottom:16px">
      <h2>Pieces — <span id="pieceLabel" class="mut"></span></h2>
      <div id="pieces" class="sub">no task selected</div>
    </div>

    <div class="card" style="margin-bottom:16px">
      <h2>Range probe</h2>
      <div class="row">
        <input id="range" placeholder="bytes=0-1048575">
        <button onclick="probe()">Fetch</button>
      </div>
      <pre id="probeOut" class="mut">Sends a real Range request to /stream and reports status, headers and byte count. 206 = Partial Content.</pre>
    </div>

    <div class="card">
      <h2>Player</h2>
      <video id="player" controls preload="metadata"></video>
      <div class="row" style="margin-top:10px">
        <input id="vlcUrl" readonly placeholder="stream URL — paste into VLC / MX Player">
        <button onclick="copyVlc()">Copy</button>
      </div>
      <div class="sub">VLC/MX Player is the real gate (PLAN.md §23). Far-ahead seeking on a partial file does not work reliably in a browser.</div>
    </div>
  </div>
</div>

<script>
let current = null, files = [];

const $ = id => document.getElementById(id);
const mb = b => (b / 1048576).toFixed(1) + " MB";
const esc = s => String(s ?? "").replace(/[<>&]/g, c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]));

async function api(path, opts) {
  const r = await fetch(path, opts);
  const body = await r.text();
  let json = null;
  try { json = JSON.parse(body); } catch (e) {}
  return { ok: r.ok, status: r.status, json, body };
}

async function createTask() {
  const magnet = $("magnet").value.trim();
  const opts = { method: "POST", headers: { "Content-Type": "application/json" },
                 body: JSON.stringify(magnet ? { magnet } : {}) };
  const r = await api("/tasks", opts);
  $("createMsg").innerHTML = r.ok
    ? '<span class="v">created ' + esc(r.json.id) + "</span>"
    : '<span class="err">' + r.status + " " + esc(r.json?.detail || r.body) + "</span>";
  refresh();
}

async function refresh() {
  const t = $("tasks");
  const r = await api("/console/state");
  if (!r.ok) { t.innerHTML = '<tr><td colspan="5" class="err">' + r.status + "</td></tr>"; return; }
  const list = r.json.tasks;
  $("age").textContent = "updated " + new Date().toLocaleTimeString();

  if (!list.length) {
    t.innerHTML = '<tr><td colspan="5" class="mut">no tasks yet</td></tr>';
    if (current) { current = null; $("taskLabel").textContent = ""; $("files").innerHTML = ""; }
    return;
  }
  if (!current || !list.some(x => x.id === current)) current = list[list.length - 1].id;

  t.innerHTML = list.map(x => `
    <tr>
      <td><a href="#" onclick="pick('${x.id}');return false" class="${x.id === current ? "v" : ""}">${esc(x.id.slice(0, 8))}</a>
          <div class="mut" style="font-size:11px">${esc((x.magnet || "").slice(-12))}</div></td>
      <td><span class="tag">${esc(x.state)}</span>${x.queue_state ? ' <span class="mut">' + esc(x.queue_state) + "</span>" : ""}</td>
      <td><div class="row" style="margin:0"><div class="bar"><i style="width:${(x.progress * 100).toFixed(1)}%"></i></div>
          <span class="mut">${(x.progress * 100).toFixed(1)}%</span></div></td>
      <td class="mut">${x.download_speed ? mb(x.download_speed) + "/s" : "—"}</td>
      <td><button class="ghost" onclick="pick('${x.id}')">open</button></td>
    </tr>`).join("");

  await loadDetail();
}

async function pick(id) { current = id; await loadDetail(); }

async function loadDetail() {
  const d = await api("/tasks/" + current);
  if (!d.ok) return;
  files = d.json.files || [];
  $("taskLabel").textContent = current.slice(0, 8) + " — " + (d.json.name || "resolving metadata…");
  $("files").innerHTML = files.map(f => `
    <tr>
      <td><input type="checkbox" class="fs" value="${f.index}"></td>
      <td class="mut">${f.index}</td>
      <td>${esc(f.name)}</td>
      <td class="mut">${mb(f.size)}</td>
      <td><div class="bar"><i style="width:${(f.progress * 100).toFixed(1)}%"></i></div></td>
      <td>${f.is_video ? '<span class="v">yes</span>' : '<span class="mut">no</span>'}</td>
      <td><button class="ghost" onclick="play(${f.index})">play</button></td>
    </tr>`).join("") || '<tr><td colspan="7" class="mut">no files yet</td></tr>';

  const p = await api("/tasks/" + current + "/state");
  if (p.ok) {
    const ps = p.json.piece_states;
    $("pieceLabel").textContent = current.slice(0, 8);
    $("pieces").innerHTML =
      `<div class="row">
        <span class="tag">${ps.total} pieces</span>
        <span class="tag v">${ps.downloaded} downloaded</span>
        <span class="tag">${ps.downloading} downloading</span>
        <span class="tag mut">${ps.not_downloaded} pending</span>
      </div>
      <div class="bar"><i style="width:${(ps.downloaded / ps.total * 100).toFixed(1)}%"></i></div>`;
  }
}

function selectAll(on) {
  document.querySelectorAll(".fs").forEach(c => { if (on || c.checked) c.checked = on; });
}

async function doSelect() {
  const idx = [...document.querySelectorAll(".fs")].filter(c => c.checked).map(c => +c.value);
  if (!idx.length) return;
  const r = await api("/tasks/" + current + "/select", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file_indexes: idx }) });
  alert(r.ok ? "state: " + r.json.state : r.status + " " + r.body);
  refresh();
}

async function cancelTask() { await api("/tasks/" + current + "/cancel", { method: "POST" }); refresh(); }
async function removeTask() { await api("/tasks/" + current, { method: "DELETE" }); refresh(); }

function play(i) {
  const url = "/stream/" + current + "/" + i;
  $("player").src = url;
  $("vlcUrl").value = window.location.origin + url;
  $("player").play().catch(() => {});
}

function copyVlc() { $("vlcUrl").select(); document.execCommand("copy"); }

async function probe() {
  const v = files.find(f => f.is_video);
  if (!v) return ($("probeOut").innerHTML = '<span class="err">no video file selected</span>');
  const range = $("range").value.trim() || "bytes=0-1048575";
  const url = "/stream/" + current + "/" + v.index;
  const t0 = performance.now();
  let r;
  try { r = await fetch(url, { headers: { Range: range } }); }
  catch (e) { $("probeOut").innerHTML = '<span class="err">' + esc(e) + "</span>"; return; }
  const buf = await r.arrayBuffer();
  const ms = (performance.now() - t0).toFixed(0);
  const head = [...r.headers.entries()].map(([k, v]) => k + ": " + v).join("\\n");
  $("probeOut").innerHTML =
    `<span class="${r.status === 206 ? "v" : "err"}">HTTP ${r.status} ${r.status === 206 ? "(206 Partial Content OK)" : "UNEXPECTED"}</span>\n` +
    esc(head) + "\n\n" +
    "received: " + buf.byteLength + " bytes in " + ms + " ms\\n" +
    "first 16 bytes: " + [...new Uint8Array(buf).slice(0, 16)].map(b => b.toString(16).padStart(2, "0")).join(" ");
}

refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


@app.get("/console", response_class=HTMLResponse, include_in_schema=False)
def console() -> str:
    return CONSOLE_HTML


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