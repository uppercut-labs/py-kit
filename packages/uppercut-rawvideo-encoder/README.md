# uppercut-rawvideo-encoder

Pipes RGB frames from a Pillow (or numpy) render into ffmpeg, which writes H.264 in yuv420p. It checks ffmpeg's exit code and raises with the end of ffmpeg's error output, so a failed encode is an exception instead of the path of a file that was never written. It also carries the two helpers render scripts keep copying: `nearest()` for whole-number pixel upscales and a cached `font()` loader.

## Install

```sh
pip install uppercut-rawvideo-encoder
```

Requires Python 3.10 or newer and Pillow 10 or newer. `ffmpeg` with libx264 must be on your PATH.

## Example

```python
from PIL import Image, ImageDraw
from uppercut_rawvideo_encoder import RawVideoEncoder, font, nearest

W, H = 1920, 1080
label = font("C:/Windows/Fonts/consolab.ttf", 48)  # loaded once, shared after that

with RawVideoEncoder("out.mp4", (W, H), 60, crf=16, preset="medium") as enc:
    for i in range(120):
        sprite = nearest(Image.open("sprite.png").convert("RGB"), 4)
        frame = Image.new("RGB", (W, H))
        frame.paste(sprite, (40 + i * 4, 200))
        ImageDraw.Draw(frame).text((40, 40), f"frame {i}", font=label, fill="white")
        enc.write(frame)
```

Leaving the `with` block normally finishes the file and raises `EncodeError` if ffmpeg exits with a nonzero code, or if no frame was written. If your own code raises inside the block, ffmpeg is stopped, the partial file is deleted and your exception goes through unchanged.

`fps`, `crf` and `preset` have no defaults. Each caller says what it wants, so changing to this package never changes a render's quality, and a video never gets a frame rate nobody chose.

## RawVideoEncoder

`RawVideoEncoder(path, size, fps, *, crf, preset, extra_inputs=(), output_args=(), ffmpeg="ffmpeg", tail_bytes=4096)`

| Argument | Meaning |
| --- | --- |
| `path` | The output file. Its folder must exist. |
| `size` | `(width, height)` of every frame. |
| `fps` | Frames per second as an int, float, `Fraction` or `"num/den"` such as `"24000/1001"`. Required. |
| `crf`, `preset` | The x264 settings. Required. |
| `extra_inputs` | ffmpeg arguments for more inputs, placed after the frame input. |
| `output_args` | More ffmpeg output arguments, placed after the x264 settings and before the file name. |
| `ffmpeg` | The executable, or a command prefix as a list. |
| `tail_bytes` | How much of ffmpeg's error output goes into `EncodeError`. |

| Member | What it does |
| --- | --- |
| `write(frame)` | Adds a frame: a Pillow image of the video's size (converted to RGB when it is not), or exactly width x height x 3 bytes, as `bytes` or any object with `tobytes()` such as a numpy array. A wrong size raises `ValueError`. |
| `close()` | Finishes the file. Raises `EncodeError` on a nonzero exit or when nothing was written. |
| `abort()` | Stops ffmpeg and deletes the partial file. |
| `frames` | How many frames have been written. |
| `command` | The ffmpeg argument list that was run. |

The command always carries `-nostdin`, so ffmpeg never reads keystrokes from the terminal, and `-y`, so an existing file is replaced. It reads `rgb24` from stdin at the rate you gave and writes `libx264` with `-pix_fmt yuv420p`.

`EncodeError` has `returncode`, `stderr_tail` (the end of ffmpeg's error output) and `frames`. If ffmpeg dies while you are still writing, the next `write` raises it, instead of a bare `BrokenPipeError`. A failed encode deletes its output file.

`ffmpeg_command(path, size, fps, *, crf, preset, extra_inputs=(), output_args=(), ffmpeg="ffmpeg")` returns the argument list without running it. `format_fps(fps)` is the text that goes after `-r`.

### A soundtrack

```python
with RawVideoEncoder(
    "cut.mp4", (1920, 1080), 24, crf=14, preset="slow",
    extra_inputs=["-ss", "12.5", "-t", "30", "-i", "song.wav"],
    output_args=["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "256k", "-shortest"],
) as enc:
    ...
```

## Pixel helpers

| Function | What it does |
| --- | --- |
| `nearest(img, n)` | Scales an image up by the whole number `n` with no smoothing. |
| `font(path, size, *, index=0)` | `ImageFont.truetype`, cached by file, size and index. `font.cache_clear()` empties the cache. `index` picks a face inside a `.ttc` collection. |

## License

MIT
