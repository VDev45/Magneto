/* Magneto stream-validation console.
 *
 * Served verbatim by console.py. Do not paste this into a Python string:
 * escapes like "\n" are meaningful here and a non-raw Python literal would
 * rewrite them into real newlines, which is a JS SyntaxError that silently
 * kills the entire page. See ConsoleScriptSyntaxTests.
 */

let current = null, files = [], lastMagnet = "";

const $ = id => document.getElementById(id);
const mb = b => (b / 1048576).toFixed(1) + " MB";
const esc = s => String(s ?? "").replace(/[<>&"]/g, c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" }[c]));

function magnetFrom(text) {
  // Magnet URIs get shared inside page URLs and log lines, so pull the URI
  // out of surrounding text rather than demanding the whole string be one.
  const m = String(text || "").match(/magnet:\?[^\s"'<>]+/i);
  return m ? m[0] : "";
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  const body = await r.text();
  let json = null;
  try { json = JSON.parse(body); } catch (e) {}
  return { ok: r.ok, status: r.status, json, body };
}

/* ---------- clipboard ---------- */

async function readClipboard() {
  // Clipboard read needs a secure context and usually a user gesture, and it
  // rejects with NotAllowedError when permission is withheld. Every caller
  // treats failure as "no magnet" rather than as an error worth shouting
  // about, because a denied prompt must not break the console.
  if (!navigator.clipboard || !navigator.clipboard.readText) return "";
  try { return magnetFrom(await navigator.clipboard.readText()); } catch (e) { return ""; }
}

function setMagnet(value, note) {
  $("magnet").value = value;
  lastMagnet = value;
  $("createMsg").innerHTML = note
    ? '<span class="v">' + esc(note) + "</span>"
    : "";
  if (value) $("createTask").disabled = false;
}

async function pasteFromClipboard() {
  const msg = $("createMsg");
  msg.innerHTML = '<span class="mut">Reading clipboard…</span>';
  const m = await readClipboard();
  if (!m) {
    msg.innerHTML = '<span class="err">No magnet URI on the clipboard. ' +
      "Copy a magnet link, or paste it into the field.</span>";
    return;
  }
  setMagnet(m, "Pasted from clipboard (" + m.length + " chars). Press Create.");
}

function watchClipboard() {
  const input = $("magnet");
  // A native paste event needs no permission prompt at all, so this is the
  // reliable path. readText() above is only the convenience.
  input.addEventListener("paste", e => {
    const text = (e.clipboardData || window.clipboardData).getData("text");
    const m = magnetFrom(text);
    if (!m) return;
    e.preventDefault();
    setMagnet(m, "Pasted from clipboard (" + m.length + " chars). Press Create.");
  });

  // Auto-fill when the field is focused and the clipboard already holds a
  // magnet. Only reads on focus so it cannot nag on page load.
  input.addEventListener("focus", async () => {
    if (input.value.trim() || lastMagnet === "asked") return;
    lastMagnet = "asked";
    const m = await readClipboard();
    if (m && !input.value.trim()) {
      setMagnet(m, "Clipboard magnet detected (" + m.length + " chars). Press Create.");
    }
  });

  // A magnet can also arrive by drag-and-drop onto the field.
  input.addEventListener("drop", e => {
    const text = e.dataTransfer && e.dataTransfer.getData("text");
    const m = magnetFrom(text);
    if (!m) return;
    e.preventDefault();
    setMagnet(m, "Dropped magnet (" + m.length + " chars). Press Create.");
  });
}

/* ---------- task lifecycle ---------- */

async function createTask() {
  const input = $("magnet");
  const button = $("createTask");
  const message = $("createMsg");
  const magnet = input.value.trim();

  if (magnet && !magnet.toLowerCase().startsWith("magnet:?")) {
    message.innerHTML = '<span class="err">That does not look like a magnet URI.</span>';
    input.focus();
    return;
  }

  button.disabled = true;
  message.innerHTML = '<span class="mut">Creating task… waiting for qBittorrent to resolve metadata.</span>';

  try {
    const opts = {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(magnet ? { magnet } : {})
    };
    const r = await api("/tasks", opts);

    if (r.ok) {
      message.innerHTML = '<span class="v">created ' + esc(r.json.id) +
        ' — ' + esc(r.json.name || 'metadata ready') + '</span>';
      input.value = "";
      lastMagnet = "";
      await refresh();
      await pick(r.json.id);
    } else {
      message.innerHTML = '<span class="err">Create failed (' + r.status + '): ' +
        esc(r.json?.detail || r.body || 'unknown error') + '</span>';
    }
  } catch (e) {
    message.innerHTML = '<span class="err">Create failed: ' + esc(e?.message || e) + '</span>';
  } finally {
    button.disabled = false;
  }
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
    <tr${x.id === current ? ' class="sel"' : ""}>
      <td><a href="#" onclick="pick('${x.id}');return false">${esc(x.id.slice(0, 8))}</a>
          <div class="mut wrap">${esc((x.magnet || "").slice(-14))}</div></td>
      <td><span class="tag">${esc(x.state)}</span>${x.queue_state ? ' <span class="mut">' + esc(x.queue_state) + "</span>" : ""}</td>
      <td><div class="prog"><div class="bar"><i style="width:${(x.progress * 100).toFixed(1)}%"></i></div>
          <span class="mut">${(x.progress * 100).toFixed(1)}%</span></div></td>
      <td class="mut">${x.download_speed ? mb(x.download_speed) + "/s" : "—"}</td>
      <td><button class="ghost sm" onclick="pick('${x.id}')">open</button></td>
    </tr>`).join("");

  await loadDetail();
}

async function pick(id) { current = id; await loadDetail(); }

async function loadDetail() {
  if (!current) return;
  const d = await api("/tasks/" + current);
  if (!d.ok) return;
  files = d.json.files || [];
  $("taskLabel").textContent = current.slice(0, 8) + " — " + (d.json.name || "resolving metadata…");

  $("files").innerHTML = files.map(f => `
    <tr>
      <td><input type="checkbox" class="fs" value="${f.index}"></td>
      <td class="mut">${f.index}</td>
      <td class="name">${esc(f.name)}</td>
      <td class="mut nowrap">${mb(f.size)}</td>
      <td><div class="bar"><i style="width:${(f.progress * 100).toFixed(1)}%"></i></div></td>
      <td>${f.is_video ? '<span class="v">yes</span>' : '<span class="mut">no</span>'}</td>
      <td class="actions">
        <button class="ghost sm" onclick="play(${f.index})">play</button>
        <button class="ghost sm" onclick="download(${f.index}, ${JSON.stringify(f.name).replace(/"/g, "&quot;")})">save</button>
      </td>
    </tr>`).join("") || '<tr><td colspan="7" class="mut">no files yet</td></tr>';

  const p = await api("/tasks/" + current + "/state");
  if (p.ok) {
    const ps = p.json.piece_states;
    $("pieceLabel").textContent = current.slice(0, 8);
    $("pieces").innerHTML =
      `<div class="chips">
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
  note(r.ok ? "Selected " + idx.length + " file(s) — state: " + r.json.state
            : "Select failed (" + r.status + "): " + (r.json?.detail || r.body));
  refresh();
}

async function cancelTask() { await api("/tasks/" + current + "/cancel", { method: "POST" }); note("Cancelled."); refresh(); }
async function removeTask() { await api("/tasks/" + current, { method: "DELETE" }); note("Removed."); refresh(); }

function note(text) { $("createMsg").innerHTML = '<span class="mut">' + esc(text) + "</span>"; }

/* ---------- playback and download ---------- */

function streamUrl(i) { return "/stream/" + current + "/" + i; }

function play(i) {
  const url = streamUrl(i);
  $("player").src = url;
  $("vlcUrl").value = window.location.origin + url;
  $("player").play().catch(() => {});
}

/* Download the selected file through the same Range layer the player uses.
 *
 * A plain <a download> would bypass the gate and hand back sparse zeros, so
 * the bytes are pulled with fetch and handed to the browser as a blob. The
 * filename comes from the torrent, which is untrusted text, so it is passed
 * through the download attribute rather than a header we would have to
 * escape. */
async function download(i, name) {
  const button = $("dlMsg");
  button.innerHTML = '<span class="mut">Downloading ' + esc(name) + "…</span>";
  try {
    const res = await fetch(streamUrl(i));
    if (!res.ok) {
      button.innerHTML = '<span class="err">Download failed — HTTP ' + res.status +
        (res.status === 425 ? " (not downloaded yet)" : "") + "</span>";
      return;
    }
    const type = res.headers.get("content-type") || "application/octet-stream";
    const blob = await res.blob();
    const href = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = href;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    // Revoke on the next turn: revoking synchronously can cancel the
    // download in some browsers before it has read the blob.
    setTimeout(() => URL.revokeObjectURL(href), 10000);
    button.innerHTML = '<span class="v">Saved ' + esc(name) + " (" + mb(blob.size) + ").</span>";
  } catch (e) {
    button.innerHTML = '<span class="err">Download failed: ' + esc(e?.message || e) + "</span>";
  }
}

function copyVlc() {
  const field = $("vlcUrl");
  if (!field.value) return;
  field.select();
  navigator.clipboard?.writeText(field.value)
    .then(() => note("Stream URL copied."))
    .catch(() => { try { document.execCommand("copy"); note("Stream URL copied."); } catch (e) {} });
}

async function probe() {
  const v = files.find(f => f.is_video);
  if (!v) return ($("probeOut").innerHTML = '<span class="err">no video file selected</span>');
  const range = $("range").value.trim() || "bytes=0-1048575";
  const t0 = performance.now();
  let r;
  try { r = await fetch(streamUrl(v.index), { headers: { Range: range } }); }
  catch (e) { $("probeOut").innerHTML = '<span class="err">' + esc(e) + "</span>"; return; }
  const buf = await r.arrayBuffer();
  const ms = (performance.now() - t0).toFixed(0);
  const head = [...r.headers.entries()].map(([k, v]) => k + ": " + v).join("\n");
  $("probeOut").innerHTML =
    `<span class="${r.status === 206 ? "v" : "err"}">HTTP ${r.status} ${r.status === 206 ? "(206 Partial Content OK)" : "UNEXPECTED"}</span>\n` +
    esc(head) + "\n\n" +
    "received: " + buf.byteLength + " bytes in " + ms + " ms\n" +
    "first 16 bytes: " + [...new Uint8Array(buf).slice(0, 16)].map(b => b.toString(16).padStart(2, "0")).join(" ");
}

/* ---------- wiring ---------- */

$("createTask").addEventListener("click", createTask);
$("pasteMagnet").addEventListener("click", pasteFromClipboard);
$("magnet").addEventListener("keydown", e => { if (e.key === "Enter") createTask(); });
document.querySelectorAll("[data-act]").forEach(el =>
  el.addEventListener("click", () => window[el.dataset.act]()));

watchClipboard();
refresh();
setInterval(refresh, 2000);
