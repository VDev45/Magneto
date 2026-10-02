# Magneto — Stream Validation

This experiment validates the core technical path before Magneto grows into a Telegram bot:

`Task → TorrentManager → qBittorrent → FileManager → StreamManager → HTTP Range → VLC/MX Player`

## What this proves

- qBittorrent can accept a magnet and expose torrent metadata.
- A video file can be identified before the torrent completes.
- The local partial file can be served with HTTP byte ranges.
- Playback can begin before 100% completion.
- Seeking behavior can be observed while the torrent is still downloading.
- qBittorrent piece state can be compared with HTTP requests.

## Important limitation being tested

qBittorrent exposes file priorities, sequential download, first/last-piece priority, and piece-state information through its Web API. It does **not** expose arbitrary piece-priority control through the Web API, so true seek-driven downloading may require a lower-level libtorrent integration if this experiment shows that qBittorrent cannot react quickly to a far-ahead seek.

## Run

1. Start qBittorrent:

```bash
docker compose -f experiments/stream-validation/docker-compose.yml up -d
```

2. Install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r experiments/stream-validation/requirements.txt
```

3. Get the qBittorrent WebUI password from the container log (qBittorrent may generate an initial password):

```bash
docker logs magneto-qbittorrent 2>&1 | grep -i password
```

4. Set a test magnet and credentials:

```bash
export MAGNET_URI='magnet:?xt=urn:btih:...'
export QBIT_PASSWORD='your-password'
```

5. Start the validator.

   It must run from **inside this directory** — the modules use flat imports, so
   `main:app` only resolves when this directory is on `sys.path`. The
   `LOCAL_SAVE_PATH` default is repo-root-relative, which would otherwise
   resolve to `experiments/stream-validation/experiments/stream-validation/downloads`,
   so set it explicitly:

```bash
cd experiments/stream-validation
export LOCAL_SAVE_PATH="$PWD/downloads"
uvicorn main:app --reload
```

   Interactive API docs are at http://127.0.0.1:8000/docs.

6. Create a task. `POST /tasks` reads the magnet from the request body and falls
   back to `MAGNET_URI` when no body is sent:

```bash
curl -X POST localhost:8000/tasks \
  -H 'Content-Type: application/json' \
  -d '{"magnet":"magnet:?xt=urn:btih:..."}'
```

7. Use `POST /tasks/{task_id}/select` to select a video file, then open `/stream/{task_id}/{file_index}` in VLC/MX Player.

## Browser test console

For interactive testing there is a console that adds a UI on top of the same
API. It serves everything `main.py` does, plus a task list, live piece states,
a Range probe, and a player:

```bash
cd experiments/stream-validation
export LOCAL_SAVE_PATH="$PWD/downloads"
export QBIT_PASSWORD='...'
uvicorn console:app --port 8000
```

Then open <http://127.0.0.1:8000/console>.

The **Range probe** sends a real `Range` header and reports the status,
headers and byte count — `206 Partial Content` is the thing to confirm.
The `<video>` element only proves a browser will play the head of the file;
far-ahead seeking on a partial download needs VLC/MX Player, which is the
actual gate (PLAN.md §23).

There is also a manual CI workflow at `.github/workflows/validate.yml`
(`Actions → Stream validation → Run workflow`) that starts everything on a
self-hosted runner and holds it open for testing.

## Environment

- `QBIT_URL` — default `http://127.0.0.1:8080`
- `QBIT_USERNAME` — default `admin`
- `QBIT_PASSWORD` — default `adminadmin`
- `QBIT_REMOTE_SAVE_PATH` — qBittorrent container path, default `/downloads`
- `LOCAL_SAVE_PATH` — host path mapped to that container path, default `./experiments/stream-validation/downloads`
- `MAGNET_URI` — fallback magnet; a magnet in the `POST /tasks` body takes precedence

## Validation checklist

- [ ] Magnet accepted
- [ ] Metadata becomes available
- [ ] Video file discovered
- [ ] File exists before completion
- [ ] HTTP `206 Partial Content` works
- [ ] VLC/MX Player starts before torrent reaches 100%
- [ ] Forward seek works while incomplete
- [ ] Backward seek works while incomplete
- [ ] Piece states show requested regions becoming available
- [ ] First-frame latency recorded
- [ ] qBittorrent-only approach accepted or rejected based on evidence


## Lifecycle endpoints

- `POST /tasks` — create a task and resolve metadata.
- `GET /tasks/{task_id}` — refresh task/file state.
- `GET /tasks/{task_id}/state` — inspect piece states.
- `POST /tasks/{task_id}/select` — select files and start or queue the task.
- `POST /tasks/{task_id}/cancel` — stop a task.
- `DELETE /tasks/{task_id}` — remove the torrent from qBittorrent.
