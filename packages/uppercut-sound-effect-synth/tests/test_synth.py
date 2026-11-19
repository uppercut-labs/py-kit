import hashlib
import json
import wave

import numpy as np
import pytest

from uppercut_sound_effect_synth import (
    SOUNDS,
    Rng,
    band_noise,
    bandpass,
    derive_seed,
    envelope,
    exp_decay,
    fade,
    finish,
    highpass,
    lowpass,
    mix_into,
    pcm_bytes,
    render,
    render_pack,
    saturate,
    seconds,
    sweep,
    sweep_lowpass,
    tone,
    wav_bytes,
    write_wav,
)
from uppercut_sound_effect_synth.cli import main as cli_main

RATE = 48000


def band_power(x, lo, hi):
    spec = np.abs(np.fft.rfft(x)) ** 2
    freqs = np.fft.rfftfreq(len(x), 1 / RATE)
    return float(spec[(freqs >= lo) & (freqs < hi)].sum())


# ---- determinism (the main guarantee) ---------------------------------------------


@pytest.mark.parametrize("name", list(SOUNDS))
def test_same_seed_gives_identical_bytes(name):
    a = wav_bytes(render(name, seed=7), RATE, 24)
    b = wav_bytes(render(name, seed=7), RATE, 24)
    assert a == b
    assert hashlib.sha256(a).digest() == hashlib.sha256(b).digest()


def test_different_seed_or_take_changes_noise_sounds():
    base = wav_bytes(render("whoosh", seed=7))
    assert wav_bytes(render("whoosh", seed=8)) != base
    assert wav_bytes(render("whoosh", seed=7, take=1)) != base


def test_pack_is_byte_identical_across_runs(tmp_path):
    m1 = render_pack(tmp_path / "a", seed=3)
    m2 = render_pack(tmp_path / "b", seed=3)
    assert m1 == m2
    for name in SOUNDS:
        assert (tmp_path / "a" / f"{name}.wav").read_bytes() == (tmp_path / "b" / f"{name}.wav").read_bytes()
    assert (tmp_path / "a" / "manifest.json").read_bytes() == (tmp_path / "b" / "manifest.json").read_bytes()


def test_rng_sequences():
    a, b = Rng(5), Rng(5)
    assert np.array_equal(a.white(100), b.white(100))
    assert a.unit() == b.unit()
    assert not np.array_equal(Rng(5).white(50), Rng(6).white(50))


def test_rng_ranges_and_stats():
    r = Rng(1)
    u = r.unit(20000)
    assert u.min() >= 0 and u.max() < 1 and abs(u.mean() - 0.5) < 0.02
    n = Rng(2).normal(20001)
    assert len(n) == 20001 and abs(n.mean()) < 0.05 and abs(n.std() - 1) < 0.05
    assert 3.0 <= r.uniform(3.0, 4.0) < 4.0
    assert r.choice(["a"]) == "a"
    assert Rng(3).unit(0).shape == (0,)
    with pytest.raises(ValueError):
        r.choice([])


def test_derive_seed_is_stable_and_label_sensitive():
    assert derive_seed(7, "whoosh", 0) == derive_seed(7, "whoosh", 0)
    assert derive_seed(7, "whoosh", 0) != derive_seed(7, "whoosh", 1)
    assert derive_seed(7, "a") != derive_seed(8, "a")


# ---- DSP --------------------------------------------------------------------------


def test_envelope_shape():
    e = envelope(1000, attack=0.001, curve=2.0, rate=RATE)
    assert e[0] == 0 and e.max() == pytest.approx(1.0, abs=0.02)
    assert e[-1] == pytest.approx(0.0, abs=1e-9)
    assert np.all(np.diff(e[60:]) <= 1e-12)
    assert len(envelope(1)) == 1
    with pytest.raises(ValueError):
        envelope(0)


def test_exp_decay():
    e = exp_decay(RATE, 0.1, attack=0.01, rate=RATE)
    assert e[0] == 0 and e[RATE // 2] == pytest.approx(np.exp(-5), rel=0.05)
    with pytest.raises(ValueError):
        exp_decay(10, 0)


def test_filters_pass_and_stop():
    x = tone(100, RATE) + tone(8000, RATE)
    low = lowpass(x, 1000, rate=RATE)
    assert band_power(low, 7000, 9000) < 1e-3 * band_power(low, 50, 150)
    high = highpass(x, 2000, rate=RATE)
    assert band_power(high, 50, 150) < 1e-3 * band_power(high, 7000, 9000)
    band = bandpass(x + tone(1000, RATE), 800, 1200, rate=RATE)
    assert band_power(band, 900, 1100) > 100 * band_power(band, 50, 150)
    assert len(low) == len(x)


def test_filter_errors():
    x = np.zeros(100)
    with pytest.raises(ValueError):
        lowpass(x, 30000)
    with pytest.raises(ValueError):
        bandpass(x, 2000, 1000)
    with pytest.raises(ValueError):
        lowpass(x, 100, order=0)


def test_band_noise_is_band_limited_and_seeded():
    n = band_noise(Rng(1), 8192, 2000, 6000)
    assert band_power(n, 2000, 6000) > 20 * band_power(n, 0, 500)
    assert np.array_equal(n, band_noise(Rng(1), 8192, 2000, 6000))


def test_sweep_lowpass_darkens_over_time():
    x = Rng(1).white(RATE)
    y = sweep_lowpass(x, 8000, 300)
    assert len(y) == len(x)
    first, last = y[: RATE // 4], y[-RATE // 4 :]
    assert band_power(first, 4000, 20000) > 10 * band_power(last, 4000, 20000)
    assert np.isfinite(y).all()


def test_sweep_and_tone():
    f = sweep(100, 1000, 5)
    assert f[0] == pytest.approx(100) and f[-1] == pytest.approx(1000)
    assert sweep(100, 200, 3, log=False).tolist() == [100, 150, 200]
    with pytest.raises(ValueError):
        sweep(0, 100, 5)
    t = tone(440, 480, RATE)
    assert abs(t).max() <= 1.0 and len(t) == 480
    with pytest.raises(ValueError):
        tone(np.ones(3), 5)


def test_saturate_bounds_and_small_signal():
    big = saturate(np.array([-10.0, 10.0]), 3)
    assert np.all(np.abs(big) <= 1.0 + 1e-12)
    small = saturate(np.array([0.001]), 2)
    assert small[0] == pytest.approx(0.002, rel=1e-3)


def test_fade_and_mix_into():
    x = np.ones(1000)
    y = fade(x, 0.001, 0.002, RATE)
    assert y[0] == 0 and y[-1] == 0 and y[500] == 1
    buf = np.zeros(100)
    mix_into(buf, np.ones(50), at=60 / RATE, rate=RATE)
    assert buf[:60].sum() == 0 and buf[60:].sum() == 40
    mix_into(buf, np.ones(5), at=10.0, rate=RATE)
    with pytest.raises(ValueError):
        mix_into(buf, np.ones(5), at=-1)


def test_seconds_helper():
    assert seconds(0.5, RATE) == 24000
    assert seconds(0.0, RATE) == 1


def test_finish_levels_and_shape():
    y = finish(np.sin(np.linspace(0, 100, 4800)) * 0.2, peak_db=-6, tail=0.01)
    assert y.shape == (4800 + 480, 2)
    assert np.abs(y).max() == pytest.approx(10 ** (-6 / 20), rel=1e-6)
    assert np.all(y[-400:] == 0)
    mono = finish(np.ones(100), channels=1)
    assert mono.ndim == 1
    silent = finish(np.zeros(100), channels=1)
    assert np.all(silent == 0)
    with pytest.raises(ValueError):
        finish(np.ones(10), channels=3)
    with pytest.raises(ValueError):
        finish(np.ones(10), peak_db=2)


# ---- WAV -----------------------------------------------------------------------------


@pytest.mark.parametrize("bits", [8, 16, 24])
def test_wav_roundtrip_header(tmp_path, bits):
    audio = render("click", seed=1, channels=2)
    digest = write_wav(tmp_path / "x.wav", audio, RATE, bits)
    data = (tmp_path / "x.wav").read_bytes()
    assert hashlib.sha256(data).hexdigest() == digest
    with wave.open(str(tmp_path / "x.wav")) as w:
        assert w.getnchannels() == 2 and w.getframerate() == RATE and w.getsampwidth() == bits // 8
        assert w.getnframes() == len(audio)


def test_pcm_values():
    assert pcm_bytes(np.array([0.0, 1.0, -1.0, 2.0]), 16) == np.array([0, 32767, -32767, 32767], "<i2").tobytes()
    assert pcm_bytes(np.array([0.0, 1.0, -1.0]), 8) == bytes([128, 255, 1])
    assert pcm_bytes(np.array([1.0, -1.0]), 24) == bytes([0xFF, 0xFF, 0x7F, 0x01, 0x00, 0x80])
    with pytest.raises(ValueError):
        pcm_bytes(np.zeros(3), 12)


def test_wav_bytes_shape_errors():
    with pytest.raises(ValueError):
        wav_bytes(np.zeros((4, 3, 2)))
    assert wav_bytes(np.zeros(5), RATE, 8)[:4] == b"RIFF"


# ---- recipes and packs --------------------------------------------------------------------


@pytest.mark.parametrize("name", list(SOUNDS))
def test_each_recipe_renders_clean_audio(name):
    y = render(name, seed=7)
    assert y.ndim == 2 and y.shape[1] == 2 and len(y) > 1000
    assert np.isfinite(y).all()
    assert np.abs(y).max() == pytest.approx(10 ** (-3 / 20), rel=1e-6)


def test_render_options_and_errors():
    mono = render("tick", channels=1, peak_db=-12)
    assert mono.ndim == 1 and np.abs(mono).max() == pytest.approx(10 ** (-12 / 20), rel=1e-6)
    with pytest.raises(KeyError, match="unknown sound"):
        render("nope")


def test_custom_recipe_table():
    table = {"beep": lambda rng, rate: tone(1000, rate // 10, rate)}
    y = render("beep", sounds=table, channels=1)
    assert len(y) > RATE // 10


def test_render_pack_writes_manifest_and_preview(tmp_path):
    manifest = render_pack(tmp_path, ["click", "tick"], seed=2, bits=16, channels=1, preview=True)
    assert set(manifest["sounds"]) == {"click", "tick"}
    on_disk = json.loads((tmp_path / "manifest.json").read_text())
    assert on_disk == manifest
    for entry in manifest["sounds"].values():
        assert hashlib.sha256((tmp_path / entry["file"]).read_bytes()).hexdigest() == entry["sha256"]
    assert (tmp_path / "_preview.wav").exists()
    with pytest.raises(KeyError):
        render_pack(tmp_path, ["missing"])


def test_cli(tmp_path, capsys):
    assert cli_main(["--list"]) == 0
    assert "whoosh" in capsys.readouterr().out
    assert cli_main(["--out", str(tmp_path), "--only", "click,pop", "--bits", "16", "--mono"]) == 0
    assert (tmp_path / "click.wav").exists() and (tmp_path / "pop.wav").exists()
    assert cli_main(["--out", str(tmp_path), "--only", "bogus"]) == 2
