# AGENTS.md

## What this repo is right now

A single-purpose Python experiment under `experiments/stream-validation/` that proves (or disproves) streaming a **partially downloaded** torrent over HTTP Range into VLC/MX Player.

There is **no** pyproject/setup.cfg, no lockfile, no linter, no typechecker, no formatter, and no CI. Don't invent commands for them, and don't assume `pytest` is available — it isn't installed and the tests are stdlib `unittest`.

Deps: `pip install -r experiments/stream-validation/requirements.txt` (fastapi, uvicorn, requests only).
**Requires Python 3.11+** — `models.py` / `task_manager.py` use `enum.StrEnum`.

No `httpx` is installed, so FastAPI's `TestClient` is unavailable — it would raise on import. Test endpoints by calling the handler directly with `main.tasks` / `main.torrents` swapped for stubs (see `test_api_tasks.py`).

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

### Task ↔ torrent correlation is tag-based

`TorrentManager.add_magnet` passes `tags=task.id`, and `wait_for_hash` resolves the hash by querying `/api/v2/torrents/info?tag=<task_id>` — *not* by hash, because the hash doesn't exist yet. Torrents are added `stopped="true"` so file selection can happen before any bytes move. Don't replace the tag mechanism with a list-scan like old `app.py` did; it's ambiguous with multiple tasks.

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

## Not yet implemented (don't assume it works)

`stream_manager.stream_file` reads bytes straight off disk and **does not wait for pieces**. It raises 425 only when the file doesn't exist at all; for a sparse preallocated file, ranges that aren't downloaded yet are served as zeros. This is why `PLAN.md` §12 (piece-on-demand) is still open and why "real-world partial playback and seek behavior" is listed as *unproven* in §33.

That's the project's whole purpose. If you're asked to make playback work before the torrent completes, that's a substantive engineering task, not a bug fix.

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
