# uppercut-ffmpeg-loudness

Measures integrated loudness (LUFS), loudness range and true peak of an audio or video file with ffmpeg's `ebur128` filter, plans a gain against a loudness target and a true-peak ceiling, and writes a normalized copy. It uses only the Python standard library and an `ffmpeg` binary on `PATH`. Parsing of ffmpeg output and filter-string building are pure functions you can test without ffmpeg.

## Install

```sh
pip install uppercut-ffmpeg-loudness
```

## Example

```python
from uppercut_ffmpeg_loudness import measure, normalize

before = measure("mix.wav")
print(before.integrated, before.true_peak)

result = normalize("mix.wav", "mix-14.wav", target_lufs=-14.0, true_peak=-1.0)
print(result.gain_db, result.limited_by, result.reached_target)
```

Command line:

```sh
uppercut-ffmpeg-loudness a.wav b.mp4 --target -14 --tp -1
uppercut-ffmpeg-loudness a.wav --normalize out.wav --target -16 --mode loudnorm
```

Measuring exits with 1 when a file is more than 1 LU from `--target` or its true peak is above `--tp`.

## Functions

| Function | What it does |
| --- | --- |
| `measure(path, audio_stream=0)` | Returns `Measure(integrated, true_peak, lra)`. Silence reads as `-inf`. |
| `measure_loudnorm(path, target_lufs, true_peak, lra)` | First `loudnorm` pass; returns the parsed JSON values. |
| `parse_ebur128_summary(log)` | Parses the final `Summary:` block of an `ebur128` run. |
| `parse_loudnorm_json(log)` | Parses the JSON block printed by `loudnorm=print_format=json`. |
| `plan_gain(measure, target_lufs, true_peak_ceiling, headroom_db=0.0, max_gain_db=None)` | Returns `(gain_db, limited_by)`; the smallest of the gain to the target, the gain to the peak ceiling minus headroom, and the cap. `limited_by` is `"target"`, `"peak"` or `"cap"`. |
| `common_target(measures, target_lufs, true_peak_ceiling)` | The loudness a batch of clips can share without any clip exceeding the peak ceiling. |
| `apply_gain(src, dst, gain_db, extra_filters=(), copy_video=True, codec=None, bitrate=192000, rate=48000)` | Writes `dst` with a gain applied. Video is copied. The codec follows the output suffix unless given. |
| `normalize(src, dst, target_lufs=-14.0, true_peak=-1.0, mode="gain", headroom_db=0.5, max_gain_db=12.0, retries=3, ...)` | Normalizes to the target and re-measures the output. If the true peak is still over the ceiling, it lowers the gain and encodes again, up to `retries` times. Returns `NormalizeResult(gain_db, limited_by, before, after, attempts, reached_target)`. |
| `loudnorm_filter(...)`, `limiter_filter(limit_dbfs, sample_rate=48000, oversample=4)`, `volume_filter(gain_db)` | Build filter strings. |

`mode="gain"` applies one linear gain. `mode="loudnorm"` runs two-pass `loudnorm` in linear mode, then trims any remaining peak overshoot.

## Errors

| Error | Raised when |
| --- | --- |
| `FfmpegNotFoundError` | `ffmpeg` is not on `PATH`. |
| `LoudnessError` | ffmpeg fails, the output cannot be parsed, the input is silent, `dst` equals `src`, or the true peak stays over the ceiling after all retries. |

## License

MIT
