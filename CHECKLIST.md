# 🧲 Magneto — Development Checklist

> **Goal:** Send a magnet link to Telegram → resolve it through qBittorrent → expose the selected media as an HTTP Range stream → play it in VLC/MX Player while the torrent is still downloading.

## Legend
- [ ] Not started
- [x] Completed
- [~] In progress

---

# 0. Project Definition
- [x] Create GitHub repository
- [x] Choose project name: **Magneto**
- [ ] Write project README
- [ ] Define supported use cases
- [ ] Define MVP scope
- [ ] Define non-goals
- [ ] Document high-level architecture

# 1. Technical Validation — Critical
> **Do this before building the full application.**
- [ ] Validate qBittorrent Web API integration
- [ ] Add a magnet through the API
- [ ] Retrieve torrent metadata
- [ ] List files in a torrent
- [ ] Identify video files
- [ ] Verify libtorrent piece availability behavior
- [ ] Verify playback can begin before torrent completion
- [ ] Verify HTTP Range requests can be satisfied while downloading
- [ ] Verify VLC playback
- [ ] Verify VLC seeking
- [ ] Measure time-to-first-byte / first-frame
- [ ] Document the result and any limitations

**Milestone:** A partially downloaded torrent can be streamed and seeked successfully.

# 2. Project Foundation
- [ ] Choose backend language/runtime
- [ ] Create project structure
- [ ] Add dependency management
- [ ] Add configuration system
- [ ] Add `.env.example`
- [ ] Add structured logging
- [ ] Add error handling strategy
- [ ] Add Dockerfile
- [ ] Add Docker Compose
- [ ] Add development setup documentation
- [ ] Add GitHub Actions CI
- [ ] Add formatting/linting
- [ ] Add unit-test framework

# 3. Telegram Bot
- [ ] Configure Telegram Bot API
- [ ] Implement `/start`
- [ ] Implement `/help`
- [ ] Detect magnet links
- [ ] Validate magnet links
- [ ] Handle invalid input
- [ ] Track Telegram user/session
- [ ] Add friendly processing messages
- [ ] Add error/status messages

# 4. Torrent Manager
- [ ] Connect to qBittorrent API
- [ ] Add magnet
- [ ] Track torrent ID
- [ ] Wait for metadata
- [ ] Retrieve torrent information
- [ ] Retrieve torrent files
- [ ] Monitor torrent state
- [ ] Monitor peers/seeds
- [ ] Monitor download speed
- [ ] Monitor availability
- [ ] Remove torrent
- [ ] Handle stalled torrents
- [ ] Handle failed torrents
- [ ] Handle qBittorrent restart/recovery

# 5. Media/File Manager
- [ ] Detect video files
- [ ] Detect audio files
- [ ] Detect subtitle files
- [ ] Ignore non-media/junk files
- [ ] Determine file size
- [ ] Determine MIME type
- [ ] Automatically select the primary video
- [ ] Support manual file selection
- [ ] Persist selected file information

# 6. Streaming Engine
- [ ] Create HTTP streaming endpoint
- [ ] Implement `GET /stream/{token}`
- [ ] Implement HTTP Range parsing
- [ ] Return `206 Partial Content`
- [ ] Return `Accept-Ranges`
- [ ] Return `Content-Range`
- [ ] Return correct `Content-Length`
- [ ] Return correct `Content-Type`
- [ ] Support normal sequential playback
- [ ] Support seeking
- [ ] Handle concurrent range requests
- [ ] Handle client disconnects
- [ ] Handle unavailable torrent pieces
- [ ] Handle upstream/torrent errors
- [ ] Prevent unsafe filesystem paths

# 7. Stream Sessions & URLs
- [ ] Generate cryptographically random stream tokens
- [ ] Map token → torrent/file
- [ ] Add stream ownership
- [ ] Add stream creation timestamp
- [ ] Add stream expiration
- [ ] Add stream revocation
- [ ] Prevent token guessing
- [ ] Clean up expired streams

# 8. Telegram UX
- [ ] Show torrent name
- [ ] Show metadata loading state
- [ ] Show file list
- [ ] Add file-selection buttons
- [ ] Add Stream button
- [ ] Add Status button
- [ ] Add Remove button
- [ ] Show stream URL
- [ ] Show torrent progress
- [ ] Show peers/seeds
- [ ] Show download speed
- [ ] Show stream state

# 9. Torrent Lifecycle
- [ ] Define torrent states
- [ ] `NEW`
- [ ] `METADATA`
- [ ] `READY`
- [ ] `STREAMING`
- [ ] `IDLE`
- [ ] `FAILED`
- [ ] `CLEANUP`
- [ ] Detect inactive torrents
- [ ] Automatic cleanup
- [ ] Configurable idle timeout
- [ ] Maximum active torrent limit
- [ ] Maximum storage limit
- [ ] Per-user limits
- [ ] Graceful shutdown
- [ ] Recovery after application restart

# 10. Storage & Database
- [ ] Choose initial database
- [ ] Create users table
- [ ] Create torrents table
- [ ] Create torrent files table
- [ ] Create streams table
- [ ] Create sessions table
- [ ] Add database migrations
- [ ] Persist torrent state
- [ ] Persist stream state
- [ ] Add cleanup queries
- [ ] Document PostgreSQL migration path if needed

# 11. Security
- [ ] Keep qBittorrent API private
- [ ] Protect admin functionality
- [ ] Validate all Telegram input
- [ ] Validate magnet URI
- [ ] Secure stream tokens
- [ ] Add stream expiration
- [ ] Add rate limiting
- [ ] Add connection limits
- [ ] Prevent path traversal
- [ ] Prevent arbitrary file access
- [ ] Protect internal service endpoints
- [ ] Configure HTTPS
- [ ] Review Docker/container permissions

# 12. Testing
## Unit Tests
- [ ] Magnet parser tests
- [ ] File-selection tests
- [ ] Token tests
- [ ] Range parser tests
- [ ] Torrent-state tests
- [ ] Cleanup tests

## Integration Tests
- [ ] Telegram → bot
- [ ] Bot → qBittorrent
- [ ] qBittorrent → torrent metadata
- [ ] Torrent → file selection
- [ ] Stream endpoint → torrent file
- [ ] Range request → correct bytes

## Real Playback Tests
- [ ] VLC starts playback
- [ ] VLC pauses/resumes
- [ ] VLC seeks forward
- [ ] VLC seeks backward
- [ ] VLC reconnects
- [ ] MX Player playback
- [ ] Browser playback where supported
- [ ] Multiple simultaneous streams

## Failure Tests
- [ ] No peers
- [ ] Torrent stalls
- [ ] qBittorrent unavailable
- [ ] qBittorrent restarts
- [ ] Magnet metadata unavailable
- [ ] Stream client disconnects
- [ ] Disk becomes full
- [ ] Application restarts during streaming
- [ ] Network interruption

# 13. Performance
- [ ] Measure metadata resolution time
- [ ] Measure time to first byte
- [ ] Measure time to first frame
- [ ] Measure seek latency
- [ ] Measure streaming throughput
- [ ] Measure CPU usage
- [ ] Measure RAM usage
- [ ] Measure disk usage
- [ ] Test large files
- [ ] Test multiple concurrent streams
- [ ] Identify bottlenecks
- [ ] Optimize only after measurement

# 14. Production Deployment
- [ ] Production Docker Compose
- [ ] qBittorrent service
- [ ] Magneto service
- [ ] Database service
- [ ] Reverse proxy
- [ ] HTTPS
- [ ] Persistent torrent storage
- [ ] Persistent database storage
- [ ] Health checks
- [ ] Restart policies
- [ ] Logging
- [ ] Backup strategy
- [ ] Resource limits
- [ ] Deployment documentation

# 15. Admin & Operations
- [ ] Admin user configuration
- [ ] `/status`
- [ ] `/torrents`
- [ ] `/remove`
- [ ] Active stream count
- [ ] Active torrent count
- [ ] Disk usage
- [ ] CPU/RAM metrics
- [ ] Network metrics
- [ ] Error monitoring
- [ ] Cleanup monitoring

# 16. V1 Features
- [ ] Reliable magnet → stream workflow
- [ ] Automatic video selection
- [ ] Manual file selection
- [ ] HTTP Range streaming
- [ ] VLC seeking
- [ ] MX Player playback
- [ ] Automatic cleanup
- [ ] Secure stream URLs
- [ ] Multi-user support
- [ ] Production deployment
- [ ] Complete documentation

# 17. Future / Experimental
- [ ] HLS output
- [ ] DASH support
- [ ] Subtitle handling
- [ ] Automatic subtitle selection
- [ ] Thumbnail generation
- [ ] Media metadata
- [ ] Quality selection
- [ ] Transcoding
- [ ] Web dashboard
- [ ] Stream history
- [ ] Remote control
- [ ] Redis caching
- [ ] PostgreSQL scaling
- [ ] Multi-node torrent workers

---

# 🏆 Definition of Done

Magneto reaches its core goal when this works:

```text
Telegram
   │
   │ magnet
   ▼
Magneto
   │
   ▼
qBittorrent / libtorrent
   │
   │ torrent metadata
   ▼
Video file
   │
   ▼
HTTP Range Stream
   │
   ▼
VLC / MX Player
   │
   ├── PLAY
   ├── PAUSE
   └── SEEK
```

### Final MVP test
- [ ] Submit a magnet through Telegram
- [ ] Magneto resolves metadata
- [ ] Magneto identifies the video
- [ ] Magneto generates a stream URL
- [ ] Playback begins before torrent completion
- [ ] Seeking works before torrent completion
- [ ] Stream survives normal pause/resume
- [ ] Expired streams are cleaned up
- [ ] Torrent resources are eventually cleaned up

> **Core principle:** Do not move to large feature work until the true piece-on-demand streaming experiment passes.