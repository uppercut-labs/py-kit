import os
import subprocess
import sys

import pytest

import uppercut_windows_window_capture as wc
from uppercut_windows_window_capture import cli

ON_WINDOWS = sys.platform == "win32"
not_windows = pytest.mark.skipif(ON_WINDOWS, reason="checks the error raised on other systems")
windows_only = pytest.mark.skipif(not ON_WINDOWS, reason="needs Windows")


# --- every platform -----------------------------------------------------------------------------------------


def test_package_imports_without_win32():
    assert wc.__version__
    assert "_win32" not in sys.modules or ON_WINDOWS


def test_parse_crop():
    assert cli.parse_crop("1,2,30,40") == (1, 2, 30, 40)
    for bad in ("1,2,3", "a,b,c,d", "0,0,0,5", "0,0,5,-1"):
        with pytest.raises(ValueError):
            cli.parse_crop(bad)


def test_cli_usage_errors_exit_64(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main([])
    assert info.value.code == cli.EXIT_USAGE
    with pytest.raises(SystemExit) as info:
        cli.main(["--title", "x", "--series", "1"])
    assert info.value.code == cli.EXIT_USAGE
    with pytest.raises(SystemExit) as info:
        cli.main(["--title", "x", "--crop", "oops"])
    assert info.value.code == cli.EXIT_USAGE
    with pytest.raises(SystemExit) as info:
        cli.main(["--title", "x", "--match", "fuzzy"])
    assert info.value.code == cli.EXIT_USAGE


def test_find_windows_needs_a_filter_before_touching_the_platform():
    with pytest.raises(ValueError):
        wc.find_windows()


def test_capture_rejects_unknown_fallback_before_touching_the_platform():
    with pytest.raises(ValueError):
        wc.capture_window(1, fallback="magic")


# --- other platforms ----------------------------------------------------------------------------------------


@not_windows
def test_use_off_windows_raises_a_clear_error():
    with pytest.raises(wc.UnsupportedPlatformError) as info:
        wc.find_window(title="anything")
    assert "Windows" in str(info.value)
    assert isinstance(info.value, RuntimeError)
    with pytest.raises(wc.UnsupportedPlatformError):
        wc.list_windows()
    with pytest.raises(wc.UnsupportedPlatformError):
        wc.capture_window(1234)
    with pytest.raises(wc.UnsupportedPlatformError):
        wc.enable_dpi_awareness()


@not_windows
def test_cli_off_windows_exits_5(capsys):
    assert cli.main(["--title", "anything"]) == cli.EXIT_UNSUPPORTED
    assert "Windows" in capsys.readouterr().err
    assert cli.main(["--pid", "1", "--list"]) == cli.EXIT_UNSUPPORTED


@not_windows
def test_module_entry_point_exits_5():
    done = subprocess.run(
        [sys.executable, "-m", "uppercut_windows_window_capture", "--title", "x"], capture_output=True, text=True
    )
    assert done.returncode == cli.EXIT_UNSUPPORTED


# --- Windows only -------------------------------------------------------------------------------------------


@windows_only
def test_find_nothing_raises_not_found():
    with pytest.raises(wc.WindowNotFoundError):
        wc.find_window(title="no window has this title 8f3a2c91")


@windows_only
def test_cli_reports_not_found(capsys):
    assert cli.main(["--title", "no window has this title 8f3a2c91"]) == cli.EXIT_NOT_FOUND


@windows_only
def test_list_windows_returns_records():
    windows = wc.list_windows(visible_only=False)
    assert windows and all(isinstance(w, wc.WindowInfo) for w in windows)


@windows_only
@pytest.mark.skipif(not os.environ.get("WINDOW_CAPTURE_LIVE_TEST"), reason="set WINDOW_CAPTURE_LIVE_TEST=1 on a desktop session")
def test_capture_a_real_window_to_png(tmp_path):
    proc = subprocess.Popen(["notepad.exe"])
    try:
        window = wc.find_window(pid=proc.pid, timeout=10, prefer_largest=True)
        shot = wc.capture_window(window)
        path = shot.save(tmp_path / "notepad.png")
        assert path.read_bytes()[:4] == b"\x89PNG"
        assert shot.width > 0 and shot.height > 0
    finally:
        proc.kill()
