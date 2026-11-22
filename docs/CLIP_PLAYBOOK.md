# Clip playbook: what to clip and what to skip

The plain-English rules learned from watching streamer clips on TikTok and
YouTube. Each rule compares accounts' own breakouts with their own flops.
Evidence and numbers: [TIKTOK_CONTENT_RESEARCH.md](TIKTOK_CONTENT_RESEARCH.md),
[TIKTOK_RESEARCH.md](TIKTOK_RESEARCH.md), [VIRAL_CONTENT_RESEARCH.md](VIRAL_CONTENT_RESEARCH.md).
The editor model gets the same rules in its prompt (`src/clips/editor.py`).

## Clip these

1. **Something actually happens, in real life.** A stunt, a prank, a scare,
   police turning up, something breaking, a surprise guest, a gift being
   opened, a drama reveal, an award result. When a face looks shocked, it
   was a real-life event in 42% of breakouts vs 10% of flops.
2. **Let the reaction breathe.** Give the clip seconds where nobody speaks:
   someone dying laughing, screaming, frozen in shock. Flops tended to be more
   wall-to-wall talking (20 of 24 accounts in the first sample), but on 11 new
   accounts this was only a weak tendency. Treat it as a tie-breaker, not a rule.
3. **Laughs with a cause.** Laughing at chaos, at an awkward or embarrassing
   moment, at a reveal. Not polite laughs, not routine banter.
4. **Other real people.** Guests, friends, family, strangers, celebrities.
   Clips with more people on screen lean towards breakouts.
5. **This week's story.** Awards, a new game launch, a feud, a famous guest.
   Post it fast: the first clip of a moment tends to beat later copies
   (61 of 102 same-moment pairs).

6. **Romance, crushes and wholesome moments.** A streamer clearly into
   someone (N3on with Alabama Barker), a crush being turned down, a kind
   gesture (inviting a shy kid for a photo, doing something for mom).
   These were common among the biggest breakouts (20x-2,800x usual).
7. **A running bit with a big reaction.** Jynxzi's "almost almost" near-miss
   clips broke out again and again (381x, 343x, 138x). An in-game moment can
   work when the exaggerated reaction is a recurring bit fans know.

## Skip these

- **In-game hype on its own:** a win, a kill, a near-miss, a buzzer-beater.
  It was 33% of flop shock moments vs 5% of breakout ones. ("gta", "kill",
  "fight", "winner" are flop words.)
- **Explaining:** someone setting up or describing what is about to happen,
  telling a story without anything happening, "basically...".
- **Polite moments:** greetings, thank-yous, small talk, showing a room or an item.
- **A laughing face with no cause.** Streamers smile constantly; that alone
  is not a clip.

## How to cut it

- **Start inside the situation**, not on someone explaining it. Talking
  set-ups were 45% of flop openings vs 17% of breakout openings.
- **Keep the reaction seconds.** Do not trim a loud gap without words; that
  is the laugh or the scream (`trimming.py` now keeps them).
- **Leave pauses in.** Breakouts had more quiet pauses as well as loud ones
  (0.41 vs 0.29 per 30 s; 18 of 24 accounts, and 13 of 20 new accounts). Trimming
  now only shortens silences longer than 2.5 s (`trimming.max_dead_air_seconds`, was 1.4).
- **End about 1 second after the last word**, longer while people are still
  laughing (hits 1.0 s vs flops 0.5 s; `trimming.reaction_tail_seconds`).
- **Length follows the moment:** a single instant can be short; a story needs
  its whole arc (part 1: 90-180 s clips did best for streamer clips,
  10-30 s worst).
- **Format does not matter much.** Each account's hits and flops used the same
  layout, captions, title banner and editing.

## What the tools can and cannot see

| Signal | Tool | Reliable? |
|---|---|---|
| No words for a few seconds | TikTok/Whisper transcript | Yes |
| Loud vs quiet | loudness per second | Yes |
| Laughter / scream sound | CED-tiny sound model (`audio_events.py`) | Misses most laughs under game sound |
| Laughing / shocked face | FER+ (`expressions.py`) | Reads laughs well, but smiles also count and open-mouth talking reads as surprise; use it with "no words" |
| *Why* people react | the editor model reading the transcript | The real judgement; the signals above only point it at the right seconds |
