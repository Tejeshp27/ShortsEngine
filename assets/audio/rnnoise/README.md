<!-- Attribution and reproducibility information for the bundled speech denoiser. -->
# RNNoise standard model

`std.rnnn` is the original Xiph RNNoise model converted to FFmpeg's text format.
It is bundled (302,903 bytes) so clip processing needs no network or model install.
RNNoise is a widely used, small CPU speech denoiser; it avoids a much larger
PyTorch/source-separation dependency on the planned budget host.

- File: [FFmpeg model collection, pinned commit](https://github.com/richardpl/arnndn-models/blob/0fda24c46d78f0207d820bb970fbe85c1971b39c/std.rnnn).
- SHA-256: `6b8943dc4a9b6b24425873992a44f29c0577503276456af46a8854774faeb294`.
- Upstream: [Xiph RNNoise](https://github.com/xiph/rnnoise).
- Original [BSD 3-clause license](https://github.com/xiph/rnnoise/blob/v0.1/COPYING) retained in `COPYING`.

FFmpeg's `arnndn` runs at 48 kHz with a 480-sample delay. The clip audio filter
pads/trims that delay and returns to 44.1 kHz before loudness normalization.
The default channel blend keeps 20% of the original to reduce voice artifacts.
It suppresses non-speech sound; it cannot identify a specific person's voice or
remove another person's/game character's dialogue independently.
