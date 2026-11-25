import json
import sys
import threading
import time
from pathlib import Path

import pytest

from uppercut_wave_gate import (
    Call,
    HostError,
    WaveConfig,
    WaveError,
    Worker,
    command_runner,
    gate_fingerprint,
    load_pieces,
    main,
    output_diversity,
    parse_workers,
    run_gate,
    run_wave,
    summarize_samples,
    write_atomic,
)

PY = sys.executable
# A command gate: passes any file that does not contain the word BAD.
CMD_GATE = '"%s" -c "import sys; sys.exit(1 if \'BAD\' in open(sys.argv[1]).read() else 0)" {out}' % PY


def text_gate(text):
    return "BAD" not in text


@pytest.fixture
def run(tmp_path):
    """Build a run folder with n pieces, a prompt and a known-bad sample; returns a config factory."""

    def make(n=3, gate=text_gate, **overrides):
        pieces = tmp_path / "pieces.jsonl"
        pieces.write_text("".join(json.dumps({"id": "p%02d" % i, "text": "piece %d" % i}) + "\n" for i in range(1, n + 1)),
                          encoding="utf-8")
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("Say something about the piece.\n", encoding="utf-8")
        bad = tmp_path / "bad.out"
        bad.write_text("BAD output", encoding="utf-8")
        values = dict(pieces=str(pieces), prompt=str(prompt), out=str(tmp_path / "run"),
                      workers=[Worker("a", "m1")], gate=gate, bad=str(bad), retries=1)
        values.update(overrides)
        return WaveConfig(**values)

    return make


def ok_runner(call: Call) -> str:
    return "answer to " + call.text


def read_ledger(cfg):
    return [json.loads(line) for line in (Path(cfg.out) / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]


def summary(cfg):
    return json.loads((Path(cfg.out) / "summary.json").read_text(encoding="utf-8"))


# gates ------------------------------------------------------------------------------------------------

def test_command_gate_exit_codes(tmp_path):
    out = tmp_path / "sample.out"
    out.write_text("fine", encoding="utf-8")
    assert run_gate(CMD_GATE, out, 10) == (True, "ok")
    out.write_text("BAD", encoding="utf-8")
    passed, why = run_gate(CMD_GATE, out, 10)
    assert not passed and "gate exit 1" in why


def test_gate_replaces_out_and_id(tmp_path):
    out = tmp_path / "p07.out"
    out.write_text("x", encoding="utf-8")
    code = "import sys, os; assert os.path.exists(sys.argv[1]); assert sys.argv[2] == 'p07'"
    assert run_gate('"%s" -c "%s" {out} {id}' % (PY, code), out, 10).passed
    assert run_gate([PY, "-c", code, "{out}", "{id}"], out, 10).passed  # argument list, no shell


def test_gate_timeout_fails_and_says_so(tmp_path):
    out = tmp_path / "a.out"
    out.write_text("x", encoding="utf-8")
    passed, why = run_gate('"%s" -c "import time; time.sleep(10)"' % PY, out, 0.5)
    assert not passed and "timed out" in why


def test_gate_that_cannot_start_fails_closed(tmp_path):
    out = tmp_path / "a.out"
    out.write_text("x", encoding="utf-8")
    assert not run_gate(["/no/such/program-xyz", "{out}"], out, 5).passed


def test_function_gate_forms(tmp_path):
    out = tmp_path / "a.out"
    out.write_text("hello", encoding="utf-8")
    assert run_gate(lambda t: t == "hello", out).passed
    assert run_gate(lambda t: (False, "too short"), out) == (False, "too short")
    passed, why = run_gate(lambda t: 1 / 0, out)
    assert not passed and "gate error" in why


def test_fingerprint_changes_with_the_gate_script(tmp_path):
    script = tmp_path / "check.py"
    script.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    gate = '"%s" "%s" {out}' % (PY, script)
    before = gate_fingerprint(gate)
    assert gate_fingerprint(gate) == before
    script.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    assert gate_fingerprint(gate) != before
    assert gate_fingerprint(gate) != gate_fingerprint(gate + " --strict")


# the probe --------------------------------------------------------------------------------------------

def test_refuses_to_start_when_the_gate_passes_the_bad_sample(run):
    cfg = run(gate=lambda text: True)
    calls = []
    with pytest.raises(WaveError, match="passed the known-bad output"):
        run_wave(cfg, lambda call: calls.append(call) or "x")
    assert calls == []
    assert not (Path(cfg.out) / "manifest.json").exists()


def test_refuses_a_command_gate_that_passes_everything(run):
    cfg = run(gate='"%s" -c "import sys; sys.exit(0)"' % PY)
    with pytest.raises(WaveError, match="passed the known-bad"):
        run_wave(cfg, ok_runner)


def test_refuses_a_gate_that_crashes_on_the_bad_sample(run):
    cfg = run(gate=lambda text: 1 / 0)
    with pytest.raises(WaveError, match="could not judge"):
        run_wave(cfg, ok_runner)


def test_refuses_a_missing_bad_sample(run, tmp_path):
    cfg = run(bad=str(tmp_path / "nope.out"))
    with pytest.raises(WaveError, match="does not exist"):
        run_wave(cfg, ok_runner)


# a run -------------------------------------------------------------------------------------------------

def test_all_pass(run):
    cfg = run(n=3)
    assert run_wave(cfg, ok_runner) == 0
    out = Path(cfg.out)
    assert sorted(p.name for p in (out / "results").iterdir()) == ["p01.out", "p02.out", "p03.out"]
    assert (out / "results" / "p02.out").read_text(encoding="utf-8") == "answer to piece 2"
    assert list((out / "attempts").iterdir()) == []
    s = summary(cfg)
    assert (s["pieces"], s["passed"], s["failed"], s["remaining"], s["pass_rate"]) == (3, 3, 0, 0, 1.0)
    assert s["unique_outputs"] == 3 and s["duplicate_count"] == 0 and s["diversity_ratio"] == 1.0
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["pieces"]) == {"p01", "p02", "p03"} and manifest["workers"] == [["a", "m1"]]
    assert {r["status"] for r in read_ledger(cfg)} == {"pass"}


def test_only_gated_output_counts_and_failures_are_kept(run):
    cfg = run(n=2, retries=1)
    attempts = {"p01": 0}

    def runner(call):
        attempts[call.piece_id] = attempts.get(call.piece_id, 0) + 1
        return "BAD answer" if call.piece_id == "p02" else "good"

    assert run_wave(cfg, runner) == 3
    out = Path(cfg.out)
    assert (out / "results" / "p01.out").exists() and not (out / "results" / "p02.out").exists()
    assert (out / "failed" / "p02.out").read_text(encoding="utf-8") == "BAD answer"
    assert "rejected" in (out / "failed" / "p02.why").read_text(encoding="utf-8")
    assert attempts["p02"] == 2  # one try and one retry
    assert summary(cfg)["failed"] == 1 and summary(cfg)["pass_rate"] == 0.5


def test_retry_can_succeed(run):
    cfg = run(n=1, retries=2)
    seen = []

    def runner(call):
        seen.append(call.piece_id)
        return "BAD" if len(seen) < 3 else "good now"

    assert run_wave(cfg, runner) == 0
    assert len(seen) == 3
    assert [r["status"] for r in read_ledger(cfg)] == ["gate_fail", "gate_fail", "pass"]


def test_empty_output_fails_without_calling_the_gate(run):
    gate_calls = []
    cfg = run(n=1, retries=0, gate=lambda t: gate_calls.append(t) or "BAD" not in t)
    assert run_wave(cfg, lambda call: "   ") == 3
    assert "empty output" in (Path(cfg.out) / "failed" / "p01.why").read_text(encoding="utf-8")
    assert gate_calls == [Path(cfg.bad).read_text(encoding="utf-8")]  # only the probe ran it


def test_settings_and_prompt_reach_the_runner(run):
    cfg = run(n=1, settings={"temp": 0.2}, timeout=7)
    seen = []
    run_wave(cfg, lambda call: seen.append(call) or "ok")
    call = seen[0]
    assert call.system == "Say something about the piece." and call.text == "piece 1"
    assert call.settings == {"temp": 0.2} and call.timeout == 7 and call.worker == Worker("a", "m1")


def test_slots_run_in_parallel(run):
    cfg = run(n=4, workers=parse_workers(["a=m1*2", "b=m2"]))
    threads = set()
    barrier = threading.Barrier(3, timeout=10)

    def runner(call):
        threads.add(threading.get_ident())
        if call.piece_id in {"p01", "p02", "p03"}:
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
        return "ok " + call.piece_id

    assert run_wave(cfg, runner) == 0
    assert len(threads) == 3


def test_duplicate_outputs_show_in_the_summary(run):
    cfg = run(n=4)
    assert run_wave(cfg, lambda call: "same text") == 0
    s = summary(cfg)
    assert (s["unique_outputs"], s["duplicate_count"], s["diversity_ratio"]) == (1, 3, 0.25)


# host errors -------------------------------------------------------------------------------------------

def test_host_errors_requeue_and_drop_a_dead_worker(run):
    cfg = run(n=3, workers=[Worker("dead", "m"), Worker("live", "m")])

    def runner(call):
        if call.worker.host == "dead":
            raise HostError("connection refused")
        return "ok"

    assert run_wave(cfg, runner) == 0
    statuses = [(r["host"], r["status"]) for r in read_ledger(cfg)]
    assert ("dead", "host_error") in statuses
    assert statuses.count(("dead", "host_error")) <= 3  # struck out after three in a row
    assert summary(cfg)["passed"] == 3


def test_every_worker_dead_leaves_pieces_remaining(run):
    cfg = run(n=6, workers=[Worker("dead", "m")])

    def runner(call):
        raise ConnectionError("no route")

    assert run_wave(cfg, runner) == 4
    s = summary(cfg)
    assert s["passed"] == 0 and s["remaining"] > 0
    assert any("ConnectionError: no route" in r["why"] for r in read_ledger(cfg))


def test_a_piece_that_keeps_hitting_host_errors_is_given_up(run):
    cfg = run(n=1, workers=[Worker("a", "m")] * 6)

    def runner(call):
        raise HostError("overloaded")

    assert run_wave(cfg, runner) == 3
    assert "host error: overloaded" in (Path(cfg.out) / "failed" / "p01.why").read_text(encoding="utf-8")


# stopping ---------------------------------------------------------------------------------------------

def test_stop_after_trips_and_exits_6(run):
    cfg = run(n=10, stop_after=3, retries=0)
    assert run_wave(cfg, lambda call: "BAD") == 6
    s = summary(cfg)
    assert s["failed"] == 3 and s["remaining"] == 7


def test_stop_after_counts_in_a_row_only(run):
    cfg = run(n=6, stop_after=3, retries=0)
    assert run_wave(cfg, lambda call: "BAD" if call.piece_id in {"p01", "p02", "p04", "p05"} else "ok") == 3


def test_budget_exit_5(run):
    cfg = run(n=5, budget=1)

    def runner(call):
        time.sleep(0.6)
        return "ok"

    assert run_wave(cfg, runner) == 5
    assert 0 < summary(cfg)["passed"] < 5


def test_limit_runs_a_pilot_and_a_rerun_finishes(run):
    cfg = run(n=4, limit=2)
    assert run_wave(cfg, ok_runner) == 0
    assert summary(cfg)["passed"] == 2 and summary(cfg)["remaining"] == 2
    cfg.limit = None
    assert run_wave(cfg, ok_runner) == 0
    assert summary(cfg)["passed"] == 4


# resume ------------------------------------------------------------------------------------------------

def test_rerun_skips_finished_pieces(run):
    cfg = run(n=3)
    run_wave(cfg, ok_runner)
    calls = []
    assert run_wave(cfg, lambda call: calls.append(call.piece_id) or "ok") == 0
    assert calls == []


def test_rerun_skips_given_up_pieces_unless_retry_failed(run):
    cfg = run(n=1, retries=0)
    assert run_wave(cfg, lambda call: "BAD") == 3
    calls = []
    assert run_wave(cfg, lambda call: calls.append(1) or "good") == 3
    assert calls == []
    cfg.retry_failed = True
    assert run_wave(cfg, lambda call: calls.append(1) or "good") == 0
    assert calls == [1]
    assert not (Path(cfg.out) / "failed" / "p01.why").exists()


@pytest.mark.parametrize("change", ["prompt", "gate", "workers", "settings", "piece"])
def test_resume_refuses_stale_results(run, tmp_path, change):
    script = tmp_path / "check.py"
    script.write_text("import sys\nsys.exit(1 if 'BAD' in open(sys.argv[1]).read() else 0)\n", encoding="utf-8")
    cfg = run(n=2, gate='"%s" "%s" {out}' % (PY, script), settings={"temp": 0.1})
    assert run_wave(cfg, ok_runner) == 0
    if change == "prompt":
        Path(cfg.prompt).write_text("A different prompt", encoding="utf-8")
    elif change == "gate":
        script.write_text(script.read_text(encoding="utf-8") + "# stricter\n", encoding="utf-8")
    elif change == "workers":
        cfg.workers = [Worker("a", "m2")]
    elif change == "settings":
        cfg.settings = {"temp": 0.9}
    else:
        Path(cfg.pieces).write_text(json.dumps({"id": "p01", "text": "edited"}) + "\n" +
                                    json.dumps({"id": "p02", "text": "piece 2"}) + "\n", encoding="utf-8")
    calls = []
    with pytest.raises(WaveError, match="changed since previous run|content changed"):
        run_wave(cfg, lambda call: calls.append(1) or "ok")
    assert calls == []
    assert (Path(cfg.out) / "results" / "p01.out").exists()  # nothing was deleted by the refusal


def test_invalidate_changed_reruns_everything_after_a_config_change(run):
    cfg = run(n=2)
    run_wave(cfg, ok_runner)
    Path(cfg.prompt).write_text("A different prompt", encoding="utf-8")
    cfg.invalidate_changed = True
    calls = []
    assert run_wave(cfg, lambda call: calls.append(call.piece_id) or "new") == 0
    assert sorted(calls) == ["p01", "p02"]
    assert (Path(cfg.out) / "results" / "p01.out").read_text(encoding="utf-8") == "new"


def test_invalidate_changed_reruns_only_the_edited_piece(run):
    cfg = run(n=2)
    run_wave(cfg, ok_runner)
    Path(cfg.pieces).write_text(json.dumps({"id": "p01", "text": "edited"}) + "\n" +
                                json.dumps({"id": "p02", "text": "piece 2"}) + "\n", encoding="utf-8")
    cfg.invalidate_changed = True
    calls = []
    assert run_wave(cfg, lambda call: calls.append(call.piece_id) or "new") == 0
    assert calls == ["p01"]


def test_new_run_refuses_a_folder_with_a_manifest(run):
    cfg = run(n=1)
    run_wave(cfg, ok_runner)
    cfg.new_run = True
    with pytest.raises(WaveError, match="--new-run"):
        run_wave(cfg, ok_runner)


def test_results_without_a_manifest_are_not_trusted(run):
    cfg = run(n=1)
    results = Path(cfg.out) / "results"
    results.mkdir(parents=True)
    (results / "p01.out").write_text("BAD output nobody checked", encoding="utf-8")
    with pytest.raises(WaveError, match="without a valid manifest"):
        run_wave(cfg, ok_runner)
    cfg.invalidate_changed = True
    assert run_wave(cfg, ok_runner) == 0
    assert (results / "p01.out").read_text(encoding="utf-8") == "answer to piece 1"


def test_corrupt_manifest_is_refused(run):
    cfg = run(n=1)
    run_wave(cfg, ok_runner)
    (Path(cfg.out) / "manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(WaveError, match="corrupt manifest"):
        run_wave(cfg, ok_runner)


# inputs ------------------------------------------------------------------------------------------------

def test_load_pieces_rules(tmp_path):
    path = tmp_path / "p.jsonl"
    path.write_text('{"id": "a", "text": "one"}\n\n{"id": "b.1-x_y", "text": "two"}\n', encoding="utf-8")
    assert [p["id"] for p in load_pieces(path)] == ["a", "b.1-x_y"]
    for line, message in [
        ('{"id": "../x", "text": "t"}', "filename-safe"),
        ('{"id": "a", "text": "  "}', "non-empty"),
        ('{"id": "a"}', "non-empty"),
        ("[1, 2]", "filename-safe"),
        ("not json", "not JSON"),
    ]:
        path.write_text(line + "\n", encoding="utf-8")
        with pytest.raises(WaveError, match=message):
            load_pieces(path)
    path.write_text('{"id": "a", "text": "t"}\n{"id": "a", "text": "u"}\n', encoding="utf-8")
    with pytest.raises(WaveError, match="duplicate id a"):
        load_pieces(path)


def test_parse_workers():
    assert parse_workers(["a=m", "b=big:9b*2"]) == [Worker("a", "m"), Worker("b", "big:9b"), Worker("b", "big:9b")]
    assert parse_workers(["h-1.local=org/model:q4*3"])[0] == Worker("h-1.local", "org/model:q4")
    for bad in ["nomodel", "=m", "a="]:
        with pytest.raises(WaveError, match="bad --worker"):
            parse_workers([bad])


def test_empty_prompt_is_refused(run):
    cfg = run()
    Path(cfg.prompt).write_text("  \n", encoding="utf-8")
    with pytest.raises(WaveError, match="empty prompt"):
        run_wave(cfg, ok_runner)


def test_write_atomic_replaces_and_leaves_no_temp_file(tmp_path):
    target = tmp_path / "x.json"
    write_atomic(str(target), "one")
    write_atomic(str(target), "two")
    assert target.read_text(encoding="utf-8") == "two"
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


# command runner and the command line ------------------------------------------------------------------

ECHO = "import sys; print('echo:' + sys.stdin.read())"


def test_command_runner_sends_the_piece_on_stdin(run):
    cfg = run(n=1)
    runner = command_runner(lambda call: [PY, "-c", ECHO])
    assert run_wave(cfg, runner) == 0
    assert (Path(cfg.out) / "results" / "p01.out").read_text(encoding="utf-8") == "echo:piece 1"


def test_command_runner_nonzero_exit_is_a_host_error(run):
    runner = command_runner(lambda call: [PY, "-c", "import sys; sys.stderr.write('model not loaded'); sys.exit(3)"])
    with pytest.raises(HostError, match="model not loaded"):
        runner(Call(Worker("a", "m"), "p01", "text", "sys", 10, {}))


def test_command_runner_timeout_and_missing_program():
    call = Call(Worker("a", "m"), "p01", "text", "sys", 0.5, {})
    with pytest.raises(HostError, match="timed out"):
        command_runner(lambda c: [PY, "-c", "import time; time.sleep(10)"])(call)
    with pytest.raises(HostError):
        command_runner(lambda c: ["/no/such/program-xyz"])(call)


def test_command_runner_without_stdin_passes_text_in_argv():
    runner = command_runner(lambda c: [PY, "-c", "import sys; print(sys.argv[1].upper())", c.text], stdin=False)
    assert runner(Call(Worker("a", "m"), "p01", "hello", "sys", 10, {})) == "HELLO"


def cli_args(cfg, *extra):
    return ["--pieces", cfg.pieces, "--prompt", cfg.prompt, "--out", cfg.out, "--worker", "a=m1",
            "--bad", cfg.bad, *extra]


def test_cli_with_runner_cmd(run, capsys):
    cfg = run(n=2)
    code = main(cli_args(cfg, "--gate", CMD_GATE, "--runner-cmd", '"%s" -c "%s"' % (PY, ECHO)))
    assert code == 0
    assert json.loads(capsys.readouterr().out)["passed"] == 2


def test_cli_substitutes_host_model_and_prompt_file(run):
    cfg = run(n=1)
    script = ("import sys; print(' '.join(sys.argv[1:]) + ' ' + sys.stdin.read())")
    code = main(cli_args(cfg, "--gate", CMD_GATE, "--runner-cmd",
                         '"%s" -c "%s" {host} {model} {piece_id} {prompt_file}' % (PY, script)))
    assert code == 0
    line = (Path(cfg.out) / "results" / "p01.out").read_text(encoding="utf-8")
    assert line == "a m1 p01 %s piece 1" % cfg.prompt


def test_cli_returns_2_when_it_cannot_start(run, capsys):
    cfg = run(n=1)
    code = main(cli_args(cfg, "--gate", '"%s" -c "import sys; sys.exit(0)"' % PY, "--runner-cmd", "true"))
    assert code == 2
    assert "passed the known-bad" in capsys.readouterr().err
    assert main(cli_args(cfg, "--gate", CMD_GATE)) == 2  # no runner command


def test_cli_takes_a_runner_object(run):
    cfg = run(n=1)
    assert main(cli_args(cfg, "--gate", CMD_GATE), runner=ok_runner) == 0


# metrics -----------------------------------------------------------------------------------------------

def test_summarize_samples():
    samples = [
        {"passed": True, "wall_clock_s": 2.0, "ttft_s": 0.5, "eval_tokens_per_s": 40.0},
        {"passed": True, "wall_clock_s": 4.0, "ttft_s": 1.0, "eval_tokens_per_s": 20.0},
        {"passed": False, "wall_clock_s": 4.0, "ttft_s": None, "eval_tokens_per_s": 0.0},
        {"passed": False, "wall_clock_s": 2.0, "ttft_s": 0.0},
    ]
    s = summarize_samples(samples)
    assert s == {
        "total_samples": 4, "passed_samples": 2, "pass_rate": 0.5,
        "accepted_outputs_per_s": round(2 / 12, 3), "mean_wall_clock_s": 3.0,
        "mean_ttft_s": 0.5, "mean_tokens_per_s": 30.0, "quality_adjusted_tokens_per_s": 15.0,
    }


def test_summarize_samples_empty_and_all_failed():
    assert summarize_samples([])["pass_rate"] == 0.0 and summarize_samples([])["accepted_outputs_per_s"] == 0.0
    failed = summarize_samples([{"passed": False, "wall_clock_s": 1.0, "eval_tokens_per_s": 50.0}])
    assert failed["quality_adjusted_tokens_per_s"] == 0.0 and failed["mean_tokens_per_s"] == 50.0


def test_output_diversity():
    assert output_diversity([]) == {"accepted": 0, "unique_outputs": 0, "duplicate_count": 0, "diversity_ratio": 1.0}
    d = output_diversity(["a", " a\n", "b", "c"])
    assert (d["unique_outputs"], d["duplicate_count"], d["diversity_ratio"]) == (3, 1, 0.75)
