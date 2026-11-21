# Viral streamer-clip research (2026-11-20)

This report records what was actually measured or observed, the sources, and how each
finding changed ShortsEngine's code. Facts are separated from assumptions. Nothing in
it proves that a technique *causes* views: these are descriptions of successful videos,
not controlled experiments.

## 1. Sources and method

| Source | What was collected | How to re-check |
|---|---|---|
| YouTube Data API v3 (official, read-only, the channel's own connected account) | 130 Shorts, uploaded 2026-08-23 to 2026-11-16, found by `search.list` (type=video, videoDuration=short, order=viewCount, last 90 days) for 9 searches: jasontheween, kai cenat, ishowspeed, jynxzi, caseoh, stableronaldo, lacy twitch, adin ross, marlon twitch. For each: URL, channel, title, upload date, views, likes, comments, length. | `docs/research/youtube_shorts_2026-11-20.json` (every record has its URL). Re-run: `python -m src.clips.research --channel twitch_clips` (`src/clips/research.py`, ~900 free quota units). |
| Watching real Shorts in Chrome (paused frames at several times) | 5 videos from 4 clipping channels: [core_fx Jason](https://www.youtube.com/shorts/tTsLkjJ9YpQ), [ImGoochy Jason](https://www.youtube.com/shorts/gnEakqqa0vc), [tentinpants_ CaseOh](https://www.youtube.com/shorts/NJAk8v9tfUE), [Snickerly Kai Cenat](https://www.youtube.com/shorts/LJaZYtmQ_TI) | Open the links; observations below. |
| GitHub (via `gh`, stars/licence/last push on 2026-11-20) | ~25 repositories (table in section 5) | Links in section 5. |
| Platform documentation | [YouTube: how engagement metrics are counted](https://support.google.com/youtube/answer/2991785), [TikTok creative best practices](https://ads.tiktok.com/help/article/creative-best-practices) | Official pages. |
| Creator/vendor discussions | [Streamlabs: what makes a good Twitch clip](https://streamlabs.com/content-hub/post/what-makes-a-good-twitch-clip), [cut.pro: hook, tension and turn](https://cut.pro/en/blog/viral-clip-structure-2026), [findclout clip editing guide](https://findclout.com/blog/clip-editing-guide), [eesel: OpusClip reviews](https://www.eesel.ai/blog/opusclip-reviews), [G2 OpusClip reviews](https://www.g2.com/products/opusclip/reviews), [r/contentcreation thread (mirror)](https://libreddit.winscloud.net/r/contentcreation/comments/1o074m3/new_to_clipping_looking_for_advice_automation) | Mostly vendor content; treated as qualitative only. |
| Speech-recognition research | [Cornell: Whisper hallucinations (2024)](https://www.news.cornell.edu/stories/2024/06/ai-speech-text-can-hallucinate-violent-language), [summary of 2025 arXiv study](https://yage.ai/share/whisper-repetition-hallucination-en-20260526.html), [OpenAI forum: hallucinating on empty sections](https://community.openai.com/t/whisper-api-hallucinating-on-empty-sections/93646) | Linked. |
| Our own footage | 109 saved stream excerpts (±30 s around earlier picks) from Jason, Lacy, Marlon, StableRonaldo, Kai Cenat, Adin Ross plus a full 7.5-minute StableRonaldo chunk | `cache/clips/excerpts/`, `output/overhaul_test/` (git-ignored) |

**Not done / not possible:** TikTok has no public API for other creators' video statistics,
so TikTok numbers were not collected (no scraping). Shares, watch time and retention of
other people's videos are private; none are claimed. Only 5 videos were watched frame by
frame (not 50–100): the metadata of 130 videos is measured, the visual observations are
a small sample. Reddit search returned few real threads; most "creator advice" found is
written by clipping-tool companies.

## 2. Observed patterns (measured on the 130 videos)

- **Views:** 0.31 M to 151 M, median 7.1 M. 80 different channels; only ~7 videos are
  from the streamers' own channels: **independent clippers dominate**.
- **Length:** median **27.5 s**. 10 under 15 s, 61 between 15–30 s, 19 between 30–45 s,
  26 between 45–60 s, 14 over 60 s. The most-viewed quarter has a median of 44 s vs. 26 s
  for the rest, so successful clips are **not one fixed length**: short reactions and
  longer stories both work.
- **Likes per 1,000 views:** median 18.7 (under 30 s: 16.8; 30–60 s: 20.2; over 60 s:
  31.2, n=14). Longer clips that people chose to finish may collect more likes; small
  samples, not causal.
- **Comments per 1,000 views:** median 0.12 — comments are rare on Shorts.
- **Titles:** median 9 words / 54 characters. 58% start with the streamer's name. 36%
  contain 😭; 73% contain at least one emoji, almost always exactly one or two at the end.
  27% contain one ALL-CAPS word; only 6% are fully lowercase. 34% put hashtags in the
  title. Most-used verbs: *got, reacts, finds out, made, crashed out, tried*.
- **Title shape** (observed, not a rule): *who* + *a specific thing that happened*, in
  past tense: "Kai Cenat Got Donated $300 Just To Call Speed 😭", "CaseOh's Shoplifter
  Got Instant KARMA", "Jason Stood Up Before They Even Announced the Winner 😭", "Marlon
  Was SHOCKED When He Clicked on Jason's Stream 😭". Generic hype words ("insane",
  "epic", "you won't believe") were rare among the top videos.

## 3. Observations from watched videos (5 videos, small sample)

- **Layout:** in 4 of 5, the facecam is stacked on top and fills ~45–55% of the height,
  with the **whole head plus headroom**; gameplay or context is below. The facecam is
  enlarged even when it was a small overlay on stream. The fifth (award show) used the
  broadcast picture stacked.
- **Text:** one short caption (1–3 words, bold, yellow/cyan with a dark outline) sits on
  the **seam between face and gameplay**, never over the face. tentinpants_ shows a
  single quote ("Goodbye Timmy" + one emoji) at the seam for the whole clip instead of
  word-by-word captions. Captions are sparse, not every word animated.
- **Openings:** every clip opens directly on the setup line ("And the winner…",
  "Give me ammo…"), no intro, logo or "wait for it".
- **Edits:** clean cuts; one inserted a screenshot of the chat ban to explain context;
  one turned the picture black-and-white at the punchline. No constant zooms.
- **Endings:** end on the reaction (laugh, stunned face), within ~1 s.

## 4. Common mistakes (from sources and from our own old output)

- Starting before the interesting part / ending before the payoff ([cut.pro](https://cut.pro/en/blog/viral-clip-structure-2026), [findclout](https://findclout.com/blog/clip-editing-guide)).
- AI clip pickers that "miss the point", titles "off the mark", captions drifting out of sync ([eesel](https://www.eesel.ai/blog/opusclip-reviews), [G2](https://www.g2.com/products/opusclip/reviews)).
- Whisper inventing "thank you"/"thanks for watching" over silence or music, and repeating
  itself in loops ([Cornell](https://www.news.cornell.edu/stories/2024/06/ai-speech-text-can-hallucinate-violent-language), [study summary](https://yage.ai/share/whisper-repetition-hallucination-en-20260526.html)).
- **Our own old output (measured on the database):** the last 30 clips were 22–60 s,
  mostly 50–60 s (padded to the limit); every one scored 82–97/100 so the score rejected
  nothing; every title followed one template ("StableRonaldo CONFUSED by…", "Jason
  LOSES IT…"); only Jason had a facecam position, so other streamers' faces were tiny;
  Jason's saved webcam box was measured on one stream and did not match his later
  layout (camera moved to the right edge); GTA cutscene dialogue and NPC faces were
  present in the footage.

## 5. Reusable open-source work (inspected 2026-11-20)

| Repository | Stars | Licence | Last push | What it does | Decision |
|---|---:|---|---|---|---|
| [opencv/opencv](https://github.com/opencv/opencv) + [opencv_zoo YuNet](https://github.com/opencv/opencv_zoo) | — / 1.1k | Apache-2.0 / MIT model | 2026-10 | Fast CPU face detector (230 KB ONNX) | **Adopted** for facecam detection (`opencv-python-headless`) |
| [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | 16k | Apache-2.0 | 2026-11 | VAD, speaker ID, **audio tagging (CED, AudioSet: Laughter, Screaming, Speech, Music)** | **Already used** (voices); now also laughter/scream/music detection (CED-tiny, 6 MB) |
| [mutonby/openshorts](https://github.com/mutonby/openshorts) | 9.5k | MIT | 2026-11 | LLM moment ranking; per-scene layouts TRACK/GENERAL/SPLIT; captions on the seam | Design reference for per-moment layouts + seam captions (no code copied) |
| [utkualsir/sharkyclips](https://github.com/utkualsir/sharkyclips) | 23 | MIT | 2026-11 | Stream clipper: loudness+laughter (sherpa-onnx)+chat+HUD; facecam-top; filler/silence cuts "never inside a laugh" | Confirms the same signal set; small project, used as a reference only |
| [Anil-matcha/AI-Youtube-Shorts-Generator](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator) | 6.4k | MIT | 2026-11 | LLM virality framework, overlap dedupe, OpenCV face crop | Reference for dedupe; its LLM-only ranking is what we avoid relying on alone |
| [ClipsAI/clipsai](https://github.com/ClipsAI/clipsai) | 595 | MIT | 2024-01 | WhisperX + pyannote + face reframing | Unmaintained since 2024, needs PyTorch + HF token: not adopted |
| [m-bain/whisperX](https://github.com/m-bain/whisperX) | 25.5k | BSD-2 | 2026-11 | Forced word alignment + diarization | Needs PyTorch (no Python 3.15 wheels); not adopted; our faster-whisper re-timing already covers swear words |
| [pyannote/pyannote-audio](https://github.com/pyannote/pyannote-audio) | 10.8k | MIT | 2026-11 | Diarization | PyTorch + gated models; sherpa-onnx speaker embeddings already give per-piece voice IDs |
| [snakers4/silero-vad](https://github.com/snakers4/silero-vad) | 10.7k | MIT | 2026-11 | VAD | Available through sherpa-onnx; CED's "Speech" score is used instead because stream audio is mostly music+speech |
| [facebookresearch/demucs](https://github.com/facebookresearch/demucs) | 10.4k | MIT | 2024-04 | Vocal/music separation | Heavy PyTorch, minutes per hour of audio; not adopted (documented as a future option) |
| [Breakthrough/PySceneDetect](https://github.com/Breakthrough/PySceneDetect) | 5.6k | BSD-3 | 2026-11 | Scene cuts | Not needed: stream footage is one continuous shot; facecam is re-checked per clip instead |
| [WyattBlue/auto-editor](https://github.com/WyattBlue/auto-editor) | 6.0k | Unlicense | 2026-11 | Loudness-based silence cutting | Not adopted: cuts mechanically by loudness, which the brief forbids |
| [jrgillick/laughter-detection](https://github.com/jrgillick/laughter-detection) | 298 | MIT | 2024-07 | Laughter model | PyTorch; CED covers Laughter/Giggle/Chuckle |
| [ggml-org/whisper.cpp](https://github.com/ggml-org/whisper.cpp), [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | 55k / 27k | MIT | 2026-11 | Transcription | Already used |
| [linto-ai/whisper-timestamped](https://github.com/linto-ai/whisper-timestamped) | 2.9k | **AGPL-3.0** | 2026-09 | Word confidence | Licence incompatible with a closed portfolio app; not used |

## 6. Recommended technical changes → what was implemented

| Finding | Change in ShortsEngine |
|---|---|
| Successful clips are 10–60 s depending on content; our clips were padded to 50–60 s | `clips.moment_min_seconds` 20 → 10; the editor model chooses setup start and payoff end; `trimming.py` ends shortly after the reaction (`src/clips/trimming.py`) |
| Facecam on top, whole head, captions on the seam; layouts vary | Automatic facecam detection with YuNet, per-clip layout choice (stacked / face-focused / full picture), head-safety and caption-overlap checks with a fit-blur fallback (`src/clips/framing.py`) |
| Loud ≠ funny; humour depends on setup and payoff | New 7-category ranking with the brief's weights; loudness only feeds part of "surprise"; laughter, chat and a model that reads the transcript judge humour; weak clips are rejected (`src/clips/ranking.py`, `src/clips/editor.py`) |
| Titles: specific, name + what happened, ≤1–2 emoji, rarely hype words | Title generator: understand first, then several candidates, scored for specificity, accuracy (grounded in transcript words), naturalness; banned-phrase list; separate hook / YouTube title / TikTok description / hashtags (`src/clips/titles.py`) |
| Whisper hallucinates on silence/music and loops | Caption checks: known hallucination phrases, repeated loops, words where no speech is heard, captions lingering after speech (`src/clips/caption_check.py`) |
| Game/NPC dialogue in captions | Speaker filter now keeps another person's lines only when they are part of an exchange with the streamer; NPC lines (no exchange) stay out |
| Captions sparse and clean in good clips | Caption style library (`clean`, `bold`, `boxed`, `quote`), natural phrase grouping, no forced all-caps, one highlight colour |
| Platforms count every play; engaged views drive revenue | Feedback loop normalises by age and channel median and records which version of the settings made each clip (`src/clips/feedback.py`) |

## 7. Confidence and limitations

- **High confidence:** the length distribution, title statistics and channel mix (measured,
  reproducible JSON). Sample bias: YouTube's search ranking, 9 search phrases, last 90 days.
- **Medium:** layout/caption observations (5 videos, 4 channels).
- **Low:** creator/vendor advice (marketing content). Used only where it matched what we saw.
- **Unknown:** anything about the recommendation algorithm, TikTok numbers, retention.
  No claim here that a technique causes views.

## 8. Prioritised roadmap

1. Done in this pass: framing, selection/ranking, trimming, caption checks/styles, titles,
   QA stage, decision log, feedback/versioning (see `docs/stream-clips.md`).
2. Next, needs real published data: after ~30 posted clips, run `python -m src.clips
   --channel twitch_clips feedback` and A/B the title styles and lengths it suggests.
3. Later: game-HUD reading (OCR) for kill/win events; vocal separation (demucs) on a
   server with a GPU; TikTok analytics once the TikTok app has the needed scopes.

## 9. Second round (2026-11-20 evening): what separates a channel's hits from its flops

Prompted by the owner: the first clips "are not funny".

**Method.** `python -m src.clips.research --channels-from docs/research/youtube_shorts_2026-11-20.json`
read the recent uploads of all 80 clip channels found in round 1 through the
official API (uploads playlists; about 1 quota unit per 50 videos): **10,263 videos,
~10 billion views**, saved in `docs/research/youtube_channels_2026-11-20.json.gz`. For each
channel with ≥20 Shorts (78 channels), its best quarter by views was compared with
its worst quarter. Same channel = same audience and editor, so this removes most of
the "big channel" effect. Then **16 more videos were watched frame by frame**
(8 frames each) in Chrome: 13 of the biggest relative hits across channels and 3
of the worst flops from channels that clip our streamers, plus a TikTok search
page for Jason clips.

**Packaging barely differs between hits and flops (measured):**

| Feature (average) | Best quarter | Worst quarter |
|---|---:|---:|
| Length (s) | 33.3 | 32.7 |
| Title words | 9.2 | 9.0 |
| Emoji per title | 1.12 | 1.09 |
| Capital-letter share | 0.21 | 0.21 |
| Contains 😭 | 36% | 37% |
| Names 2+ people | 75% | 70% |
| Hashtags in title | 34% | 36% |

Within-channel views by length were flat (median relative views 0.94–1.03 for every
bucket from <15 s to >65 s). **Conclusion: title style and length do not explain
which clips win; the moment does.**

**Words over-represented in hits:** other real people (a celebrity/guest name,
"cameraman", "Ryan Garcia", "Duke"), real events ("Streamer Awards"), "didn't",
"asks", "tried to", "finds", "funniest moments". **In flops:** "in GTA" (2.6x more
common among flops), generic hashtag strings ("#twitchstreamer #streamerclips",
"streamer viral", "#fyp": 3–6x more common among flops), "song", "tries".

**Watched hits (examples, all URLs in the JSON):**
[CaseOh kitty](https://www.youtube.com/shorts/YkdcveGeDg8),
[Jynxzi girlfriend](https://www.youtube.com/shorts/omxHLtHMVSU),
[Kai cameraman](https://www.youtube.com/shorts/IgNiZJAbxzE),
[Lil Uzi / Kai](https://www.youtube.com/shorts/pGovdLs-MBk),
[Ron arcade](https://www.youtube.com/shorts/0ixReQG-C-o),
[Lacy bill](https://www.youtube.com/shorts/6wJWXQanwlU),
[Jynxzi / xQc](https://www.youtube.com/shorts/CmbwmrBnYdw),
[Burger the cat](https://www.youtube.com/shorts/7AxDq2_6Q3w),
[Lacy drums](https://www.youtube.com/shorts/BsQOxl-czT8),
[CaseOh shoplifter](https://www.youtube.com/shorts/TiqS6K2h5BI),
[Lacy $12k bill](https://www.youtube.com/shorts/WEiKyTlH-Cw),
[Rizzler trolls Ron](https://www.youtube.com/shorts/T_RoJxfMY6Q),
[Marlon "before streaming"](https://www.youtube.com/shorts/4LFgpZ3mXIg),
[Jason TikTok live](https://www.youtube.com/shorts/WrlFwrqFZmA).
Watched flops: [Joe Bart talk](https://www.youtube.com/shorts/7aBB1-inFwA),
[Ron buys GTA 6](https://www.youtube.com/shorts/wD_v6JNeG38),
[Lacy mansion](https://www.youtube.com/shorts/kaw5N5yKxxI).

**Observed format (hits and flops alike — the baseline every clip needs):**
- Captions: 1–2 words at a time, bold capitals, heavy outline, the spoken word
  coloured; in multi-person clips each speaker has their own colour (Lil Uzi yellow,
  the guest purple).
- A **context line** explaining the situation stays on screen for the whole clip in
  7 of 13 hits ("CHAT TOLD CASEOH TO SPAM THE KITTY…", "The Rizzler played Payphone
  in front of Ron 😂", "Luke after Lacy said that 50/50 split 😂"); none of the 3
  flops had one (small sample).
- Non-speech moments are labelled: *SHOCKED*, *KNOCKS*, *BEAT*, *SKIP*.
- Payoff emphasis: a push-in and/or black-and-white on the reaction (CaseOh ×2,
  Rizzler), a red "NO!" sticker; one used a meme image as the punchline.
- Gaming: face on top (~45–55%), gameplay below; IRL: full-frame crop that cuts to
  whoever talks.

**The difference is content.** All 3 flops had the same caption style and cuts;
they were calm (a heartfelt talk in a car, buying a game in a store, showing a
mansion). Every hit had one unexpected event a stranger understands within seconds.

**Changes made from this round:** editor prompt now carries this evidence and must
name the unexpected `event` (else rejected); stricter profile
`config/clip_ranking/v2.yaml` (surprise 25, pacing 5, minimum 66, payoff floor 0.55,
model share 0.85, `require_event`); caption style `punch` (1–2 capital words, other
speakers coloured); the context line stays up the whole clip as white outlined text
under the face; *LAUGHING*/*SCREAMING* labels from the sound model when nobody talks;
a 10% push-in on the reaction after the payoff; generic channel hashtags removed.
Review queue rebuilt so clips can be watched and judged in one page.

**Limits.** View counts include all plays (YouTube counts replays); within-channel
ranks still mix in posting time and trends; 16 watched videos is a small sample for
the visual patterns; TikTok numbers were only glanced at (likes on a search page).
