<!-- Acquisition-first roadmap: obtain usable originals before editing or posting work. -->
# Streamer footage → source library → editing → posting

Current priority: get authorized Twitch/Kick source files into the private site
reliably. Editing and posting resume only after source acquisition is demonstrated.

**Latest owner direction (2026-11-18):** test locally before a $25/month always-on
cloud deployment. The owner allowed Streamlink public-stream recording despite
the earlier official-API-only restriction. A one-off local Twitch pilot now proves
real capture, original playback/seeking/deduplication and the existing editing
pipeline on real Jason footage. See [measured evidence](local-recording-pilot.md).
Continuous capture/recovery, Kick capture and the Linux container remain untested;
the full unattended acquisition gate below is still incomplete.

## Phase 1: source acquisition

### Step 1 — source library and an acquisition-only pipeline (implemented)

- Reuse current creator configuration, official Twitch discovery, raw SQLite jobs,
  fingerprinted recording cache and launchd supervisor.
- Show discovered source links separately from locally stored, playable originals
  at `/clips/twitch_clips/sources`. Metadata is never counted as an acquired video.
- Accept authorized completed MP4/MOV/MKV/WebM uploads from either platform through
  the site. Check the source, retain creator attribution, avoid duplicate copies,
  and support private original-file playback and seeking. No local path typing
  or manual file movement is needed for this fallback.
- Configure `acquisition.only: true`: background cycles still discover, download
  where already authorized and scan completed inboxes, but stop before Whisper,
  highlight analysis, editing or scheduled publishing.
- Browser uploads default to 2048 MB; original recordings/final videos are not
  deleted. Existing disk protection and local-path imports remain available.

Verified: **503 tests pass**, including an isolated real FFmpeg test video uploaded
through the authenticated site, identical original-byte playback, range requests,
deduplication, private access, file-size/extension checks and phase isolation.
Live background-worker job `3c933317dde942f8bb21de9bdc635991` completed acquisition-only:
**67 listed discoveries, 0 acquired originals**, no analysis/renders/uploads.
Test media is explicitly synthetic; no real streamer download is claimed.

### Step 2 — select and connect an actual automatic media-delivery route

**Discovery is now implemented:** bounded, paginated official Twitch Get Clips
and Get Videos (archived VODs), saved as metadata-only records in the existing
source library. An existing-queue discovery job runs every six hours when
automation is started; CLI/browser checks are available too. Duplicate identities
update metadata, including views. Uploads can attach footage to the selected
discovery instead of displaying another source card. See
[setup, bounds and manual landscape imports](source-discovery.md).
This is not an automatic media-delivery route; the actual acquisition gate below
remains incomplete. Background cycles no longer call the clip download adapter.

| Platform/source | Discovery | Acquisition route | Current limit |
|---|---|---|---|
| Twitch clips | Paginated Get Clips metadata with date window | Manual official landscape export where available → upload to matching discovery | No media downloaded by discovery; broadcaster/editor API access is absent for Jason |
| Twitch full VODs | Bounded paginated Get Videos archive metadata | Authorized supplied recording/export or supported media feed | Public VOD metadata does not supply downloadable bytes |
| Kick | Existing official status adapter | Authorized completed recordings or a supported media feed | Kick app credentials are absent; no general VOD download connector identified in reviewed public API docs |
| Your/creator-authorized recording producer | Configured creator inbox | Write completed output directly into its inbox | Needs a real recording producer/source with permission and access |

The next step is to establish one working media source, first for Twitch and then
Kick. A viewer's Twitch sign-in, an embed or a public clip link is not automatically
an unattended download integration. The earlier browser-menu investigation remains
an optional one-file fallback, not the autonomous acquisition design.

For a permitted remote media feed, use the existing HTTP client and ingestion code:
bounded streaming downloads, disk reservation, temporary paths, creator/source ID,
hashing, validation and atomic import. Keep signed URLs/secrets out of logs. The
actual provider's credentials and permitted interface must be identified before
implementing its adapter; do not build speculative third-party playback scraping.

### Step 3 — make transfers efficient and recoverable

- Begin with short official clips or bounded recordings to prove the route before
  transferring many hours of video. Limit concurrent downloads and per-cycle volume.
- Use source IDs and saved metadata to skip known content before transferring it;
  retain existing SHA-256 deduplication after transfer.
- Preserve interrupted stages, retry transient failures with backoff, and avoid
  treating changing/live files as finished recordings. Add resumption only when
  the chosen delivery endpoint supports it.
- Show access failures per source, and keep discovered, downloading, stored and
  failed states distinct. Reuse the current worker/job history and low-disk guard.

### Step 4 — acceptance gate before editing

At least one real authorized Twitch source and one Kick source must be acquired,
playable with their original audio on the site, correctly attributed and not
duplicated on the next unattended cycle. Exercise interruption/restart on the
chosen transfer route. Record actual source IDs, byte sizes, duration and results.
This gate is **not complete**: no accessible creator media has been obtained yet.

## Phase 2: editing after source acquisition passes

Reuse the existing Whisper, scoring, timestamp/context, vertical layouts, captions,
audio normalization and quality checks. Evaluate them on the acquired real sources
and fix observed failures. Keep originals unchanged. Disable acquisition-only mode
only when this phase is intentionally started.

## Phase 3: posting after editing passes

Connect the chosen destinations and verify real upload/publication results. No
posting changes are part of the current acquisition milestone.

## Official interface references

- [Twitch Get Clips/download authorization](https://dev.twitch.tv/docs/api/reference/clips/)
- [Twitch VOD metadata API](https://dev.twitch.tv/docs/api/reference/videos/)
- [Kick public API index](https://docs.kick.com/llms.txt)
- [Kick own-channel VOD download controls](https://help.kick.com/en/articles/7832538-how-to-download-your-kick-vod)

No new package, paid API or hosting service is required for the implemented step.
