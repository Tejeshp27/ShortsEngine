<!-- How the rolling live recorder works, its settings, storage maths and limits. -->

# Rolling live recorder

The live recorder records approved streamers' public live streams, turns each
finished piece into clips, then deletes the piece so the disk is reused.

```
who is live? ──► record in chunks ──► chunk finished ──► import as a recording
 (every 60 s)    (Streamlink → FFmpeg,      │              (with chat + viewer-clip
                  plus chat counter)        │               reaction counts)
                                            ▼
                      clip pipeline: transcribe, score, pick ≤3 moments, render
                                            │
                                            ▼
            keep a ±30 s excerpt per moment, delete the full chunk
                                            │
                       video delivered everywhere (or rejected) + 24 h
                                            ▼
                               delete the video file (row kept)
```

## Files

| File | Job |
|---|---|
| `src/clips/live_recorder.py` | Called by the supervisor every tick: checks who is live, starts/stops captures, turns finished chunks into recordings and works out reaction counts. |
| `src/clips/live_capture.py` | One background process per live stream: Streamlink → FFmpeg chunking, clock log, chat counter. |
| `src/clips/live_chat.py` | Reads Twitch chat anonymously (read-only guest login) and saves message **counts** per second. No text or usernames are stored. |
| `src/clips/retention.py` | Keeps short excerpts and deletes processed chunks; deletes delivered/rejected video files. |
| `src/clips/runtime.py` | The existing supervisor; `manage_live_channels()` runs the two steps above each tick. |

Database: `live_sessions` (one row per recorded stream; a unique index allows
only one active session per streamer), `live_chunks` (one row per chunk, so a
chunk is never imported twice), plus `recordings.source_kind`/`raw_deleted_at`
and `clips.files_released_at`.

## How engagement is measured

The clip scorer already had an optional `audience` score that was empty for
every recording. The recorder now fills it with real reaction data:

1. **Chat bursts.** Messages are counted per second while recording. FFmpeg logs
   (clock time, saved stream time) every 5 seconds, so each chat second is mapped
   to the moment in the video it reacted to, minus `chat_reaction_seconds`. This
   stays correct through ad breaks, because the stream time pauses while the clock
   keeps going.
2. **Viewer-made clips.** After each chunk, Twitch's public Get Clips metadata
   lists clips viewers made during it. Each clip covers the seconds just before
   it was created and adds `viewer_clip_weight` × a normal bucket of chat.
3. Counts go into 5-second buckets. A bucket at least twice the typical level
   proposes a candidate moment (and one starting earlier, to keep the build-up,
   since chat reacts after the moment). The `audience` weight is 0.35, the largest,
   because people reacting is the most direct evidence a moment works.

Scores remain estimates, not view predictions. Kick chat is not read yet, so Kick
recordings are scored without the audience signal.

## Chunk boundaries

Each chunk gets the last `overlap_seconds` (default 120) of the previous chunk
added to its start, so a moment on the boundary is not cut in half. A chunk file
is kept until the next chunk has used its tail.

## Storage

1080p60 is about 3.1 GB per streamer-hour. At any time there are roughly two
chunks per active recording on disk (the one being written and the previous one
waiting for its tail), plus the chunk being analysed:

| Concurrent recordings | Peak raw footage (60-minute chunks) |
|---|---|
| 1 | ~6–9 GB |
| 2 | ~12–19 GB |
| 3 | ~19–28 GB |

Rendered clips are ~30–50 MB each and are deleted 24 hours after reaching every
platform. Excerpts are ~50–100 MB each and go with their video.

Safety limits: no new recording starts below `min_free_gb_to_start` (20 GB);
running recordings stop below `stop_below_free_gb` (10 GB); a chunk waits rather
than failing when there is not enough space to join it.

## Turning it on

1. `acquisition.only: false` in `channels/twitch_clips.yaml` (the recorder refuses
   to start otherwise, because chunks would never be clipped and deleted).
2. `live_recording.enabled: true`.
3. Clip automation Start (clips page) must be on.
4. Restart the supervisor: `launchctl kickstart -k gui/$(id -u)/com.shortsengine.clips`.

Watch `output/runtime/events.jsonl` for `live_recording` events and
`output/runtime/health.json` → `live_recording`.

Status (2026-11-20): paused by the owner in the morning, **resumed at 18:16** the same
day. If the supervisor reports a YAML error that the file no longer has (for example
`bleep → volume`), it is running old code: restart it with the `launchctl kickstart`
line above. Each finished chunk is now analysed by the new ranking/editor pipeline
(see [stream-clips.md](stream-clips.md#how-moments-are-chosen-and-edited-overhaul-2026-11-20)),
so an hour with nothing worth posting produces no clips; the reasons are in
`python -m src.clips --channel twitch_clips decisions`.

## Measured test (2026-11-19, local Mac)

A bounded real run used a copy of the database, 2-minute chunks, 30-second
overlap, one stream at a time and a 306-second daily limit:

- The recorder picked the most-watched live streamer (Kai Cenat), recorded
  1920x1080 60 fps H.264/AAC (~3.4 GB/hour) and stopped at the limit (308 s).
  The freed slot then went to the next live streamer (StableRonaldo).
- 3 chunks per stream were imported: 120.0 s, 150.2 s (120 + 30 s overlap) and
  96.2 s. Session folders were deleted after the last chunk.
- The StableRonaldo capture outlived the test script (by design); a fresh process
  found it, imported its 3 chunks and finalized it, so a supervisor restart loses nothing.
- Chat counts were present for every chunk. Whisper + scoring took ~10 s per
  2-minute chunk. Seven clips were chosen (scores 63–83; the best had audience 0.88)
  and rendered at 1080x1920/60 fps; all passed the technical gate with no warnings.
- All three full chunks were deleted and each clip moved to a ~70 s excerpt;
  re-rendering from the excerpt worked without running Whisper again.

Two problems found and fixed during this run:

1. **Viewer clips.** Twitch's date filter is loose, and many clips are cut later
   from earlier parts of the broadcast. Only clips made live are counted now
   (`vod_offset` within 2 minutes of the creation time, or no offset yet).
2. **Duplicate moments across chunks.** Two clip pairs overlapped in the shared
   boundary minutes. `overlaps_neighbouring_chunk` now skips a moment that covers
   more than a quarter of a clip already chosen from the neighbouring chunk.

Also fixed in the renderer: rendering from 0 s failed when a source's picture
starts a fraction of a second after its sound (normal for an overlap piece).

## Known limits

- Kick: live detection uses Streamlink (no Kick credentials); chat is not read.
- A capture whose stream ends and restarts becomes a new session; nothing is
  lost, but the first chunk of the new session has no overlap.
- Measuring an hour-long 1080p60 chunk's picture takes ~2.5 minutes on this Mac
  (10.3 s for 4 minutes), plus Whisper on the audio.
- Recording public streams uses the owner-approved Streamlink exception noted in
  `CLAUDE.md`; permission is still required per streamer.
