<!-- Engineering audit and evidence; the supervisor maintains only its marked live-status section. -->
# Automation status — November 18, 2026 launch

**Quality revision (2026-11-18):** replaced draft 8's 720p input with matched native
1080p60 archive footage (recording 2), preserving recording 1. Audio correlation
0.965 and picture comparison verified the same exchange. Higher-quality export
settings are now per channel: 60 fps, CRF17/medium for Twitch, with Lanczos scaling
and a matching caption clock. First HQ export measured ~12.1 Mbps video and passed
technical checks without warnings. Original caption timing was retained through
the measured source offset. **563 offline tests pass**. Zero paid AI calls/uploads.
Native webcam crop size still bounds perceived detail; no generated face detail.

**Presentation revision (2026-11-18):** draft 8 now keeps Jason's measured facecam
above gameplay, removes the on-video Twitch URL pill, and opens with
**ONE Roblox Question Set Jason OFF** for eight seconds. Configured cameras take
priority in every template; unknown positions keep the entire source visible.
Future dramatic hooks use the existing cached/metered model independently of local
scoring, with job budget reservations. Re-render/export technical checks passed
without warnings; opening/later frames inspected. No paid call for this revision
or publication. **556 tests pass**. Latest bundle:
`output/local_recording_probe/ready_to_post_facecam_v2`.

**Real local recording/clipping pilot (2026-11-18):** a bounded Streamlink capture
of Jason's public live stream succeeded (46 seconds, 720p with audio). Existing
importer saved recording 1 and reused that ID on repeat import. The existing free
pipeline produced draft 8: 32.24 seconds, 1080x1920 H.264/AAC, technical gate passed
without warnings, one ready-to-post export bundle. Private original playback was
byte-identical, seeking returned 206, and the draft page returned 200. Zero paid
AI calls, uploads or public posts. **545 offline tests pass**. This supersedes
earlier zero-creator-footage snapshots below; those remain historical audit.
This was a one-off diagnostic, not unattended recording. The supervisor remains
acquisition-only. Owner budget for future cloud is $25/month, local testing first.
See [pilot evidence and next steps](docs/local-recording-pilot.md).

**Metadata discovery update (2026-11-18):** public Twitch clip/VOD discovery now
reuses the existing source rows, queue/worker and supervisor schedule (six hours
by default). Discovery never downloads footage; background inbox cycles no longer
call the legacy downloader. Manual uploads can attach to the original discovery.
Kick discovery remains an unsupported stub. After reloading the existing services,
real job `8e67dcd8a30644c59d968047639aee8a` succeeded with 5,286 new and 535 updated
metadata items, zero errors, five creator page-limit warnings and the Kick notice.
The database then held **5,358 sources including 369 VODs; zero attached files and
zero imported creator recordings**. This verifies discovery, not media acquisition
or publication. See [discovery setup and evidence](docs/source-discovery.md).
The prior acquisition audit below and the runtime-maintained section remain
historical/measured evidence.
Full offline suite: **545 tests pass**, including 31 new discovery regressions.

**Latest direction: acquisition first, editing second, posting third.** The first
step of [the acquisition plan](docs/video-acquisition-plan.md) is implemented:
private source-video library, browser import and original playback, with automatic
analysis/editing/scheduled posting paused by `acquisition.only: true`.
**503 tests pass**. Real-media upload/playback/deduplication were verified using an
isolated original synthetic test video. The real acquisition-only cycle lists
67 discoveries and **0 locally acquired creator videos**. Source access is still
the next milestone; no automated remote VOD acquisition is being claimed.

**Session handoff saved:** the owner is stepping away. Resume instructions are at
the top of `CLAUDE.md`: next engineering step is bounded official Twitch VOD
discovery, followed by establishing actual permitted automatic media delivery.
Keep acquisition-only mode enabled; editing and posting remain deferred. Commit
the current working step locally; no remote push was requested.

**The Mac supervisor is installed and running. The system is not yet an autonomous
public clipping channel: authorized media access and clipping-channel account
connections remain unresolved, and TikTok inbox delivery still needs a human Post.**

## Audit before changes

Inventoried the existing source/configuration/docs/tests, traced the recording,
worker, scoring, editing, account and publishing paths, inspected SQLite without
printing credentials, checked installed tools, and ran all tests before adding code.
Baseline: **476 tests passed in 21.36 seconds**. The real HTTP app started:
`/login` returned 200; `/library` and `/clips/twitch_clips` returned 303 when logged out.

Already implemented and preserved:

- Official Twitch clip discovery/download adapter, broadcaster OAuth and token refresh.
- Official Twitch/Kick live-status polling; supplied completed-recording ingestion.
- Persistent SQLite jobs, isolated media workers, heartbeats, capacity/budget limits.
- Local Whisper word timings, cached transcripts, speech/audio/frame-change ranking,
  optional supplied engagement counts, sentence context and duplicate suppression.
- Optional cached/metered Claude speech assessment and metadata. **Paid assessment remains off.**
- 1080×1920 H.264/AAC rendering, captions, hook, credit, audio normalization,
  configured facecam/crops or whole-frame blurred fill, optional silence trimming.
- Technical export gates, exact timestamp cuts, review/re-cut, manual-upload bundles.
- Encrypted per-channel OAuth, YouTube uploads, TikTok inbox chunk uploads,
  schedules, daily upload reservations and conservative uncertain-upload handling.
- Existing Docker/Compose/cloud instructions. No paid host was provisioned.

The live Twitch check returned six live configured channels and one offline; Kick
correctly reported unknown because credentials are absent. Public clip discovery
subsequently saved **32 candidate metadata rows, including five JasonTheWeen clips**.
These rows are not downloaded footage or finished videos. No broadcaster download
logins exist. No creator recording is present. Only news has connected platform
accounts; none were reassigned to clips.

The clips automation database flag was already enabled, but no app/supervisor was
running when this audit began. No launchd agent existed. Those were runtime gaps,
not reasons to rebuild the pipeline.

## Added in this pass

- `src/clips/runtime.py`: independent supervision, file lock against duplicate
  supervisors, periodic stale-job recovery, existing queue/scheduler integration,
  bounded supervisor backoff, graceful signals and health reports.
- `src/clips/service.py`: installed separate launchd agents for runtime/dashboard;
  restart on exit and next login, 30-second throttle, private localhost dashboard.
  The Mac dashboard's clip/publishing loops are disabled so the runtime owns clip
  scheduling; news is not published by the new supervisor.
- Disk protection: pause new jobs and scheduled uploads below 5 GB free; idle-only
  cleanup of abandoned temporary fragments after 48 hours. Original recordings,
  cached source media, models, transcripts and final videos are retained.
- Rotating JSON event logs; machine health includes liveness, source/account
  blockers, accepted uploads and separately verified public URLs. This file's
  live section refreshes automatically without replacing the audit.
- `src/publishing/verification.py`: official YouTube processing/privacy and TikTok
  upload/inbox/public-post checks, persistent results, actual share URLs, bounded
  polling and existing token refresh. Verification never re-uploads a file.
- Optional silence-edit failure now falls back to original media and word timing.
- Clip auto-approval enabled per the owner's explicit autonomous-workflow request;
  it applies only to exports that pass and have no quality warnings. News review
  stays mandatory. Clip **publishing remains a dry run** until rights/accounts and
  the desired public destination are ready.
- Cloud Compose runtime and healthcheck added; deployment/build remains untested
  because Docker is not installed here. No hosting charges incurred.
- OAuth callback request URLs are excluded from dashboard access logging.

## Verification evidence

- Full suite after runtime/status/fallback additions: **492 passed in 20.46 seconds**;
  subsequent final-suite results are recorded in the handoff below.
- Actual launchd installation: both plist validations passed and both services ran.
- Actual crash recovery: killed the supervisor created in this session; launchd
  restarted it with a different PID and a fresh heartbeat. Evidence:
  `output/runtime/verification.json`. No reboot was performed; restart-at-login
  configuration is installed, not claimed as a measured full-reboot test.
- Existing same-day real-media evidence reused rather than rebuilding/retesting
  working components: real whisper.cpp transcribed 70 words from original synthetic
  media; automatic selection produced a 21.43-second 1080×1920 export scoring 79.7,
  with full decode and audio checks passing. Timestamp fallback also produced a
  checked 20-second export; mean audio −18 dB, peak −1.5 dB. Sample frame inspected.
  Reports: `output/launch_2026_11_18/smoke/{report,automatic_report}.json`.
- These are **synthetic diagnostics**, not Jason footage or launch-success clips.
  Exact caption correctness and subjective entertainment on real creator material
  remain unverified until accessible authorized footage exists.
- Real YouTube/TikTok clipping-channel OAuth/uploads are **not verified**. Unit
  status tests use simulated API responses. No private/public upload was attempted.

## Remaining limitations and human-only requirements

1. **First: accessible authorized footage.** Jason broadcaster login was canceled
   because the owner does not know him. Do not ask the owner to contact him or log
   into his account. The official viewer-created clip download/share route needs
   the owner's own Twitch session and eligible channel controls; it is not an
   unrestricted headless VOD-download API. Browser access was unavailable during
   the earlier check. Rights notes are owner reports, not independently verified
   licenses. Public campaign listings were found, but no creator endorsement or
   private campaign license was verified; no campaign was joined or purchased.
2. Connect the selected **clipping channel's** YouTube account. The owner previously
   deferred destination selection; retain that choice until they supply it.
3. Connect TikTok for an authorized inbox workflow if desired. Public unattended
   Direct Post is **not supported for this private copying utility under current
   guidelines**. Approval alone is not a promised workaround. Manual Studio/inbox
   posting is the supported fallback; see the setup guide's official sources.
4. Kick app credentials are optional for monitoring, not a VOD-download solution.
   Its own-channel VOD downloads use its Creator Dashboard. No arbitrary third-party
   VOD acquisition, DRM/access-control bypass or private playback endpoint was added.
5. Actual semantic vision, automatic face/speaker tracking, live chat collection
   and true remote livestream recording remain unimplemented. They are optional
   enhancements, not represented as tested capabilities. Fixed/full-frame layouts
   and existing local signal ranking provide the current free alternative.

Mac operation requires power, wakefulness, network access and the user's logged-in
session. Login agents do not run before login or while the computer is asleep/off.
Cloud configuration is prepared for a user-approved always-on host; no cloud
account or paid machine was created. `HUMAN_SETUP_GUIDE.md` gives one first action
and keeps later account/approval requirements in priority order.

## Launch counts and URLs

At the latest production database check: **0 finished creator clips, 0 accepted
YouTube uploads, 0 accepted TikTok uploads, 0 verified public URLs.** Candidate
metadata rows and synthetic test videos do not count. The live section below is
the supervisor's current cumulative measurement, not a promise of publication.

## Handoff

Final full suite: **495 passed in 21.68 seconds**. `git diff --check` and Python
header/docstring checks passed. Final installed services were restarted with the
finished code; current heartbeat and localhost login checks passed. The live
status section is being maintained by the actual launchd supervisor.

Repository files changed in this pass: `src/clips/runtime.py`, `src/clips/service.py`,
`src/publishing/verification.py`, `src/clips/maker.py`, `src/dashboard/app.py`,
`channels/twitch_clips.yaml`, `config/runtime.yaml`, `docker-compose.yml`,
`tests/test_clip_runtime.py`, `tests/test_upload_verification.py`,
`tests/test_optional_clip_edit.py`, `README.md`, `CLAUDE.md`,
`TODAY_LAUNCH_CHECKLIST.md`, `AUTOMATION_STATUS.md`, `HUMAN_SETUP_GUIDE.md`.

Installed local files: `~/Library/LaunchAgents/com.shortsengine.clips.plist` and
`~/Library/LaunchAgents/com.shortsengine.dashboard.plist`. Logs and health evidence
live under ignored `output/runtime/`. Secrets remain in ignored local `.env`.

Suggested commit after review: `feat: supervise clip automation with launchd and verified upload status`.

<!-- RUNTIME_STATUS_START -->
## Live supervisor status

Updated: 2026-11-19 14:52:14 CST · PID 74145
Free disk: 138.48 GB · Low-disk pause: False

- twitch_clips: queue enabled=True; recordings=2; finished clips=1.
  - TikTok inbox delivery requires manual posting; Direct Post is not enabled.
  - Scheduled publishing is a dry run.

Accepted file uploads (these are not public-post counts):
- YouTube: 0; TikTok: 0.

Verified public URLs:
- None.
<!-- RUNTIME_STATUS_END -->
