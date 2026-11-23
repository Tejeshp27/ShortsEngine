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
   was a real-life event in 42% of breakouts vs 10% of flops (first sample;
   on 45 fresh account pairs the type of moment alone did not separate hits
   from flops, see rule 5).
2. **Let the reaction breathe.** Give the clip seconds where nobody speaks:
   someone dying laughing, screaming, frozen in shock. Cutting a laugh ruins the
   moment. But on 56 unseen accounts, loud wordless seconds did not predict views
   (27 vs 28). Among the biggest breakouts there was a little less talking (21 vs 35).
3. **Laughs with a cause.** Laughing at chaos, at an awkward or embarrassing
   moment, at a reveal. Not polite laughs, not routine banter.
4. **Other real people.** Guests, friends, family, strangers, celebrities.
   Clips with more people on screen lean towards breakouts.
5. **This week's story and the people fans follow (the strongest signal).** Awards, a new game launch, a feud, a famous guest.
   Post it fast: the first clip of a moment tends to beat later copies
   (61 of 102 same-moment pairs).

6. **Romance, crushes and wholesome moments.** A streamer clearly into
   someone (N3on with Alabama Barker), a crush being turned down, a kind
   gesture (inviting a shy kid for a photo, doing something for mom).
   These were common among the biggest breakouts (20x-2,800x usual).
7. **Music moments and celebrity guests.** Someone's favourite song comes on
   and they lock in, a freestyle, a "banger", a famous guest walking in
   (Druski meeting Chris Rock, Dave Chappelle and Michael B. Jordan). These were
   among the biggest breakouts on 26 unseen accounts too, and part 1 found
   song/music/dance words had the highest share rates.
   Music clips with no talking are a long shot: about 12% of ordinary clips,
   but 20% of the 100x+ giants. Worth an occasional try, not a default.
8. **A running bit with a big reaction.** Jynxzi's "almost almost" near-miss
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
  set-ups were 45% of flop openings vs 17% of breakout openings in the first
  sample, but not on 45 fresh pairs (27% vs 36%). Keep it as editing sense.
- **Keep the reaction seconds.** Do not trim a loud gap without words; that
  is the laugh or the scream (`trimming.py` now keeps them).
- **Leave pauses in.** Breakouts had more quiet pauses as well as loud ones
  (0.41 vs 0.29 per 30 s; 18 of 24 accounts in the first sample, but not on 40
  unseen accounts: 19 vs 21). Treat it as harmless rather than proven. Trimming
  now only shortens silences longer than 2.5 s (`trimming.max_dead_air_seconds`,
  was 1.4); set it back to 1.4 if clips feel slow.
- **End about 1 second after the last word**, longer while people are still
  laughing (hits 1.0 s vs flops 0.5 s; `trimming.reaction_tail_seconds`).
- **Longer is safer than shorter.** Breakouts ran longer than the same
  account's flops in every sample (40 s vs 35 s on 20 unseen accounts, 15 of
  20). Limits are now 18-45 s for reactions/gameplay, up to 60 s for awkward
  moments and conversations, and up to 90 s for stories (`config/clip_ranking/v2.yaml`,
  `clips.moment_max_seconds: 90`). A single instant can still be short; a
  story needs its whole arc.
- **Format does not matter much.** Each account's hits and flops used the same
  layout, captions, title banner and editing.

## What the tools can and cannot see

| Signal | Tool | Reliable? |
|---|---|---|
| No words for a few seconds | TikTok/Whisper transcript | Yes |
| Loud vs quiet | loudness per second | Yes |
| Laughter / scream sound | CED-tiny sound model (`audio_events.py`) | Misses most laughs under game sound |
| Laughing / shocked face | FER+ (`expressions.py`), shown to the editor as `(face: laughing hard)` / `(face: shocked)` | Reads laughs well at full HD; smiles also count and open-mouth talking reads as surprise, so shocked marks skip talking seconds. On 60 unseen accounts a bigger surprised face went with breakouts (40 vs 20); laughing-face counts did not (30 vs 30) |
| *Why* people react | the editor model reading the transcript | The real judgement; the signals above only point it at the right seconds |
