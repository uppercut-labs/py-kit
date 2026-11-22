import json

import pytest

from uppercut_presentmon_measure import ConfigError, load_config, parse_config


def base(**over):
    scenario = {"kind": "idle", "command": ["my-app.exe"]}
    scenario.update(over)
    return {"scenarios": [scenario]}


def test_defaults():
    config = parse_config(base())
    scenario = config.scenarios[0]
    assert scenario.name == "idle"
    assert scenario.runs == 1
    assert scenario.capture_seconds == 10.0
    assert config.out_dir == "out"
    assert config.presentmon is None


def test_startup_defaults_to_three_runs_and_needs_marks_file():
    with pytest.raises(ConfigError, match="marks_file"):
        parse_config(base(kind="startup"))
    scenario = parse_config(base(kind="startup", marks_file="marks.json")).scenarios[0]
    assert scenario.runs == 3


def test_workload_needs_command():
    with pytest.raises(ConfigError, match="workload_command"):
        parse_config(base(kind="workload"))
    scenario = parse_config(base(kind="workload", workload_command=["node", "drive.js"])).scenarios[0]
    assert scenario.workload_command == ["node", "drive.js"]


@pytest.mark.parametrize(
    "over",
    [
        {"kind": "other"},
        {"command": []},
        {"command": "my-app.exe"},
        {"env": {"A": 1}},
        {"runs": 0},
        {"runs": True},
        {"capture_seconds": 0},
        {"warmup_seconds": -1},
        {"refresh_hz": 0},
    ],
)
def test_invalid_scenarios(over):
    with pytest.raises(ConfigError):
        parse_config(base(**over))


def test_top_level_errors():
    with pytest.raises(ConfigError):
        parse_config([])
    with pytest.raises(ConfigError):
        parse_config({"scenarios": []})
    with pytest.raises(ConfigError, match="unique"):
        parse_config({"scenarios": [{"kind": "idle", "command": ["a"]}, {"kind": "idle", "command": ["b"]}]})
    with pytest.raises(ConfigError, match="presentmon_args"):
        parse_config({**base(), "presentmon_args": "x"})


def test_load_config_file(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({**base(), "presentmon": "pm.exe", "out_dir": "results", "presentmon_args": ["--no_console_stats"]}))
    config = load_config(path)
    assert config.presentmon == "pm.exe"
    assert config.out_dir == "results"
    assert config.presentmon_args == ["--no_console_stats"]


def test_load_config_bad_json(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{nope")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(path)


def test_capture_target_options():
    scenario = parse_config(base(capture_child_match="--type=gpu-process", capture_delay_seconds=2)).scenarios[0]
    assert scenario.capture_child_match == "--type=gpu-process"
    assert scenario.capture_delay_seconds == 2.0
    default = parse_config(base()).scenarios[0]
    assert default.capture_child_match is None
    assert default.capture_delay_seconds == 0.0


def test_capture_target_option_errors():
    with pytest.raises(ConfigError, match="not both"):
        parse_config(base(capture_child_match="x", capture_process_name="a.exe"))
    with pytest.raises(ConfigError, match="capture_child_match"):
        parse_config(base(capture_child_match=""))
    with pytest.raises(ConfigError, match="capture_delay_seconds"):
        parse_config(base(capture_delay_seconds=-1))
