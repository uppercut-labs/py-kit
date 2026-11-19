import os
import subprocess
import sys
import threading
import time

import pytest

from uppercut_subprocess_runner import CommandError, RunResult, kill_tree, popen_group, run

PY = sys.executable
POSIX = os.name != "nt"
posix_only = pytest.mark.skipif(not POSIX, reason="needs POSIX process groups and signals")


def py(code, **kwargs):
    return run([PY, "-c", code], **kwargs)


def pid_alive(pid):
    if POSIX:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid], capture_output=True, text=True).stdout
    return str(pid) in out


def wait_dead(pid, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    return not pid_alive(pid)


# --- normal runs -------------------------------------------------------------------------------------------


def test_captures_stdout_stderr_and_exit_code():
    r = py("import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)")
    assert isinstance(r, RunResult)
    assert r.exit_code == 3
    assert r.stop_reason == "exited"
    assert r.stdout_text.strip() == "out"
    assert r.stderr_text.strip() == "err"
    assert not r.ok and not r.timed_out and not r.cancelled and not r.truncated
    assert r.duration > 0 and r.pid


def test_ok_on_zero_exit():
    r = py("print('hi')")
    assert r.ok and r.exit_code == 0


def test_stdin_input_is_delivered_and_default_stdin_is_closed():
    r = py("import sys; print(sys.stdin.read().upper())", input=b"hello")
    assert r.stdout_text.strip() == "HELLO"
    r = py("import sys; print(repr(sys.stdin.read()))", timeout=10)
    assert r.stdout_text.strip() == "''"


def test_large_input_does_not_deadlock():
    data = b"x" * 3_000_000
    r = py("import sys; d = sys.stdin.buffer.read(); print(len(d))", input=data, timeout=30)
    assert r.stdout_text.strip() == str(len(data))


def test_cwd_and_env(tmp_path):
    r = py("import os; print(os.getcwd()); print(os.environ['RUNNER_TEST'])", cwd=tmp_path,
           env={**os.environ, "RUNNER_TEST": "value"})
    lines = r.stdout_text.split()
    assert os.path.realpath(lines[0]) == os.path.realpath(str(tmp_path))
    assert lines[1] == "value"


def test_accepts_path_like_in_argv(tmp_path):
    script = tmp_path / "s.py"
    script.write_text("print('ran')")
    r = run([PY, script])
    assert r.stdout_text.strip() == "ran"


def test_on_output_receives_stream_names_and_chunks():
    seen = []
    py("import sys; print('a'); print('b', file=sys.stderr)", on_output=lambda name, chunk: seen.append((name, chunk)))
    assert ("stdout", b"a") in [(n, c.strip()) for n, c in seen]
    assert ("stderr", b"b") in [(n, c.strip()) for n, c in seen]


def test_failing_callback_does_not_break_capture():
    def boom(name, chunk):
        raise RuntimeError("no")

    r = py("print('still captured')", on_output=boom)
    assert r.stdout_text.strip() == "still captured"


# --- spawn failure and check ---------------------------------------------------------------------------------


def test_spawn_failure_is_a_result():
    r = run(["definitely-not-a-real-command-xyz"])
    assert r.stop_reason == "spawn-failed"
    assert r.exit_code is None and r.pid is None
    assert r.error
    assert not r.ok


def test_check_raises_with_stderr_tail():
    with pytest.raises(CommandError) as info:
        py("import sys; print('boom detail', file=sys.stderr); sys.exit(2)", check=True)
    assert "exited with code 2" in str(info.value)
    assert "boom detail" in str(info.value)
    assert info.value.result.exit_code == 2


def test_check_falls_back_to_stdout_tail():
    with pytest.raises(CommandError) as info:
        py("print('only stdout'); raise SystemExit(1)", check=True)
    assert "only stdout" in str(info.value)


def test_check_raises_on_spawn_failure_and_timeout():
    with pytest.raises(CommandError):
        run(["definitely-not-a-real-command-xyz"], check=True)
    with pytest.raises(CommandError) as info:
        py("import time; time.sleep(30)", timeout=0.3, grace=0.2, check=True)
    assert "timeout" in str(info.value)


def test_check_returncode_returns_the_result_when_ok():
    r = py("pass")
    assert r.check_returncode() is r


def test_error_message_does_not_echo_arguments():
    with pytest.raises(CommandError) as info:
        run([PY, "-c", "import sys; sys.exit(1)", "SECRET-ARG"], check=True)
    assert "SECRET-ARG" not in str(info.value)


# --- timeout -----------------------------------------------------------------------------------------------


def test_timeout_stops_the_command_and_keeps_partial_output():
    r = py("import time; print('partial', flush=True); time.sleep(30)", timeout=1.0, grace=0.5)
    assert r.timed_out
    assert r.stop_reason == "timeout"
    assert r.stdout_text.strip() == "partial"
    assert r.duration < 10
    assert r.kill is not None
    assert not pid_alive(r.pid)


@posix_only
def test_term_ignoring_child_is_killed_after_the_grace_period():
    code = (
        "import signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n"
    )
    r = py(code, timeout=1.0, grace=0.5)
    assert r.timed_out
    assert r.kill.forced and not r.kill.graceful
    assert r.exit_code == -9
    assert r.duration >= 1.4


@posix_only
def test_well_behaved_child_stops_on_sigterm_without_force():
    r = py("import time; print('x', flush=True); time.sleep(60)", timeout=0.8, grace=5.0)
    assert r.timed_out
    assert r.kill.graceful and not r.kill.forced
    assert r.exit_code == -15
    assert r.duration < 4


@posix_only
def test_timeout_kills_the_whole_process_tree():
    code = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    r = py(code, timeout=2.0, grace=0.5)
    assert r.timed_out
    grandchild = int(r.stdout_text.split()[0])
    assert wait_dead(grandchild)


@posix_only
def test_descendant_holding_the_pipes_does_not_hang_the_call():
    code = (
        "import subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(child.pid, flush=True)\n"
    )
    start = time.monotonic()
    r = py(code, drain_timeout=0.3, grace=0.5)
    assert time.monotonic() - start < 10
    assert r.stop_reason == "exited" and r.exit_code == 0
    assert wait_dead(int(r.stdout_text.split()[0]))


@posix_only
def test_leader_only_mode_leaves_descendants_alone():
    code = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    r = py(code, timeout=1.5, grace=0.3, kill_process_tree=False, drain_timeout=0.2)
    grandchild = int(r.stdout_text.split()[0])
    try:
        assert r.timed_out
        assert pid_alive(grandchild)
    finally:
        os.kill(grandchild, 9)


# --- output cap --------------------------------------------------------------------------------------------


def test_output_limit_stops_the_command_and_marks_truncated():
    code = "import sys\nwhile True:\n    sys.stdout.write('x' * 1000)\n    sys.stdout.flush()\n"
    r = py(code, max_output=5000, timeout=20, grace=0.5)
    assert r.stop_reason == "output-limit"
    assert r.truncated
    assert len(r.stdout) == 5000
    assert not r.timed_out
    assert r.duration < 15


def test_output_limit_without_kill_drains_and_discards_the_excess():
    r = py("print('y' * 20000)", max_output=100, stop_on_output_limit=False, timeout=20)
    assert r.stop_reason == "exited" and r.exit_code == 0
    assert r.truncated
    assert len(r.stdout) == 100
    assert r.stdout_tail.endswith(b"y\n") or r.stdout_tail.endswith(b"y\r\n")


def test_output_exactly_at_the_cap_is_not_truncated():
    r = py("import sys; sys.stdout.write('z' * 50)", max_output=50)
    assert len(r.stdout) == 50
    assert not r.truncated


def test_cap_applies_to_each_stream_and_stderr_can_trigger_it():
    code = "import sys\nwhile True:\n    sys.stderr.write('e' * 1000)\n    sys.stderr.flush()\n"
    r = py(code, max_output=3000, timeout=20, grace=0.5)
    assert r.stop_reason == "output-limit" and r.truncated
    assert len(r.stderr) == 3000


def test_no_cap_when_max_output_is_none():
    r = py("print('q' * 200000)", max_output=None)
    assert len(r.stdout) > 200000 and not r.truncated


def test_tail_keeps_the_end_even_when_the_head_is_capped():
    r = py("print('a' * 5000 + 'THE-END')", max_output=100, tail_bytes=20, stop_on_output_limit=False)
    assert b"THE-END" in r.stdout_tail
    assert len(r.stdout_tail) <= 20
    assert b"THE-END" not in r.stdout


def test_tail_bytes_zero_disables_the_tail():
    r = py("print('hello')", tail_bytes=0)
    assert r.stdout_tail == b""


# --- cancel ------------------------------------------------------------------------------------------------


def test_cancel_stops_the_command():
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    r = py("import time; print('go', flush=True); time.sleep(60)", cancel=cancel, grace=0.5)
    assert r.cancelled and r.stop_reason == "cancelled"
    assert r.stdout_text.strip() == "go"
    assert r.duration < 10
    assert not pid_alive(r.pid)


def test_cancel_set_before_start_returns_quickly():
    cancel = threading.Event()
    cancel.set()
    r = py("import time; time.sleep(60)", cancel=cancel, grace=0.3)
    assert r.cancelled
    assert r.duration < 10


def test_cancel_that_is_never_set_is_harmless():
    r = py("print(1)", cancel=threading.Event())
    assert r.ok


# --- argument validation ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [{"timeout": 0}, {"timeout": -1}, {"max_output": 0}, {"tail_bytes": -1}, {"grace": -1}],
)
def test_invalid_options_raise_value_error(kwargs):
    with pytest.raises(ValueError):
        run([PY, "-c", "pass"], **kwargs)


def test_empty_argv_raises_value_error():
    with pytest.raises(ValueError):
        run([])


# --- kill_tree ---------------------------------------------------------------------------------------------


@posix_only
def test_kill_tree_stops_a_popen_group_and_its_children():
    code = (
        "import subprocess, sys, time\n"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(c.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    proc = popen_group([PY, "-c", code], stdout=subprocess.PIPE)
    grandchild = int(proc.stdout.readline())
    info = kill_tree(proc, grace=2.0)
    assert proc.poll() is not None
    assert info.method == "killpg"
    assert wait_dead(grandchild)
    proc.stdout.close()


def test_kill_tree_on_an_exited_process_reports_already_exited():
    proc = popen_group([PY, "-c", "pass"])
    proc.wait()
    info = kill_tree(proc, grace=0.2)
    assert info.already_exited


@posix_only
def test_kill_tree_refuses_the_callers_own_group():
    proc = subprocess.Popen([PY, "-c", "import time; time.sleep(30)"])  # same group as the test run
    try:
        with pytest.raises(ValueError):
            kill_tree(proc)
    finally:
        proc.kill()
        proc.wait()


@posix_only
def test_kill_tree_with_zero_grace_goes_straight_to_sigkill():
    proc = popen_group([PY, "-c", "import time; time.sleep(30)"])
    info = kill_tree(proc, grace=0)
    assert info.forced
    assert proc.returncode == -9


def test_kill_tree_rejects_negative_grace():
    with pytest.raises(ValueError):
        kill_tree(1, grace=-1)


@posix_only
def test_kill_tree_accepts_a_plain_pid():
    proc = popen_group([PY, "-c", "import time; time.sleep(30)"])
    kill_tree(proc.pid, grace=1.0)
    proc.wait(timeout=5)
    assert proc.returncode is not None
