<!-- Review handoff for Claude Code: behavior, validation and complete changed-file list. -->
# Clips automation handoff

Built on the existing ShortsEngine app. The news workflow stays available.

- Added completed-recording ingestion, official Twitch/Kick live-status monitoring,
  local multimodal scoring, optional Claude speech assessment/metadata, deduplication,
  persistent retries, caption-safe exports and optional true-silence cuts.
- Added private streamer/automation pages, source revocation, recording imports,
  retry controls, editable metadata and per-video publishing destinations.
- Reused existing encrypted OAuth accounts and publishing delivery reservations.
  Added daily clip-upload limits and large-file TikTok chunking.
- Review and scheduled publishing dry runs default on. Stop pauses acquisition
  and scheduled clip uploads. Manual Post now remains an explicit action.
- Current Google API guidance supersedes older quota/private-upload assumptions.
  TikTok Direct Post remains unavailable for this private copying utility;
  authorized inbox/manual export is the supported alternative.

## Completion pass

- Isolated corrupt-file and unreadable-folder failures so other inbox recordings
  continue; safe source-specific errors appear on the existing job page.
- Renamed duplicates no longer consume the per-cycle import limit. Unchanged-file
  checks include creator, platform and channel, preserving attribution checks
  when an inbox is reassigned.
- The demo rejects empty macOS speech and generates a video with a finite length.
- Added five regression cases covering these failures.

Files changed in this completion pass: `src/clips/recordings.py`,
`src/clips/automation.py`, `src/clips/demo.py`, `tests/test_clip_automation.py`,
`README.md`, `CLAUDE.md`, `docs/stream-clips.md`, `docs/clips-handoff.md`.

## Validation

- `python -m pytest -q --tb=short`: **460 passed**.
- Real original local video → whisper.cpp (70 words) → ranked moment (79.7/100)
  → captioned 1080×1920 H.264/AAC MP4 → passing technical quality gate → draft.
- Latest demo report: `output/clips_finish_verification_2026_11_17/report.json`.
  Export duration was 21.43 seconds; a sample frame was also inspected.
- Actual separate-process worker: `python -m src.clips --channel twitch_clips cycle --dry-run`
  passed with an isolated temporary database.
- Sandboxed macOS speech produced an empty AIFF; the demo passed with speech access
  outside the sandbox. The new guard prevents encoding empty speech.
- `git diff --check` passed. Changed Python files have headers and documented functions/classes.
- Live Twitch/Kick API calls and account OAuth/uploads were not exercised; contracts use mocks.
- Docker is unavailable on this Mac, so the updated CPU whisper.cpp container build is untested.
- The existing optional faster-whisper install lacks PyAV/ONNX Runtime; this pipeline uses
  the installed whisper.cpp executable and does not import that optional package.
- No paid API calls, public uploads, git commits or deployments were made.

## Operating instructions

Use [the complete guide](stream-clips.md) for Mac/server setup, API credentials,
configuration, demos, failure recovery and the boundaries of the MVP. Restart the
dashboard after pulling the changes, then open `/clips/twitch_clips`.

Automatic subject tracking, semantic gameplay vision and live chat collection remain
future work. Visual activity is a frame-change signal; scores are estimates, not
guarantees. Kick acquisition uses creator-supplied completed recordings.

## Files changed

- [.dockerignore](../.dockerignore)
- [.env.example](../.env.example)
- [.github/workflows/tests.yml](../.github/workflows/tests.yml)
- [.gitignore](../.gitignore)
- [CLAUDE.md](../CLAUDE.md)
- [Dockerfile](../Dockerfile)
- [README.md](../README.md)
- [channels/twitch_clips.yaml](../channels/twitch_clips.yaml)
- [docker-compose.yml](../docker-compose.yml)
- [docs/clips-handoff.md](clips-handoff.md)
- [docs/control-center.md](control-center.md)
- [docs/stream-clips.md](stream-clips.md)
- [src/clips/__main__.py](../src/clips/__main__.py)
- [src/clips/automation.py](../src/clips/automation.py)
- [src/clips/demo.py](../src/clips/demo.py)
- [src/clips/editing.py](../src/clips/editing.py)
- [src/clips/maker.py](../src/clips/maker.py)
- [src/clips/metadata.py](../src/clips/metadata.py)
- [src/clips/monitor.py](../src/clips/monitor.py)
- [src/clips/quality.py](../src/clips/quality.py)
- [src/clips/recordings.py](../src/clips/recordings.py)
- [src/clips/schema.py](../src/clips/schema.py)
- [src/clips/settings.py](../src/clips/settings.py)
- [src/clips/signals.py](../src/clips/signals.py)
- [src/clips/store.py](../src/clips/store.py)
- [src/clips/transcribe.py](../src/clips/transcribe.py)
- [src/dashboard/app.py](../src/dashboard/app.py)
- [src/dashboard/clip_routes.py](../src/dashboard/clip_routes.py)
- [src/dashboard/jobs.py](../src/dashboard/jobs.py)
- [src/dashboard/templates/base.html](../src/dashboard/templates/base.html)
- [src/dashboard/templates/channel.html](../src/dashboard/templates/channel.html)
- [src/dashboard/templates/clips.html](../src/dashboard/templates/clips.html)
- [src/dashboard/templates/clips_index.html](../src/dashboard/templates/clips_index.html)
- [src/dashboard/templates/job.html](../src/dashboard/templates/job.html)
- [src/dashboard/templates/video_detail.html](../src/dashboard/templates/video_detail.html)
- [src/dashboard/worker.py](../src/dashboard/worker.py)
- [src/publishing/scheduler.py](../src/publishing/scheduler.py)
- [src/publishing/tiktok.py](../src/publishing/tiktok.py)
- [src/publishing/youtube.py](../src/publishing/youtube.py)
- [src/sources/kick.py](../src/sources/kick.py)
- [src/sources/twitch.py](../src/sources/twitch.py)
- [src/storage/database.py](../src/storage/database.py)
- [src/video/clip_render.py](../src/video/clip_render.py)
- [tests/test_clip_automation.py](../tests/test_clip_automation.py)
- [tests/test_configured_streamers.py](../tests/test_configured_streamers.py)
- [tests/test_youtube_publishing.py](../tests/test_youtube_publishing.py)

Suggested commit: `feat: automate authorized stream recording clipping and review`.
