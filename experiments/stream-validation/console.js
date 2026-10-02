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

/* ---------- seek tester ----------
 *
 * The last arrow in PLAN.md §36's loop: SEEK -> NEW PIECES -> CONTINUOUS
 * PLAYBACK. Measured 2026-10-02, and it does not close.
 *
 * Playback from a partial file works. Seeking into bytes that are not on disk
 * returns 425 after the gate waits out STREAM_PIECE_WAIT, because the gate can
 * *poll* pieceStates but cannot *ask* qBittorrent to fetch a piece: the Web API
 * has no per-piece priority endpoint at all. Closing that needs the
 * LibtorrentEngine swap, not a longer wait.
 *
 * This probes an arbitrary byte offset and reports what came back, so the
 * outcome is a status code and a byte count rather than "seeking feels
 * broken". Four outcomes, three of them correct behaviour:
 *
 *   206, full requested length  -> the range is on disk
 *   206 + X-Magneto-Truncated   -> straddles a hole, clamped honestly
 *   425                         -> nothing here is on disk
 *   DEFECT                      -> bytes the engine cannot verify; a real bug
 *
 * The 425 is the gate refusing to fabricate sparse zeros, which is the whole
 * point. A seek that lands there is not a server bug; it is the answer to "can
 * you seek past what you have?", and today the answer is no.
 *
 * There is no single frontier. libtorrent selects rarest-first, so complete
 * pieces scatter -- on Sintel at 35%, 333 of 987 pieces sat in 217 separate
 * runs while the playable prefix was at 1%. The /frontier payload therefore
 * reports the prefix and, for a given offset, how far playback could run from
 * there to the next hole. Both numbers are needed; reporting one claims a byte
 * at 99% is reachable when it is not.
 */

const SEEK_WINDOW = 65536;

// Does this look like a time the user meant to type, even if we cannot
// convert it? Used only to choose the error message, so it can be generous.
function isTimeForm(raw) {
  const text = String(raw || "").trim().toLowerCase();
  return /^\d+(\.\d+)?s$/.test(text) || /^\d+(:\d{1,2}){1,2}$/.test(text);
}

function parseSeekTarget(raw, duration, size) {
  // Returns a file-relative byte offset, or null if the input cannot be
  // turned into one. Accepts a plain byte offset, or a time: "90s", "1:30",
  // "1:02:03".
  //
  // Every time form needs both the player's duration and the file size,
  // because a time only becomes a byte offset through the file's length.
  // Without both it returns null rather than guessing -- a wrong guess here
  // probes the wrong bytes and then reports a confident verdict about them,
  // which is worse than refusing.
  //
  // A bare integer is always bytes, never seconds. "900" is indistinguishable
  // from 900 seconds by design, and reading it as time while the duration is
  // unknown silently turned "90s" into byte 90.
  const text = String(raw || "").trim().toLowerCase();
  if (!text) return null;
  if (/^\d+$/.test(text)) return Number(text);

  let seconds = null;
  if (/^\d+(\.\d+)?s$/.test(text)) {
    seconds = Number(text.slice(0, -1));
  } else if (/^\d+(:\d{1,2}){1,2}$/.test(text)) {
    const parts = text.split(":").map(Number);
    // Every part after the first is a seconds/minutes component and must be
    // under 60. "1:90" is not a time, and reading it as 150s would probe a
    // region the user never asked about.
    if (parts.slice(1).some(part => part >= 60)) return null;
    seconds = parts.reduce((total, part) => total * 60 + part, 0);
  }
  if (seconds === null) return null;

  if (!isFinite(duration) || duration <= 0) return null;
  if (!isFinite(size) || size <= 0) return null;
  // Linear across the file. Not exact -- the moov index holds the real sample
  // table -- but close enough to pick a region, and the response reports what
  // actually happened at the byte we landed on.
  return Math.floor((seconds / duration) * size);
}

async function seekProbeAt(offset) {
  const out = $("seekOut");
  const v = files.find(f => f.is_video);
  if (!v) return (out.innerHTML = '<span class="err">no video file selected</span>');

  const size = v.size;
  const at = Math.max(0, Math.min(offset, size - 1));
  const end = Math.min(at + SEEK_WINDOW - 1, size - 1);
  const fr = await api("/tasks/" + current + "/files/" + v.index + "/frontier?at=" + at);

  if (!fr.ok || !fr.json || !fr.json.geometry_known) {
    const reason = fr.ok
      ? "piece geometry unknown — the metadata has not resolved yet."
      : "the frontier query failed with HTTP " + fr.status + ".";
    return (out.innerHTML = '<span class="err">Cannot reason about what is on '
      + "disk: " + esc(reason) + "</span>");
  }
  const f = fr.json;

  const t0 = performance.now();
  let r;
  try {
    r = await fetch(streamUrl(v.index), { headers: { Range: "bytes=" + at + "-" + end } });
  } catch (e) {
    out.innerHTML = '<span class="err">request failed: ' + esc(e?.message || e) + "</span>";
    return;
  }
  const ms = (performance.now() - t0).toFixed(0);
  const buf = await r.arrayBuffer();
  const body = new Uint8Array(buf);

  // Re-read availability now that the response is in. The gate polls piece
  // states *during* its wait, so a request that sat for 10s may have had its
  // pieces arrive while we held a stale snapshot. Comparing against the
  // pre-request snapshot reports a healthy wait as a defect, which is worse
  // than useless: it sends people to hunt a bug in the gate that is not there.
  const after = await api("/tasks/" + current + "/files/" + v.index + "/frontier?at=" + at);
  const live = (after.ok && after.json && after.json.geometry_known) ? after.json : f;

  const L = [];
  const pct = n => (n / size * 100).toFixed(2) + "%";
  L.push("offset:    " + at.toLocaleString() + " of " + size.toLocaleString() + "  (" + pct(at) + ")");
  L.push("piece:     " + live.piece + "  state " + live.piece_state + " (2 = complete)"
    + (live.piece_state !== f.piece_state
       ? "  (was " + f.piece_state + " when this probe started — it arrived during the wait)" : ""));
  L.push("pieces:    " + live.downloaded_pieces + " of " + live.total_pieces + " complete");
  L.push("prefix:    " + live.prefix_available.toLocaleString() + "  (" + pct(live.prefix_available)
    + ")  end of the unbroken run from byte 0");
  L.push("askable:   " + live.available_bytes.toLocaleString() + " bytes from here to the next hole"
    + (live.available_end !== null ? " (ends " + live.available_end.toLocaleString() + ")" : ""));
  L.push("asked:     bytes " + at + "-" + end + "  (" + (end - at + 1) + " bytes)");
  L.push("got:       HTTP " + r.status + ", " + buf.byteLength + " bytes in " + ms + " ms");
  const got = r.headers.get("content-range");
  if (got) L.push("range:     " + got);
  const truncated = r.headers.get("x-magneto-truncated");
  if (truncated) L.push("truncated: " + truncated + "  (clamped to the verified frontier)");
  L.push("first 16:  " + [...body.slice(0, 16)].map(b => b.toString(16).padStart(2, "0")).join(" "));

  // Judge against the post-response state, which is what the gate itself saw.
  const requested = end - at + 1;
  const predicted = Math.min(live.available_bytes, requested);
  const served = buf.byteLength;
  const waited = Number(ms) > 500;
  let verdict, cls;
  if (r.status === 425) {
    // The refusal is the design working, not a failure. It only becomes
    // suspicious if the engine claims bytes here, so that is called out.
    verdict = predicted > 0
      ? "MISMATCH: the engine reported " + predicted.toLocaleString() + " bytes on disk "
        + "here, but the gate refused with 425. The gate and the engine disagree — report this."
      : "425 — the gate refused after waiting. Nothing here is on disk and it will not serve "
        + "the sparse zeros qBittorrent preallocates. That refusal is the behaviour the whole "
        + "design rests on.";
    cls = predicted > 0 ? "err" : "";
  } else if (r.status !== 206) {
    verdict = "HTTP " + r.status + " — unexpected.";
    cls = "err";
  } else if (live.piece_state !== 2) {
    // Served, yet the engine still reports this piece incomplete. That is the
    // one outcome that means the gate handed out bytes it could not verify.
    verdict = "DEFECT: served " + served.toLocaleString() + " bytes, but piece "
      + live.piece + " is still state " + live.piece_state
      + " (not complete). Bytes beyond the verified frontier were returned.";
    cls = "err";
  } else if (served >= requested) {
    verdict = "206, " + served.toLocaleString() + " bytes — the whole requested range is on "
      + "disk" + (waited ? ", and the gate had to wait for it to arrive." : ".");
    cls = "v";
  } else {
    verdict = "206, " + served.toLocaleString() + " of " + requested.toLocaleString()
      + " bytes requested, clamped at the next hole rather than the end of the file."
      + (size - end > 0 ? " " + (size - end).toLocaleString() + " bytes beyond are not on disk yet." : "");
    cls = "";
  }
  if (live.prefix_missing_piece !== null && live.prefix_missing_piece !== undefined) {
    L.push("");
    L.push("Sequential playback from byte 0 stops at piece " + live.prefix_missing_piece
      + " (" + pct(live.prefix_available) + "): a player starting at 0 cannot reach past it.");
    // Only worth saying when the two numbers actually differ. With sequential
    // download the prefix tracks overall progress and there is nothing after
    // the hole; with rarest-first the tail is full of pieces the prefix player
    // still cannot get to, which is the confusing case worth explaining.
    const ahead = live.total_pieces - live.downloaded_pieces;
    const beyond = live.total_pieces - live.prefix_missing_piece;
    if (beyond > ahead) {
      L.push("Some " + (beyond - ahead).toLocaleString() + " pieces past that hole are already "
        + "complete, but not the ones immediately after it, so they are unreachable in");
      L.push("sequence. "
        + (live.seq_dl
            ? "Sequential download is on, so this is the last-piece pull fetching the " +
              "container index, not rarest-first selection."
            : "This is rarest-first selection: enabling sequential download makes the "
              + "prefix track progress."));
    }
  }

  // Sparse-hole detector: a long run of zero bytes where the engine claims
  // complete pieces is the defect the gate exists to prevent.
  //
  // Offset 0 is exempt. Every MP4 opens 00 00 00 20 'ftyp', where the leading
  // zeros are a box length; the zero-run scan in stream_manager makes the same
  // exclusion ("and index > 0"). An earlier version of this check did not, so
  // it reported Sintel's own ftyp box as a sparse hole.
  const zeros = at === 0 ? 0 : countLeadingZeros(body);
  if (r.status === 206 && zeros >= SPARSE_HOLE_THRESHOLD) {
    L.push("");
    L.push("WARNING: first " + zeros.toLocaleString() + " bytes are zero, at a non-zero "
      + "offset where the engine reports complete pieces. That is the");
    L.push("sparse-hole signature — report it.");
  }

  out.innerHTML = '<span class="' + cls + '">' + esc(verdict) + "</span>\n\n" + esc(L.join("\n"));
  return {
    status: r.status, at, predicted, served, waited,
    piece: live.piece, pieceState: live.piece_state,
  };
}

// Long enough that it cannot be coincidence. Real media contains zero bytes,
// and long runs of them in black frames, so the threshold is deliberately high.
const SPARSE_HOLE_THRESHOLD = 4096;

function countLeadingZeros(bytes) {
  let n = 0;
  for (let i = 0; i < bytes.length; i++) {
    if (bytes[i] !== 0) break;
    n++;
  }
  return n;
}

async function seekProbe() {
  const el = $("player");
  const v = files.find(f => f.is_video);
  if (!v) return ($("seekOut").innerHTML = '<span class="err">no video file selected</span>');
  // Prefer the player's own position: it is the byte offset a real seek
  // would target, not a guess.
  if (el && isFinite(el.duration) && el.duration > 0) {
    const approx = Math.floor((el.currentTime / el.duration) * v.size);
    return seekProbeAt(approx);
  }
  return seekProbeAt(0);
}

function seekMid() {
  const v = files.find(f => f.is_video);
  return v ? seekProbeAt(Math.floor(v.size / 2)) : null;
}

function seekTail() {
  const v = files.find(f => f.is_video);
  return v ? seekProbeAt(Math.floor(v.size * 0.99)) : null;
}

async function seekInput() {
  const v = files.find(f => f.is_video);
  if (!v) return ($("seekOut").innerHTML = '<span class="err">no video file selected</span>');
  const el = $("player");
  const duration = el && isFinite(el.duration) ? el.duration : 0;
  const raw = $("seekOffset").value.trim();
const offset = parseSeekTarget(raw, duration, v.size);
if (offset === null) {
    // Name the actual reason. "Could not read that" for a time that simply
    // needs metadata loaded sends people hunting for a typo. A bare integer
    // is never rejected here -- it is always a valid offset -- so the only
    // ways to land on null are a time needing metadata or malformed input.
    const why = isTimeForm(raw)
      ? "A time needs the player's duration. Play or load the stream first, "
        + "or enter a byte offset."
      : "Use a byte offset, or a time like <code>90s</code> or <code>12:30</code>.";
    return ($("seekOut").innerHTML = '<span class="err">' + why + "</span>");
  }
  return seekProbeAt(offset);
}

/* ---------- wiring ---------- */

$("createTask").addEventListener("click", createTask);
$("pasteMagnet").addEventListener("click", pasteFromClipboard);
$("magnet").addEventListener("keydown", e => { if (e.key === "Enter") createTask(); });
$("seekOffset").addEventListener("keydown", e => { if (e.key === "Enter") seekInput(); });
document.querySelectorAll("[data-act]").forEach(el =>
  el.addEventListener("click", () => window[el.dataset.act]()));

watchClipboard();
refresh();
setInterval(refresh, 2000);
