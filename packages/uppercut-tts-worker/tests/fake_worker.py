"""A fake synthesizer that speaks the worker protocol. It writes a silent float WAV whose length follows the text."""
import os
import struct
import sys

from uppercut_tts_worker import serve


def write_float_wav(path, seconds, rate=24000):
    frames = int(seconds * rate)
    data = struct.pack("<%df" % frames, *([0.0] * frames))
    fmt = struct.pack("<HHIIHH", 3, 1, rate, rate * 4, 4, 32)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(data)) + data
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", len(body)) + body)
    return frames, rate


def synthesize(request):
    text = request["text"]
    if "CRASH" in text:
        os._exit(3)
    if "FAIL" in text:
        raise RuntimeError("cannot say that")
    if "NOISE" in text:
        print("a library log line that is not JSON", flush=True)
        print("[1, 2, 3]", flush=True)
    frames, rate = write_float_wav(request["output_path"], len(text) / 100.0)
    return {"sample_rate": rate, "samples": frames, "echo_voice": request.get("voice_id"),
            "echo_seed": request.get("seed"), "echo_settings": request.get("settings"),
            "env": os.environ.get("FAKE_WORKER_MARK")}


if __name__ == "__main__":
    mark = sys.argv[1] if len(sys.argv) > 1 else "none"
    sys.exit(serve(synthesize, ready={"device": "fake:0", "dtype": "float32", "mark": mark}))
