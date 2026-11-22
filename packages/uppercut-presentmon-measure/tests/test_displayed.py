"""Display-change timing: msBetweenDisplayChange, when frames reached the display."""
import csv
import io
import random

import pytest

from uppercut_presentmon_measure import (
    FrameParseError,
    parse_presentmon_csv,
    percentile,
    summarize_displayed,
)

HEADER = "Application,TimeInSeconds,msBetweenPresents,msBetweenDisplayChange,Dropped,PresentMode\n"
SAMPLE = HEADER + (
    "app.exe,0.000,8.4,8.4,0,Hardware: Independent Flip\n"
    "app.exe,0.008,8.3,8.3,0,Hardware: Independent Flip\n"
    "app.exe,0.017,8.4,8.4,0,Hardware: Independent Flip\n"
    "app.exe,0.025,8.3,0,1,Hardware: Independent Flip\n"
    "app.exe,0.033,8.4,NA,1,Hardware: Independent Flip\n"
    "app.exe,0.050,16.7,25.0,0,Hardware: Independent Flip\n"
    "app.exe,0.058,8.3,8.3,0,Hardware: Independent Flip\n"
)


def test_parse_reads_display_change_and_time():
    frames = parse_presentmon_csv(SAMPLE)
    assert [f["display_ms"] for f in frames] == [8.4, 8.3, 8.4, 0.0, None, 25.0, 8.3]
    assert frames[0]["time_s"] == 0.0
    assert frames[-1]["time_s"] == 0.058
    # the frame-time column is still msBetweenPresents
    assert frames[5]["frame_time_ms"] == 16.7


def test_parse_without_the_columns_gives_none():
    frames = parse_presentmon_csv("FrameTime,Dropped\n10,0\n")
    assert frames == [{"frame_time_ms": 10.0, "dropped": False, "display_ms": None, "time_s": None}]


def test_parse_rejects_a_display_value_that_is_not_a_number():
    with pytest.raises(FrameParseError, match="not a number"):
        parse_presentmon_csv(HEADER + "app.exe,0,8,x,0,m\n")


def test_summarize_displayed_counts_only_displayed_frames():
    summary = summarize_displayed(parse_presentmon_csv(SAMPLE), refresh_hz=120)
    assert summary["presents"] == 7
    assert summary["displayed"] == 5  # the 0 and the NA were never displayed
    assert summary["periodMs"] == 8.333
    assert summary["refreshHz"] == 120
    assert summary["lateFrames"] == 1  # only 25.0 ms is over 1.5 periods (12.5 ms)
    assert summary["latePct"] == 20.0
    assert summary["durationSeconds"] == 0.058
    assert summary["displayedFps"] == pytest.approx(86.21, abs=0.01)
    assert summary["displayIntervalMs"]["max"] == 25.0
    assert summary["displayIntervalMs"]["min"] == 8.3
    assert summary["displayIntervalMs"]["p50"] == 8.4


def test_summarize_displayed_with_no_displayed_frames():
    frames = parse_presentmon_csv("FrameTime,msBetweenDisplayChange\n10,0\n11,NA\n")
    assert summarize_displayed(frames, 60) == {"presents": 2, "displayed": 0}
    assert summarize_displayed([], 60) == {"presents": 0, "displayed": 0}


def test_summarize_displayed_without_time_has_no_duration():
    frames = parse_presentmon_csv("FrameTime,msBetweenDisplayChange\n16,16\n17,17\n")
    summary = summarize_displayed(frames, 60)
    assert "durationSeconds" not in summary and "displayedFps" not in summary
    assert summary["displayed"] == 2


def test_summarize_displayed_needs_a_refresh_rate():
    with pytest.raises(ValueError, match="refresh_hz"):
        summarize_displayed([], 0)


def test_percentile_index_method():
    values = [1, 2, 3, 4]
    assert percentile(values, 50, "index") == 3  # sorted[int(0.5 * 4)]
    assert percentile(values, 100, "index") == 4  # clamped to the last
    assert percentile(values, 0, "index") == 1
    assert percentile(values, 50) == 2.5  # linear stays the default
    with pytest.raises(ValueError, match="method"):
        percentile(values, 50, "nearest")


# A hand-written displayed-cadence summary, as desktop-app measure scripts carry it: rows with a
# display interval above zero, plain-index percentiles, late = more than 1.5 periods. The kit must
# report the same numbers for the same capture when asked for the same percentile method.
def reference_summary(text: str, hz: float) -> dict:
    period = 1000.0 / hz
    rows = list(csv.DictReader(io.StringIO(text)))
    disp = sorted(
        float(r["msBetweenDisplayChange"]) for r in rows
        if r.get("msBetweenDisplayChange") not in (None, "", "NA") and float(r["msBetweenDisplayChange"]) > 0
    )

    def pct(v, p):
        return v[min(len(v) - 1, int(p / 100 * len(v)))]

    span = float(rows[-1]["TimeInSeconds"]) - float(rows[0]["TimeInSeconds"]) if len(rows) > 1 else 0.0
    late = sum(1 for x in disp if x > 1.5 * period)
    return {
        "presents": len(rows), "displayed": len(disp),
        "displayedFps": round(len(disp) / span, 2), "p50": round(pct(disp, 50), 3),
        "p95": round(pct(disp, 95), 3), "p99": round(pct(disp, 99), 3), "max": round(disp[-1], 3),
        "latePct": round(100.0 * late / len(disp), 2), "lateFrames": late,
    }


def synthetic_capture(seed: int, frames: int = 700, hz: float = 120.0) -> str:
    rng = random.Random(seed)
    period = 1000.0 / hz
    out, t = [HEADER.rstrip("\n")], 0.0
    for _ in range(frames):
        roll = rng.random()
        shown = period * (2.0 if roll < 0.02 else 3.0 if roll < 0.025 else 1.0) + rng.uniform(-0.1, 0.1)
        t += shown / 1000.0
        if rng.random() < 0.03:
            out.append(f"app.exe,{t:.6f},{period:.3f},{rng.choice(['0', 'NA'])},1,Hardware: Independent Flip")
        else:
            out.append(f"app.exe,{t:.6f},{period:.3f},{shown:.4f},0,Hardware: Independent Flip")
    return "\n".join(out) + "\n"


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_matches_a_hand_written_summary_on_the_same_capture(seed):
    text = synthetic_capture(seed)
    ref = reference_summary(text, 120.0)
    got = summarize_displayed(parse_presentmon_csv(text), 120.0, percentile_method="index")
    assert got["presents"] == ref["presents"]
    assert got["displayed"] == ref["displayed"]
    assert got["lateFrames"] == ref["lateFrames"]
    assert got["latePct"] == ref["latePct"]
    assert got["displayedFps"] == ref["displayedFps"]
    intervals = got["displayIntervalMs"]
    assert (intervals["p50"], intervals["p95"], intervals["p99"], intervals["max"]) == (
        ref["p50"], ref["p95"], ref["p99"], ref["max"]
    )
    # the default interpolating method stays within a period fraction of the plain index
    linear = summarize_displayed(parse_presentmon_csv(text), 120.0)["displayIntervalMs"]
    assert abs(linear["p50"] - ref["p50"]) < 0.5
