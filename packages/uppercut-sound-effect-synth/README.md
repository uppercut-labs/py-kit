# uppercut-sound-effect-synth

Procedural sound effects that render the same way every time. It provides envelopes, band-limited noise, zero-phase and sweeping filters, pitch sweeps, soft saturation, a seeded random source, 8/16/24-bit WAV writing, thirteen ready-made sounds (whoosh, riser, impact, click, ding and more) and a pack renderer that writes a manifest of SHA-256 hashes. The same seed, sound name and take give identical samples and an identical WAV file on the same NumPy version and platform. The only dependency is NumPy.

## Install

```sh
pip install uppercut-sound-effect-synth
```

## Example

```python
from uppercut_sound_effect_synth import render, write_wav, render_pack

audio = render("impact", seed=7)            # (n, 2) float array, peak -3 dBFS
write_wav("impact.wav", audio)              # returns the file's SHA-256

render_pack("sfx", ["whoosh", "click"], seed=7, bits=16)   # also writes sfx/manifest.json
```

Your own recipe is a function `(rng, rate) -> 1-D array`:

```python
from uppercut_sound_effect_synth import render, tone, envelope

beep = {"beep": lambda rng, rate: tone(1000, rate // 5, rate) * envelope(rate // 5, 0.002, 2.0, rate)}
audio = render("beep", sounds=beep, seed=1)
```

Command line:

```sh
uppercut-sound-effect-synth --list
uppercut-sound-effect-synth --out sfx --only whoosh,click --seed 7 --bits 16 --preview
```

## Functions

| Function | What it does |
| --- | --- |
| `render(name, seed=7, take=0, sounds=None, rate=48000, peak_db=-3.0, tail=0.05, width=0.15, channels=2)` | Renders one sound. The generator is seeded from `seed`, `name` and `take`, so a new `take` is a new variation. |
| `render_pack(out, names=None, seed=7, take=0, sounds=None, rate, bits=24, channels=2, peak_db, preview=False)` | Writes WAV files and `manifest.json`; returns the manifest. |
| `write_wav(path, x, rate=48000, bits=24)` / `wav_bytes(...)` / `pcm_bytes(x, bits)` | WAV output. `write_wav` returns the SHA-256. |
| `Rng(seed)`, `derive_seed(seed, *labels)` | Seeded random source: `unit`, `uniform`, `normal`, `white`, `choice`. |
| `envelope(n, attack, curve, rate)`, `exp_decay(n, time_constant, attack, rate)`, `fade(x, fade_in, fade_out, rate)` | Amplitude shapes. |
| `lowpass`, `highpass`, `bandpass`, `band_noise(rng, n, low, high)`, `sweep_lowpass(x, f0, f1)` | Filters; Butterworth magnitude response, applied zero-phase. |
| `tone(freq, n, rate, phase)`, `sweep(f0, f1, n, log=True)` | Sine oscillator with a constant or per-sample frequency; frequency curves. |
| `saturate(x, drive)`, `mix_into(buf, x, at, gain, rate)`, `finish(x, peak_db, tail, width, channels, rng, rate)`, `seconds(duration, rate)` | Saturation, mixing, final level and stereo width, sample counts. |

Built-in names: `whoosh`, `whoosh-short`, `riser`, `impact`, `sub-drop`, `click`, `tick`, `pop`, `shimmer`, `glitch`, `type`, `ding`, `swell`.

## Errors

| Error | Raised when |
| --- | --- |
| `KeyError` | A sound name is not in the table. |
| `ValueError` | A cutoff is outside 0 to Nyquist, `bits` is not 8, 16 or 24, `channels` is not 1 or 2, or an argument is out of range. |

## License

MIT
