# uppercut-windows-window-capture

Captures one window on Windows with the Win32 `PrintWindow` call and saves it as PNG or BMP. It finds the window by title, class name or process id, and works as a Python module and as a command line tool. It uses only `ctypes` and the standard library, never brings the window to the front, and can take a series of shots to tell whether a window's picture is changing or stuck. The package imports on macOS and Linux, where every call that needs Windows raises `UnsupportedPlatformError`.

```sh
pip install uppercut-windows-window-capture
pip install "uppercut-windows-window-capture[pillow]"   # only needed for Capture.to_pil()
```

```python
from uppercut_windows_window_capture import capture_window, find_window

window = find_window(title="Untitled - Notepad", timeout=10)
capture_window(window).save("notepad.png")
```

```sh
uppercut-window-capture --title "Untitled - Notepad" --out notepad.png
uppercut-window-capture --pid 4242 --client --out client.bmp
uppercut-window-capture --title "My App" --series 10 --interval 1 --out-dir shots
```

## Functions

| Function | Returns |
| --- | --- |
| `list_windows(visible_only=True)` | every top-level window as a `WindowInfo` |
| `find_windows(title, class_name, pid, match, visible_only, min_size)` | the `WindowInfo` list that matches every given filter |
| `find_window(title, class_name, pid, match, prefer_largest, timeout, interval)` | one `WindowInfo`; raises `WindowNotFoundError` after `timeout` seconds |
| `capture_window(window, client_only, fallback, full_content, crop_to_frame, restore)` | a `Capture`; `window` is a `WindowInfo` or a handle |
| `capture_series(window, count, interval, min_changed, **capture_options)` | a `SeriesResult` with the shots, the changed-pixel count of each pair and `live` |
| `watch(grab, count, interval, min_changed)` | the same `SeriesResult`, from any function that returns a `Capture` |
| `enable_dpi_awareness()` | nothing; call it first on scaled displays so sizes are real pixels |
| `match_text(text, pattern, mode)` | whether the text matches. Modes: `substring`, `exact`, `glob`, `regex`, all case-insensitive |
| `select_windows(windows, ...)` | filters a list of `WindowInfo` with the same rules as `find_windows` |
| `frame_crop_box(window_rect, frame_rect)` | the `(left, top, width, height)` of the visible frame |
| `encode_png`, `encode_bmp`, `bgra_to_rgb`, `count_changed_pixels` | helpers on raw top-down BGRA bytes |

`Capture` has `width`, `height`, `bgra`, `save(path, fmt=None)`, `to_bytes(fmt)`, `to_rgb()`, `to_pil()`, `crop(left, top, width, height)`, `changed_pixels(other)` and `is_blank()`.

### Capture options

| Option | Default | Meaning |
| --- | --- | --- |
| `client_only` | `False` | Capture the client area only, without title bar and borders |
| `fallback` | `"bitblt"` | If PrintWindow fails: `"bitblt"` copies from the window's device context, `"screen"` copies the window's rectangle from the screen (anything covering it shows up), `"none"` raises `CaptureFailedError` |
| `full_content` | `True` | Pass `PW_RENDERFULLCONTENT` so GPU-composed windows render |
| `crop_to_frame` | `False` | Trim the invisible resize border that the window rectangle includes on Windows 10 and 11 |
| `restore` | `False` | Show a minimized window again, without activating it, before capturing |

## Command line

`uppercut-window-capture` takes `--title`, `--class` or `--pid` (any combination), plus:

| Option | Meaning |
| --- | --- |
| `--match substring\|exact\|glob\|regex` | How `--title` and `--class` match; default `substring` |
| `--largest` | Use the largest window when several match |
| `--wait SECONDS` | Keep looking for the window |
| `--list` | Print the matching windows and exit |
| `--client`, `--fallback`, `--no-full-content`, `--crop-frame`, `--restore`, `--dpi-aware` | The capture options above |
| `--crop X,Y,W,H` | Keep only this rectangle |
| `--fail-on-blank` | Exit 4 if the capture is one flat colour |
| `--out FILE`, `--format png\|bmp` | Output file; the format comes from the extension unless given. Default `capture.png` |
| `--series N`, `--interval SECONDS`, `--min-changed PIXELS`, `--out-dir DIR` | Take N shots, save them as `cap_00`, `cap_01` and so on, and print `LIVE` or `FROZEN` |

| Exit code | Meaning |
| --- | --- |
| 0 | Captured, or the series is live |
| 2 | No window matched |
| 3 | The series is frozen: no pair of shots differed by `--min-changed` pixels |
| 4 | The capture failed |
| 5 | Not running on Windows |
| 64 | Wrong arguments |

## Errors

All errors derive from `WindowCaptureError`.

| Error | Raised when |
| --- | --- |
| `UnsupportedPlatformError` (also a `RuntimeError`) | A window function is used on a system other than Windows |
| `WindowNotFoundError` (also a `LookupError`) | No window matches, or the handle no longer exists |
| `CaptureFailedError` | Windows returns no pixels, or the window has no visible area |

## Notes

- Some Direct3D and OpenGL windows come back black from `PrintWindow`. Use `Capture.is_blank()` or `--fail-on-blank` to detect it, and try `--fallback screen`.
- Capturing never activates the window or changes its position.

## License

MIT
