"""Capture a chosen pid, and start a capture after a delay."""
import json
import sys
import textwrap
import time

import pytest

import uppercut_presentmon_measure.runner as runner
from uppercut_presentmon_measure import (
    Scenario,
    capture_frames,
    find_descendant_pid,
    list_processes,
    run_scenario,
)
from uppercut_presentmon_measure.cli import main

# Writes a CSV with display-change timing and records when it started and which pid it was given.
FAKE_PRESENTMON = textwrap.dedent(
    """
    import json, sys, time
    from pathlib import Path
    args = sys.argv[1:]
    out = Path(args[args.index("--output_file") + 1])
    pid = args[args.index("--process_id") + 1] if "--process_id" in args else None
    Path(str(out) + ".started").write_text(json.dumps({"at": time.time(), "pid": pid}))
    rows = "".join(f"app.exe,{i * 0.0083:.4f},8.3,{'8.3' if i % 10 else '25.0'},0,Hardware: Independent Flip\\n" for i in range(20))
    out.write_text("Application,TimeInSeconds,msBetweenPresents,msBetweenDisplayChange,Dropped,PresentMode\\n" + rows)
    """
)

# An app that starts a helper process with a marker argument, writes the helper's pid, and sleeps.
APP_WITH_HELPER = textwrap.dedent(
    """
    import subprocess, sys, time
    from pathlib import Path
    helper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "--type=gpu-process"])
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "--type=renderer"])
    Path(sys.argv[1]).write_text(str(helper.pid))
    time.sleep(60)
    """
)


@pytest.fixture
def pm(tmp_path):
    path = tmp_path / "fake_presentmon.py"
    path.write_text(FAKE_PRESENTMON)
    return [sys.executable, str(path)]


def started(tmp_path, name):
    return json.loads((tmp_path / "out" / f"{name}.csv.started").read_text())


def test_find_descendant_pid_searches_the_whole_tree():
    listing = [
        (1, 0, "init"),
        (10, 1, "app --main"),
        (11, 10, "app --type=renderer"),
        (12, 11, "app --type=gpu-process --extra"),  # a grandchild
        (13, 10, "app --type=gpu-process"),
        (20, 1, "other --type=gpu-process"),  # not under the app
    ]
    assert find_descendant_pid(10, "--type=gpu-process", processes=listing) == 12
    assert find_descendant_pid(10, "--type=utility", processes=listing) is None
    assert find_descendant_pid(10, "--main", processes=listing) is None  # the root is not a candidate
    with pytest.raises(ValueError):
        find_descendant_pid(10, "", processes=listing)


def test_find_descendant_pid_ignores_a_parent_cycle():
    listing = [(2, 3, "a"), (3, 2, "b --marker")]
    assert find_descendant_pid(2, "--marker", processes=listing) == 3


def test_find_descendant_pid_in_real_processes(tmp_path):
    pid_file = tmp_path / "helper.pid"
    import subprocess

    app = subprocess.Popen([sys.executable, "-c", APP_WITH_HELPER, str(pid_file)])
    try:
        found = find_descendant_pid(app.pid, "--type=gpu-process", timeout_seconds=20)
        assert found is not None
        assert found == int(pid_file.read_text())
        assert any(pid == found and parent == app.pid for pid, parent, _ in list_processes())
    finally:
        app.terminate()
        app.wait(timeout=10)
        # the helpers are children of the app; stop them by pid
        if pid_file.exists():
            import os
            import signal

            for pid in {int(pid_file.read_text())}:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass


def test_scenario_captures_the_matching_child_pid(pm, tmp_path):
    pid_file = tmp_path / "helper.pid"
    scenario = Scenario(
        name="gpu", kind="idle", command=[sys.executable, "-c", APP_WITH_HELPER, str(pid_file)],
        warmup_seconds=1.5, capture_seconds=1, capture_child_match="--type=gpu-process", refresh_hz=120,
    )
    try:
        result = run_scenario(scenario, pm, tmp_path / "out", allow_any_platform=True)
    finally:
        import os
        import signal

        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGTERM)
            except OSError:
                pass
    helper = int(pid_file.read_text())
    assert result["capturedPid"] == helper != result["launchedPid"]
    assert started(tmp_path, "gpu")["pid"] == str(helper)
    assert result["frames"]["frames"] == 20


def test_scenario_without_a_matching_child_is_an_error(pm, tmp_path, monkeypatch):
    real = runner.find_descendant_pid
    monkeypatch.setattr(runner, "find_descendant_pid", lambda pid, text, **kw: real(pid, text, timeout_seconds=0))
    scenario = Scenario(
        name="gpu", kind="idle", command=[sys.executable, "-c", "import time; time.sleep(30)"],
        warmup_seconds=0.3, capture_seconds=1, capture_child_match="--type=gpu-process",
    )
    with pytest.raises(RuntimeError, match="no child of the app"):
        run_scenario(scenario, pm, tmp_path / "out", allow_any_platform=True)


def test_scenario_reports_displayed_frames(pm, tmp_path):
    scenario = Scenario(
        name="shown", kind="idle", command=[sys.executable, "-c", "import time; time.sleep(30)"],
        warmup_seconds=0.3, capture_seconds=1, refresh_hz=120,
    )
    result = run_scenario(scenario, pm, tmp_path / "out", allow_any_platform=True)
    assert result["capturedPid"] == result["launchedPid"]
    shown = result["displayed"]
    assert shown["presents"] == 20 and shown["displayed"] == 20
    assert shown["lateFrames"] == 2  # i = 0 and 10 are 25 ms, over 12.5 ms
    assert result["frames"]["frames"] == 20


def test_scenario_without_refresh_rate_has_no_displayed_summary(pm, tmp_path):
    scenario = Scenario(
        name="plain", kind="idle", command=[sys.executable, "-c", "import time; time.sleep(30)"],
        warmup_seconds=0.3, capture_seconds=1,
    )
    assert "displayed" not in run_scenario(scenario, pm, tmp_path / "out", allow_any_platform=True)


def test_workload_capture_starts_after_the_delay(pm, tmp_path):
    marker = tmp_path / "workload-started"
    workload = [sys.executable, "-c", f"import time, pathlib; pathlib.Path(r'{marker}').write_text(str(time.time())); time.sleep(3)"]
    scenario = Scenario(
        name="late", kind="workload", command=[sys.executable, "-c", "import time; time.sleep(30)"],
        warmup_seconds=0.3, capture_seconds=1, workload_command=workload, capture_delay_seconds=2.0,
    )
    result = run_scenario(scenario, pm, tmp_path / "out", allow_any_platform=True)
    gap = started(tmp_path, "late")["at"] - float(marker.read_text())
    assert 1.9 <= gap < 4.0, gap
    assert result["workloadExitCode"] == 0
    assert result["frames"]["frames"] == 20


def test_idle_capture_starts_after_the_delay(pm, tmp_path):
    before = time.time()
    scenario = Scenario(
        name="idle", kind="idle", command=[sys.executable, "-c", "import time; time.sleep(30)"],
        warmup_seconds=0.3, capture_seconds=1, capture_delay_seconds=1.5,
    )
    run_scenario(scenario, pm, tmp_path / "out", allow_any_platform=True)
    assert started(tmp_path, "idle")["at"] - before >= 1.8


def test_app_that_exits_during_the_delay_is_an_error(pm, tmp_path):
    scenario = Scenario(
        name="gone", kind="idle", command=[sys.executable, "-c", "import time; time.sleep(1.2)"],
        warmup_seconds=0.3, capture_seconds=1, capture_delay_seconds=2.5,
    )
    with pytest.raises(RuntimeError, match="exited"):
        run_scenario(scenario, pm, tmp_path / "out", allow_any_platform=True)


def test_capture_frames_on_a_chosen_pid_after_a_delay(pm, tmp_path):
    before = time.time()
    result = capture_frames(pm, tmp_path / "out" / "chosen.csv", 1, pid=4321, delay_seconds=1.0, refresh_hz=120)
    assert (tmp_path / "out" / "chosen.csv.started").exists()
    info = json.loads((tmp_path / "out" / "chosen.csv.started").read_text())
    assert info["pid"] == "4321"
    assert info["at"] - before >= 0.9
    assert result["capturedPid"] == 4321
    assert result["presentmonExitCode"] == 0
    assert result["frames"]["frames"] == 20
    assert result["displayed"]["displayed"] == 20
    assert result["csv"].endswith("chosen.csv")
    assert "note" not in result


def test_capture_frames_by_process_name_and_empty_capture(tmp_path):
    silent = [sys.executable, "-c", "pass"]
    result = capture_frames(silent, tmp_path / "quiet.csv", 1, process_name="my-app.exe")
    assert result["frames"] == {"frames": 0}
    assert "no frames" in result["note"]
    assert "capturedPid" not in result
    with pytest.raises(ValueError, match="exactly one"):
        capture_frames(silent, tmp_path / "x.csv", 1)


def test_cli_passes_the_new_options(pm, tmp_path, monkeypatch, capsys):
    seen = {}

    def fake_run(scenario, presentmon, out_dir, extra=()):
        seen["scenario"] = scenario
        return {"name": scenario.name, "kind": scenario.kind}

    monkeypatch.setattr("uppercut_presentmon_measure.cli.run_scenario", fake_run)
    monkeypatch.setattr("uppercut_presentmon_measure.cli.resolve_presentmon", lambda option: "pm")
    monkeypatch.setattr(sys, "platform", "win32")
    code = main([
        "run", "--kind", "idle", "--capture-child-match=--type=gpu-process", "--capture-delay", "2",
        "--out", str(tmp_path), "--", "app.exe",
    ])
    assert code == 0
    assert seen["scenario"].capture_child_match == "--type=gpu-process"
    assert seen["scenario"].capture_delay_seconds == 2.0


def test_cli_summarize_adds_the_displayed_summary(tmp_path, capsys):
    csv_file = tmp_path / "f.csv"
    csv_file.write_text("TimeInSeconds,msBetweenPresents,msBetweenDisplayChange,Dropped\n0,16,16,0\n0.016,16,16,0\n0.04,24,24,0\n")
    assert main(["summarize", str(csv_file), "--refresh-hz", "60"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["displayed"]["lateFrames"] == 0  # 24 ms is under 1.5 periods (25 ms)
    assert out["displayed"]["displayed"] == 3
    assert out["frames"] == 3
