<!-- Real bounded local recording evidence, current limits and the next unattended-recorder milestone. -->
# Local recording → website → clipped MP4 pilot

**Latest audio revision (2026-11-18):** draft 8 now uses the configured local
speech-first pass: dialogue emphasis, gentle EQ/compression, consistent model
input levels, bundled RNNoise suppression blended at 80%, then fixed measured
gain and oversampled peak limiting. A dynamic loudness pass after denoising
had raised quiet gaps again; final level control now preserves that contrast.
The original mixed source is effectively mono, so stereo dialogue emphasis alone
was insufficient. The model can suppress non-speech sound, but cannot independently
remove game dialogue or another person's voice. No paid call or model download
is needed during rendering.

The final 32.24-second H.264/AAC MP4 is 49,528,262 bytes. Its encoded picture
packets have exactly the previous video hash, preserving 1080x1920 at 60 fps,
facecam, captions and hook. AAC measured -15.0 LUFS and -1.4 dB true peak. The
technical gate passed without warnings. Transcript-marked windows versus gaps
show RMS contrast of 5.19 dB (previously 1.81 dB); this is a speech-heavy/gap-heavy
comparison, not a measurement of separate microphone/game tracks. Subjective
voice quality still needs listening. Private draft page returned 200, a byte-range
request returned 206 with matching bytes, and logged-out access remained 303.
Services were reloaded; source media, acquisition-only and publishing dry-run
settings remain unchanged.

**Latest bundle:** `output/local_recording_probe/ready_to_post_voice_v5/`.
Evidence: `output/local_recording_probe/audio/upgrade.json`; pre-audio MP4:
`audio/before-speech-enhancement.mp4`. Earlier bundles are historical versions.
New audio regression coverage checks settings, fallbacks, centered speech versus
stereo background, mono/stereo sync, partial blocks, silence, model paths and
final contrast preservation. No paid calls, uploads or publications.
Full offline suite: **582 passed in 26.30 seconds**, including 19 audio regressions.

Files changed for audio: `src/video/clip_audio.py`, `src/video/clip_render.py`,
`src/clips/settings.py`, `channels/twitch_clips.yaml`, `tests/test_clip_audio.py`,
`assets/audio/rnnoise/{std.rnnn,COPYING,README.md}`, `README.md`, `CLAUDE.md`,
`docs/stream-clips.md`, and this file. The supervisor independently maintains the
marked runtime section in `AUTOMATION_STATUS.md`.

**Latest quality revision (2026-11-18):** after the owner reported softness, the
same exchange was recovered from the public archive in native 1920x1080/60 fps,
around 7.0 Mbps video. The first pilot used a 1280x720/60 fps variant at 3.1 Mbps;
its camera occupied only 285x191 pixels before enlargement. The higher-resolution
source provides approximately 428x287 camera pixels, not a native 1080p facecam.

A bounded four-minute window was retrieved and remuxed with stream copy, then
matched to the old recording by audio (normalized correlation 0.965) and checked
visually. The old recording begins 133.994625 seconds into the new window. The
same edit now selects 147.43–179.67 seconds. The original 83 caption words/timings
were shifted by that measured offset, preserving the existing caption sequence.
Both original media variants and prior transcript evidence remain available.

The HQ source is recording 2; recording 1 is retained. New per-channel export
settings use 60 fps, CRF17 and medium preset, with explicit Lanczos scaling.
Decoder, encoder and caption timing use the same frame rate. Older channel
defaults remain 30 fps/CRF20/fast. The first HQ render measured 1080x1920/60 fps,
32.24 seconds, about 12.1 Mbps video, with no technical errors/warnings. Full suite:
**563 passed in 23.87 seconds**. No paid AI calls or uploads.

Evidence: `output/local_recording_probe/hq/upgrade.json`,
`hq/audio-alignment.json`, `hq/matched-original-frame.jpg`, and
`hq/final-hook-preview.jpg`. These are local ignored artifacts. This quality
change does not install continuous recording; the future recorder must prefer
native source quality instead of silently choosing the first 720p variant.
Final checked export: `output/local_recording_probe/ready_to_post_1080p60_v3/`.
The MP4 is 49,529,307 bytes, 32.24 seconds, 1080x1920 at 60 fps, approximately
12.09 Mbps video. Final re-render job: `42d71d84b3094c2bbcffaf5d372bed2b`.
It passed the technical gate with no warnings; original caption words/timings,
facecam layout, hook and hidden on-video URL setting are retained. Earlier export
folders document prior revisions and are not the latest output.

Files changed for quality: `src/clips/settings.py`, `src/video/clip_layout.py`,
`src/video/clip_render.py`, `channels/twitch_clips.yaml`,
`tests/test_clip_presentation.py`, `README.md`, `CLAUDE.md`,
`AUTOMATION_STATUS.md`, `docs/stream-clips.md`, and this file.

**Presentation revision (2026-11-18):** the existing draft 8 was re-rendered after
owner feedback. Jason's measured camera (995,403,285x191 in the 1280x720 original)
now fills the top 720 pixels, with gameplay below. The on-video URL pill is hidden;
creator credit remains in post captions/notes. The new eight-second opening hook
is **ONE Roblox Question Set Jason OFF**, placed below the facecam so it does not
cover his face. Opening and later frames were inspected, and full technical checks
passed with no warnings. No paid model call was made for this revised sample.
Future hooks use the existing cached/metered title model with a dramatic but
grounded style, independently of local scoring. Unknown camera positions preserve
the full source; no automatic face detection or tracking is claimed.
Latest export: `output/local_recording_probe/ready_to_post_facecam_v2/`.
Re-render job: `f429e6f5a9084ca492a0b7923a66cbbb`; full suite **556 tests pass**.
Revision metadata is saved in the ignored `report.json` under `presentation_revision`.

Files changed for this presentation revision: `channels/twitch_clips.yaml`,
`src/clips/settings.py`, `src/clips/maker.py`, `src/clips/metadata.py`,
`src/video/clip_render.py`, `src/dashboard/jobs.py`,
`tests/test_clip_presentation.py`, `README.md`, `CLAUDE.md`,
`AUTOMATION_STATUS.md`, `docs/stream-clips.md`, and this file.

The owner chose a $25/month budget for a later always-on server and asked to test
locally first. They explicitly allowed the Streamlink approach beyond the earlier
official-API-only constraint. No hosting account, subscription or server was created.

## What actually passed on November 18, 2026

1. Installed tools were already available: Streamlink 8.6.3, FFmpeg and whisper.cpp.
2. A one-off subprocess recorded approximately 45 seconds from the configured
   JasonTheWeen public live stream. The resulting media duration was 46 seconds
   because live recording ends at stream-segment boundaries.
3. Streamlink used no default config, user cookies, browser/integrity fallback or
   plugin sideloading. A 90-second process deadline bounded the probe. Its native
   output went to a temporary `.capture.part` file; stderr was sanitized before
   reporting. No playback URL, credential or private token was saved in the report.
4. FFmpeg remuxed the media into MP4 with stream copy and fast-start metadata.
   The original is **19,035,529 bytes, 1280x720, 46 seconds, with audio**.
5. The existing fingerprinted recording importer stored it in the actual local
   database as **recording 1**, attributed to `twitch / jasontheween`. Importing the
   same bytes again returned the same ID. The private source library lists it.
6. The existing pipeline ran with a temporary, one-clip test configuration. Local
   Whisper, scoring and rendering produced **draft 8**, selecting 13.44–45.68
   seconds of the original. The score is 79/100, a local estimate rather than a
   measured entertainment or performance result.
7. The finished clip is **32.24 seconds, 1080x1920, H.264/AAC**, with captions and
   creator credit. The full decode/format/audio gate passed with no warnings.
   A representative frame was inspected. Semantic caption accuracy and preferred
   framing still need the owner's review.
8. The running private website returned HTTP 200 for the source page, original
   MP4 and draft page. Original playback returned identical bytes; a 16-byte range
   request returned HTTP 206 and exactly 16 bytes.
9. The existing export function produced one MP4 plus upload text/review metadata.
   The video stays drafted. **Zero paid AI calls, uploads or public posts.**
10. The full offline suite passed: **545 tests in 24.33 seconds**. No application
    code was changed for this diagnostic; existing functionality was exercised.

## Review and evidence

- [Watch the finished draft](http://localhost:8000/video/8).
- [Open the source library](http://localhost:8000/clips/twitch_clips/sources?streamer=jasontheween).
- [Play the original](http://localhost:8000/clips/twitch_clips/sources/recording/1/file).
- Local evidence: `output/local_recording_probe/report.json`.
- Original: `output/local_recording_probe/jasontheween-pilot.mp4`.
- Finished clip: `output/videos/twitch_clips/2026-11-19-jasontheween-clip5359.mp4`.
  The filename uses UTC, which was already November 19 during the November 18 CST session.
- Export folder: `output/local_recording_probe/ready_to_post/`.
- Sample frame: `output/local_recording_probe/clip-frame.jpg`.

These ignored files contain real creator footage, not synthetic test patterns.
The capture/import was programmatic; the owner did not download or upload a file.
Creator-use permission remains the owner's existing configuration attestation,
not an independently verified license. Discovery itself still downloads no media.

## What this does not prove

This is a successful single local recording and edit, **not an installed continuous
recorder**. No interruption/restart exercise was performed on the capture route.
Kick recording was not tested. Docker is not installed here, so the Linux image,
CPU transcription speed and prospective cloud deployment remain unverified.

The actual channel keeps `acquisition.only: true`. Only an in-memory configuration
copy enabled processing for this diagnostic, capped output to one clip, disabled
paid assessment, required review and kept publishing dry-run enabled. Other sources
were not recorded. The existing supervisor continues its established schedule.

## Next implementation

1. Wrap the tested Streamlink CLI route in a bounded recorder adapter driven by
   existing live-status monitoring and the supervisor. Begin with Jason only,
   one recording at a time; retain channel-specific creator settings.
2. Persist stream/chunk identity and provenance; cap time/bytes/daily volume and
   reserve disk for capture, remuxing and the importer's cached copy. Reuse current
   source rows and the existing queue rather than creating a parallel scheduler.
3. Finalize MP4s atomically, validate them and import automatically. Keep incomplete
   files out of the source library. Preserve originals; agree on a retention plan
   before continuous cloud recording can fill a small server disk.
4. Prove repeated polling creates no duplicate recording/process, and exercise
   disconnects, interrupted remux/import, worker/supervisor restart and low disk.
5. Test recurring local capture → editing → review output. Only then enable the
   normal editing phase and expand creators/platforms.
6. Validate the Linux/Docker build, benchmark one worker, then choose a server
   whose complete monthly cost fits the $25 budget. Keep account connections and
   actual publishing separate from recording/editing acceptance.

Files changed in this documentation-only pass: `README.md`, `CLAUDE.md`,
`AUTOMATION_STATUS.md`, `docs/video-acquisition-plan.md`,
`docs/local-recording-pilot.md`. Media, evidence JSON and temporary diagnostic
scripts are local artifacts, not new production recorder modules.
