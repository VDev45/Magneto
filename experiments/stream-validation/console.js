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
  const input = $("magnet");
  const button = document.querySelector('button[onclick="createTask()"]');
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
