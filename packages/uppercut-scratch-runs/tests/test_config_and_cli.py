import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from uppercut_scratch_runs import ConfigError, ScratchConfig, find_config, load_config, platform_keys
from uppercut_scratch_runs.cli import main

WINDOWS = os.name == "nt"


def cli(*args, cwd=None):
    return subprocess.run([sys.executable, "-m", "uppercut_scratch_runs", *map(str, args)],
                          capture_output=True, text=True, cwd=cwd)


def write_pyproject(directory, root, large=None, extra=""):
    roots = [root.as_posix()] + ([large.as_posix()] if large else [])
    listed = ", ".join(f'"{value}"' for value in roots)
    path = directory / "pyproject.toml"
    path.write_text(
        "[project]\nname = \"consumer\"\n\n"
        "[tool.uppercut-scratch-runs]\n"
        "project = \"example\"\n"
        f"{extra}"
        "[tool.uppercut-scratch-runs.roots]\n"
        f"default = [{listed}]\n",
        encoding="utf-8")
    return path


def test_platform_keys_end_with_default():
    keys = platform_keys()
    assert keys[-1] == "default"
    if WINDOWS:
        assert keys == ("windows", "default")
    elif sys.platform == "darwin":
        assert keys == ("macos", "default")


def test_from_mapping_derives_the_marker_from_the_project():
    config = ScratchConfig.from_mapping({"project": "floodgun", "roots": {"linux": "/tmp/x"}})
    assert config.marker == ".floodgun-run"
    assert config.variable == "SCRATCH_RUN"
    assert config.platform_roots(["linux"]) == (Path("/tmp/x"),)


def test_platform_roots_prefer_the_most_specific_key():
    config = ScratchConfig.from_mapping({"roots": {
        "windows": ["C:/scratch/a", "E:/big/a"],
        "wsl": "/scratch/a",
        "linux": "/srv/scratch/a",
        "default": "~/scratch/a",
    }})
    assert config.platform_roots(["windows", "default"]) == (Path("C:/scratch/a"), Path("E:/big/a"))
    assert config.platform_roots(["wsl", "linux", "default"]) == (Path("/scratch/a"),)
    assert config.platform_roots(["linux", "default"]) == (Path("/srv/scratch/a"),)
    assert config.platform_roots(["macos", "default"]) == (Path.home() / "scratch" / "a",)


@pytest.mark.parametrize("table", [
    {"roots": {"freebsd": "/x"}},
    {"roots": {"default": "relative/path"}},
    {"roots": {"default": []}},
    {"roots": "/x"},
    {"colour": "blue"},
    {"variable": "1BAD"},
    {"marker": "../escape"},
    {"project": "has space"},
])
def test_invalid_settings_are_refused(table):
    with pytest.raises(ConfigError):
        ScratchConfig.from_mapping(table)


def test_no_roots_for_the_platform_is_an_error():
    with pytest.raises(ConfigError, match="roots.macos"):
        ScratchConfig.from_mapping({"roots": {"windows": "C:/x"}}).platform_roots(["macos"])


def test_session_and_large_root(tmp_path):
    config = ScratchConfig(roots={"default": (tmp_path / "small", tmp_path / "large")}, marker=".t-run")
    with config.session(large=True) as run:
        assert run.parent == tmp_path / "large"
        (run / "work").write_text("x")
    assert not run.exists()
    with pytest.raises(RuntimeError):
        with config.session() as run:
            assert run.parent == tmp_path / "small"
            raise RuntimeError("job failed")
    assert not run.exists()


def test_run_method_exports_and_cleans(tmp_path):
    config = ScratchConfig(roots={"default": (tmp_path / "runs",)}, variable="LAB_SCRATCH")
    script = "import os;print(os.environ['LAB_SCRATCH']);print(os.environ['TMP'])"
    result = config.run([sys.executable, "-c", script], capture_output=True, text=True)
    first, second = result.stdout.split()
    assert first == second
    assert Path(first).parent == tmp_path / "runs"
    assert not Path(first).exists()


def test_find_and_load_config_from_a_subdirectory(tmp_path):
    write_pyproject(tmp_path, tmp_path / "runs", extra='variable = "LAB_SCRATCH"\n')
    inner = tmp_path / "platforms" / "n64"
    inner.mkdir(parents=True)
    assert find_config(inner) == tmp_path / "pyproject.toml"
    config = load_config(start=inner)
    assert config.marker == ".example-run"
    assert config.variable == "LAB_SCRATCH"
    assert config.platform_roots() == (tmp_path / "runs",)


def test_a_pyproject_without_the_table_is_skipped(tmp_path):
    write_pyproject(tmp_path, tmp_path / "runs")
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "pyproject.toml").write_text("[project]\nname = \"other\"\n")
    assert find_config(inner) == tmp_path / "pyproject.toml"
    with pytest.raises(ConfigError):
        load_config(inner / "pyproject.toml")


def test_cli_command_status_and_automatic_cleanup(tmp_path):
    root = tmp_path / "runs"
    for status in (0, 7):
        script = (
            "import os,sys;from pathlib import Path;"
            "p=Path(os.environ['LAB_SCRATCH']);"
            "assert all(Path(os.environ[k])==p for k in ('TMP','TEMP','TMPDIR'));"
            "(p/'nested').mkdir();(p/'nested/file').touch();"
            f"print(p);sys.exit({status})"
        )
        result = cli("run", "--root", root, "--variable", "LAB_SCRATCH", "--", sys.executable, "-c", script)
        assert result.returncode == status, result.stderr
        path = Path(result.stdout.strip())
        assert path.parent == root
        assert not path.exists()
        assert f"Scratch: {path}" in result.stderr


@pytest.mark.skipif(WINDOWS, reason="POSIX signals")
def test_cli_reports_a_signal_like_a_shell(tmp_path):
    script = "import os,signal;os.kill(os.getpid(),signal.SIGTERM)"
    result = cli("run", "--root", tmp_path / "runs", "--", sys.executable, "-c", script)
    assert result.returncode == 128 + signal.SIGTERM
    assert list((tmp_path / "runs").iterdir()) == []


def test_cli_new_and_clean_with_a_pyproject(tmp_path):
    config = write_pyproject(tmp_path, tmp_path / "runs", tmp_path / "large")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    made = cli("new", "--config", config, cwd=elsewhere)
    assert made.returncode == 0, made.stderr
    run = Path(made.stdout.strip())
    assert run.parent == tmp_path / "runs"
    assert (run / ".example-run").is_file()
    large = cli("new", "--large", cwd=tmp_path)  # Found by searching upward from the working directory.
    assert large.returncode == 0, large.stderr
    big = Path(large.stdout.strip())
    assert big.parent == tmp_path / "large"
    for path in (run, big):
        removed = cli("clean", "--config", config, path)
        assert removed.returncode == 0, removed.stderr
        assert not path.exists()


def test_cli_refuses_and_explains(tmp_path, capsys):
    config = write_pyproject(tmp_path, tmp_path / "runs")
    assert main(["clean", "--config", str(config), str(tmp_path / "runs")]) == 1
    assert "Expected the exact path returned by new" in capsys.readouterr().err
    empty = tmp_path / "empty"
    empty.mkdir()
    result = cli("new", cwd=empty)
    if find_config(empty) is None:
        assert result.returncode == 1
        assert "No scratch roots" in result.stderr


def test_cli_run_requires_a_command(tmp_path):
    with pytest.raises(SystemExit) as exit_info:
        main(["run", "--root", str(tmp_path / "runs"), "--"])
    assert exit_info.value.code == 2


def test_cli_overrides_the_file(tmp_path):
    config = write_pyproject(tmp_path, tmp_path / "runs", extra='marker = ".from-file"\n')
    other = tmp_path / "other"
    made = cli("new", "--config", config, "--root", other, "--marker", ".from-cli")
    assert made.returncode == 0, made.stderr
    run = Path(made.stdout.strip())
    assert run.parent == other
    assert (run / ".from-cli").is_file()
    # Cleaning with the file's settings refuses: the root and the marker differ.
    assert cli("clean", "--config", config, run).returncode == 1
    assert cli("clean", "--config", config, "--root", other, "--marker", ".from-cli", run).returncode == 0
    assert not run.exists()
