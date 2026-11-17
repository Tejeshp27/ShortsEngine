# Video quality upgrade (Q-series, November 2026)

Why our videos looked like low-effort AI content, what top news-explainer
channels do instead, and what was changed. Measured on a real render before
and after.

## Q.1 — Audit (render of 2026-11-16, "NVDA slides as Anthropic's mega-IPO nears")

Measured with FFmpeg: 84 s long (target 75), audio −20.5 LUFS, 24 kHz mono,
19 picture cuts (one every 4.4 s, gaps up to 12 s), about 1 s of dead air
between every section, no music, no branding.

| # | Problem (worst first) | What top explainer channels do | Fix |
|---|---|---|---|
| 1 | Footage unrelated to the story (an Nvidia story over a mixing desk and hands counting cash) | Real footage or photos of the actual people, places and things | Wikimedia Commons photos of named subjects (free licences only) + 9 new hand-checked footage topics |
| 2 | Weak, generic hook ("AI stocks just had a rough Monday") | A surprising fact or a direct question in the first 1–2 s | Channel hook rules + banned openers + Haiku self-check that scores the hook |
| 3 | Dead air: ~1 s silence between sections, flat delivery | Tight pacing, a beat after the hook, emphasis on key facts | `pacing.py` reshapes every pause from word timings; energetic voice tuning |
| 4 | Too quiet (−20.5 LUFS) and muffled (24 kHz mono) | −14 LUFS, full-band audio | 44.1 kHz voice, two-pass loudness normalisation to −14 LUFS |
| 5 | No music or sound design | Quiet ducking music, whooshes on cuts, pops on numbers | `sound_design.py` (CC0 / own licensed tracks only, credits logged) |
| 6 | Picture changes only every ~4.4 s, gaps up to 12 s | A visual change every 1–3 s | Beats of ≤ 8 words + shot planner (≤ 2.8 s per shot) + pan/zoom on every background |
| 7 | Text walls that repeat the captions as sentence fragments ("OR SAVE THE MARKET ENTIRELY") | Captions carry the words; graphics show things | Headline ≤ 1 in 5 beats; prompt asks graphics to show, not repeat |
| 8 | Captions reach x = 1020 px, under TikTok's like/comment buttons; spoken word highlighted rather than the key word | 1–3 big words in the safe zone, key word in the accent colour | Safe-zone constants, keyword choice (number → name → long word), width-aware splitting |
| 9 | Script too long (205 words ≈ 82 s, target 75) and no plain-language explanation or analogy | 60–75 s, explains jargon, one everyday analogy | Seven-section "News Made Simple" structure with per-section word budgets checked in code |
| 10 | No channel identity: no logo, no consistent end, initials instead of faces | Consistent brand bug, colours, end card | Brand bug, right-aligned section chip, end card with slogan; real photos in name cards |

## Q.2 — Script writer

- Style lives in `channels/news.yaml` (`slogan`, `style.hook`, `style.language`,
  `style.analogies`, `style.audience_rules`, `style.sections` with `max_words`).
- `reviewer.py`: Claude Haiku scores hook, clarity, neutrality and accuracy
  (1–10) against the source; plain code checks banned openers, the slogan and
  length per section. Below 7 or a failed code check → one rewrite by Sonnet,
  then a second score. Everything is logged in the script's `## Quality check`.
- Cost per script: old prompt ≈ $0.012; new prompt + check ≈ $0.020; with a
  rewrite ≈ $0.04.
- Lesson: the model ignored a 197-word total limit (wrote 268). Per-section
  budgets, checked in code, brought it to 173.

## Q.3 — Voice

Costs per minute assume ~950 characters per minute of speech.

| Option | Type | Cost / min | Quality | Commercial use |
|---|---|---|---|---|
| ElevenLabs (current) | Paid API | ~$0.05–0.29 depending on plan/model | Best; very natural, expressive voices | Yes on paid plans (Starter and up); free tier is non-commercial |
| Google Chirp 3 HD | Paid API | ~$0.03 (1M chars/month free) | Very good | Yes |
| Cartesia Sonic | Paid API | ~$0.03 | Very good, fast | Yes on paid plans |
| OpenAI gpt-4o-mini-tts | Paid API | ~$0.015 | Good, steerable tone | Yes (disclose AI voice); reportedly being replaced |
| Kokoro-82M | Free, local | $0 | Very good for its size | Yes (Apache 2.0). Needs Python ≤ 3.13; this project runs 3.15 |
| Chatterbox (Resemble AI) | Free, local | $0 | Very expressive | Yes (MIT). Needs PyTorch (not on 3.15 yet) |
| Microsoft Edge voices (edge-tts) | Free, unofficial | $0 | Good | Risky: not an official licence |
| macOS voices | Free, local | $0 | Good | No: Apple licenses them for personal, non-commercial use |

Samples of the same 20-second script are in `output/voice_samples/`.

## Q.4 — Visuals and sound

- Priority per scene: real photo of a named subject (Wikimedia Commons,
  public domain / CC0 / CC BY / CC BY-SA only) → hand-checked stock footage
  (Pixabay/Pexels licence) → moving gradient. AI images are not used.
- `shots.py` plans a cut at least every 2.8 s, restarting reused clips at new
  points and alternating pan direction.
- New `analogy` template; name cards use real photos when licensed.
- Captions: 1–3 words, phrase breaks, keyword highlight, safe zone.
- Branding: brand bug, colours and fonts from the channel config, end card.
- Sound: hook hit, whooshes, pops (Freesound CC0), ducking music (your own
  tracks in `assets/music/<channel>/` with `credits.yaml`, else Freesound CC0).

## Q.5 — Quality gate

`quality_gate.py` runs after planning, before drawing. Errors (stop): missing
graphic, missing licence/credit, caption outside the safe zone. Warnings: a
shot over 3 s, gradient fallback, loudness off −14 LUFS by > 1 LU or peaks
above −1 dBTP, video > 10% over target length. Saved as `<video>_quality.json`.
On its first real run it stopped a render whose script was 127 s long and
whose captions overflowed — both fixed before any frame was drawn.

## After (draft #6, "What went wrong with air traffic control")

77 s (target 75), −14.0 LUFS, 44.1 kHz stereo, 41 shots (one every 1.9 s),
longest pause 0.22 s, 27 credited assets, gate passed with no warnings.

## Still to do

- Pick the voice (see Q.3). Liam / Matilda read about 3 words/s; Daniel about 2.
- Listen to the CC0 music pick; better: add your own tracks from the YouTube
  Audio Library to `assets/music/news/` with a `credits.yaml`.
- Headline graphics are still built only from the spoken words (an accuracy
  rule), so they can read as fragments. A "key phrase" rule could help.
- Show the quality report and credits on the dashboard's video page.
- More footage: a free `PEXELS_API_KEY` adds a second stock library; DVIDS
  (US military public-domain video) needs a free key.
