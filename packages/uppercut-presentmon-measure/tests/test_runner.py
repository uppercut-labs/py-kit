import json
import sys
import textwrap

import pytest

from uppercut_presentmon_measure import (
    Scenario,
    build_presentmon_command,
    resolve_presentmon,
    run_scenario,
)
from uppercut_presentmon_measure.cli import main


def test_build_command_by_pid_and_name():
    cmd = build_presentmon_command("pm", "out.csv", 7, pid=42, extra_args=["--no_console_stats"])
    assert cmd == ["pm", "--process_id", "42", "--output_file", "out.csv", "--timed", "7", "--no_console_stats"]
    cmd = build_presentmon_command(["py", "tool.py"], "o.csv", 2.5, process_name="my-app.exe")
    assert cmd[:4] == ["py", "tool.py", "--process_name", "my-app.exe"]
    assert cmd[-2:] == ["--timed", "2.5"]


def test_build_command_errors():
    with pytest.raises(ValueError):
        build_presentmon_command("pm", "o.csv", 5)
    with pytest.raises(ValueError):
        build_presentmon_command("pm", "o.csv", 5, pid=1, process_name="a")
    with pytest.raises(ValueError):
        build_presentmon_command("pm", "o.csv", 0, pid=1)


def test_resolve_presentmon_order(tmp_path):
    option = tmp_path / "from-option"
    option.write_text("")
    via_env = tmp_path / "from-env"
    via_env.write_text("")
    assert resolve_presentmon(str(option), {"PRESENTMON_CLI": str(via_env)}) == str(option)
    assert resolve_presentmon(None, {"PRESENTMON_CLI": str(via_env)}) == str(via_env)


def test_resolve_presentmon_errors(tmp_path):
    with pytest.raises(FileNotFoundError, match="option"):
        resolve_presentmon(str(tmp_path / "missing"), {})
    with pytest.raises(FileNotFoundError, match="PRESENTMON_CLI"):
        resolve_presentmon(None, {"PATH": str(tmp_path)})


@pytest.mark.skipif(sys.platform == "win32", reason="checks the non-Windows refusal")
def test_run_scenario_refuses_off_windows(tmp_path):
    scenario = Scenario(name="s", kind="idle", command=["x"])
    with pytest.raises(RuntimeError, match="Windows"):
        run_scenario(scenario, "pm", tmp_path)
    assert main(["run", "--kind", "idle", "--", "x"]) == 2


APP = textwrap.dedent(
    """
    import json, sys, time
    json.dump({"marks": {"window": time.time() * 1000 + 50, "ready": time.time() * 1000 + 120}}, open(sys.argv[1], "w"))
    time.sleep(60)
    """
)

FAKE_PRESENTMON = textwrap.dedent(
    """
    import sys
    args = sys.argv[1:]
    out = args[args.index("--output_file") + 1]
    open(out, "w").write("Application,FrameTime,Dropped\\n" + "".join("app.exe,16.7,0\\n" for _ in range(9)) + "app.exe,40.0,1\\n")
    """
)


@pytest.fixture
def tools(tmp_path):
    app = tmp_path / "app.py"
    app.write_text(APP)
    pm = tmp_path / "fake_presentmon.py"
    pm.write_text(FAKE_PRESENTMON)
    return app, [sys.executable, str(pm)], tmp_path / "marks.json"


def test_startup_scenario_with_stand_in_app(tools, tmp_path):
    app, _, marks = tools
    scenario = Scenario(
        name="start", kind="startup", command=[sys.executable, str(app), str(marks)],
        runs=2, marks_file=str(marks), marks_timeout_seconds=20,
    )
    result = run_scenario(scenario, None, tmp_path / "out", allow_any_platform=True)
    assert len(result["runs"]) == 2
    assert result["marks"]["ready"]["failedRuns"] == 0
    assert result["marks"]["window"]["medianMs"] > 0


def test_startup_scenario_records_failed_runs(tmp_path):
    scenario = Scenario(
        name="start", kind="startup", command=[sys.executable, "-c", "pass"],
        runs=1, marks_file=str(tmp_path / "never.json"), marks_timeout_seconds=0.3,
    )
    result = run_scenario(scenario, None, tmp_path / "out", allow_any_platform=True)
    assert result["runs"] == [{}]
    assert result["marks"] == {}


def test_idle_and_workload_scenarios_with_stand_in_tools(tools, tmp_path):
    app, pm, marks = tools
    workload = [sys.executable, "-c", "import sys; sys.exit(0)"]
    scenario = Scenario(
        name="scroll", kind="workload", command=[sys.executable, str(app), str(marks)],
        warmup_seconds=0.5, capture_seconds=1, workload_command=workload,
        marks_file=str(marks), refresh_hz=60,
    )
    result = run_scenario(scenario, pm, tmp_path / "out", allow_any_platform=True)
    assert result["frames"]["frames"] == 10
    assert result["frames"]["droppedFrames"] == 1
    assert result["frames"]["missedRefreshFrames"] == 1
    assert result["workloadExitCode"] == 0
    assert "ready" in result["marksMs"]
    assert (tmp_path / "out" / "scroll.csv").exists()


SUFFIXING_PRESENTMON = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    args = sys.argv[1:]
    out = Path(args[args.index("--output_file") + 1])
    named = out.with_name(out.stem + "-app.exe[4242]-261119T210000" + out.suffix)
    named.write_text("Application,FrameTime,Dropped\\n" + "".join("app.exe,8.3,0\\n" for _ in range(6)))
    """
)


def test_capture_finds_the_suffixed_csv_the_intel_cli_writes(tools, tmp_path):
    app, _, marks = tools
    pm = tmp_path / "suffixing_presentmon.py"
    pm.write_text(SUFFIXING_PRESENTMON)
    scenario = Scenario(
        name="idle", kind="idle", command=[sys.executable, str(app), str(marks)],
        warmup_seconds=0.3, capture_seconds=1, refresh_hz=120,
    )
    result = run_scenario(scenario, [sys.executable, str(pm)], tmp_path / "out", allow_any_platform=True)
    assert result["frames"]["frames"] == 6
    assert result["csv"].endswith("idle-app.exe[4242]-261119T210000.csv")


def test_find_capture_csv_ignores_older_files(tmp_path):
    from uppercut_presentmon_measure import find_capture_csv
    old = tmp_path / "run-a.exe[1]-1.csv"
    old.write_text("x")
    import os
    os.utime(old, (1000, 1000))
    assert find_capture_csv(tmp_path / "run.csv", since=2000) is None
    assert find_capture_csv(tmp_path / "run.csv") == old
    exact = tmp_path / "run.csv"
    exact.write_text("y")
    assert find_capture_csv(exact, since=2000) == exact


def test_capture_without_a_csv_reports_empty_capture(tools, tmp_path):
    app, _, marks = tools
    silent = [sys.executable, "-c", "pass"]
    scenario = Scenario(
        name="quiet", kind="idle", command=[sys.executable, str(app), str(marks)],
        warmup_seconds=0.3, capture_seconds=1,
    )
    result = run_scenario(scenario, silent, tmp_path / "out", allow_any_platform=True)
    assert result["frames"] == {"frames": 0}
    assert "no frames" in result["note"]


def test_idle_needs_presentmon(tmp_path):
    scenario = Scenario(name="i", kind="idle", command=["x"])
    with pytest.raises(ValueError, match="PresentMon"):
        run_scenario(scenario, None, tmp_path, allow_any_platform=True)


def test_app_that_exits_early_is_an_error(tools, tmp_path):
    _, pm, _ = tools
    scenario = Scenario(name="i", kind="idle", command=[sys.executable, "-c", "pass"], warmup_seconds=1, capture_seconds=1)
    with pytest.raises(RuntimeError, match="exited"):
        run_scenario(scenario, pm, tmp_path / "out", allow_any_platform=True)


def test_cli_summarize(tmp_path, capsys):
    csv_file = tmp_path / "f.csv"
    csv_file.write_text("FrameTime,Dropped\n10,0\n20,1\n")
    assert main(["summarize", str(csv_file), "--refresh-hz", "60"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["frames"] == 2
    assert out["droppedFrames"] == 1


def test_cli_summarize_errors(tmp_path, capsys):
    bad = tmp_path / "f.csv"
    bad.write_text("Other\n1\n")
    assert main(["summarize", str(bad)]) == 2
    assert main(["summarize", str(tmp_path / "missing.csv")]) == 2
    assert "error:" in capsys.readouterr().err


def test_cli_needs_config_or_kind(capsys):
    assert main(["run"]) == 2
    assert "--config" in capsys.readouterr().err


def test_cli_rejects_config_and_kind(tmp_path, capsys):
    cfg = tmp_path / "c.json"
    cfg.write_text("{}")
    assert main(["run", "--config", str(cfg), "--kind", "idle", "x"]) == 2
