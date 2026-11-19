import pytest

from uppercut_presentmon_measure import (
    FrameParseError,
    parse_marks,
    parse_presentmon_csv,
    percentile,
    summarize_frames,
    summarize_runs,
    summarize_startup_marks,
)

SAMPLE = """Application,ProcessID,FrameTime,Dropped,PresentMode
app.exe,100,16.0,0,Hardware: Independent Flip
app.exe,100,16.5,0,Hardware: Independent Flip
app.exe,100,17.0,0,Hardware: Independent Flip
app.exe,100,50.0,1,Hardware: Independent Flip
app.exe,100,16.5,0,Hardware: Independent Flip
"""


def test_parse_reads_frame_time_and_dropped():
    frames = parse_presentmon_csv(SAMPLE)
    assert [f["frame_time_ms"] for f in frames] == [16.0, 16.5, 17.0, 50.0, 16.5]
    assert [f["dropped"] for f in frames] == [False, False, False, True, False]


def test_parse_older_column_names_and_bom():
    text = "﻿Application,msBetweenPresents,Dropped\napp.exe,8.3,False\napp.exe,8.4,True\n"
    frames = parse_presentmon_csv(text)
    assert [f["dropped"] for f in frames] == [False, True]
    assert frames[0]["frame_time_ms"] == 8.3


def test_parse_explicit_column_and_missing_dropped_column():
    frames = parse_presentmon_csv("A,Custom\nx,5\nx,6\n", frame_time_column="custom")
    assert [f["frame_time_ms"] for f in frames] == [5.0, 6.0]
    assert not any(f["dropped"] for f in frames)


def test_parse_skips_blank_and_na_values():
    frames = parse_presentmon_csv("FrameTime\n10\nNA\n\n12\n")
    assert [f["frame_time_ms"] for f in frames] == [10.0, 12.0]


def test_parse_empty_capture_is_empty_list():
    assert parse_presentmon_csv("") == []
    assert parse_presentmon_csv("FrameTime,Dropped\n") == []


def test_parse_errors():
    with pytest.raises(FrameParseError, match="no frame-time column"):
        parse_presentmon_csv("Application,Other\napp,1\n")
    with pytest.raises(FrameParseError, match="not a number"):
        parse_presentmon_csv("FrameTime\nabc\n")


def test_percentile_interpolates():
    assert percentile([1, 2, 3, 4], 0) == 1
    assert percentile([1, 2, 3, 4], 100) == 4
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([5], 99) == 5


def test_percentile_errors():
    with pytest.raises(ValueError):
        percentile([], 50)
    with pytest.raises(ValueError):
        percentile([1], 101)


def test_summarize_frames():
    summary = summarize_frames(parse_presentmon_csv(SAMPLE), refresh_hz=60)
    assert summary["frames"] == 5
    assert summary["droppedFrames"] == 1
    assert summary["droppedPercent"] == 20.0
    assert summary["frameTimeMs"]["min"] == 16.0
    assert summary["frameTimeMs"]["max"] == 50.0
    assert summary["frameTimeMs"]["p50"] == 16.5
    assert summary["frameTimeMs"]["mean"] == 23.2
    assert summary["durationSeconds"] == 0.116
    assert summary["averageFps"] == pytest.approx(43.1, abs=0.01)
    assert summary["missedRefreshFrames"] == 1  # only 50 ms exceeds 25 ms
    assert summary["refreshHz"] == 60


def test_summarize_frames_without_refresh_has_no_missed_count():
    assert "missedRefreshFrames" not in summarize_frames([{"frame_time_ms": 10.0}])


def test_summarize_frames_empty():
    assert summarize_frames([]) == {"frames": 0}


def test_summarize_runs():
    summary = summarize_runs([300.0, None, 100.0, 200.0])
    assert summary["medianMs"] == 200.0
    assert summary["minMs"] == 100.0
    assert summary["maxMs"] == 300.0
    assert summary["failedRuns"] == 1
    assert summarize_runs([None]) == {"runsMs": [None], "failedRuns": 1, "minMs": None, "medianMs": None, "maxMs": None}


def test_summarize_startup_marks():
    marks = {"window": 1250.0, "ready": 2000.5, "early": 900.0, "bad": "x", "flag": True}
    assert summarize_startup_marks(marks, 1000.0) == {"window": 250.0, "ready": 1000.5}


def test_parse_marks_both_shapes_and_garbage():
    assert parse_marks('{"marks": {"ready": 5, "skip": "a"}}') == {"ready": 5.0}
    assert parse_marks('{"ready": 7.5}') == {"ready": 7.5}
    assert parse_marks("{half written") == {}
    assert parse_marks("[1, 2]") == {}
