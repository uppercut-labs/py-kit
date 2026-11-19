# uppercut-contact-sheet

Builds contact sheets with Pillow: a grid of thumbnails, each with a caption, saved as one image or split across numbered sheets. Tiles can come from image files or, when ffmpeg is installed, from frames of a video. It works as a Python module and as a command line tool.

## Install

```sh
pip install uppercut-contact-sheet
```

Requires Python 3.10 or newer and Pillow 10 or newer. Video input needs `ffmpeg` and `ffprobe` on your PATH; everything else works without them.

## Example

```python
from uppercut_contact_sheet import contact_sheet, tiles_from_paths

tiles = tiles_from_paths(["a.png", "b.png", "c.png"])
sheet = contact_sheet(tiles, cols=3, cell=(320, 200), title="My set")
sheet.save("sheet.png")
```

From the command line:

```sh
uppercut-contact-sheet frames/ -o sheet.jpg --cols 4 --cell 320x200
uppercut-contact-sheet --video clip.mp4 --every 5 -o overview.jpg --per-sheet 16
```

With `--per-sheet`, the output name is used as a prefix and the sheets are written as `overview_01.jpg`, `overview_02.jpg`, and so on.

## Module

| Name | What it does |
| --- | --- |
| `Tile(image, label="", error=None)` | One cell. `image=None` draws a MISSING marker, `error` draws that message instead of the picture, and a `label` with newlines gives several caption lines. |
| `contact_sheet(tiles, ...)` | Returns an RGB `PIL.Image` with the tiles in a grid. |
| `save_paged(tiles, out_prefix, per_sheet, *, ext="jpg", quality=88, title=None, **options)` | Writes `<prefix>_01.<ext>`, `<prefix>_02.<ext>` and so on, and returns the paths. `options` go to `contact_sheet`. |
| `save_image(img, path, *, quality=88)` | Saves JPEG with a quality, or any format Pillow infers from the suffix. |
| `tiles_from_paths(paths, labels=None)` | Opens files as tiles, labelled with the file name by default. A file that cannot be read becomes an error tile. |
| `crop_to_bbox(tiles, margin=6)` | Crops all tiles to one shared box around the non-transparent pixels of every tile. |
| `load_font(size, candidates=None)` | Tries your font paths, then common monospace fonts for macOS, Windows and Linux, then Pillow's built-in font. |
| `ffmpeg_available()` | True when `ffmpeg` and `ffprobe` are on PATH. |
| `frame_times(duration, *, every=None, count=None, start=0, end=None)` | Timestamps, one every N seconds or N evenly spaced. |
| `grab_frame(video, t, width=None)` | Decodes one frame as an RGB image. Steps back a few frames if the seek lands past the end. |
| `video_tiles(video, times, *, width=480)` | One tile per timestamp, labelled `m:ss`. |
| `probe_duration(video)`, `parse_time(text)`, `format_time(seconds)` | Helpers for timestamps. |

### contact_sheet options

| Option | Default | Meaning |
| --- | --- | --- |
| `cols` | `4` | Tiles per row. |
| `cell` | `(320, 200)` | Picture area of each tile in pixels. |
| `fit` | `"thumbnail"` | `thumbnail` only shrinks, `contain` scales up or down, `nearest` scales up or down without smoothing, for pixel art. The aspect ratio is always kept and the picture is centred. |
| `bg` | `"#111419"` | Background color. Transparent images are composited onto it. |
| `fg` | `"white"` | Caption and title color. |
| `caption` | `"below"` | `below`, `above` or `none`. The caption sits in its own strip, so it never covers the picture. Long lines are shortened with an ellipsis. |
| `pad` | `8` | Space around and between cells. |
| `font` | auto | A Pillow font. Defaults to `load_font` at a size that suits the cell width. |
| `title` | `None` | Text at the top of the sheet. |
| `border` | `None` | Outline color drawn around each picture area. |
| `error_color` | `"#d24b50"` | Color of MISSING and error messages. |

### Command line options

`inputs` (files or folders), `-o/--out`, `--video`, `--every`, `--count`, `--at`, `--start`, `--end`, `--cols`, `--cell WxH`, `--fit`, `--bg`, `--fg`, `--caption`, `--pad`, `--title`, `--border`, `--font`, `--per-sheet`, `--quality`. The command exits with status 1 and a message on stderr when it cannot build the sheet.

## Errors

- `ValueError` for no tiles, `cols` or `cell` below 1, an unknown `fit` or `caption`, or bad `frame_times` arguments.
- `RuntimeError` when a video function is called without ffmpeg, or a frame cannot be decoded.

## License

MIT
