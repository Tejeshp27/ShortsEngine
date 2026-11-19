<!-- Operating guide for the local Twitch/Kick clipping pipeline, its API limits and reproducible tests. -->
# Twitch and Kick clipping automation

This extends the existing ShortsEngine app. It keeps the news channel, login,
encrypted publishing accounts and review queue. The clips workflow is:

```text
Official Twitch clips ──┐
Completed OBS/creator  ─┴─► fingerprint ► Whisper ► speech/audio/frame signals
recordings (both sites)                        │
                   ranked, diverse moments ◄───┘
                       │
             optional Claude assessment/metadata
                       │
            FFmpeg vertical edit + word captions
                       │
            full decode + format/duration check
                       │
          review ► persistent posting schedule
                       │
          YouTube upload / TikTok inbox or manual MP4 export
```

## Run on your Mac

Use the existing environment if it is installed. For a fresh installation:

```bash
brew install python@3.11 ffmpeg whisper-cpp
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python -m src.dashboard.set_password
python -m src.dashboard.app
```

Open `http://localhost:8000/clips/twitch_clips`. Set a password before exposing the
app to any network. The server binds to localhost by default. Existing `.env` files
must be kept; do not overwrite yours with the example.

No new Python package is required for clipping. It uses installed FFmpeg, Pillow,
NumPy, HTTPX, Pydantic, FastAPI, SQLite and PyYAML. whisper.cpp is used instead of
Python Whisper packages because it works with this Mac's Python 3.15 and Apple
Silicon without PyAV/ONNX Runtime wheels. CPU-only Linux also works. The optional
`faster-whisper` install in the existing environment has missing `av`/`onnxruntime`
dependencies; clipping does not import it. Prefer Python 3.11 for fresh installs.

The first transcription downloads the free model to `models/whisper/` (small.en is
roughly 466 MB). Select `transcription.whisper_model: base.en` for a smaller model.
Use `small`/`base` and change `language` for other languages. Transcripts are cached
by recording fingerprint, model and language. Paid APIs are off by default.

## Demo without API credentials

```bash
source .venv/bin/activate
python -m src.clips.demo
```

On macOS this generates original test-pattern video with the system `say` voice,
runs **real whisper.cpp**, scores moments, renders captions, verifies the export,
saves drafts in an isolated demo database and exercises the publishing dry-run
path. It never uploads. See `output/clips_demo/report.json` for actual MP4 paths.
Generated media lives under `output/` and is not committed. The attribution is a
synthetic test label, not a real streamer's channel.

For deterministic testing without a speech model, or on a host without `say`:

```bash
python -m src.clips.demo --fixture --output output/clips_demo_fixture
```

This version uses a tone and an explicitly labelled caption-timing fixture. It
does **not** demonstrate speech recognition. Neither demo uses the real account
database, starts automation or calls paid APIs. Run the normal dashboard workflow
below to see imported videos in your own review queue.

## Add creators and recordings

Use **Stream clips → Add an approved streamer**. Supply platform, username,
written-permission note/date, and optionally a completed-recording directory.
Settings are stored in `channels/twitch_clips.yaml`; the dashboard preserves
unrelated YAML text and comments. You can edit that file directly too:

```yaml
# Sources with permission to edit and publish their recordings.
streamers:
  # One authorized creator.
  - username: example_creator        # channel login, without the site URL
    platform: kick                  # twitch or kick
    permission_note: Written agreement received  # reference to your agreement
    permission_date: '2026-11-17'     # date permission was actually granted
    enabled: true                   # false blocks further processing/uploads
    recording_directory: /Users/you/Movies/creator-completed  # completed files only
```

Place only finished MP4/MOV/MKV/WebM files in the inbox. Use a separate OBS working
folder and move finalized recordings into it. Recently modified files, symlinks,
temporary extensions and files changing during import are skipped/refused. An
unchanged source path/size/mtime is not rehashed every poll. SHA-256 also catches
identical files with different names; the same footage cannot be assigned to a
different creator silently. Keep enough disk space for the original and cached copy.
Unreadable folders and unusable files are skipped independently and reported on
the cycle's job page. Other recordings continue, and duplicates do not consume
the per-cycle import limit. Reassigning an inbox to another creator reports
conflicts with footage already attributed to the original creator.

Alternatively use the dashboard import form or the terminal:

```bash
python -m src.clips --channel twitch_clips record /path/session.mp4 \
  --streamer example_creator --platform kick --i-own-this
python -m src.clips --channel twitch_clips cycle --dry-run
python -m src.clips --channel twitch_clips cycle
```

`record` and `cycle` use the same persistent job worker as the dashboard. The CLI
waits and prints the job result. `--dry-run` skips ingestion, external requests,
model downloads, rendering and publishing. The older `find`, `make`, `run`, `upload`
commands remain available; `make`/`run` use the older per-clip Claude picker and
can incur the configured existing API costs. Use `cycle` for the new free default.

For already-exported short Kick clips, `upload FILE --streamer NAME --i-own-this`
still works. Full recordings should use `record`, which produces multiple moments.

Optional engagement data can accompany an imported recording as
`session.engagement.json`. Provide aggregated real counts with timestamps relative
to the recording, for example `[{"time": 10, "count": 2}, {"time": 20, "count": 40}]`.
It is read once at import. Do not include chat message content or private identifiers.
There is no historical chat scraping or live EventSub/chat collector in this MVP.

## What the scores mean

The local scorer proposes context windows around speech cues, loudness peaks,
frame changes and supplied engagement spikes, plus regular coverage of the whole
recording. It scores the windows rather than accepting arbitrary time slices.

| Dimension | Local evidence |
|---|---|
| Entertainment | Configured excitement words and volume change |
| Hook | Speech cues in the opening six seconds |
| Audience | Relative engagement counts, only when supplied |
| Context/clarity | Speech coverage and sentence endings |
| Audio | Audibility and probable clipping |
| Visual | Differences between small grayscale frames sampled once per second |

`analysis.weights` controls the weighted average. Missing audience data is excluded
and the remaining weights are normalized. Scores and component evidence are saved
in SQLite and shown on the clips page. Scores estimate usefulness; they are **not
calibrated virality probabilities**. Loud game sound can be mistaken for excitement;
frame changes do not identify a win, emotion or specific game event.

Candidates include setup and reaction padding, snap away from words and prefer
sentence endings. They obey configurable duration/count limits and suppress
substantial overlap and very similar speech. Recent near-identical transcripts
from the same creator are also suppressed across recordings. Re-encoded silent
footage is not reliably deduplicated perceptually.

Set `analysis.use_llm: true` to add cached Claude speech assessment and metadata.
This uses the existing Anthropic integration, configured model/prices, output
token limit, daily spending cap and maximum calls per run. Only short selected
transcripts are sent. It updates entertainment, hook and clarity estimates and
generates a grounded title/description. The local alternative is free and derives
titles/descriptions from actual speech. Invalid model output falls back to local
metadata. Unknown paid-call outcomes retain a spending estimate; inspect before
retrying. No new paid provider was added.

For contextual headlines without changing local scores, use
`hook_title.use_llm: true` and leave `analysis.use_llm: false`. The configured
clips channel uses this mode with `metadata.style` requesting dramatic, specific
hooks grounded in the selected speech. Hook-only requests omit paid scoring
fields. These calls use the existing `models.clip_pick`, token/call limits,
caching and dashboard job budget reservation. The preview's manually reviewed
headline required no paid call. `hook_title.seconds: 8` keeps the opening hook
readable; it appears below the camera panel so it does not obscure the face.

## Editing, captions and review

- Every template (`gaming`, `reaction`, `conversation`) retains a configured
  facecam in a top panel above the focused gameplay/content crop. Templates cannot
  discard the camera configuration.
- Unknown facecam positions preserve the entire source picture over a blurred
  fill; a centered crop must not cut away a possible embedded facecam. Configure
  the creator's measured `facecam` box to enlarge it. There is no automatic
  facecam detection or tracking. Jason's current pilot layout has a measured box.
- `credit.enabled: false` hides the on-video URL pill. Creator attribution remains
  in the post caption, export text and review notes. Other channel configurations
  retain the optional overlay by default for compatibility.

Facecam rectangles and focus positions are explicit per-creator settings. Automatic
face tracking, active-speaker detection, semantic vision models and live remote
stream recording are not implemented. OBS can record a creator-authorized local
feed; the completed-file inbox handles acquisition after recording finishes.

Outputs are 1080×1920 H.264/AAC MP4 with normalized audio, highlighted
word captions, hook text and attribution. Caption settings support local font
paths, size, colors, outline and vertical position. Choose positions that avoid
the creator's face/gameplay; the app does not detect those automatically. Text
too wide for the safe area stops the render rather than silently overflowing.

Export quality is per channel under `encoding`: `frame_rate` (24–60), `crf`
(14–28, lower retains more detail) and the H.264 `preset`. Defaults remain
30 fps/CRF20/fast for older channels. The configured Twitch channel now uses
60 fps/CRF17/medium to retain motion and reduce compression loss from native
1080p60 sources. Decoder, encoder and caption clock use the same frame rate.
Panel/foreground resizing uses Lanczos. Higher frame rates and slower presets
require more CPU and storage; benchmark before cloud deployment.

### Speech and game audio

The Twitch clips channel enables this local audio pass on every new render and
re-cut. Existing drafts need a re-cut to adopt changed audio settings; draft 8
has already been updated. Configure the channel YAML:

```yaml
audio:
  speech_enhancement: true  # emphasize dialogue, reduce rumble and control loud peaks
  dialogue_boost: 2.0       # centered-dialogue emphasis factor, from 0 to 3
  noise_reduction: 0.8      # RNNoise blend, from 0 to 1; 0 disables denoising
```

FFmpeg enhances centered dialogue, adds gentle speech EQ/compression and levels
the model input. Its `arnndn` filter then runs the bundled 303 KB RNNoise model,
retaining 20% of the original sound with the configured blend. It works on mono
mixes too. Final measured gain aims for -14 LUFS, with oversampled -1.5 dB peak
limiting. It can finish slightly below the loudness target when limiting peaks.
It does not dynamically lift quiet gaps, which would undo the noise reduction.
Both filter delays are padded/trimmed so the cut, lips and captions stay in sync.

A broadcast usually already mixes microphone, game and other voices together.
This is speech enhancement, not separate mic/game stems: game dialogue or another
person can also remain prominent. Stronger suppression can damage voices; preview
before increasing the blend. Original recordings stay untouched. Missing sections
default to the older normalization-only path. An enhancement failure logs a warning
and uses normalized original sound rather than dropping the soundtrack.

No new Python dependency, API key or paid service. The model and original license
are in `assets/audio/rnnoise/`; no runtime download is performed. Implementation
references: [FFmpeg dialogue enhancement](https://ffmpeg.org/ffmpeg-filters.html#dialoguenhance),
[FFmpeg RNNoise filter](https://ffmpeg.org/ffmpeg-filters.html#arnndn) and
[Xiph RNNoise](https://github.com/xiph/rnnoise).

### Source detail and optional silence edits

Capture the highest available original before enlarging facecam crops. The first
720p pilot's camera was only 285×191 pixels; encoding a large portrait cannot
recreate detail missing from that crop. Prefer native 1080p60 when available and
report unavailable quality explicitly in the future recorder. This does not
imply the continuous recorder is already installed.

Optional `editing.remove_long_silence` removes the middle of true audio-silent gaps
longer than `minimum_silence_seconds`, retaining padding and remapping captions.
It is off by default so game reactions are preserved. Spoken filler is kept;
removing it automatically can change meaning. Original timestamps stay in notes.

The quality gate checks H.264, AAC, resolution, duration and full-file decoding.
It warns on long black sections and very quiet sound. It does not verify every
word or decide whether content is appropriate to publish. Open each draft, preview
on a phone, edit publishing text/destinations, re-cut if needed, then approve.
Metadata edits and re-cuts return the video to review.

## Continuous operation and publishing

**Start automation** on the clips page, or:

```bash
python -m src.clips --channel twitch_clips watch
```

While the dashboard is open, its background checker enqueues due cycles. There
is one heavy worker by default, with heartbeats, a time limit and persistent
recording/render retry counts and exponential backoff. A failed source does not
stop other creators. Stop pauses future cycles and scheduled clip uploads; active workers finish safely.
Restarting recovers saved recordings, picked intervals and captions. Interrupted
jobs retain their history and the next cycle resumes saved clip stages, subject
to retry limits. Completed draft/link writes are atomic to avoid orphan duplicates.

The clips settings form controls interval, threshold, maximum clips, daily upload
limit, template, posting times, review and publishing dry runs. Timezone and
destination accounts remain per channel. The Start/Stop choice is persisted in
SQLite and overrides the YAML's initial `automation.enabled` value.

Connect the **clips channel's own** YouTube/TikTok accounts under Channels.
After validating drafts, start automation and set `automation.publish_dry_run: false` to enable
scheduled uploads of approved clips. Set `require_approval: false` only if you
explicitly want technically checked clips automatically approved. That option
applies only to clips with passing quality checks and no quality warnings; it does
not change mandatory review for the news channel.
Use `youtube.privacy: private` initially and explicitly choose public when ready.
Set posting times to an empty list to disable scheduled uploads. **Post now** is
an explicit publishing action and has its own dry-run checkbox.

Daily reservations count distinct video upload attempts in the channel timezone;
TikTok and YouTube share one reservation for the same clip. Failed/uncertain
attempts conservatively consume that day's reservation. Successful and uncertain
deliveries are never automatically resent. Safe initialization failures can retry
on the next scheduled check. YouTube IDs link to their upload URLs in video details.
TikTok inbox IDs are not public video URLs.

Disable a source with **Disable and unqueue**. It removes unsent drafts from the
approved queue. Permissions are checked again before rendering and upload claims,
including after removing a creator from YAML. A platform request already in flight
cannot be recalled. Delete unused local videos with the existing delete control;
publishing history is retained. Changing source permissions never deletes evidence
of a prior upload.

## Official API requirements (checked November 17, 2026)

**Twitch:** create a developer app and set `TWITCH_CLIENT_ID`/
`TWITCH_CLIENT_SECRET`. Public clip/live metadata uses an app token. Run
`python -m src.clips --channel twitch_clips connect NAME` for the creator's
`channel:manage:clips` authorization. Download links require broadcaster/editor
authorization; written repurposing permission alone does not grant API access.
VOD metadata is not unrestricted VOD download access. This integration fetches
existing clips; owner-provided recordings cover complete broadcasts.
[Twitch clips API](https://dev.twitch.tv/docs/api/reference/clips/),
[live-status API](https://dev.twitch.tv/docs/api/reference/streams/).

**Kick:** optional `KICK_CLIENT_ID`/`KICK_CLIENT_SECRET` enable app-token channel
status polling. No OAuth user login is needed for this public app-token query.
The published API index has no clip/VOD download endpoint, so we use supplied
recordings and never private playback endpoints or stream keys.
[Kick channels](https://docs.kick.com/apis/channels),
[API index](https://docs.kick.com/llms.txt).

**YouTube:** enable YouTube Data API v3, create a Web OAuth client and set
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `YOUTUBE_REDIRECT_URI` (default local
callback: `http://localhost:8000/oauth/youtube/callback`). Connect with Google from
the channel page. Refresh credentials are encrypted. The implementation reads the
returned privacy status rather than assuming public visibility. Current docs list
100 default upload calls/day in a separate bucket; inspect your own Cloud quotas.
The current reference says unverified API projects are not categorically limited
to private uploads; this supersedes the older assumption in this repo's history.
[Upload reference](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert),
[quota guide](https://developers.google.com/youtube/data/guides/quota_and_compliance_audits).

**TikTok:** approved Login Kit/Content Posting access and the `video.upload` scope
are needed for inbox delivery. Configure `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`
and `TIKTOK_REDIRECT_URI`, then connect the account. Uploads support sequential
chunks and refresh-token rotation. Draft captions are pasted in the TikTok app.
Direct Post is deliberately unavailable here: current guidelines restrict private
account-management utilities and cross-platform copying, and require creator
controls/consent. Inbox/manual export is the supported alternative; it still
requires the app/account to be authorized. Attribution and captions do not by
themselves prove compliance or eligibility for monetization.
[Posting guidelines](https://developers.tiktok.com/docs/en/content-sharing-guidelines),
[media transfer](https://developers.tiktok.com/docs/en/content-posting-api-media-transfer-guide).

No DRM/authentication bypass, scraping, browser-upload automation or yt-dlp source
fallback is used. App approval and content rights remain separate requirements.

## Tests and troubleshooting

Verified locally on November 17, 2026: **460 tests passed**, the separate worker
completed a CLI dry run, and the real Whisper demo produced a technically checked
captioned clip. Latest report: `output/clips_finish_verification_2026_11_17/report.json`.
Live account OAuth/posting and the Docker build were not tested.
[Review handoff and complete changed-file list](clips-handoff.md).

```bash
python -m pytest tests/test_clip_automation.py tests/test_clips.py -q
python -m pytest -q
python -m src.publishing.scheduler --dry-run
```

- **Source unknown:** inspect the displayed credential/API error. Supplied local
  recordings still work. Offline is shown only after a successful API response.
- **No candidates:** check transcript quality, lower the minimum score, adjust
  excitement terms/weights, or enable optional speech assessment. Scores are local
  heuristics and will miss some good moments.
- **Whisper missing/model error:** run `whisper-cli --help`; confirm disk space,
  model name and network access for the first model download.
- **Demo speech is empty:** macOS speech may report success inside a sandbox
  without writing audio samples. The demo now stops before encoding and bounds
  its generated video length. Run with macOS speech access, or use `--fixture`
  for the explicitly synthetic timing demonstration.
- **No render:** inspect the clip's error and job page; failed FFmpeg logs stay
  next to its temporary output. Fix missing fonts/codec tools before retrying.
- **Retries exhausted:** fix the cause and use the retry controls on the clips
  page. Inspect any uncertain paid calls/uploads before resetting work.
- **Nothing posts:** approval, rendered file, enabled creator/channel, current
  OAuth, destination choices, posting time, dry-run state and daily cap must all
  permit it. TikTok inbox delivery still requires posting in its app.
- **Unknown upload outcome:** inspect YouTube/TikTok first. Do not clear succeeded
  deliveries. The existing scheduler guide explains manual reconciliation.

For Linux, install FFmpeg and build/install `whisper-cli` following
[whisper.cpp](https://github.com/ggml-org/whisper.cpp). The Dockerfile builds that
binary for CPU use; volumes keep models, cached recordings, output and SQLite
persistent. The Docker/hosting path needs a real container validation on the target
server. Nothing is deployed by this change. Allow several GB RAM and sufficient
disk; video processing is much heavier than an idle web dashboard.
