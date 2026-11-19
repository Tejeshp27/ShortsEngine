# CLAUDE.md — ShortsEngine Project Instructions

## Session instructions

Read this file at the start of every session before reviewing or changing code.
Update this file whenever significant behavior or architecture changes.

### Latest: rolling live recorder built (2026-11-19)

The recorder from the next-steps below is implemented and tested (see the first
Current phase entry and `docs/live-recording.md`). It was switched on 2026-11-19. Next:
watch the first real hour-long chunks for processing time vs. recording time,
disk peak, clip quality; then Linux/Docker and the $25/month server choice.

### Latest owner direction and local pilot (2026-11-18)

The owner selected a **$25/month future always-on budget**, with local testing
before paying for hosting. They explicitly set aside the earlier official-API-only
restriction for the proposed Streamlink recording approach. Use that narrow
exception for configured, owner-approved public streams; retain all credential,
cost and publishing safeguards. Do not repeat requests for Jason's account.

A real bounded Streamlink 8.6.3 capture succeeded locally without cookies,
browser automation or manual downloading/uploading: 46 seconds, 1280x720 with
audio, 19,035,529-byte MP4. Existing import returned recording 1 twice for identical
bytes. Existing local Whisper/scoring/rendering created draft 8: 32.24 seconds,
1080x1920 H.264/AAC, score 79, full decode/technical gate passed with no warnings.
Original playback was byte-identical through the running private site; seeking
returned HTTP 206; draft page returned 200. Export bundle includes one clip and
upload text. Zero paid AI calls or uploads. Details: `docs/local-recording-pilot.md`;
ignored report: `output/local_recording_probe/report.json`.

This was a one-off bounded diagnostic using existing pipeline functions, not a
continuous recorder installation. Next: build a bounded recorder adapter around
this measured route, reusing supervisor/queue/importer, then test recovery and
Linux/Docker before deployment. Docker is absent locally. Keep production
`acquisition.only: true` and publishing dry-run enabled until that validation.
No cloud resources were provisioned. These newer directions/evidence supersede
historical zero-footage snapshots and source-access instructions below.

### Resume here — owner stepping away (2026-11-18)

The owner requested saving the handoff and committing all current work. The next
session should resume **source acquisition**, then editing, then posting. Read
`docs/video-acquisition-plan.md` and the latest `AUTOMATION_STATUS.md` first. This
handoff takes priority over historical "next human steps" farther down this file.

1. Check `git status`, the installed launchd services and
   `output/runtime/health.json`. Preserve existing work and do not reinstall or
   rebuild the working supervisor, source library, importer or editing pipeline.
   Last verified: **514 tests pass** (2026-11-18 review), 67 discovered sources, **0 stored creator
   originals**, zero real creator clips/publications. Discovery counts can change;
   query current state rather than treating this snapshot as live status.
2. Keep `acquisition.only: true` in `channels/twitch_clips.yaml`. The private site
   at `http://localhost:8000/clips/twitch_clips/sources` supports raw-source upload,
   playback, seeking and duplicate prevention. Browser upload is a fallback, not
   the requested unattended acquisition solution. Do not restart browser connection
   troubleshooting unless it is needed for a concrete supported media route.
3. Public clip/VOD discovery is now implemented; see `docs/source-discovery.md`
   and the latest Current phase entry. Use `python -m src.clips --channel
   twitch_clips discover` or the source page's Check button. Metadata is never
   downloaded footage. The next acquisition step remains an actual permitted
   media-delivery source; do not build another queue/dashboard or reinstate the
   downloader in background cycles.
4. Establish a real permitted automatic media-delivery route before claiming
   acquisition works. Existing Twitch clip downloads need broadcaster/editor
   authorization; the owner does not know Jason and cannot sign in as him. Do not
   repeat that request. Twitch app credentials work, but a viewer login/public URL
   does not establish download access. Full-VOD discovery alone also supplies no
   video bytes. Kick credentials are absent; a general official VOD-download
   connector has not been identified. Inspect current official interfaces and any
   already-configured authorized recording/feed sources. Implement a provider
   adapter only when its permitted interface and access are known; no speculative
   private playback scraping or access-control bypasses. If all remaining routes
   need owner action, document the precise missing access and ask one question.
5. Reuse existing fingerprinted ingestion for a confirmed route. Bound transfers,
   check disk space, use temporary files and atomic completion, retain attribution,
   retry transient failures and skip known source IDs before downloading. Prove a
   real authorized original plays with audio, repeats without duplication and
   survives interrupted processing/restart. Record actual evidence; synthetic
   uploads and remote metadata are not acquired creator footage.
6. Update status, setup instructions and the plan as facts change; run pytest.
   Editing starts after real-source acquisition is demonstrated, and posting comes
   after editing. Keep account selection and publishing setup deferred. Do not
   incur costs, expose credentials or push commits without an owner instruction.

The Mac services run independently while the Mac is awake, powered, online and
logged in. `AUTOMATION_STATUS.md` has a runtime-maintained section that may change
after a commit; do not discard its audit or stop services just to hide that diff.

## What this project is

**ShortsEngine** is a multi-channel content automation platform. It finds news and other
source material, uses AI to write and produce original short-form videos, sends them
through a human review step, publishes them to TikTok and Instagram, and tracks how
each video performs.

It has two goals:
1. **Portfolio project:** Show real software engineering (APIs, AI integration, OAuth,
   databases, data pipelines, an analytics dashboard). Code quality, structure and
   documentation matter as much as features.
2. **Real channels:** Grow accounts that could eventually earn money. The first channel
   is a news channel that explains politics and current events in an interesting,
   easy-to-understand way.

## About the developer

I am a CS & Data Science student directing this project. I must be able to read,
understand and **explain every part of it in an interview**.

## AI assistants on this project

- **Claude Code is the main assistant.** It does most of the building, reviews any
  changes made elsewhere, and keeps this file up to date.
- **Codex is used now and then** for smaller tasks. `AGENTS.md` (the file Codex reads)
  only points here, so both assistants follow exactly the same rules. Do not copy
  these instructions into `AGENTS.md`; edit this file instead.
- Whichever assistant makes a change must follow the code style below and add tests.
  Claude Code should review Codex-made changes against this file at the start of the
  next session (run the tests, check the style rules, update the docs).

## How to write code

**Priority order:** 1) beginner-friendly, 2) efficient and correct. Write the simplest
version first. Only use a more advanced approach when the simple one would be clearly
slow, wasteful or wrong, and when you do, explain why in a comment.

**Beginner-level syntax and format, always:**
- Clear, descriptive names (`fetch_top_stories`, not `fts` or `get_data`).
- Short functions that each do one thing.
- Plain `for` loops and `if` statements instead of dense comprehensions, lambdas,
  nested ternaries or chained one-liners.
- Avoid advanced features (decorators, metaclasses, generators, complex inheritance,
  heavy type tricks) unless truly needed. If one is needed, explain it in a comment.
- Type hints are fine when they make the code clearer.

**Descriptions for everything:**
- Every file starts with a comment block: what the file is for and how it fits into
  the project.
- Every function, method, and class must have a docstring written in plain English,
  as if explaining it to someone seeing Python for the first time. Cover: what it does
  in one sentence, what each input means, what it returns, and a short example where
  that helps. Avoid technical jargon in the docstring itself — use a follow-up comment
  if you need to explain an implementation detail.
- Add comments above any step that isn't obvious, explaining *why*, not just *what*.
- Config files (YAML, `.env.example`) have a comment for every setting.

**After each change**, give me a short plain-English summary: what you built, which
files changed, how it works, and how to run or test it.

If a request is unclear or there is a better approach, ask or suggest it before
building.

## Keeping costs as low as possible

API costs must stay minimal. Always choose the cheapest option that does the job well.

- **Use the cheapest model that works.** Use a small, cheap model (e.g. Claude Haiku)
  for simple tasks like ranking headlines, tagging topics and short summaries. Use a
  stronger model (e.g. Claude Sonnet) only for words that get published: the final
  script, and the on-screen text of animated videos (the storyboard). Model names
  live in the channel/config files, not in the code.
- **Send less text.** Trim articles to the relevant parts, strip HTML, and set a
  `max_tokens` limit on every call.
- **Never pay twice for the same thing.** Cache fetched articles and LLM results
  on disk or in the database. Skip stories that were already processed.
- **Use prompt caching** for long system prompts that repeat on every call.
- **Use batch processing** (e.g. Anthropic's Message Batches API, about 50% cheaper)
  for any work that doesn't need an instant result.
- **Prefer free or local tools** when the quality is good enough: free RSS feeds,
  local Whisper for transcription, FFmpeg for video, and a free or local text-to-speech
  option before a paid voice service.
- **Spending guards:** a `--dry-run` mode that makes no paid API calls, a maximum
  number of LLM calls per run, and a log of the estimated cost of every run
  (tokens in/out × price) so I can see what I'm spending.
- Before adding any new paid API or service, tell me what it costs and suggest a
  free or cheaper alternative if one exists.

## Core design rule: everything is per channel

The system must support **many channels**, not just the news channel. Never hard-code
channel-specific details (topics, tone, sources, models, voice, schedule) in Python
code. They belong in a channel config file, for example:

```yaml
# channels/news.yaml
name: "News Explained"                  # display name of the channel
niche: "US politics and current events" # what the channel covers
audience: "College students who don't follow the news closely"
tone: "Neutral, clear, engaging; explain jargon; present each side fairly"
target_length_seconds: 75               # target video length
models:
  ranking: "claude-haiku-4-5"           # cheap model for picking stories
  writing: "claude-sonnet-4-6"          # stronger model for the final script
sources:
  - type: rss
    url: "https://feeds.npr.org/1001/rss.xml"
platforms: [tiktok, instagram]
```

Adding a new channel should only mean adding a new YAML file.

## Tech stack

Always choose the tool that is **most widely used in the industry** for that job. The
goal is a portfolio that shows the same stack a hiring manager at an AI company or top
tech company would expect to see. When a simpler but less standard tool would work,
use the standard one and note why.

- **Language:** Python 3.11+ — the dominant language for AI/ML and backend work.
- **LLM:** Anthropic API (Claude) via the official `anthropic` Python SDK. All LLM
  calls go through one small module (`llm.py`) so the provider can be swapped and
  costs can be tracked in one place.
- **Data validation:** `pydantic` — the standard for data models and config parsing
  in Python; used heavily in FastAPI and AI pipelines at real companies.
- **Web framework:** `FastAPI` — the fastest-growing Python API framework; async,
  OpenAPI docs generated automatically, used by companies like Netflix and Uber.
- **Database:** SQLite for local dev, PostgreSQL for production. Access via raw SQL
  (not an ORM) so the queries are readable and easy to explain.
- **Frontend (Phase 5+):** React + TypeScript — the most in-demand frontend stack.
  TypeScript is expected at almost every company hiring frontend engineers.
- **Video:** FFmpeg (industry standard for video processing) + `edge-tts` (free
  neural TTS; swap for ElevenLabs later if quality needs to improve).
- **Testing:** `pytest` — the standard Python test framework.
- **CI/CD (Phase 7):** GitHub Actions — the most common CI system; free for public
  repos and expected knowledge in most engineering interviews.
- **Containerisation (Phase 7):** Docker — standard for reproducible deployments;
  knowing Docker is expected at almost every SWE role.
- **News sources:** Public RSS feeds via `feedparser`.
- **Config:** YAML files, one per channel. Secrets in `.env` via `python-dotenv`.
- **Dependencies:** `requirements.txt`; use a virtual environment (`.venv`).

**Rule:** Before picking any library, check that it is the most-used option for that
job (by GitHub stars, PyPI downloads, or industry surveys such as the Stack Overflow
Developer Survey). If a less popular library is chosen for a good reason (e.g. cost,
licence), add a comment explaining the trade-off.

## Planned folder structure

```
shortsengine/
  CLAUDE.md
  README.md
  requirements.txt
  .env.example          # lists required keys, no real values
  channels/             # one YAML config per channel
  config/               # shared settings, e.g. pricing.yaml (model prices)
  src/
    sources/            # fetching news / clips (rss.py, twitch.py later)
    generation/         # script writing (llm.py, prompts.py)
    video/              # voiceover, captions, rendering
      animation/        # animated video engine (storyboard, footage, scenes, renderer)
    publishing/         # TikTok / Instagram APIs
    clips/              # clips channel type (Twitch highlights): python -m src.clips
    storage/            # database models and queries
    main.py             # command-line entry point
  output/               # generated scripts / videos (git-ignored)
  cache/                # cached articles and LLM results (git-ignored)
  tests/
```

## Content rules (news channel)

- **Accuracy first.** Scripts may only use facts found in the source articles provided.
  Never invent quotes, numbers, names or events. If something is unclear, say so in the
  script notes instead of guessing.
- **Every script lists its sources** (title and URL) at the bottom, for my fact-check.
- **Neutral and fair.** Explain what happened, why it matters and what each major side
  says. No opinions, no loaded language, no telling viewers what to think.
- **Script format:** a hook (first 3 seconds), the story, why it matters, what happens
  next, and a closing line. Aim for the channel's `target_length_seconds`.
- **Human review is mandatory.** Nothing is ever published without my approval.

## Safety and security rules

- **Never commit secrets.** API keys live only in `.env`, which is in `.gitignore`.
  Keep `.env.example` updated with key *names* only.
- Only use official APIs and content we have rights to use. Do not scrape sites in ways
  that break their terms, and do not download copyrighted video (for example, network
  news broadcasts).
- Respect platform rules: only post through official APIs the app is allowed to use.
  Meta apps in Development mode may post to accounts with a role on the app (the
  user's own); other people's accounts need App Review. TikTok stays inbox-only
  until its audit passes.

## Content permissions

I have permission from every streamer whose clips this project uses to download,
edit, and repost their clips to TikTok and YouTube Shorts with credit.

**Rules:**
- When a new streamer is added to a clips channel config (e.g.
  `channels/twitch_clips.yaml`), add their name and the date permission was given
  to the list below in the same change. The config's `permission_date` must match.
- Every published video must credit the streamer: their channel name in the
  caption/description (`🎥 Clip: twitch.tv/name`, added by
  `maker.build_post_caption`), and the clipper where known (`✂️ Clipped by name`).

**Streamers with permission** (name, platform, date permission was given as
recorded in `permission_date`; the owner confirmed these on that date, and earlier
original agreement dates were not supplied):

| Streamer | Platform | Permission date |
|---|---|---|
| kaicenat | Twitch | 2026-11-17 |
| adinross | Kick | 2026-11-17 |
| stableronaldo | Twitch | 2026-11-17 |
| adapt | Twitch | 2026-11-17 |
| marlon | Twitch | 2026-11-17 |
| lacy | Twitch | 2026-11-17 |
| jasontheween | Twitch | 2026-11-17 |
| silky | Twitch | 2026-11-17 |

## How to work

1. Default to small, explainable features. When the user authorizes a broader pass,
   complete that scope without waiting between roadmap steps. Explain how to run
   and test the finished work.
2. Prefer simple, working code over a big framework.
3. Handle errors with clear messages (for example, "Missing ANTHROPIC_API_KEY in .env").
4. Add a basic test for any core logic you write.
5. Suggest a git commit with a clear message after each working step.
6. Keep `README.md` up to date: what the project does, setup steps and how to run it.
   It is part of my portfolio.
7. **Updating this file:** You may update `CLAUDE.md` at any time without asking
   permission. Always tell me after you do, and briefly explain what changed and why.

## Current phase

**Rolling live recorder (2026-11-19, Claude Code):** owner asked to record every
approved streamer, process each hour, keep the most engaging moments, delete the
raw footage and delete videos after publishing. Built: `src/clips/live_recorder.py`
(supervisor tick: one Twitch `/streams` call for all streamers every 60 s, Kick via
`streamlink --json`; starts captures most-viewers-first up to `max_concurrent`;
per-streamer daily hours; disk start/stop limits; refuses while `acquisition.only`
is true; finalizes chunks and imports them with `move=True`, `source_kind='live'`),
`src/clips/live_capture.py` (one process per stream, `start_new_session` so it
survives supervisor restarts: Streamlink → FFmpeg segment muxer into MPEG-TS
chunks + `segments.csv`, FFmpeg `-progress` clock log), `src/clips/live_chat.py`
(anonymous read-only Twitch IRC, message counts per second only), and
`src/clips/retention.py` (keyframe-aligned ±30 s excerpt per clip with copied
transcript cache, then delete chunk; delete MP4/excerpt 24 h after delivery to every
platform or rejection). Engagement fills the existing `audience` score (weight now
0.35): chat mapped clock→stream time through the FFmpeg clock log, minus a reaction
delay, plus viewer clips made live (`vod_offset` within 120 s of creation). Each chunk
carries the previous chunk's last 120 s; `overlaps_neighbouring_chunk` stops the same
moment being clipped twice. Tables `live_sessions`/`live_chunks`; columns
`recordings.source_kind/raw_deleted_at`, `clips.files_released_at`. Renderer fix:
decoder broken pipe after the encoder already finished is no longer a failure
(picture starting after sound). Bounded real test on a DB copy (2-min chunks): Kai
Cenat then StableRonaldo recorded at 1080p60, 6 chunks imported, restart adoption
worked, 7 clips rendered and gated with no warnings, all chunks deleted, excerpt
re-render worked. Details: `docs/live-recording.md`. **669 tests pass** (19 new).
**Switched on 2026-11-19 14:43 local** at the owner's request: `acquisition.only: false`,
`live_recording.enabled: true`; supervisor and dashboard restarted; Kai Cenat and
StableRonaldo began recording immediately. The restart also fixed a stale supervisor
that had been failing every clip cycle since the quality commit (`frame_rate: source`). Publishing stays dry-run; hook titles use the
existing metered Haiku call (~$0.001 per clip). Kick chat is not read.

**Clip quality pipeline + bleeping (2026-11-18, Claude Code):** owner-specified
upgrade of the existing renderer (no parallel pipeline). Layouts in
`src/video/clip_layout.py`: `fit-blur` (default; whole picture over blurred/darkened
copy), `split` (gameplay top 60%, facecam crop inside the configured box below;
per-streamer `layout:` override — Jason uses split) and `center-crop`. One decoder
filter graph (fps first, Lanczos everywhere, `unsharp=5:5:0.6` only on enlarged
panels) → Pillow text → one encode: libx264 slow/CRF18/High/yuv420p, source frame
rate (cap 60), AAC 192k 48 kHz, +faststart. Audio (`src/video/clip_audio.py`):
one cleanup pass (highpass 80, optional dialoguenhance, afftdn nr 10, compressor)
to float WAV; numpy bleeps; loudnorm pass 1 with iterative oversampled peak
limiting so pass 2 stays linear; pass 2 inside the final encode (-14 LUFS, TP
target −1.5 = −1 minus 0.5 dB AAC headroom). afftdn adds a 1200-sample delay and
dialoguenhance 1536; both are compensated (regression tests). RNNoise asset and
code removed. Bleeping: `src/clips/profanity.py` + editable `config/profanity.yaml`
(suffixes, inside-matches, allow list, stretched letters, look-alikes, censored
stars); spans padded 40 ms, too-short words widened to ~0.07 s/letter; 1 kHz beep
at the replaced speech's level ×0.8 with 8 ms cross-fades; captions and post text
masked; `<video>.transcript.json` and `.bleeps.json` written next to each output
(deleted with the video). Also: top-left credit tag for 4 s, clipper credit in post
captions, <720p source warning/reject, quiet start/end trimming, layout-aware safe
text positions, dashboard layout selector. faster-whisper engine added (PyAV 19
installed; audio passed as an array because faster-whisper 1.2.1's PyAV call is
incompatible), but `auto` prefers whisper.cpp: on a 45-second window of the real
clip faster-whisper returned 37 words and skipped most of the shouted rant, which
whisper.cpp kept. Draft 8
re-rendered via `rerender_clip`: 22.42 s (trimmed 1.63 s dead start, 8.2 s silent
tail), split, 1080x1920 H.264 High 60/1 fps yuv420p, AAC 48 kHz, faststart, −13.9 LUFS,
−1.5 dBTP, 7 bleeps covering all 8 swear words, technical gate passed, no warnings.
Previous draft MP4 preserved in `output/local_recording_probe/ready_to_post_voice_v5/`.
No paid calls, uploads or deployment. Known limit: whisper.cpp word times are
segment edges, so some beeps run ~1 s. See `docs/stream-clips.md`.

**Speech-first clip audio (2026-11-18, Codex; RNNoise and `noise_reduction` superseded
by the entry above):** owner requested voices above
gameplay. Added per-channel `audio.speech_enhancement`, `dialogue_boost` (0–3)
and `noise_reduction` (0–1); configured Twitch to true/2/0.8. Older channels
retain normalization-only sound. FFmpeg dialogue enhancement, speech EQ and
gentle compression feed a bundled original RNNoise model (303 KB, pinned hash,
original BSD license retained), with no new Python library, paid call or runtime
download. Model input is levelled consistently. Final measured fixed gain toward
-14 LUFS plus oversampled -1.5 dB peak limiting preserves the cleaned background;
the existing dynamic loudness pass had raised quiet gaps again after denoising.
Both filter delays are padded/trimmed; mono/stereo timing, partial blocks, silence,
invalid settings, fallback/cleanup and voice/background contrast have offline
regressions. Enhancement failure logs a warning and preserves normalized original
sound. Original source media stays unchanged; mixed-in game dialogue/other voices
cannot be controlled independently.

Draft 8 was updated by audio-only remux; its encoded 1080x1920/60 fps picture is
bit-identical (packet hash checked), same 32.24-second edit/captions/hook/facecam.
Final AAC measured -15.0 LUFS, -1.4 dB true peak; technical gate passed without
warnings. Transcript-window versus between-word RMS contrast improved from 1.81
to 5.19 dB (a window-based proxy, not isolated stem SNR). Private page returned
200, seeking 206, logged-out access 303. Services reloaded; acquisition-only and
publishing dry-run preserved. Latest export:
`output/local_recording_probe/ready_to_post_voice_v5/`; ignored evidence:
`output/local_recording_probe/audio/upgrade.json`, with previous MP4 retained.
Updated README and operating/pilot docs. **582 tests passed in 26.30 seconds**,
including 19 new audio regressions. Suggested commit:
`feat: prioritize speech in stream clip audio`.

**Source/export quality upgrade (2026-11-18, Codex):** owner reported softness.
Diagnosed the first pilot's 1280x720/60 fps, ~3.1 Mbps source and its 285x191 camera
crop being enlarged into a 1080x720 panel. Recovered the same moment from public
archive 2932190650 in native 1920x1080/60 fps (~7.0 Mbps). Bounded capture was
remuxed without video transcoding; matched to the old source with audio correlation
0.965 and checked matching frames. Imported as recording 2; recording 1 is retained.
Draft 8 remains the same edit/title/layout, now seeking 147.43–179.67 seconds into
the HQ window. Original caption words/timings were translated by the measured
133.994625-second offset, avoiding a context-dependent re-transcription change.
Added per-channel `encoding.frame_rate/crf/preset` (older defaults 30/20/fast),
configured Twitch to 60/17/medium; decoder, encoder and caption clock match.
Explicit Lanczos resizing for panels/foreground. First HQ render measured
1080x1920/60 fps, ~12.1 Mbps video; technical gate passed without warnings.
**563 tests pass**, including seven added encoding/scaling regressions.
No paid AI calls, publication or host provisioned. Native camera detail remains
limited; this is not AI upscaling. Updated README, operating/pilot docs and status.

**Facecam and hook presentation (2026-11-18, Codex):** owner requested visible
facecams on every source, no in-video Twitch URL, and more dramatic descriptive
hooks. Saved Jason's camera box measured from the 1280x720 original (995,403,
285x191 pixels). Every template now honors a configured camera in the top panel;
unknown camera coordinates retain the full source on blur rather than cropping
away possible embedded cameras. This is not automatic face detection/tracking.
`credit.enabled: false` hides the URL pill for the clips channel; captions/notes
retain creator attribution. `hook_title.seconds: 8`, `hook_title.use_llm: true`
and `metadata.style` configure grounded dramatic headlines. Hook-only model calls
reuse `clip_pick`, caching, token/call limits and the existing job spending
reservation; `analysis.use_llm` stays false and local scores are unchanged.
Re-rendered draft 8 with **ONE Roblox Question Set Jason OFF**, large facecam above
gameplay, hook below the camera and no URL overlay; inspected opening/later frames,
technical gate passed without warnings. No paid call for this re-render and no
publishing. New export: `output/local_recording_probe/ready_to_post_facecam_v2`.
**556 tests pass** (11 new regressions). README, pilot/operating docs and status
updated. Original recordings and prior local evidence preserved.

**Local real-footage pilot (2026-11-18, Codex):** the owner requested local testing
before a $25/month cloud pilot and allowed Streamlink public-stream recording.
Real Jason footage was captured, imported, selected, transcribed, captioned,
rendered and exported locally: recording 1, draft 8. Source playback/seeking and
duplicate import passed. Output 32.24 seconds, 1080x1920 H.264/AAC, technical checks
passed without warnings; sample frame inspected. No paid calls or publishing.
**545 tests pass** (24.33 seconds). Only documentation/status changed in this
pass; diagnostic media/reports are ignored local artifacts. The installed runtime
is still acquisition-only; no continuous capture or cloud deployment is claimed.
Next work/evidence: `docs/local-recording-pilot.md`.

**Metadata-only Twitch discovery (2026-11-18, Codex):** extended the existing
Twitch client with app-token refresh, bounded rate-limit retries, login resolution
and paginated Get Clips/Get Videos (archive) queries. `src/clips/discovery.py`
normalizes metadata into existing `clips` rows with source type/external ID,
creator/clipper credit, URL, dates, duration, views, thumbnail/game/VOD references,
and permission defaulting false. Additive migrations preserve old finder IDs and
enforce `(platform, external_id)` uniqueness. Repeat discovery updates metadata
without clearing attached media or permission. New lightweight `clip_discovery`
jobs reuse the existing queue/worker and supervisor scheduler every six hours
(configurable, following Start/Stop), with browser and CLI triggers. Background
inbox cycles no longer invoke the legacy clip downloader. Kick is an explicit
unsupported metadata stub. Browser uploads can select a discovery, reuse the
fingerprinted recording importer and mark that same entry acquired; linked
internal recordings are not shown as duplicate library cards. Acquisition-only
mode stays enabled. No video downloads, new dependency, paid call or publication.
Offline tests cover HTTP/auth/pagination/rate limits, identity, provenance,
attachment, scheduling and pagination/creator filtering in the source library.
The installed services were reloaded without reinstalling. Actual discovery job
`8e67dcd8a30644c59d968047639aee8a` succeeded: 5,286 new/535 updated metadata items,
no source errors, page-limit warnings for five creators plus the Kick stub notice.
Database afterward: 5,358 discoveries including 369 VODs, zero acquired files and
zero creator recordings. Metadata is not footage. Setup, manual landscape-download
flow and changed-file list: `docs/source-discovery.md`.
Full offline suite: **545 tests pass** (31 added; baseline 514).

**Full-repo review (2026-11-18, Claude Code):** audited security (login gate,
CSRF on every POST, path guards, allow-listed dynamic SQL, `.env` 0600,
dashboard bound to 127.0.0.1), Python 3.11 compatibility for CI/Docker, and
the Codex clips/runtime modules. Fixed one latent bug: `verify_deliveries`
re-polled every non-public upload every 5 minutes forever (private YouTube
validation uploads, TikTok inbox items, failed/deleted uploads), which would
exhaust YouTube's daily quota within weeks. Finished answers are now never
re-checked and person-dependent states (private/inbox) every 6 hours
(`is_due_for_check` in `publishing/verification.py`). Removed unused imports
and a stale variable. **514 tests pass.**

**Acquisition-first phase (2026-11-18, Codex, latest owner direction):** prioritize
getting Twitch/Kick originals into the site; editing comes next, posting afterward.
Plan: `docs/video-acquisition-plan.md`. First step implemented: private raw-source
library at `/clips/twitch_clips/sources`, using existing discovery/recording tables
and cache; browser upload of authorized completed recordings, original playback
with range requests, existing fingerprint deduplication and permission checks.
`src/clips/acquisition.py` separates discovered links from real local bytes.
`acquisition.only: true` in the configured channel stops automatic cycles before
Whisper/analysis/rendering and skips scheduled posting. Source upload only imports
the original; it does not launch an editing job. Default browser limit is 2048 MB.
New routes/template/tests reuse current authentication/CSRF, FFprobe and importer.
**503 tests pass**; a real original synthetic video was uploaded, replayed byte
for byte with seeking and imported twice without duplication in an isolated test.
Actual acquisition-only worker job completed with 67 discovered entries, **0 stored
creator sources**, no renders/uploads. Download access for Jason remains absent;
Kick polling credentials are absent. Full-VOD/authorized feed adapters are later
steps, not claimed implemented. No new dependency or paid service. Updated README,
automation status and setup/launch notes. The owner has requested committing this
working step with the resume instructions above; see Git history for its commit.

**Independent automation pass (2026-11-18, Codex):** audited first; **476 baseline
tests passed**, real dashboard HTTP/privacy checks passed, and live official Twitch
monitoring worked with corrected credentials. Existing signal scoring, media,
Whisper, account, queue and publishing code was reused. Added independent
`src/clips/runtime.py` and Mac installer `src/clips/service.py`; installed
`com.shortsengine.clips` and `com.shortsengine.dashboard` user LaunchAgents.
The supervisor now runs without Terminal/Codex, maintains health and rotating JSON
logs, periodically recovers stale jobs, protects low disk and cleans only old
idle temporary fragments. Killed the installed supervisor deliberately; launchd
restarted it under a new PID with a fresh heartbeat. Login-at-start configuration
is installed; no full reboot was performed. Mac must be powered, awake, online
and logged in. Cloud Compose runtime/healthcheck prepared; Docker is absent so
container build/deployment is still unverified and no paid server was provisioned.
Added official YouTube/TikTok post-status polling in
`src/publishing/verification.py`, persisting confirmed public URLs separately from
accepted uploads/inbox notifications without resetting duplicate reservations.
Optional silence-edit failures preserve original media/timing. Dashboard access
logs omit OAuth callback query strings. The user explicitly requested autonomous
clips: `require_approval` is now false for clean technically passing clips; news
review stays mandatory. Scheduled clip publishing remains a dry run because
footage access/destination connections remain unresolved. The existing database
automation flag was already enabled and was preserved.
Live discovery found 32 public metadata candidates, including five Jason clips;
none are downloaded/rendered creator footage. Creator recordings, clip-channel
account connections and live upload tests are absent. TikTok public Direct Post
remains incompatible with this private utility's current use under official
guidelines; no bypass was added. First human step and exact limits are in
`HUMAN_SETUP_GUIDE.md`; `AUTOMATION_STATUS.md` records the audit and auto-updating
counts. The final full suite has **495 passing tests** (21.68 seconds). New coverage includes
runtime, safe cleanup, recovery, source/account readiness, upload-state contracts,
persistent verification and optional editing fallback. Existing original synthetic
Whisper/render evidence from today is retained; it does not count toward publication.

**Today launch readiness (2026-11-18, Codex):** verified the existing pipeline
before extending it; baseline **460 tests passed**. Added free `clips cut` for
exact 15–60 second authorized selections and `clips export` for checked MP4/text/
review-note bundles for official manual uploads. Reuses existing Whisper, renderer,
permissions and review; no new dependencies or paid services. Exports reject bad
media/audio warnings, revoked sources, duplicate bytes/overlapping moments and
active or uncertain/completed uploads to the requested destinations. A TikTok-only
export can finish the second destination after YouTube has received a clip.
Sixteen new regression cases; **476 tests pass**. Fresh real whisper.cpp transcribed
70 words from original synthetic media, rendered a checked 20-second 1080×1920
H.264/AAC draft and exported a manual bundle. Audio mean −18.0 dB, peak −1.5 dB;
sample-frame inspection passed. A separate automatic scoring/render/export check
also passed. Reports: `output/launch_2026_11_18/smoke/{report,automatic_report}.json`.
Whisper GPU initialization needed execution outside the assistant sandbox.
The owner selected JasonTheWeen; no matching recording was found in accessible
local/OneDrive folders, and iCloud remained blocked by macOS privacy controls after
an approved retry. The supplied Twitch credentials were saved with owner-only `.env`
permissions. An initial HTTP 403 was resolved by correcting the client ID: the
official token endpoint now returns HTTP 200 and Jason's official channel lookup
also succeeds. The `connect jasontheween` CLI was canceled when the owner clarified
they do not know Jason and cannot obtain broadcaster authorization. Investigate
Twitch's official viewer-created clip download/share UI, using the owner's account
and the channel's permitted settings. This differs from the restricted download
API. Actual Jason controls/reuse scope remain unverified; browser inventory reported
the access host unavailable. Unattended acquisition is not demonstrated. App login
alone does not authorize downloads. No credential/token values were logged in project docs.
Existing Google/TikTok app credentials and news-account connections were detected,
but clips has no connected destinations; the owner deferred account selection.
**Zero real creator clips and zero uploads/publications** so far; synthetic checks
do not count. Live clips OAuth/uploads remain unvalidated. See
`TODAY_LAUNCH_CHECKLIST.md` for exact owner steps, counts, evidence and blockers.
Changed this pass: `src/clips/launch.py`, `src/clips/__main__.py`,
`tests/test_clip_launch.py`, `README.md`, `CLAUDE.md`, `TODAY_LAUNCH_CHECKLIST.md`.

**Clips completion checks (2026-11-17, Codex):** inbox scans now isolate corrupt
files and unreadable folders, report source-specific errors on the existing job
page, and count only newly imported recordings toward the per-cycle limit.
Unchanged-file checks include the channel/creator/platform so reassigned inboxes
cannot silently bypass attribution conflicts. The demo rejects empty macOS
speech before encoding and uses a finite test-video duration. Five regression
cases added; **460 tests pass**. A separate-worker dry run passed with an isolated
database. The real Whisper demo transcribed 70 words and produced a checked
21.43-second 1080×1920 H.264/AAC draft (79.7/100); sample-frame review passed.
Report: output/clips_finish_verification_2026_11_17/report.json. macOS speech
needed execution outside the sandbox; no paid calls or uploads were made.
README, operating guide and handoff updated. Live account uploads and Docker
remain unvalidated; the existing credential/hosting steps below still apply.

**Configured creator sources (2026-11-17, Codex):** added KaiCenat, StableRonaldo,
Adapt, Marlon, Lacy, JasonTheWeen and Silky on Twitch, plus AdinRoss on Kick, at
the user's request. Consent notes record the user's report in this conversation;
the original agreement dates/methods were not supplied. Each creator has a separate
completed-recording inbox under recordings/. No facecam coordinates were guessed.
Review, paused automation and publishing dry runs remain the configured defaults.
Twitch app secret and creator download logins were absent during the readiness
check; Kick app credentials were also absent. Creator-footage acquisition therefore
still needs official access or a supplied authorized recording. A source-config
test checks parsing, attribution and nonoverlapping inboxes. README updated.
Verified: **455 tests pass**. The configured channel's one-cycle run recorded
all eight sources as unknown due to the missing credentials, with no downloads
or creator videos produced. The separate synthetic-media demo completed with
real Whisper-derived word timings, a 79.7/100 candidate and a passing export gate;
report: output/creator_setup_demo_2026_11_17/report.json. Nothing was uploaded.

**Clips automation extension (2026-11-17, Codex):**
- Extended the existing app; news/Meta workflows remain available. New private
  `/clips` pages manage sources, permission notes, completed recordings, scoring,
  automation, retry resets and configurable review/publishing dry runs.
- New `src/clips/{schema,recordings,signals,monitor,automation,metadata,quality,editing,demo}.py`
  reuse the job worker, raw SQLite, existing renderer and publishing scheduler.
  New schema tables/columns are additive. Recording hashes, cached word timings,
  bounded retry/backoff, atomic draft links, per-video destinations and daily
  upload reservations support restart recovery and duplicate prevention.
- Existing `find/make/run` retain the older Claude picker. New `record/cycle/watch`
  use free local scoring by default, with optional metered Claude speech assessment
  and metadata. Review and scheduled dry runs default on. The user's broad request
  explicitly authorizes opt-in automatic approval for clips; news review stays mandatory.
- Official Kick channel polling added; media comes only from completed creator
  recordings. Twitch still downloads authorized official clips, not arbitrary VODs.
  True-silence trimming is optional. Templates use fixed crops/facecam rectangles
  or a full-frame blurred fill; no automated subject tracking or live chat collector.
  Optional supplied engagement counts are supported; scores are estimates.
- Public TikTok Direct Post remains unavailable: current guidelines restrict
  private upload utilities/cross-platform copying. Authorized inbox/manual export
  is the supported path. TikTok transfers now handle large files in chunks.
- Current YouTube docs changed: uploads are not categorically forced private for
  unverified projects, and the default upload quota is a separate 100/day bucket.
  Older notes below reflect previous assumptions. Actual returned privacy controls
  public-post recording. Clips YAML now requests private uploads for validation.
- Real local demo used macOS speech + whisper.cpp (70 words), rendered a checked
  1080×1920 H.264/AAC draft; no paid calls or publishing. See docs/stream-clips.md.
  Real OAuth/API posting still needs app/account validation. No deployment performed.
- No new Python dependency; existing tools suffice. Docker adds a pinned
  whisper.cpp CPU build and persistent models/recording mounts; container validation
  remains necessary on the target host. **454 tests pass** (425 existing + 29 new
  cases); actual background-worker dry run and real local media demo also passed.
  See docs/clips-handoff.md for the changed-file list and verification limits.


**Phases 1–7 code complete. M-series visual upgrade (M.1–M.6) code complete.
A-series animated video engine (A.1–A.5) code complete — this is now the main way to render.**

- Phase 1: scripts generate for ~$0.013 ✅
- Phase 2: `--render` produces a real 9:16 MP4 (Pillow + FFmpeg + macOS TTS) ✅
- Phase 3: `--review` queue and `--report` work ✅
- Phase 4: publishing code built; needs INSTAGRAM_ACCESS_TOKEN / TIKTOK_ACCESS_TOKEN in .env ✅
- Phase 5: FastAPI dashboard runs at http://localhost:8000 (`python -m src.dashboard.app`) ✅
- Phase 6: analytics collector + feedback loop built; needs posted videos for data ✅
- Phase 7: Dockerfile, GitHub Actions CI (.github/workflows/tests.yml), README, docs/ ✅
- M.1: analysis of legal footage sources and animation tools ✅
- M.2: Claude Haiku scene planner (`scene_planner.py`) — 5 scene types ✅
- M.3: Pexels Video B-roll integration (`stock_video.py`) — needs PEXELS_API_KEY ✅
- M.4: Pillow-based animated graphics (`animated_frames.py`) — no extra deps ✅
- M.5: Renderer updated to wire all M-series types + `--style` flag ✅
- M.6: Tests for scene planner, animated frames, stock video ✅
- A.1–A.5: Animated video engine (`src/video/animation/`, `--render --animated`) ✅
- C.0: Control center foundation — private login, templates, Docker fixes ✅
  C.0 login confirmed working by the user.
- C.1–C.5: Local control center implemented (see docs/control-center.md).
- C.6: Hosting still awaits the user’s choice; nothing deployed.

**Topic search (2026-11-11, Claude Code):** `src/sources/news_search.py`. A topic on
the New video page also searches recent web news when the channel YAML has a
`search:` section: GDELT (free, no key, 1 request / 5 s) plus NewsAPI `everything`
(only with NEWS_API_KEY; free plan is development-only). Results cached 15 min in
cache/search/. Google News rejected: its links can't be resolved to article text.
GDELT returned HTTP 429 for every request from the user's network during testing;
NewsAPI works. If GDELT stays blocked, consider dropping it.

**Control-center review fixes (2026-11-11, Claude Code):**
- Readability pass on all C-series modules (full docstrings, small single-purpose
  functions, constants instead of magic numbers). Behaviour unchanged except below.
- Only heavy jobs (generate, render) count toward `max_in_progress`; finding stories
  and collecting statistics run beside them (one of each per channel at a time).
- A rejected video can be approved again; approve/reject report when nothing changed.
- A public post can only be recorded for an approved (or already posted) video.
- Startup converts old local-time timestamps to UTC (`control_schema.py`), so
  sorting, the library and history charts compare like with like.
- `accounts.token_expires_at` records when a token expires; tokens pasted by hand
  show "expiry unknown" on the channel page.

**Review fixes (November 2026):**
- Scheduler stores one video per channel/time slot in `publishing_slots` and upload
  results/returned IDs per video/platform in `publishing_deliveries`. Named time zones
  use Python's built-in `zoneinfo`, including daylight saving and midnight windows.
- Failed uploads never mark a video posted. Successful destinations are skipped.
  Network failures and interrupted sends remain `uncertain` or `sending`: check the
  platform manually before retrying, since the platform may already have accepted it.
  After confirming no upload exists, delete only that video's platform delivery row
  in SQLite to permit retry. Never clear a succeeded delivery to retry another platform.
- TikTok success means delivery to its inbox, not a confirmed public post.
  Mixed-platform videos remain approved until all configured destinations succeed.
  Account-aware daily analytics now uses the control center’s encrypted connections
  and confirmed public post IDs.
- Dashboard and ranking feedback sum only the latest cumulative snapshot per platform.
- AI and configured voice estimates now write per-call cost_events and video totals,
  including failed renders. CSV remains an additional run log; old missing costs
  require manual reconciliation.

**Control-center update (November 2026):**
- Neutral light theme: warm off-white, charcoal text, muted olive; no neon UI.
- Overview/channel pages use latest cumulative metrics. Public post counts use
  explicit `platform_posts`, not TikTok inbox delivery IDs.
- Encrypted per-channel accounts, one-use OAuth state, TikTok refresh rotation,
  Instagram via Facebook Login, and once-a-day account/post snapshots. Live
  platform flows need approved apps and real credentials for validation.
- Money ledger combines new per-call costs, manual payouts/expenses and legacy
  video totals without double-counting. New voice costs use configured character
  estimates. Historical unrecorded costs can be entered manually.
- Browser jobs run in separate processes, with SQLite capacity/spending reservations,
  heartbeats, source checks, duplicate protection and free dry runs. Renders return
  to drafted. Only the "publish" job posts (Post now). Active renders cannot be approved.
- Video library has private playback, cached thumbnails/durations, search, filters
  and an explicit untracked-file state. Paths outside output/ are refused.
- New code lives in dashboard/{routes,data,settings,accounts,jobs,worker,media}.py,
  storage/{control_schema,finance}.py, analytics/accounts.py and sources/public_web.py.
- No new paid service. The only new dependency is PyCA cryptography for tokens.
- Daily spending caps apply to browser jobs; independent CLI runs still log costs
  but are not subject to that dashboard cap. Unknown request outcomes retain an
  uncertain estimate rather than being silently retried.

**Merged (2026-11-16):** PR #2 (A-series + C.0–C.5) merged into `main`; 289 tests
pass. The user checked the new dashboard pages.

**Meta publishing (2026-11-16, Claude Code, branch `feat/meta-publishing`):**
- Instagram Reels (`publishing/instagram.py`) and Facebook Page Reels
  (`publishing/facebook.py`) via Meta's resumable upload to rupload.facebook.com:
  the MP4 goes straight from this computer, so no public hosting is needed.
  Shared helpers and `NothingPublishedError` live in `publishing/meta_api.py`.
- Facebook has no account row: it uses the Instagram connection to find the linked
  Page and its token (fetched per post, not stored). No schema change.
- `scheduler.py` is the one posting path: `publish_video_now()` (dashboard "Post now"
  button → job kind `publish`, free) and `run_scheduler()` (channel `schedule:`
  block, checked every 5 min by a dashboard thread; `DASHBOARD_AUTO_PUBLISH`).
  Failures before the final publish step release the reservation (safe retry);
  later failures stay `uncertain`. Instagram Reel IDs go into `platform_posts`.
- INSTAGRAM_SCOPES gained `instagram_content_publish` and `pages_manage_posts`:
  existing Instagram connections must be reconnected once.
- Live Meta calls are untested until the user's first real post; 305 offline tests pass.

**YouTube Shorts (2026-11-16, Claude Code, same branch):**
- `publishing/youtube.py`: resumable `videos.insert` (details → `Location` → PUT file).
  Public only if YouTube says so (unaudited projects are forced private); only public
  uploads go into `platform_posts`. `NothingPublishedError` moved to `publishing/errors.py`.
- Google OAuth in `dashboard/accounts.py` (offline access, auto refresh like TikTok;
  no token pasting). `ACCOUNT_PLATFORMS` there is the one list of connectable platforms;
  templates read it (plus `scheduler.PLATFORM_NAMES`) as Jinja globals.
- `control_schema.allow_youtube_platform()` rebuilds old `accounts`/`platform_posts`
  tables to widen their CHECK rule (tested on a copy of the real DB: rows kept).
- YouTube statistics: views/likes/comments; shares saved as NULL (API has none).
- Plain httpx chosen over google-api-python-client for consistency (noted in code).
- 320 offline tests pass; live Google calls untested until the user connects.

**Video delete (2026-11-17, Claude Code):**
- "Delete this video" on a video's page removes the record, MP4, script and its
  `_storyboard/_sceneplan/_visuals.json` files (`queries.delete_video_record`,
  `media.delete_video_file/delete_script_files`). A file another record still uses is kept.
- Money is never lost: cost_events are unlinked (video_id NULL), jobs keep their
  history, and any older untracked total becomes a `money_entries` expense.
- Refused for posted videos, videos with any delivery/public post, or an active job.
- Untracked MP4s can be deleted from their own page. 7 tests in tests/test_video_delete.py.
- Connected accounts (news): YouTube, Instagram (+Facebook), TikTok (sandbox app).

**Q-series video quality upgrade (2026-11-17, Claude Code, branch `feat/meta-publishing`):**
Full write-up in docs/video-quality-upgrade.md. Summary:
- Q.2 Scripts: channel renamed "News Made Simple"; slogan + all style rules + seven
  spoken sections with `max_words` budgets live in `channels/news.yaml` (`style:`).
  `generation/reviewer.py`: Haiku scores hook/clarity/neutrality/accuracy (1-10) +
  code checks (banned opener, slogan, length per section); <7 → ONE Sonnet rewrite,
  logged in the script's `## Quality check` (never spoken). `generation/sections.py`
  decides spoken sections (everything except Notes/Sources/Quality check), so old
  scripts still work. max_calls_per_run 7; job allowance includes check + rewrite.
- Q.3 Voice: `video/pacing.py` reshapes pauses from word timings (detects the real
  end of each word from the audio); edge-tts now uses real WordBoundary timings;
  ElevenLabs tuning/output format from YAML; 4xx errors settle the estimate to $0.
  Voice chosen by the user 2026-11-17: ElevenLabs Matilda (XrExE9yKIg1WjnnlVkGX) on
  eleven_multilingual_v2 (samples in output/voice_samples/). Kokoro and Chatterbox
  need Python ≤3.13 (venv is 3.15). macOS voices: non-commercial only.
- Q.4 Visuals: `animation/photos.py` (Wikimedia, licence allow-list), `animation/shots.py`
  (cut every ≤2.8 s, pan/zoom), analogy template, photo name cards, ≤8-word beats,
  1-3-word keyword captions in the safe zone (`drawing.SAFE_*`), brand bug, end card,
  `video/sound_design.py` (Freesound CC0 / own tracks with credits.yaml, ducking,
  two-pass -14 LUFS). Stock clips now record source + licence. Credits file per video.
- Q.5 `animation/quality_gate.py` runs before drawing; errors stop the render.
- A number range ("thirty to fifty billion") can never become one big-number graphic.
- 366 offline tests pass (tests/test_quality_upgrade.py). Session spend ≈ $1.42
  (mostly ElevenLabs character estimates).

**L-series Twitch clips channel (2026-11-17, Claude Code, branch `feat/meta-publishing`):**
- New channel type `type: clips` (`channels/twitch_clips.yaml`, replaces twitch_example.yaml).
  Video is ONLY the real clip (user rule: never animations). Adds split layout (facecam top,
  gameplay bottom; centred crop without a facecam box), word-highlight captions, hook title
  (first 3 s), credit pill. No channel logo (TikTok bans app-added promotional watermarks).
- `src/clips/`: settings.py (pydantic; `find_approved_streamer` refuses unlisted streamers,
  rechecked at render), twitch_login.py (device-code login, scope `channel:manage:clips`,
  encrypted in `twitch_logins`), finder.py (Get Clips → Get Clips Download; `clips` table
  UNIQUE(source, source_clip_id) = duplicate check), transcribe.py (whisper.cpp CLI,
  `models/whisper/ggml-small.en.bin`), moments.py (Haiku picks 20-60 s + ≤8-word title; code
  clamps/snaps), maker.py (render → notes .md → drafted `videos` row). Render code:
  `video/clip_layout.py` (pure maths) + `video/clip_render.py` (FFmpeg decode → Pillow → FFmpeg
  encode; this Mac's FFmpeg 9 has no ass/subtitles/drawtext filters).
- Twitch downloads need the STREAMER's own token (or an editor's); a DM alone is not enough.
  Twitch has no past-chat API, so chat-spike detection only works live (L.7, not built).
- Other platforms: YouTube policies forbid downloading; Kick API has no clip/VOD endpoints →
  not fetched (see the user decision below).
- TikTok inbox upload sends only `source_info` now (it never accepted a caption). Video pages
  show a "Caption to paste in TikTok"; clip captions credit + link the streamer. Scheduler uses
  the clip caption for Instagram/Facebook/YouTube too.
- Dashboard: job kind `clip_render` (heavy, free) from the "Re-cut clip" form; clips channels
  may only run clip_render/publish/sync jobs and are hidden from /new.
- 2026-11-17 user decision: sources ONLY Twitch + Kick; post ONLY to TikTok + YouTube.
  Streamers have `platform: twitch|kick`. Kick (no clip API) = `upload FILE --streamer X
  --i-own-this` (approved list still enforced; credit kick.com/x). Generic `--credit` uploads
  removed. YouTube: Gaming category 20, description = clip caption + #Shorts.
- 425 offline tests pass (tests/test_clips.py: 59). Real e2e upload→make run cost $0.0008.

**Next human steps:**
- Clips channel: create the Twitch app (see .env.example), add TWITCH_CLIENT_ID/SECRET,
  add each streamer (note + date + facecam box) to channels/twitch_clips.yaml after written
  permission, run `connect <streamer>` (Twitch only), connect a SEPARATE TikTok account
  and a SEPARATE YouTube channel to twitch_clips, then `run --dry-run`, `run`, and watch the
  first clip on a phone.
- Listen to the CC0 music pick ("Stoned Song") or add own licensed tracks to
  assets/music/news/ with a credits.yaml. Check the ElevenLabs plan covers
  multilingual_v2 credits (~1,050 characters per 75-second video).
- Watch draft #6 (FAA, upgraded render) and give visual feedback.
- Configure approved developer apps/account credentials to validate real OAuth
  and statistics. Existing .env tokens can be explicitly imported per channel.
- Watch an animated render and give visual feedback; no paid render was needed
  for the control-center implementation tests.
- Choose a hosting approach for C.6 before any deployment.
- Meta app: add `instagram_content_publish` + `pages_manage_posts`, give the user's
  account an app role, reconnect Instagram, then try "Post now" on a test video.
- YouTube: Google Cloud project + YouTube Data API v3 + OAuth client (consent screen
  "In production" to avoid 7-day expiry), add keys to .env, Connect with Google.

## Roadmap

Each phase ends with something that works on its own. Finish one before starting the next.

### Phase 0 — Project setup
- Folder structure, virtual environment, `requirements.txt`, `.gitignore`,
  `.env.example`, starter `README.md`, first git commit.
- **Done when:** the project runs a "hello world" command and no secrets are tracked by git.

### Phase 1 — News script generator (text only)
- Load the channel config, fetch stories from RSS, remove duplicates, rank them with
  the cheap model (or let me pick from a list), and write a neutral script with the
  writing model.
- Save each script to `output/<channel>/<date>-<slug>.md` with its sources.
- Includes caching, `--dry-run` and cost logging.
- **Done when:** one command produces a good, sourced script for under a few cents.

### Phase 2 — Video production
- Text-to-speech voiceover, burned-in word-by-word captions, simple visuals (headline
  text, maps, charts, backgrounds), and a 9:16 MP4 rendered with FFmpeg.
- **Done when:** one command turns an approved script into a ready-to-post video.
  (I post these by hand during this phase to test what gets views.)

### Phase 3 — Database and review queue
- SQLite tables for channels, videos (status: drafted → approved → posted → failed),
  and costs.
- A simple command-line or web review step where I approve, edit or reject scripts
  and videos.
- **Done when:** every video and its cost is tracked, and nothing moves forward
  without my approval.

### Phase 4 — Publishing
- Instagram Graph API publishing for Reels, and the TikTok Content Posting API
  (upload-to-drafts until the app passes TikTok's audit).
- Scheduled posting at set times per channel.
- **Done when:** approved videos post (or land in TikTok drafts) automatically.

### Phase 5 — Dashboard and connected accounts
- FastAPI web app: create and edit channels, connect TikTok/Instagram accounts with
  OAuth (tokens stored encrypted), use the review queue, and see stats per channel
  (videos made, posted, views, API spend, top performers).
- **Done when:** I can manage every channel from the browser.

### Phase 6 — Analytics loop and more channels
- Collect views, likes, shares and watch time daily; show trends; use the results to
  improve story selection and hooks.
- Add a second channel type (e.g. Twitch clips with streamer permission).
- **Done when:** there's a measurable improvement I can put on my resume.

### Phase 7 — Polish for the portfolio
- Deploy to a cheap cloud server, scheduled runs, tests, a clean README with an
  architecture diagram and screenshots, and a short demo video.

### M-series — Visual quality upgrade (runs alongside Phase 7+)

The M-series improves video quality without changing the core pipeline.

**M.1 — Legal footage analysis** ✅
- Identified three free sources: Pexels Video (commercial licence), Pixabay Video,
  and US government YouTube.
- Chose Pillow animations over Manim for data graphics (no system-library deps);
  Manim available as an upgrade when `brew install cairo pango`.

**M.2 — Scene plan JSON** ✅ (`src/video/scene_planner.py`)
- Claude Haiku reads the spoken script and assigns a scene type to each ~15-word
  segment: `footage`, `image`, `animated`, `chart`, or `text_card`.
- Saved as `<slug>_sceneplan.json` next to the script for review/editing.

**M.3 — Pexels Video B-roll** ✅ (`src/video/stock_video.py`)
- Searches Pexels Video API for a clip matching the segment description.
- Downloads and caches the clip; extracts one frame per caption group so the
  background "plays" through the clip while captions advance.
- Needs PEXELS_API_KEY in .env (free at pexels.com/api).

**M.4 — Pillow animated graphics** ✅ (`src/video/animated_frames.py`)
- Two animation types, both drawn with Pillow only (no extra dependencies):
  - Number callout: a large stat counts up frame by frame (e.g. "27,000 ft").
  - Comparison bars: two horizontal bars grow side by side.

**M.5 — Renderer update** ✅ (updated `src/video/render.py`, `src/main.py`)
- `render.py` handles all 5 scene types in `_build_visual_frames()`.
- Footage and animated frames skip Ken Burns (they already change per caption group).
- New `--style` flag: `auto` (Haiku decides), `footage` (prefer video), `animated`
  (prefer graphics for data segments).

**M.6 — Tests** ✅ (`tests/test_scene_planner.py`)
- 30 tests covering scene_planner, animated_frames, and stock_video.
- All run offline (dry_run + mocks) — no API calls needed.

### A-series — Animated video engine (`src/video/animation/`)

Run with `python -m src.main --channel news --render --animated`. Every frame is three
layers: stock footage playing full-screen, an animated graphic for the current sentence,
and word-by-word captions. There is never a blank or still screen.

**A.1 — Storyboard** ✅ (`storyboard.py`)
- Splits the script into beats (a sentence or part of one, max 12 words).
- One AI call (`models.storyboard`, ~3 cents) picks a footage topic and a graphic per beat.
- Saved as `<slug>_storyboard.json` next to the script: free to re-render, editable by hand.

**A.2 — Accuracy checks** ✅ (`storyboard.py`)
- On-screen words must come from the spoken sentence; numbers and names must be in the
  script; claims must keep "says" / "argues" / "expected". Failures become a plain
  headline of the spoken words. Every graphic is printed beside its spoken words for review.
- Haiku was tried first and rejected: it stated claims as facts and ignored the rules.

**A.3 — Footage** ✅ (`footage.py`, `stock_video.get_video_clip`)
- Clips come only from the channel's hand-checked `video.animation.footage_topics`
  (free-form Pixabay search returned unusable clips). Dark clips are skipped.
- Fallback order: chosen topic → general topics → previous clip → moving gradient.

**A.4 — Graphics and overlays** ✅ (`scenes.py`, `overlays.py`)
- Nine templates: headline, icon_label, person, big_number, versus, bullet_list,
  timeline, bar_chart, stamp. Icons are Noto Emoji; font is Montserrat (both open licence).
- Captions highlight the spoken word; progress bar; section label; wipe between sections.

**A.5 — Renderer and tests** ✅ (`renderer.py`, `tests/test_animation.py`)
- Draws ~30 frames per second with Pillow and pipes them to FFmpeg (no PNG files).
- 54 offline tests.

**Future ideas for the A-series**
- Sound effects on scene changes (whoosh / pop) and background music by default.
- More graphic templates (map, quote card) and more approved footage topics.

### C-series — Private control center (browser dashboard)

Goal: one private place in the browser to see and manage everything — every channel
and account, every video, followers, money made, money spent — and to make new videos
without the terminal. The responsive UI supports phones; reaching it from another
device still requires the C.6 hosting/network choice. The user authorized completing
C.1–C.5 together after confirming the C.0 login works.

**C.0 — Foundation and privacy** ✅ (`src/dashboard/`, `tests/test_dashboard_auth.py`)
- Every page is private by default: one check (`require_login` in `app.py`) runs before
  every request; only `/login` and `/static/` are public.
- Password hashed with bcrypt in the `users` table (`python -m src.dashboard.set_password`).
  Signed session cookie (`SESSION_SECRET` in `.env`). 5 wrong passwords lock an address
  out for 15 minutes (`config/dashboard.yaml`). Every form carries a secret token.
- Pages moved from inline HTML strings to Jinja2 templates (auto-escaped).
- Docker: `.dockerignore` keeps `.env`, the database and videos out of the image; the
  database lives in a mounted `data/` folder; port 8000 is bound to the server only.
- Replaces the old `DASHBOARD_PASSWORD` browser prompt, which was open when left blank.

**C.1 — Home page** ✅ (local implementation)
- Cross-channel totals, per-channel cards, recent content/activity, cumulative
  follower/view history. Unavailable metrics display a dash, not an invented zero.

**C.2 — Accounts and channels** ✅ (live credentials still required)
- Channel details, verified encrypted account connections, OAuth state validation,
  TikTok token refresh, Instagram Facebook Login, daily snapshots and best videos.
- Collector uses confirmed public post IDs. Tokens are no longer implicitly shared
  across channels. The dashboard collects missing daily snapshots while running;
  `python -m src.analytics.accounts` can also run from a server scheduler.

**C.3 — Money** ✅ (estimates, with historical gaps disclosed)
- Manual USD payouts/expenses, monthly/channel totals and a unified ledger.
- AI calls and ElevenLabs character estimates are tracked, including failures.
  Legacy video totals remain visible without counting the same event twice.

**C.4 — Video library** ✅
- Private MP4 playback and cached FFmpeg thumbnails/durations; title search,
  channel/status filters, script/source review and untracked-file flags.

**C.5 — Make videos from the browser** ✅ (article-based channels)
- Choose current sources, optionally rank them, filter by topic or paste a URL.
  Source text is required before paid writing. Script-only and animated outputs.
- Separate background processes, progress/error history, free dry runs, default
  $2/day reservation limit and one job in progress. Results remain drafted.
- Twitch example stays disabled until configured with permission; there is no
  browser Twitch clip editor. See docs/control-center.md for operating limits.

**C.6 — Hosting** (waiting for a decision — deploy nothing until chosen)
- Options compared: (a) Mac + Tailscale, (b) cloud server with public HTTPS + login,
  (c) cloud server reachable only through Tailscale. Recommended: (c).
- One animated render peaked at 1.1 GB of memory (measured on the Mac), so a 1 GB
  server is too small; 4 GB / 2 CPUs is comfortable.

### L-series — Twitch clips channel (`src/clips/`)

L.1 research/plan ✅ · L.2 config + permission check ✅ · L.3 find/download ✅ · L.4 Whisper +
moment pick ✅ · L.5 vertical render ✅ · L.6 review/re-cut/TikTok drafts/scheduler ✅ ·
L.7 live mode (EventSub chat spikes → Create Clip, N per stream) — later, not built ·
L.8 tests + README ✅. Run with `python -m src.clips --channel twitch_clips run`.

**Decisions so far:** server-rendered pages (FastAPI + Jinja2) rather than React for
this dashboard, to keep it simple and explainable — these were Claude's recommended
defaults, accepted by "start" without separate discussion, so revisit if wanted.

**Future: Manim diagrams (M.4b)**
- Richer data-vis scenes (maps, timelines, motion diagrams) using Manim Community.
- Requires: `brew install cairo pango && pip install manim`.
- Stub is in place; activate by setting `scene_type = "manim"` in scene plan JSON.

## Commands

**First-time setup:**
```bash
python3 -m venv .venv          # create the virtual environment
source .venv/bin/activate      # activate it (Mac/Linux)
pip install -r requirements.txt
cp .env.example .env           # then open .env and add your API key
```

**Every session** — activate the virtual environment first:
```bash
source .venv/bin/activate
```

**Running the pipeline:**
```bash
python -m src.main --channel news --list         # list today's new stories (free)
python -m src.main --channel news --dry-run      # full pipeline, no paid API calls
python -m src.main --channel news                # AI picks a story and writes a script
python -m src.main --channel news --pick 3       # write about story #3 from --list
```

**Making a video:**
```bash
python -m src.main --channel news --review             # approve a drafted script
python -m src.main --channel news --render --animated  # fully animated video (~3 cents)
python -m src.main --channel news --render             # classic still-picture video (free)
```
To change a graphic, edit `output/<channel>/<script>_storyboard.json` and render again (free).

**Clips channel (Twitch highlights):**
```bash
python -m src.clips --channel twitch_clips connect <streamer>  # once per approved streamer
python -m src.clips --channel twitch_clips run --dry-run       # free preview
python -m src.clips --channel twitch_clips run                 # find, download, make drafts
python -m src.clips --channel twitch_clips upload f.mp4 --streamer <kick name> --i-own-this
```

**Dashboard:**
```bash
python -m src.dashboard.set_password   # first time (or to change the password)
python -m src.dashboard.app            # then open http://localhost:8000
```

**Tests:**
```bash
pytest          # run all tests
pytest -v       # verbose (shows each test name)
```
