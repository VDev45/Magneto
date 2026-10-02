# AGENTS.md

## What this repo is right now

A single-purpose Python experiment under `experiments/stream-validation/` that proves (or disproves) streaming a **partially downloaded** torrent over HTTP Range into VLC/MX Player.

There is **no** pyproject/setup.cfg, no lockfile, no linter, no typechecker, no formatter, and no CI. Don't invent commands for them, and don't assume `pytest` is available — it isn't installed and the tests are stdlib `unittest`.

Deps: `pip install -r experiments/stream-validation/requirements.txt` (fastapi, uvicorn, requests only).
**Requires Python 3.11+** — `models.py` / `task_manager.py` use `enum.StrEnum`.

No `httpx` is installed, so FastAPI's `TestClient` is unavailable — it would raise on import. Test endpoints by calling the handler directly with `main.tasks` / `main.torrents` swapped for stubs (see `test_api_tasks.py`).

### Browser console

`console.py` exposes `GET /console` and `GET /console/state` by adding routes to `main.app`, so `uvicorn console:app` serves the console **and** every lifecycle endpoint. It is test scaffolding for PLAN.md §1, not part of the architecture — but it still routes through `TorrentManager`, never the qBittorrent API directly.

The console's Range probe is what actually proves `206 Partial Content`; the `<video>` element only proves a browser will play the head of the file.

### CI: `workflow_dispatch` only, self-hosted

`.github/workflows/validate.yml` runs the whole experiment on a **self-hosted** runner: it needs a live qBittorrent, a long download, and a human watching. Never add `on: push` / `pull_request` — PLAN.md §1 requires the experiment to pass before feature work, and a bot re-running it on every commit proves nothing.

Hosted runners are the wrong tool twice over: no inbound connectivity (so no port forwarding), and Azure IPs often can't reach BitTorrent peers, so metadata never arrives.

The default magnet is Sintel (Blender open movie, CC-BY 3.0, 123 MB). If you swap it, recompute the infohash as `sha1(bencode(info_dict))` — **not** SHA1 of the whole `.torrent` file. The wrong hash yields a magnet that silently never resolves.

## Commands

```bash
# Tests — MUST be run from inside experiments/stream-validation
cd experiments/stream-validation && python -m unittest discover -v

# Single test
cd experiments/stream-validation && python -m unittest test_task_manager.TaskManagerTests.test_queue_promotes_after_cancel

# API server
cd experiments/stream-validation && uvicorn main:app
```

- Running `python -m unittest discover` from the repo root discovers **0 tests** and silently reports `NO TESTS RAN`. Imports in this project are flat (`from models import TaskState`), so `sys.path` must contain the experiment directory.
- Because of the same flat imports, `uvicorn main:app` only resolves from inside the experiment directory.

### `app.py` is stale — don't touch it

`app.py` has no `__main__` block and no `uvicorn.run`, so running it defines routes and exits silently. (The README used to instruct exactly that; it's been corrected to `uvicorn main:app`.)

The real entrypoint is **`main.py`**. It is the only app matching the README's "Lifecycle endpoints" section (`POST /tasks`, `POST /tasks/{id}/select`, `GET /tasks/{id}/state`, `POST /tasks/{id}/cancel`, `DELETE /tasks/{id}`). `app.py`'s `/stream`, `/probe`, `/state` are a dead single-torrent prototype keyed off one global `TORRENT_HASH`.

**Do not add features to `app.py`, and don't delete it** — PLAN.md §33 tracks its contents. Leave it as an archived prototype.

## Working directory / path gotcha

`LOCAL_SAVE_PATH` defaults to the *repo-root-relative* string `./experiments/stream-validation/downloads`, but you must launch from the experiment directory (above). Run without setting it and the download root silently resolves to:

```
experiments/stream-validation/experiments/stream-validation/downloads
```

So when starting the server from the experiment directory, **always set `LOCAL_SAVE_PATH` explicitly** to the absolute path of `experiments/stream-validation/downloads`. This is the directory docker-compose bind-mounts as the qBittorrent `/downloads`.

`QBIT_REMOTE_SAVE_PATH` (default `/downloads`) is the container-side path; it must correspond to `LOCAL_SAVE_PATH` on the host. Getting this pair mismatched is the most likely cause of "file not allocated yet" (HTTP 425).

Other env: `QBIT_URL` (`http://127.0.0.1:8080`), `QBIT_USERNAME` (`admin`), `QBIT_PASSWORD` (qBittorrent generates a random initial password — `docker logs magneto-qbittorrent 2>&1 | grep -i password`), `MAGNET_URI` (optional fallback — a magnet in the request body takes precedence).

## Architecture rules (load-bearing, don't violate)

From the module docstrings and `PLAN.md`. These are deliberate boundaries, not incidental:

- **`TorrentManager` is the only thing that touches the qBittorrent Web API.** Everything else calls `TaskManager` / `FileManager` / `StreamManager`. qBittorrent's replaceability (`QBittorrentEngine` → `LibtorrentEngine`) is a stated project goal; a stray `requests.get(QBIT_URL...)` anywhere else destroys it.
- **`TaskManager` is the lifecycle boundary.** The Telegram layer must never reach past it to the torrent engine.
- **Never resolve a torrent-supplied path outside the storage root.** `FileManager.path_for` is the single place this is enforced. Don't "simplify" it into a bare `Path(torrent_file.path)`.
- qBittorrent reports torrent-relative paths. `TorrentFile.name` is basename-only (for display/MIME), `TorrentFile.path` is the full relative path (for I/O). `FileManager` tries both `<root>/<path>` and `<root>/<task.name>/<path>` to handle single-file vs multi-file torrents.

### qBittorrent 5.x changed these response shapes

The adapter is written against the `lscr.io/linuxserver/qbittorrent:latest` image, which is 5.x. Four separate 4.x assumptions broke, each surfacing only as an opaque 500:

- **Login returns `204 No Content`**, not `200 "Ok."`. Checking the body rejected every successful login.
- **`torrents/add` returns JSON** `{"success_count": 1, ...}`, not `"Ok."`.
- **`torrents/filePrio` renamed its parameters**: `hash` (not `hashes`) and `priority` (not `prio`). The 4.x spelling returns `400 Missing required parameters: hash, priority`.
- **Adding a magnet that qBittorrent already holds returns `409 Conflict`.** `add_magnet` adopts it and re-tags it, because `wait_for_hash` looks up by tag and would otherwise time out.

`info`, `files`, `pieceStates`, `start`, `stop`, and `delete` still accept the 4.x `hashes=` spelling.

### Do NOT add magnets with `stopped="true"`

qBittorrent never fetches metadata for a stopped magnet, so `/torrents/files` stays empty, file selection is impossible, and the task deadlocks with zero files. Added magnets must be left running; `select_files` priority `0` is what actually limits the download. `wait_for_hash` therefore waits for *files*, not just the hash — waiting on the hash alone returns with an empty list.

### Task ↔ torrent correlation is tag-based

`TorrentManager.add_magnet` passes `tags=task.id`, and `wait_for_hash` resolves the hash by querying `/api/v2/torrents/info?tag=<task_id>` — *not* by hash, because the hash doesn't exist yet. Don't replace the tag mechanism with a list-scan like old `app.py` did; it's ambiguous with multiple tasks.

### `tasks.tasks` maps id → `TaskRecord`, not `Task`

The managers take a `Task`; `TaskManager.tasks` holds `TaskRecord` wrappers. Three endpoints in `main.py` used `tasks.tasks.get(task_id)` and passed the wrapper straight through, producing `AttributeError: 'TaskRecord' object has no attribute 'torrent_hash'`. Use `tasks.get(task_id)` (returns `Task`, raises `KeyError`) or unwrap `.task` deliberately.

### The console's JavaScript is a real file, served verbatim

`console.js` sits next to `console.py` and `/console.js` reads it at request time. **Do not embed it back into a Python string.** Two bugs came from that:

1. `CONSOLE_HTML`/`CONSOLE_JS` were non-raw triple-quoted strings, so the JS line `esc(head) + "\n\n" +` shipped as two **real newline bytes** inside a double-quoted literal — `SyntaxError`, and Chrome discarded the whole script. The page rendered its HTML shell while `probe`, `refresh` and `copyVlc` were all `undefined`. Every HTTP test passed throughout: the API was always fine, only the browser was dead.
2. Once the script moved to `/console.js`, the on-disk `console.js` and the string `CONSOLE_JS` both still existed and **drifted** (6674 vs 6671 bytes). `/console.js` served the string, so editing the file did nothing.

`ConsoleScriptSyntaxTests` pins this: `node --check` on the served bytes (skipped when node is absent), byte-identity between what's served and `console.js` on disk, and `hasattr(console, "CONSOLE_JS") == False`.

There is deliberately **no quote-balancing heuristic** as a node-free fallback. Two were written and both were wrong — regex character classes (`/[<>&"]/`) and comments contain unbalanced quotes, so they reported failures on valid code. `test_served_script_is_the_file_on_disk` plus `node --check` cover the defect exactly; a heuristic that cries wolf is worse than none.

### Console features worth knowing

- **Mobile**: `<meta name="viewport">` is the load-bearing line — without it a phone lays out at ~980px. Inputs are `font-size: 16px` so iOS Safari doesn't zoom on focus, and tables sit in `.scroll` wrappers so they scroll sideways instead of widening the page.
- **Clipboard magnets**: three paths — a native `paste` event (needs no permission prompt, the reliable one), a `focus` read via `navigator.clipboard.readText()`, and drop. All funnel through `magnetFrom()`, which extracts `magnet:?…` from surrounding text because magnets get shared inside page URLs. `readClipboard()` returns `""` on a missing API **or** a rejected permission; it must never throw or the page breaks on a refused prompt.
- **Download** (`save` button per file) uses `fetch` + `URL.createObjectURL`, not a plain `<a download>`: a bare link would bypass the piece gate and hand back sparse zeros. The filename is torrent-supplied and untrusted, so it goes through the `download` attribute rather than a header needing escaping. Revoke the object URL on a timer — revoking synchronously can cancel the download.
- Buttons are wired via `data-act`, not inline `onclick`, except where a row template needs the file index.

### Never serialise models with `__dict__`

`TorrentFile.is_video` is a `@property`, so it is **not** in `__dict__`. Serialising files with `f.__dict__` produced JSON without `is_video`, and the console's Range probe (`files.find(f => f.is_video)`) then reported *"no video file selected"* for every torrent — while the server-side `is_video` check kept working, so `curl` tests all passed. Use `TorrentFile.to_dict()`; the field list there is deliberate. `smoke_test.py` asserts this shape, which is the only reason the probe bug got caught.

### Queue behaviour

`TaskManager(max_running=2)` — `max_running` is constructor-only, not env-configurable. Queued tasks are promoted inside `refresh()` (on `COMPLETED`) and inside `cancel()`/`remove()`. There is **no background thread**: a slot only frees up when some endpoint call happens to drive `refresh()`. Don't add polling.

### State machine is lossy by design

`TorrentManager.refresh` unconditionally overwrites `task.state` from qBittorrent's state + progress. Consequence: `task.state = TaskState.STREAMING` (set in `main.py` before serving) is gone on the next `GET /tasks/{id}`. Only `queue_state` (a separate `TaskRecord` field) survives a refresh. Don't assume `STREAMING` is queryable.

### `POST /tasks` takes an optional magnet body

Reads `{"magnet": ...}` from the request body, falling back to `MAGNET_URI` from the environment. Without this, every task collapsed onto one env magnet and the `max_running=2` queue was unreachable over HTTP — it had unit tests but no way to be exercised end-to-end.

## Testing pattern

`test_task_manager.py` uses a duck-typed `FakeTorrentManager` stub injected into `TaskManager`. Follow it:

- **Unit tests must never require a running qBittorrent container.** No live-network fixtures.
- Assert on `FakeTorrentManager.started` / `.stopped` / `.removed` to verify engine calls, and on `task.state` to verify lifecycle.
- There's no `pytest`, no parametrization, no fixtures — plain `unittest.TestCase`.

## The stream gate waits for pieces; it never serves sparse zeros

`stream_file` takes an `engine` (the `TorrentManager`) and gates every range on real piece state. The mechanics:

- `TorrentFile.piece_span(start, end)` maps file-relative bytes to piece indices using `offset` + `piece_size`. `piece_start_byte(piece)` inverts it and clamps at 0, because bytes before a file's first byte belong to a *neighbouring* file in the same piece.
- `TorrentManager.first_missing_piece` / `wait_for_pieces` poll `pieceStates`. State `1` (downloading) counts as **missing** — treating it as available is the exact bug that produced zeros.
- A span reaching **past the end of the reported array is missing**, not present. The engine not describing a piece is not evidence it's downloaded.
- The response is clamped to the piece boundary, so `Content-Length` always matches the bytes actually sent. The old loop could `break` early and still promise the full length in the header, hanging the client.
- Truncated responses carry `X-Magneto-Truncated: piece-gate`, so a client knows to re-request rather than treat it as a broken transfer.
- `STREAM_PIECE_WAIT` (default 30s) bounds the wait. A player holding a request open forever is worse than an honest short response — it can retry, but it can't wait on a socket that never speaks.

If piece geometry is unknown (`piece_size == 0`, i.e. metadata hasn't resolved) or a piece-state query raises, it falls back to a zero-run scan. With `engine=None` there is no gating at all and behaviour is byte-identical to before.

### MP4 without a faststart `moov` cannot play partially — prioritise the index

Observed live on Sintel: box layout is `ftyp`(32) `free`(8) `mdat`(128641498) then `moov`(600214) at the **end**. Every demuxer must read `moov` for duration and the sample table before playing a single frame, so it requests the tail, gets a 425, and reports `PIPELINE_ERROR_READ: FFmpegDemuxer: data source error`.

That error is the gate doing its job — it refused to fabricate the tail. Serving zeros there would not have produced playback, only a different failure. But it does mean **playback cannot start until the index lands**, which is the whole §1 gate.

The fix is in `TorrentManager._prioritize_container_index`, called from `select_files`. It enables `toggleFirstLastPiecePrio`, which raises the priority of the first and last pieces and pulls the trailing `moov` down first. Measured at a 250 KB/s throttle, the full `moov` was present **28 seconds in, at 34.5% progress**; Chrome then reached `canplay` and played in real time. Verified end-to-end through the HTTP API at **8.6%** progress: `1024x436`, 5.94s of video in 6s wall time, 100% non-black pixels.

Three traps, all of which fail silently:

- **`toggleFirstLastPiecePrio` is a TOGGLE, not a setter.** Posting it twice turns the setting back off. `_first_last_prio()` must read the current value first and skip the call when already on. Verified against the 5.0 WebUI API docs, not inferred.
- **qBittorrent 5.x renamed the field to `f_l_piece_prio`.** 4.x called it `first_last_prio_pieces`; reading the old name yields `None`, which reads as "off" and would toggle an already-enabled torrent back off. Same family as `num_pieces` → `pieces_num` and `completed` → `pieces_have`.
- **It only helps when the video is the last file in the torrent.** "Last piece" means the last piece of the whole torrent. It worked on Sintel only because `Sintel.mp4` spans pieces 0–986 of 987. For a video that is a small file in the middle, the trailing pieces belong to a different file and this does nothing. That case needs a faststart/fMP4 asset or per-piece prioritisation from a lower-level engine — this is a real bound, not a general solution.

Only applied when a video file is selected: a subtitle has no index to chase, and prioritising its pieces would slow down the file the user actually wants. Engine failures are swallowed — losing the optimisation must not lose the file selection; the fallback is the old behaviour.

Verify the box layout before blaming the gate:

```python
# walk top-level boxes; if moov sits after mdat, no partial playback
```

What is proven: bytes are never fabricated, ranges clamp to the verified frontier, 425 when nothing is available, and **playback from 8.6% of a partial download**.


## Repo hygiene

`.gitignore` covers Python bytecode, `.venv/` (README creates it at the repo root), `.env`, tooling caches, and the two docker-compose bind mounts created on first `up`:

```
experiments/stream-validation/qbit-config/   # WebUI config + credentials
experiments/stream-validation/downloads/     # partial torrent data
```

Two deliberate exceptions — don't "tidy" them away:

- `requirements.txt` is **tracked**. There's no lockfile; this file is the dependency source of truth.
- `.env.example` is **not** ignored (`!.env.example`). `CHECKLIST.md` #44 requires it as the committed template; `.env` itself holds `QBIT_PASSWORD`.

## Source-of-truth docs

- `PLAN.md` — architecture, phase sequencing, validation gates, Definition of Done. **§32 "Development Order" and §36 "The One Rule" are constraints, not suggestions**: don't reverse the phase order or build features on unproven assumptions without a strong technical reason.
- `CHECKLIST.md` — per-item work tracker. Tick items as they're completed; it's the progress record.
- `experiments/stream-validation/README.md` — run instructions for the experiment, plus the validation checklist to tick off.

When docs and code disagree, the code is current — and fix the doc in the same change.
