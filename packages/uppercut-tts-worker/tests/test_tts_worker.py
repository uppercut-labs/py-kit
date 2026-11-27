import io
import json
import os
import struct
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from uppercut_tts_worker import (
    GpuLock,
    LockBusy,
    WorkerError,
    WorkerProcess,
    main,
    make_request,
    process_alive,
    raise_tree_priority,
    read_message,
    run_job,
    serve,
    wav_seconds,
    write_message,
)

HERE = Path(__file__).resolve().parent
FAKE = str(HERE / "fake_worker.py")
PY = sys.executable


def job_for(tmp_path, items, **extra):
    return {"worker": {"python": PY, "script": FAKE, "args": ["m1"], "env": {"FAKE_WORKER_MARK": "from-env"}},
            "lock": str(tmp_path / "runs" / ".gpu.lock"), "out_dir": str(tmp_path / "out"), "items": items, **extra}


def quiet(job, **kwargs):
    lines = []
    code = run_job(job, log=lines.append, lock_log=lines.append, **kwargs)
    return code, lines


# protocol ----------------------------------------------------------------------------------------------

def test_read_message_skips_noise_and_non_objects():
    stream = io.StringIO('log line\n\n[1, 2]\n"text"\n{"ok": true}\n{broken\n{"second": 2}\n')
    assert read_message(stream) == {"ok": True}
    assert read_message(stream) == {"second": 2}
    assert read_message(stream) is None


def test_write_message_is_one_line_and_utf8():
    out = io.StringIO()
    write_message(out, {"text": "café"})
    assert out.getvalue() == '{"text": "café"}\n'


def test_make_request_leaves_out_an_empty_voice():
    assert make_request("hi", "a.wav") == {"text": "hi", "output_path": "a.wav", "seed": None, "settings": {}}
    assert make_request("hi", "a.wav", seed=5, settings={"language": "English"}, voice_id="v1") == {
        "text": "hi", "output_path": "a.wav", "seed": 5, "settings": {"language": "English"}, "voice_id": "v1"}
    assert "voice_id" not in make_request("hi", "a.wav", voice_id="")


def test_serve_answers_each_request_and_survives_a_failure():
    stdin = io.StringIO('{"text": "a"}\nnoise\n{"text": "boom"}\n{"text": "c"}\n')
    stdout = io.StringIO()

    def synth(request):
        if request["text"] == "boom":
            raise ValueError("bad sentence")
        return {"samples": 3}

    assert serve(synth, ready={"device": "x"}, stdin=stdin, stdout=stdout) == 0
    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert lines[0] == {"ready": True, "device": "x"}
    assert lines[1]["ok"] is True and lines[1]["samples"] == 3 and "wall_s" in lines[1]
    assert lines[2]["ok"] is False and lines[2]["error"] == "ValueError: bad sentence"
    assert lines[3]["ok"] is True and len(lines) == 4


# wav ---------------------------------------------------------------------------------------------------

def test_wav_seconds_reads_float_and_pcm_files(tmp_path):
    import wave

    sys.path.insert(0, str(HERE))
    from fake_worker import write_float_wav

    write_float_wav(str(tmp_path / "f.wav"), 1.5, rate=24000)
    assert wav_seconds(tmp_path / "f.wav") == pytest.approx(1.5)
    with wave.open(str(tmp_path / "p.wav"), "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 2 * 4000)
    assert wav_seconds(tmp_path / "p.wav") == pytest.approx(0.5)


def test_wav_seconds_skips_odd_sized_chunks_and_rejects_junk(tmp_path):
    fmt = struct.pack("<HHIIHH", 1, 1, 1000, 2000, 2, 16)
    body = (b"WAVE" + b"LIST" + struct.pack("<I", 3) + b"abc" + b"\x00"
            + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", 2000) + b"\x00" * 2000)
    (tmp_path / "x.wav").write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    assert wav_seconds(tmp_path / "x.wav") == pytest.approx(1.0)
    (tmp_path / "n.wav").write_bytes(b"not a wav at all")
    with pytest.raises(ValueError):
        wav_seconds(tmp_path / "n.wav")
    (tmp_path / "e.wav").write_bytes(b"RIFF\x04\x00\x00\x00WAVE")
    with pytest.raises(ValueError):
        wav_seconds(tmp_path / "e.wav")


# lock --------------------------------------------------------------------------------------------------

def test_lock_is_taken_and_released(tmp_path):
    path = tmp_path / "sub" / ".gpu.lock"
    with GpuLock(path) as lock:
        assert path.read_text(encoding="utf-8") == str(os.getpid()) and lock.held
    assert not path.exists()


def test_a_live_holder_blocks_and_is_never_deleted(tmp_path):
    path = tmp_path / ".gpu.lock"
    path.write_text(str(os.getpid()), encoding="utf-8")  # this process is alive
    with pytest.raises(LockBusy, match="GPU busy: pid %d still holds" % os.getpid()):
        GpuLock(path).acquire()
    assert path.read_text(encoding="utf-8") == str(os.getpid())


def test_a_stale_lock_is_cleared(tmp_path):
    path = tmp_path / ".gpu.lock"
    dead = subprocess.Popen([PY, "-c", "pass"])
    dead.wait()
    path.write_text(str(dead.pid), encoding="utf-8")
    with GpuLock(path):
        assert path.read_text(encoding="utf-8") == str(os.getpid())


def test_waiting_for_a_holder_that_lets_go(tmp_path):
    path = tmp_path / ".gpu.lock"
    path.write_text(str(os.getpid()), encoding="utf-8")
    messages, sleeps = [], []

    def sleep(seconds):
        sleeps.append(seconds)
        path.unlink()  # the holder finishes during the first wait

    with GpuLock(path, wait_s=60, poll_s=5, on_wait=messages.append, sleep=sleep):
        assert path.read_text(encoding="utf-8") == str(os.getpid())
    assert sleeps == [5]
    assert "waiting up to 60 s" in messages[0] and messages[-1] == "GPU lock acquired"


def test_waiting_gives_up_after_wait_s(tmp_path):
    path = tmp_path / ".gpu.lock"
    path.write_text(str(os.getpid()), encoding="utf-8")
    with pytest.raises(LockBusy, match="after 0.2 s"):
        GpuLock(path, wait_s=0.2, poll_s=0.05).acquire()


def test_release_leaves_a_lock_that_names_another_process(tmp_path):
    path = tmp_path / ".gpu.lock"
    lock = GpuLock(path).acquire()
    path.write_text("999999", encoding="utf-8")
    lock.release()
    assert path.read_text(encoding="utf-8") == "999999"


def test_a_lock_with_junk_in_it_counts_as_held(tmp_path):
    path = tmp_path / ".gpu.lock"
    path.write_text("not a pid", encoding="utf-8")
    with pytest.raises(LockBusy):
        GpuLock(path).acquire()
    assert path.exists()


def test_only_one_of_two_threads_gets_the_lock(tmp_path):
    path = tmp_path / ".gpu.lock"
    winners, barrier = [], threading.Barrier(2)

    def take():
        barrier.wait()
        try:
            GpuLock(path).acquire()
            winners.append(1)
        except LockBusy:
            pass

    threads = [threading.Thread(target=take) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert winners == [1]


def test_process_alive():
    assert process_alive(os.getpid())
    dead = subprocess.Popen([PY, "-c", "pass"])
    dead.wait()
    assert not process_alive(dead.pid)


def test_raise_tree_priority_does_nothing_off_windows():
    if os.name != "nt":
        assert raise_tree_priority(os.getpid()) == []


@pytest.mark.skipif(os.name != "nt", reason="the Win32 calls")
def test_windows_process_alive_and_priority_on_a_live_child():
    import ctypes

    child = subprocess.Popen([PY, "-c", "import time; time.sleep(30)"])
    try:
        assert process_alive(child.pid)
        assert child.pid in raise_tree_priority(child.pid, attempts=1)
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetPriorityClass.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x1000, False, child.pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        try:
            assert kernel32.GetPriorityClass(handle) == 0x8000  # ABOVE_NORMAL_PRIORITY_CLASS
        finally:
            kernel32.CloseHandle(handle)
    finally:
        child.kill()
        child.wait()
    assert not process_alive(child.pid)


# worker process ----------------------------------------------------------------------------------------

def test_worker_round_trip(tmp_path):
    with WorkerProcess(PY, FAKE, ["m1"], env={"FAKE_WORKER_MARK": "x"}, log_path=tmp_path / "w.log") as worker:
        assert worker.ready["device"] == "fake:0" and worker.ready["mark"] == "m1"
        reply = worker.request(make_request("hello world", str(tmp_path / "a.wav"), seed=7, voice_id="v9"))
        assert reply["ok"] and reply["echo_voice"] == "v9" and reply["echo_seed"] == 7 and reply["env"] == "x"
        assert wav_seconds(tmp_path / "a.wav") == pytest.approx(len("hello world") / 100.0, abs=0.001)
        assert worker.request(make_request("a NOISE b", str(tmp_path / "b.wav")))["ok"]  # stray lines are skipped
        failed = worker.request(make_request("FAIL", str(tmp_path / "c.wav")))
        assert failed["ok"] is False and "cannot say that" in failed["error"]
        assert worker.request(make_request("still alive", str(tmp_path / "d.wav")))["ok"]
    assert worker.proc is None


def test_worker_that_never_becomes_ready(tmp_path):
    worker = WorkerProcess(PY, "-c", ["pass"], log_path=tmp_path / "w.log").start()
    with pytest.raises(WorkerError, match="did not become ready; see .*w.log"):
        worker.wait_ready()


def test_worker_that_dies_mid_request(tmp_path):
    with pytest.raises(WorkerError, match="exited mid-job"):
        with WorkerProcess(PY, FAKE) as worker:
            worker.request(make_request("CRASH", str(tmp_path / "a.wav")))


# job ---------------------------------------------------------------------------------------------------

def items(*texts):
    return [{"id": "s%03d" % i, "text": t, "seed": 100 + i, "settings": {"language": "English"}} for i, t in enumerate(texts, 1)]


def test_job_generates_everything_and_releases_the_lock(tmp_path):
    job = job_for(tmp_path, items("one", "two two", "three"))
    code, lines = quiet(job)
    assert code == 0 and lines[0] == "3/3 items to generate"
    assert "worker ready on fake:0 (float32)" in lines[1]
    out = tmp_path / "out"
    assert sorted(p.name for p in out.glob("*.wav")) == ["s001.wav", "s002.wav", "s003.wav"]
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert results["s002"]["ok"] and results["s002"]["seed"] == 102 and results["s002"]["sample_rate"] == 24000
    assert results["s002"]["text_sha256"] and (out / "worker.log").exists()
    assert not Path(job["lock"]).exists()


def test_job_resumes_and_redoes_an_edited_sentence_or_seed(tmp_path):
    job = job_for(tmp_path, items("one", "two", "three"))
    quiet(job)
    before = (tmp_path / "out" / "s001.wav").stat().st_mtime_ns
    code, lines = quiet(job)
    assert code == 0 and lines == ["0/3 items to generate"]
    job["items"][1]["text"] = "two, reworded"
    job["items"][2]["seed"] = 999
    code, lines = quiet(job)
    assert lines[0] == "2/3 items to generate"
    assert (tmp_path / "out" / "s001.wav").stat().st_mtime_ns == before
    os.remove(tmp_path / "out" / "s001.wav")
    assert quiet(job)[1][0] == "1/3 items to generate"


def test_job_with_nothing_to_do_takes_no_lock(tmp_path):
    job = job_for(tmp_path, items("one"))
    quiet(job)
    Path(job["lock"]).write_text(str(os.getpid()), encoding="utf-8")  # a live holder
    assert quiet(job)[0] == 0


def test_job_reports_a_failed_item_and_keeps_going(tmp_path):
    job = job_for(tmp_path, items("fine", "FAIL here", "also fine"))
    code, lines = quiet(job)
    assert code == 1 and any(line.startswith("[FAILED] s002") for line in lines)
    results = json.loads((tmp_path / "out" / "results.json").read_text(encoding="utf-8"))
    assert results["s002"]["ok"] is False and "cannot say that" in results["s002"]["error"]
    assert results["s003"]["ok"] is True and not (tmp_path / "out" / "s002.wav").exists()


def test_job_waits_for_the_lock_then_runs(tmp_path):
    job = job_for(tmp_path, items("one"), lock_wait_s=0.2)
    Path(job["lock"]).parent.mkdir(parents=True)
    Path(job["lock"]).write_text(str(os.getpid()), encoding="utf-8")
    with pytest.raises(LockBusy):
        run_job(job, log=lambda line: None, lock_log=lambda line: None)
    assert Path(job["lock"]).read_text(encoding="utf-8") == str(os.getpid())


def test_job_voice_and_settings_reach_the_worker(tmp_path):
    job = job_for(tmp_path, [{"id": "a", "text": "hello", "voice_id": "v2"}, {"id": "b", "text": "hi", "settings": {"language": "French"}}])
    quiet(job)
    assert (tmp_path / "out" / "a.wav").exists() and (tmp_path / "out" / "b.wav").exists()


def test_job_ids_must_be_filename_safe(tmp_path):
    with pytest.raises(ValueError, match="not filename safe"):
        run_job(job_for(tmp_path, [{"id": "../escape", "text": "x"}]), log=lambda line: None)


def test_command_line(tmp_path, capsys):
    job_file = tmp_path / "job.json"
    job_file.write_text(json.dumps(job_for(tmp_path, items("one", "two"))), encoding="utf-8")
    assert main([str(job_file)]) == 0
    assert "2/2 items to generate" in capsys.readouterr().out
    assert main([]) == 2
    bad = job_for(tmp_path, items("CRASH now"))
    bad["out_dir"] = str(tmp_path / "out2")
    job_file.write_text(json.dumps(bad), encoding="utf-8")
    assert main([str(job_file)]) == 1
    assert "exited mid-job" in capsys.readouterr().err


def test_module_runs_as_a_command(tmp_path):
    job_file = tmp_path / "job.json"
    job_file.write_text(json.dumps(job_for(tmp_path, items("one"))), encoding="utf-8")
    r = subprocess.run([PY, "-m", "uppercut_tts_worker", str(job_file)], capture_output=True, text=True)
    assert r.returncode == 0 and "1/1 items to generate" in r.stdout
