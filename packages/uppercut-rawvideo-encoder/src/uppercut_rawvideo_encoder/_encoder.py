from __future__ import annotations

import os
import re
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import IO, Any, Mapping, Sequence, Union

from PIL import Image

FrameRate = Union[int, float, Fraction, str]
FfmpegCommand = Union[str, "os.PathLike[str]", Sequence[str]]

_FRACTION = re.compile(r"^\d+/\d+$")


class EncodeError(RuntimeError):
    """ffmpeg failed. `returncode` is its exit code (None if it was never started), `stderr_tail` the end of its
    error output and `frames` how many frames had been written."""

    def __init__(self, message: str, *, returncode: int | None = None, stderr_tail: str = "", frames: int = 0):
        super().__init__(message)
        self.returncode = returncode
        self.stderr_tail = stderr_tail
        self.frames = frames


def format_fps(fps: FrameRate) -> str:
    """The text ffmpeg gets for `-r`. Whole numbers print without a decimal point, so 60.0 becomes `60`."""
    if isinstance(fps, bool):
        raise TypeError("fps must be a number, a Fraction or 'num/den'")
    if isinstance(fps, str):
        text = fps.strip()
        if _FRACTION.match(text):
            num, den = (int(part) for part in text.split("/"))
            if num > 0 and den > 0:
                return text
        raise ValueError(f"fps must be positive, not {fps!r}")
    if isinstance(fps, Fraction):
        if fps <= 0:
            raise ValueError(f"fps must be positive, not {fps}")
        return str(fps.numerator) if fps.denominator == 1 else f"{fps.numerator}/{fps.denominator}"
    if isinstance(fps, (int, float)):
        if not 0 < fps < float("inf"):
            raise ValueError(f"fps must be positive, not {fps!r}")
        return str(int(fps)) if float(fps).is_integer() else repr(float(fps))
    raise TypeError("fps must be a number, a Fraction or 'num/den'")


def _prefix(ffmpeg: FfmpegCommand) -> list[str]:
    if isinstance(ffmpeg, (str, os.PathLike)):
        return [os.fspath(ffmpeg)]
    return [os.fspath(part) for part in ffmpeg]


def ffmpeg_command(
    path: str | os.PathLike[str],
    size: tuple[int, int],
    fps: FrameRate,
    *,
    crf: int | float | str,
    preset: str,
    extra_inputs: Sequence[str] = (),
    output_args: Sequence[str] = (),
    ffmpeg: FfmpegCommand = "ffmpeg",
) -> list[str]:
    """The argument list `RawVideoEncoder` runs. Always has `-nostdin` and `-y`, reads rgb24 frames from stdin and
    writes libx264 in yuv420p."""
    width, height = size
    if width <= 0 or height <= 0:
        raise ValueError(f"size must be positive, not {size!r}")
    return [
        *_prefix(ffmpeg),
        "-nostdin", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", format_fps(fps), "-i", "-",
        *map(str, extra_inputs),
        "-c:v", "libx264", "-preset", str(preset), "-crf", str(crf), "-pix_fmt", "yuv420p",
        *map(str, output_args),
        os.fspath(path),
    ]


class RawVideoEncoder:
    """Pipes RGB frames to ffmpeg and encodes them as H.264 in yuv420p.

    Frame rate, crf and preset have no defaults: each caller states the quality it wants.

        with RawVideoEncoder("out.mp4", (3840, 2160), 60, crf=16, preset="medium") as enc:
            for frame in frames:
                enc.write(frame)

    Leaving the `with` block normally closes ffmpeg's input and raises `EncodeError` if ffmpeg exits with a nonzero
    code. Leaving it with an exception stops ffmpeg, deletes the partial file and lets your exception through.

    Args:
        path: The output file.
        size: (width, height) of every frame, in pixels.
        fps: Frames per second, as an int, float, Fraction or "num/den". Required.
        crf, preset: The x264 quality settings. Required.
        extra_inputs: ffmpeg arguments for more inputs, placed after the frame input. For a soundtrack:
            `["-ss", "12.5", "-t", "30", "-i", "song.wav"]`, with `-map 0:v -map 1:a -c:a aac` in `output_args`.
        output_args: More ffmpeg output arguments, placed after the x264 settings and before the file name, such as
            `["-threads", "6", "-movflags", "+faststart"]`.
        ffmpeg: The ffmpeg executable, or a command prefix as a list.
        popen_kwargs: Extra keyword arguments for `subprocess.Popen`, such as `cwd`, `env` or Windows `creationflags`
            (to lower ffmpeg's priority or hide its window). `stdin`, `stdout` and `stderr` belong to the encoder and
            raise `ValueError`.
        tail_bytes: How much of ffmpeg's error output an `EncodeError` carries.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        size: tuple[int, int],
        fps: FrameRate,
        *,
        crf: int | float | str,
        preset: str,
        extra_inputs: Sequence[str] = (),
        output_args: Sequence[str] = (),
        ffmpeg: FfmpegCommand = "ffmpeg",
        tail_bytes: int = 4096,
        popen_kwargs: Mapping[str, Any] | None = None,
    ):
        extra = dict(popen_kwargs or {})
        reserved = sorted(set(extra) & {"stdin", "stdout", "stderr"})
        if reserved:
            raise ValueError(f"popen_kwargs cannot set {', '.join(reserved)}: the encoder owns the pipes")
        self.path = Path(path)
        self.size = (int(size[0]), int(size[1]))
        self.frames = 0
        self._frame_bytes = self.size[0] * self.size[1] * 3
        self._tail_bytes = tail_bytes
        self._done = False
        self.command = ffmpeg_command(
            self.path, self.size, fps, crf=crf, preset=preset,
            extra_inputs=extra_inputs, output_args=output_args, ffmpeg=ffmpeg,
        )
        self._stderr: IO[bytes] = tempfile.TemporaryFile()
        try:
            self._proc = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self._stderr, **extra,
            )
        except OSError as exc:
            self._stderr.close()
            self._done = True
            raise EncodeError(f"could not start {self.command[0]!r}: {exc}") from exc

    def _tail(self) -> str:
        self._stderr.flush()
        size = self._stderr.seek(0, os.SEEK_END)
        self._stderr.seek(max(0, size - self._tail_bytes))
        return self._stderr.read().decode("utf-8", "replace").strip()

    def _remove_output(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass

    def _fail(self, what: str, cause: BaseException | None = None) -> EncodeError:
        """Reap ffmpeg and build the error. Deletes the partial file."""
        try:
            self._proc.stdin.close()  # type: ignore[union-attr]
        except OSError:
            pass
        returncode = self._proc.wait()
        tail = self._tail()
        self._finish()
        self._remove_output()
        message = f"{what}: ffmpeg exited with code {returncode} after {self.frames} frame(s) for {self.path}"
        if tail:
            message += f"\n{tail}"
        error = EncodeError(message, returncode=returncode, stderr_tail=tail, frames=self.frames)
        error.__cause__ = cause
        return error

    def _finish(self) -> None:
        self._done = True
        self._stderr.close()

    def _as_bytes(self, frame: Any) -> Any:
        if isinstance(frame, Image.Image):
            if frame.size != self.size:
                raise ValueError(f"frame is {frame.size[0]}x{frame.size[1]}, the video is {self.size[0]}x{self.size[1]}")
            if frame.mode != "RGB":
                frame = frame.convert("RGB")
            return frame.tobytes()
        if not isinstance(frame, (bytes, bytearray, memoryview)):
            frame = frame.tobytes()  # numpy arrays and anything else that dumps its pixels
        if len(frame) != self._frame_bytes:
            raise ValueError(f"frame is {len(frame)} bytes, expected {self._frame_bytes} (rgb24, {self.size[0]}x{self.size[1]})")
        return frame

    def write(self, frame: Any) -> None:
        """Add one frame: a Pillow image of the video's size (converted to RGB if needed), or exactly
        width x height x 3 bytes of RGB data, as bytes or as an object with `tobytes()` such as a numpy array."""
        if self._done:
            raise ValueError("the encoder is closed")
        data = self._as_bytes(frame)
        try:
            self._proc.stdin.write(data)  # type: ignore[union-attr]
        except OSError as exc:  # ffmpeg quit early: broken pipe, or EINVAL on Windows
            raise self._fail("ffmpeg stopped reading frames", exc) from exc
        self.frames += 1

    def close(self) -> None:
        """Finish the file. Raises `EncodeError` if ffmpeg exits nonzero or no frame was written."""
        if self._done:
            return
        try:
            self._proc.stdin.close()  # type: ignore[union-attr]
        except OSError as exc:
            raise self._fail("ffmpeg stopped reading frames", exc) from exc
        returncode = self._proc.wait()
        if returncode != 0 or self.frames == 0:
            what = "ffmpeg failed" if returncode != 0 else "no frames were written"
            raise self._fail(what)
        self._finish()

    def abort(self) -> None:
        """Stop ffmpeg now and delete the partial file."""
        if self._done:
            return
        try:
            self._proc.stdin.close()  # type: ignore[union-attr]
        except OSError:
            pass
        if self._proc.poll() is None:
            self._proc.kill()
        self._proc.wait()
        self._finish()
        self._remove_output()

    def __enter__(self) -> "RawVideoEncoder":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is not None:
            self.abort()
        else:
            self.close()
