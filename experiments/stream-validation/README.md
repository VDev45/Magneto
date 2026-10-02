# Magneto — Stream Validation

This experiment validates the core technical path before Magneto grows into a Telegram bot:

`magnet → qBittorrent → partial file → HTTP Range → VLC/MX Player`

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

5. Start the validator:

```export MAGNET_URI='magnet:?xt=urn:btih:...'```

6. Start the validator:

```python experiments/stream-validation/app.py
```

7. Open the reported stream URL in VLC or MX Player.

## Environment

- `QBIT_URL` — default `http://127.0.0.1:8080`
- `QBIT_USERNAME` — default `admin`
- `QBIT_PASSWORD` — default `adminadmin`
- `QBIT_REMOTE_SAVE_PATH` — qBittorrent container path, default `/downloads`
- `LOCAL_SAVE_PATH` — host path mapped to that container path, default `./experiments/stream-validation/downloads`
- `MAGNET_URI` — required

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

