# 🧲 Magneto — Full Development Plan

> Master implementation plan for Magneto. CHECKLIST.md tracks individual work items; PLAN.md defines architecture, sequencing, validation gates, and Definition of Done.

---

# 1. Product Goal

Magneto is a Telegram-driven torrent streaming service.

Core workflow:

    Telegram
       │ magnet URI
       ▼
    Bot / API
       ▼
    TaskManager
       ▼
    TorrentManager
       ▼
    qBittorrent / libtorrent
       ▼
    partially downloaded media
       ▼
    FileManager
       ▼
    StreamManager
       ▼
    HTTP Range
       ▼
    VLC / MX Player / Browser

The defining requirement is true playback while the torrent is still downloading.

A user must be able to send a magnet, resolve metadata, select media, start playback before completion, pause/resume, seek forward/backward, and cleanly stop the task.

---

# 2. Core Engineering Principles

## 2.1 Separation of concerns

### TaskManager
- Own task identity and lifecycle
- Own queueing
- Own ownership
- Own state transitions
- Coordinate cancellation and cleanup
- Never contain Telegram-specific logic

### TorrentManager
- Own torrent-engine interaction
- Add magnets
- Resolve metadata
- List files
- Select files
- Start / pause / resume / stop
- Report status and piece information
- Remove torrents
- Handle engine recovery
- Hide qBittorrent/libtorrent implementation details

### FileManager
- Build file trees
- Detect media
- Determine MIME types
- Resolve safe local paths
- Track selected files
- Report file availability

### StreamManager
- Own HTTP streaming
- Parse Range requests
- Manage stream sessions
- Handle unavailable data
- Handle disconnects
- Coordinate requested byte ranges with torrent availability

### Telegram layer
- Commands
- Magnet detection
- Buttons
- File selection
- Status messages
- Stream URL delivery
- User-facing errors

---

# 3. Target Architecture

    Telegram
       │
       ▼
    TaskManager
       │
       ├──────────────┐
       ▼              ▼
    TorrentManager  FileManager
       │              │
       ▼              │
    Torrent Engine    │
       │              │
       └──────┬───────┘
              ▼
         StreamManager
              │
              ▼
          HTTP Server
              │
              ▼
        VLC / MX Player

The torrent engine must be replaceable.

---

# 4. Torrent Engine Strategy

## Phase 1 engine: qBittorrent

Use qBittorrent for the initial validation because it provides a mature engine, Web API, file priorities, sequential downloading, first/last piece priority, and piece-state information.

However, qBittorrent is not an architectural commitment.

## Critical limitation

The Web API does not provide the fine-grained arbitrary piece-priority control that ideal seek-driven streaming may require.

Therefore define an engine boundary:

    TorrentEngine
       ├── QBittorrentEngine
       └── LibtorrentEngine

Implement libtorrent only if real measurements prove qBittorrent cannot provide acceptable seek behavior.

---

# 5. Phase 0 — Project Definition

Goals:
- Finish project README
- Define supported use cases
- Define MVP
- Define non-goals
- Document architecture

Non-goals for MVP:
- HLS
- DASH
- Transcoding
- Web dashboard
- Distributed workers
- Media recommendation systems
- Complex media-library features

Primary goal:

    Magnet → media selection → partial download → HTTP stream → seek

---

# 6. Phase 1 — Streaming Feasibility

THIS IS THE MOST IMPORTANT PHASE.

Do not build the complete Telegram application until this phase passes.

## Validate qBittorrent
- Magnet submission
- Metadata retrieval
- File discovery
- File selection
- Partial file creation
- Piece-state inspection

## Validate HTTP Range
Test:
- Normal GET
- 206 Partial Content
- Accept-Ranges
- Content-Range
- Content-Length
- Content-Type
- Open-ended ranges
- Suffix ranges
- Invalid ranges
- Concurrent range requests

## Validate VLC
Required:
- Start before 100%
- Pause
- Resume
- Forward seek
- Backward seek
- Repeated seek
- Reconnect

## Critical experiment

    Player seeks from 2 minutes to 40 minutes
              ↓
    HTTP requests the corresponding byte range
              ↓
    Torrent engine obtains corresponding pieces
              ↓
    Stream becomes readable
              ↓
    Playback resumes

Measure:
- Torrent progress
- Piece states
- Requested byte ranges
- Download speed
- Time to first byte
- Time to first frame
- Seek latency

Decision gate:
- If qBittorrent is sufficient, continue.
- If qBittorrent fails seek-driven streaming, implement and test libtorrent.

---

# 7. Phase 2 — Project Foundation

Recommended structure:

    magneto/
    ├── app/
    │   ├── core/
    │   │   ├── models/
    │   │   ├── task_manager/
    │   │   ├── torrent_manager/
    │   │   ├── file_manager/
    │   │   └── stream_manager/
    │   ├── torrent/
    │   │   ├── interface/
    │   │   ├── qbittorrent/
    │   │   └── libtorrent/
    │   ├── telegram/
    │   ├── api/
    │   ├── storage/
    │   ├── security/
    │   └── config/
    ├── tests/
    ├── experiments/
    ├── docs/
    ├── scripts/
    ├── CHECKLIST.md
    └── PLAN.md

Foundation requirements:
- Configuration
- Environment variables
- .env.example
- Structured logging
- Exception hierarchy
- Dependency management
- Formatting
- Linting
- Unit tests
- CI
- Docker

---

# 8. Phase 3 — Domain Models

## Task

    id
    user_id
    magnet
    torrent_hash
    torrent_name
    state
    queue_state
    progress
    speed
    selected_files
    created_at
    updated_at
    last_activity_at
    error
    stream_sessions

## TorrentFile

    id
    engine_file_index
    path
    name
    size
    mime_type
    progress
    priority
    selected
    is_video

## Task states

    CREATED → METADATA → QUEUED → DOWNLOADING → STREAMING → COMPLETED

Failure paths:

    any state → FAILED
    any state → CANCELLED
    any state → CLEANING

Use explicit state transitions rather than arbitrary assignments.

---

# 9. Phase 4 — Torrent Manager

Required operations:
- add
- wait for metadata
- list files
- select files
- start
- pause
- resume
- stop
- status
- piece states
- remove

Status should expose:
- State
- Progress
- Download speed
- Upload speed
- Peers
- Seeds
- Availability
- ETA where meaningful

Recovery must handle:
- qBittorrent restart
- Connection timeout
- Torrent disappearance
- Duplicate magnets
- Stale tasks
- Engine unavailable

---

# 10. Phase 5 — File Manager

Detect:
- Video
- Audio
- Subtitles
- Images
- Archives
- Junk metadata

Initial video formats:
- MP4
- MKV
- WebM
- AVI
- MOV
- M4V
- TS

Support nested file trees and manual selection.

Security rule: never allow a torrent path to escape the configured storage root.

---

# 11. Phase 6 — Streaming Engine

Production endpoint:

    GET /stream/{secure-token}

Never expose filesystem paths or torrent hashes in the public URL.

Required HTTP behavior:
- 200 for normal requests where appropriate
- 206 for Range requests
- Accept-Ranges
- Content-Range
- Content-Length
- Correct Content-Type

Streaming flow:

    HTTP Range
       ↓
    StreamManager
       ↓
    Requested file
       ↓
    Is requested data available?
       ├── yes → read
       └── no  → wait / coordinate with torrent engine
       ↓
    HTTP response

The streamer must handle bounded waits, disconnects, cancellation, and upstream errors.

---

# 12. Phase 7 — Piece-on-Demand Streaming

This is the feature that differentiates Magneto from a normal file server.

Target flow:

    HTTP Range
       ↓
    Range → piece mapper
       ↓
    Torrent engine
       ↓
    Piece priority
       ↓
    Download
       ↓
    File data
       ↓
    HTTP stream

qBittorrent path:
- File priority
- Sequential mode
- First/last piece priority
- Piece availability

libtorrent path, if required:
- Map file offsets to piece indexes
- Prioritize requested pieces
- Prioritize a playback buffer
- Deprioritize distant pieces
- React to seeks

Do not implement complex piece scheduling until measurements justify it.

---

# 13. Phase 8 — Stream Sessions

Every stream becomes an explicit object:

    StreamSession
    ├── id
    ├── token
    ├── task_id
    ├── file_id
    ├── user_id
    ├── created_at
    ├── last_activity_at
    ├── expires_at
    └── state

States:
- CREATED
- ACTIVE
- IDLE
- EXPIRED
- REVOKED
- CLOSED

---

# 14. Phase 9 — Secure Stream URLs

Generate cryptographically random, unguessable tokens.

Token maps to:

    token → stream → task → file → owner

Tokens must be:
- Unguessable
- Expiring
- Revocable
- Scoped

The URL must not reveal Telegram user IDs, filesystem paths, or internal torrent hashes.

---

# 15. Phase 10 — Telegram Bot

Only start this after the streaming experiment passes.

Initial commands:
- /start
- /help
- /status
- /cancel

Also detect magnet URIs sent as ordinary messages.

Workflow:

    User sends magnet
         ↓
    Resolve metadata
         ↓
    Show torrent/files
         ↓
    User selects media
         ↓
    Start torrent
         ↓
    Generate stream URL
         ↓
    Return stream URL

Status should expose progress, speed, peers, state, and stream state.

---

# 16. Phase 11 — Telegram File Selection

Example UI:

    Movie Name

    ☑ movie.mkv     4.2 GB
    ☐ movie.srt      84 KB
    ☐ trailer.mp4   120 MB

    [ Stream Selected ]
    [ Select All ]
    [ Cancel ]

For large torrents use pagination, folders, filtering, and compact file metadata.

---

# 17. Phase 12 — Queue & Resource Management

Protect the service from resource exhaustion.

Global limits:
- Maximum active torrents
- Maximum active streams
- Maximum storage
- Maximum bandwidth

Per-user limits:
- Maximum active torrents
- Maximum active streams
- Maximum storage
- Rate limits

Queue example:

    A → RUNNING
    B → RUNNING
    C → QUEUED
    D → QUEUED

When A stops:

    C → RUNNING

---

# 18. Phase 13 — Database

Use in-memory state during early experiments.

For production, use PostgreSQL.

Core tables:

### users
- id
- telegram_user_id
- created_at
- updated_at

### torrents
- id
- task_id
- user_id
- torrent_hash
- name
- magnet
- state
- progress
- created_at
- updated_at
- last_activity_at

### torrent_files
- id
- torrent_id
- engine_file_index
- path
- name
- size
- mime_type
- is_video
- priority
- selected

### streams
- id
- token_hash
- torrent_id
- file_id
- user_id
- created_at
- expires_at
- last_activity_at
- state

### sessions
- id
- user_id
- created_at
- last_activity_at

---

# 19. Phase 14 — Persistence & Recovery

After application restart:

    Database
       ↓
    Active tasks
       ↓
    Torrent engine
       ↓
    Reconcile by task ID / torrent hash
       ↓
    Restore valid state
       ↓
    Requeue interrupted tasks

Expired streams must not survive restart.

---

# 20. Phase 15 — Cleanup

Cleanup triggers:
- User cancellation
- Stream expiration
- Torrent completion + idle timeout
- Torrent failure
- Storage pressure
- Admin action

Cleanup sequence:

    Stop stream
       ↓
    Stop torrent
       ↓
    Remove torrent
       ↓
    Delete files according to policy
       ↓
    Delete stream records
       ↓
    Mark task cleaned

Never delete data while an active stream can still read it.

---

# 21. Phase 16 — Security

Input security:
- Validate magnet URI
- Validate info hash
- Validate Telegram input
- Validate callback data
- Enforce task ownership

Filesystem security:
- Prevent traversal
- Reject absolute paths
- Resolve symlinks safely
- Restrict storage root
- Prevent arbitrary file reads

Network security:
- Keep qBittorrent API private
- Separate internal and public APIs
- Use HTTPS for production streams

Abuse prevention:
- Torrent limits
- Stream limits
- Rate limiting
- Storage quotas
- Bandwidth quotas
- Repeated-failure protection

---

# 22. Phase 17 — Testing

## Unit tests
- Magnet parser
- Task state machine
- Queue
- File selection
- Path validation
- MIME detection
- Range parser
- Stream tokens
- Expiration
- Cleanup

## Integration tests
- TaskManager → TorrentManager
- Magnet → metadata
- Metadata → file selection
- Start/stop/remove
- Stream endpoint → file
- Range request → correct bytes

## Failure tests
- No peers
- Torrent stalls
- qBittorrent unavailable
- qBittorrent restart
- Metadata timeout
- Client disconnect
- Disk full
- Application restart
- Network interruption

---

# 23. Phase 18 — Real Playback Matrix

| Test | Required |
|---|---|
| VLC starts before 100% | YES |
| VLC pause/resume | YES |
| VLC forward seek | YES |
| VLC backward seek | YES |
| VLC repeated seek | YES |
| VLC reconnect | YES |
| MX Player playback | YES |
| Browser playback | OPTIONAL |
| Multiple streams | YES |
| Torrent remains incomplete during playback | YES |

The critical test is:

    Torrent < 100%
        +
    Player seeks far ahead
        +
    Requested pieces become available
        +
    Playback resumes

---

# 24. Phase 19 — Performance

Measure before optimizing.

Metrics:
- Metadata latency
- Time to first byte
- Time to first frame
- Seek latency
- Buffer starvation
- Stream throughput
- CPU
- RAM
- Disk I/O
- Disk usage
- Network usage

Store benchmark results under docs/benchmarks/.

---

# 25. Phase 20 — Observability

Structured events should include:

    task_id
    user_id
    torrent_hash
    stream_id
    event
    duration
    result
    error

Useful metrics:
- Active tasks
- Queued tasks
- Active torrents
- Active streams
- Download speed
- Stream throughput
- Failures
- Cleanup count
- Storage usage

---

# 26. Phase 21 — Production Deployment

Production shape:

    Reverse Proxy
         │
      Magneto
       /   \
      /     \
 PostgreSQL  Torrent Engine
                 │
              Storage

Services may include:
- magneto
- qBittorrent
- postgres
- reverse proxy

Required:
- Persistent storage
- Health checks
- Restart policies
- Resource limits
- Logs
- Backups
- HTTPS

---

# 27. Phase 22 — CI/CD

GitHub Actions should run:
1. Formatting
2. Linting
3. Unit tests
4. Integration tests where practical
5. Docker build
6. Security checks

Do not release broken builds.

---

# 28. Phase 23 — Admin & Operations

Admin capabilities:
- Active torrents
- Active streams
- Queue
- Storage usage
- Bandwidth
- Force cleanup
- Failed task inspection
- Engine health
- Database health

Keep admin APIs separate from public stream endpoints.

---

# 29. Phase 24 — V1 Scope

V1 must provide:
- Telegram magnet submission
- Metadata resolution
- File selection
- Torrent lifecycle
- HTTP Range streaming
- Partial playback
- VLC seeking
- MX Player playback
- Secure stream URLs
- Expiration
- Ownership checks
- Queueing
- Cleanup
- Recovery
- Docker deployment
- Logging
- Health checks
- CI

---

# 30. Post-V1 Features

Only after V1 is stable:
- HLS
- DASH
- Subtitle discovery
- Automatic subtitle selection
- Thumbnails
- Media metadata
- Quality selection
- Transcoding
- Web dashboard
- Stream history
- Remote controls
- Redis
- PostgreSQL scaling
- Distributed torrent workers
- Multiple torrent engines

---

# 31. Research Strategy

Study mature projects such as WZML and MLTB for architecture ideas, not implementation copying.

Useful ideas:
- Task IDs
- Central torrent manager
- Engine abstraction
- Queueing
- Status listeners
- File-tree selection
- Ownership checks
- Cleanup
- Retry/recovery

Do not copy assumptions that belong to mirror/leech bots. Magneto's output is a live HTTP stream, not a completed Telegram upload.

---

# 32. Development Order

Follow this sequence:

    1. Streaming experiment
           ↓
    2. Engine abstraction
           ↓
    3. Task lifecycle
           ↓
    4. File manager
           ↓
    5. Stream manager
           ↓
    6. Piece-on-demand validation
           ↓
    7. Persistence
           ↓
    8. Telegram bot
           ↓
    9. Secure stream URLs
           ↓
   10. Cleanup and recovery
           ↓
   11. Testing
           ↓
   12. Performance
           ↓
   13. Production

Do not reverse this order without a strong technical reason.

---

# 33. Current Repository State

The repository currently contains an architecture-validation experiment under experiments/stream-validation.

Important components include:
- models.py
- torrent_manager.py
- task_manager.py
- file_manager.py
- stream_manager.py
- main.py
- console.py / console.js — the browser console and seek tester (served verbatim)
- smoke_test.py — the scripted validation the CI workflow runs
- test_task_manager.py
- docker-compose.yml
- requirements.txt
- README.md

Already implemented in the experiment:
- qBittorrent adapter
- task model
- task lifecycle
- queue concept
- task-tagged torrents
- file selection
- safe path mapping
- HTTP Range streaming
- cancellation
- torrent removal
- piece-state inspection
- container-index and sequential piece prioritisation
- on-disk byte availability reporting (the seek tester's backend)
- lifecycle tests

### Seeking: measured 2026-10-02, and it is an engine limitation

**Answer: no. Seeking into undownloaded bytes does not work, and cannot be made
to work through the qBittorrent Web API.** Measured with the console's seek
tester against a real throttled Sintel download:

| Probe | Result |
|---|---|
| byte 0 (inside the frontier) | `206`, full 64 KB, 12 ms — clean |
| prefix + 2 MB (engine actively fetching there) | `206` after an **8–11 s** wait; the piece landed mid-request |
| 50%, 90%, 98% (ahead of the download) | **`425` after the full 30 s `STREAM_PIECE_WAIT`** |

The 425s are the gate working. It waits for the pieces, they never arrive, and
it refuses rather than serving the sparse zeros qBittorrent preallocates. But the
wait is wasted: **the gate can poll `pieceStates` and it cannot ask.** There is
no per-piece priority endpoint in the Web API at all. Checked against the 5.0
docs and probed on a live 5.2.4 — `setPiecePrio`, `piecePrio` and
`setPiecePriority` all return `Endpoint does not exist`, while `increasePrio` and
`topPrio` move *files*, not pieces. The only piece-related endpoints that exist
are `pieceStates` (read), `pieceHashes` (read) and `toggleFirstLastPiecePrio`.

So the `SEEK → NEW PIECES` arrow in §36 cannot be closed by waiting harder. It
needs the `LibtorrentEngine` swap, which is the condition §36's "change the
torrent-engine strategy" clause already anticipates. A seek *does* succeed when
the download is already heading for that region (row 2 above) — which is what
sequential mode buys, below.

### There is no single "frontier": sequential download is the real lever

libtorrent selects rarest-first by default, so complete pieces scatter and the
number a player starting at byte 0 can actually reach — the **prefix**, the
unbroken run from byte 0 — falls far behind overall progress. Measured on
Sintel at 35.02% complete: 333 of 987 pieces in **217 separate runs**, with the
prefix at **1.01%**. A third of the file on disk, and 1% of it playable.

`select_files` now also enables `toggleSequentialDownload`, and the prefix
tracks progress directly:

| Overall | 12.56% | (was 35.02%) |
|---|---|---|
| **Prefix, sequential on** | **9.73%** (3 points behind) | |
| **Prefix, rarest-first** | | **1.01%** (34 points behind) |

Same bytes on disk, an order of magnitude more reachable. This is why the seek
tester reports two separate numbers — `prefix_available` and, for a given
offset, `available_bytes` to the next hole. Reporting one frontier was the
first version of the tester, and it called a byte at 99% "inside the frontier"
while the frontier was at 6%.

### Partial playback: proven 2026-10-02

Measured end-to-end through the HTTP API against a real qBittorrent with the
download throttled to 250 KB/s:

| | |
|---|---|
| Playback start | **8.6%** of the torrent complete |
| Resolution | 1024x436, mean luma 173, 100% non-black (real decoded frames) |
| Rate | 5.94s of video in 6s wall time — real time |
| Events | `loadedmetadata` → `canplay` → `playing`, no error |
| Buffer | 8s ahead |

The blocker was container layout, not the streaming layer. Sintel.mp4 is
`ftyp`(32) `free`(8) `mdat`(128641498) then `moov`(600214) at the very end, and
no demuxer can play a frame without `moov`. Chrome requested the tail, the piece
gate answered 425 rather than fabricate bytes, and it reported
`PIPELINE_ERROR_READ: FFmpegDemuxer: data source error`.

The fix is `toggleFirstLastPiecePrio`, enabled from `select_files`, which pulls
the trailing index down first: the full `moov` was present 28 seconds in, at
34.5% progress. `select_files` enables sequential download alongside it — see
the section above for the measured effect.

**Known bound.** The index prioritisation only helps when the video is the last
file in the torrent, because "last piece" means the last piece of the whole
torrent. Sintel works because `Sintel.mp4` spans pieces 0–986 of 987. A video
that is a small file in the middle is unaffected and still needs a
faststart/fMP4 asset or per-piece prioritisation from a lower-level engine.
§18's note that qBittorrent "does not expose arbitrary piece-priority control"
still stands — this is first/last priority, not arbitrary priority, and the
probing above confirms there is no per-piece endpoint to fall back on.

### New: the console seek tester

`GET /tasks/{id}/files/{file_index}/frontier?at=<byte>` reports what is on disk
for a file, in bytes, plus the current `seq_dl` flag. The byte arithmetic lives
on the server, not in the console, because it is the same arithmetic
`stream_manager._await_available` uses and the two must not drift — a test drives
both from one fixture and asserts they agree.

The console's seek tester probes an arbitrary offset (or a time like `90s`) and
reports the HTTP status, byte count, `X-Magneto-Truncated`, content-range, and
the first 16 bytes. It distinguishes four outcomes, and three of them are
correct behaviour rather than failure:

- **206, full range** — on disk.
- **206, clamped** — straddles a hole; `Content-Length` matches what was sent.
- **425** — nothing here is on disk. The design working as intended.
- **DEFECT** — served bytes the engine cannot verify. Never seen in practice.

It re-reads availability *after* the response, because the gate polls piece
states during its wait and a request that sat 8 seconds will have had its piece
arrive while the tester held a stale snapshot. Judging against the pre-request
snapshot reports that healthy wait as a DEFECT — a false alarm on the one tool
whose job is to raise alarms. Two earlier versions of this check did exactly
that, and both are now regression-tested by driving the real function against
stubbed `fetch`.

**The sparse-hole detector ignores offset 0.** Every MP4 opens
`00 00 00 20 'ftyp'`, where the leading zeros are a box length, not a hole; the
zero-run scan in `stream_manager` makes the same exclusion (`and index > 0`).
An earlier version of the check did not, and reported Sintel's own file header
as corruption.

---

# 34. Immediate Next Steps

1. Run qBittorrent locally.
2. Run the validation API.
3. Use a real magnet containing a playable video.
4. Verify metadata resolution.
5. Verify file selection.
6. Verify a partial file exists before completion.
7. Issue manual HTTP Range requests.
8. Open the stream in VLC.
9. Start playback before completion.
10. Seek forward.
11. Seek backward.
12. Record piece states during seeking.
13. Record first-frame and seek latency.
14. Decide whether qBittorrent is sufficient.
15. If not, implement the libtorrent engine.

---

# 35. Definition of Done

Magneto reaches its core MVP only when this exact workflow succeeds:

    Telegram
       │ magnet
       ▼
    Magneto
       ▼
    TaskManager
       ▼
    Torrent Engine
       ▼
    Metadata
       ▼
    File Selection
       ▼
    Partial Torrent
       ▼
    StreamManager
       ▼
    HTTP Range
       ▼
    VLC / MX Player
       ├── PLAY
       ├── PAUSE
       ├── RESUME
       ├── SEEK FORWARD
       └── SEEK BACKWARD

All of this must work while torrent progress is below 100%.

Additionally:
- Stream URLs are unguessable.
- Unauthorized users cannot access streams.
- Tasks can be cancelled.
- Torrents can be cleaned up.
- Expired streams are removed.
- Application restart can recover tasks.
- Torrent-engine failures are handled.
- Critical paths have automated tests.
- Deployment is reproducible.

---

# 36. The One Rule

> **Do not build around an assumption that streaming works. Prove it first.**

The fundamental loop is:

    PARTIAL TORRENT
          ↓
    REQUESTED BYTE RANGE
          ↓
    CORRESPONDING PIECES
          ↓
    AVAILABLE FILE DATA
          ↓
    PLAYER
          ↓
    SEEK
          ↓
    NEW PIECES
          ↓
    CONTINUOUS PLAYBACK

If that loop works reliably, Magneto has a foundation.

If it does not, stop feature development and change the torrent-engine strategy before adding more features.

**Status 2026-10-02: the loop breaks at `SEEK → NEW PIECES`, and that is now
measured rather than assumed.** Every other arrow is proven — partial playback
starts at 8.6% and runs in real time. The seek arrow fails because the gate can
only *poll* `pieceStates`; there is no Web API call to ask for a specific piece
(§33). This is precisely the "change the torrent-engine strategy" condition
above, so it is the trigger for `LibtorrentEngine`, not a reason to keep
building features.