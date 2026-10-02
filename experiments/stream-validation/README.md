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
a Range probe, a **seek tester**, and a player:

```bash
cd experiments/stream-validation
export LOCAL_SAVE_PATH="$PWD/downloads"
export QBIT_PASSWORD='...'
uvicorn console:app --port 8000
```

Then open <http://127.0.0.1:8000/console>.

The console is meant to be usable on a phone, since the useful place to watch
a download is not next to the machine running it:

- **Paste a magnet, or copy one somewhere else and tap the field** — the page
  reads your clipboard on focus and will pick a magnet out of whatever text
  you copied, since magnets get shared inside page URLs. The `Paste magnet`
  button is the explicit path. If clipboard permission is refused or the
  browser has no async clipboard API, nothing breaks; you just type or paste
  normally.
- **`save` on any file** downloads it through the same Range layer the player
  uses, so you get real bytes rather than the sparse zeros a plain file link
  would hand back from a partially allocated file.
- Tables scroll sideways inside their card instead of widening the page, so
  the page never forces a horizontal scroll.

The **Range probe** sends a real `Range` header and reports the status,
headers and byte count — `206 Partial Content` is the thing to confirm.
The `<video>` element only proves a browser will play the head of the file;
far-ahead seeking on a partial download needs VLC/MX Player, which is the
actual gate (PLAN.md §23).

### The seek tester

Playback from a partial file is proven. **Seeking into undownloaded bytes is
not**, and that is the last unproven arrow in PLAN.md §36. The seek tester turns
"seeking feels broken" into a number: probe a byte offset (or a time like `90s`
or `12:30`) and it reports what the server actually did — HTTP status, byte
count, `X-Magneto-Truncated`, content-range, and the first 16 bytes — next to
what the engine said was on disk at the time.

Three of its four outcomes are correct behaviour, not failure:

| | |
|---|---|
| `206`, full range | on disk |
| `206`, clamped at a hole | the gate clamped rather than padding to `Content-Length` |
| `425` after waiting | nothing here is on disk; it will not serve sparse zeros |
| `DEFECT` | bytes served the engine cannot verify — report it |

Measured on a throttled Sintel: a probe inside the frontier returns `206`
immediately; a probe 2 MB past it returns `206` after an 8–11 s wait, because the
piece landed mid-request; a probe at 90–98% returns `425` after the full 30 s
`STREAM_PIECE_WAIT`.

That last row is the finding. **The gate waits but cannot ask** — it polls
`pieceStates`, and the qBittorrent Web API has no per-piece priority endpoint at
all (checked against the 5.0 docs and probed on a live 5.2.4: `setPiecePrio`,
`piecePrio` and `setPiecePriority` all 404, and `increasePrio`/`topPrio` move
*files*, not pieces). Closing the `SEEK → NEW PIECES` gap needs the
`LibtorrentEngine` swap.

Note there is no single "frontier". libtorrent selects rarest-first, so complete
pieces scatter: at 35% complete on Sintel, 333 of 987 pieces sat in 217 separate
runs and the **playable prefix** — the unbroken run from byte 0, which is all a
player starting at the beginning can reach — was at **1.01%**. `select_files`
therefore also enables `toggleSequentialDownload`, after which the prefix tracks
overall progress (9.73% at 12.56% overall). Same bytes on disk, an order of
magnitude more reachable. This is why the tester reports `prefix` and
"askable from here" as separate numbers.

There is also a manual workflow at `.github/workflows/validate.yml`
(`Actions → Stream validation → Run workflow`) that stands the whole thing up
on a GitHub-hosted runner and publishes it through a temporary Cloudflare
tunnel. It ships **no default magnet** — leave the `magnet` input blank and
the console starts empty, which is also the only way to exercise creating more
than one task. The quick tunnel has no authentication, so anyone with the URL
can add magnets until the job ends.

> **Why playback still won't start on some torrents.** An MP4 written by most
> tools puts the `moov` atom — the sample index every demuxer needs before it
> can play a frame — at the *end* of the file. Sintel.mp4 is
> `ftyp`(32) `free`(8) `mdat`(128641498) then `moov`(600214) last. The player
> asks for the tail, the piece gate answers `425` rather than fabricate bytes,
> and it fails with a demuxer error.
>
> `select_files` handles this by enabling qBittorrent's
> `toggleFirstLastPiecePrio`, which fetches the trailing index first. Verified
> at 250 KB/s: the whole `moov` landed 28s in at 34.5% progress, and playback
> then started at **8.6%** of the download — `1024x436`, real time, no error.
>
> It also enables `toggleSequentialDownload`, so the playable prefix advances in
> order instead of scattering — see the seek tester above for the measurement.
>
> Both are **toggles, not setters**. Posting either endpoint twice turns it back
> off, which silently undoes the optimisation and stalls the download with no
> visible reason, so `TorrentManager._ensure_toggle` reads the current value
> first and posts only when it is off. qBittorrent 5.x also renamed the fields:
> `first_last_prio_pieces` → `f_l_piece_prio`, `sequential_download` → `seq_dl`.
>
> This only helps when the video is the **last file in the torrent**, because
> "last piece" means the last piece of the whole torrent. Sintel works because
> `Sintel.mp4` spans pieces 0–986 of 987. A video that's a small file in the
> middle is unaffected and still needs a faststart/fMP4 asset.
>
> Check the box layout before blaming the stream gate:
>
> ```python
> # walk top-level boxes; if moov sits after mdat, no partial playback
> ```

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
