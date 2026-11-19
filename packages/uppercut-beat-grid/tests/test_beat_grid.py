import json
import wave

import numpy as np
import pytest

from uppercut_beat_grid import (
    align,
    beat_times,
    cut_times,
    detect,
    fit_tempo,
    frame_index,
    generate_bed,
    grid_from_detection,
    load_grid,
    make_grid,
    onset_envelope,
    parse_sections,
    pick_downbeat_phase,
    read_audio,
    save_grid,
    snap_cuts,
    snap_to_grid,
    with_half_beats,
    write_bed,
    write_wav24,
)
from uppercut_beat_grid.cli import main as cli_main

SR = 22050


def click_track(bpm, seconds=24.0, offset=0.13, accent_every=4, sr=SR, seed=0):
    """Short noise-burst clicks on every beat, louder on every `accent_every`-th."""
    rng = np.random.default_rng(seed)
    y = np.zeros(int(seconds * sr))
    spb = 60.0 / bpm
    k = 0
    t = offset
    while t < seconds - 0.1:
        n = int(0.012 * sr)
        burst = rng.standard_normal(n) * np.exp(-np.arange(n) / (0.003 * sr))
        amp = 1.0 if (accent_every and k % accent_every == 0) else 0.45
        i = int(round(t * sr))
        y[i : i + n] += burst[: len(y) - i] * amp
        t += spb
        k += 1
    return y / np.abs(y).max() * 0.8


def phase_error_ms(found_phase, period, true_phase):
    d = (found_phase - true_phase + period / 2) % period - period / 2
    return abs(d) * 1000


# ---- grid -------------------------------------------------------------------------


def test_make_grid_basic():
    g = make_grid(120, 4.0, offset=0.25, fps=60)
    assert g["seconds_per_beat"] == 0.5 and g["frames_per_beat"] == 30.0
    assert [b["t"] for b in g["beats"]] == [0.25 + 0.5 * i for i in range(8)]
    assert g["beats"][0] == {"i": 0, "t": 0.25, "bar": 1, "beat": 1, "frame": 15}
    assert g["beats"][5]["bar"] == 2 and g["beats"][5]["beat"] == 2
    assert g["half_beats"][0] == 0.5


def test_make_grid_beats_per_bar_and_empty():
    g = make_grid(90, 10, beats_per_bar=3)
    assert g["beats"][3]["bar"] == 2 and g["beats"][3]["beat"] == 1
    assert make_grid(120, 1.0, offset=2.0)["beats"] == []


@pytest.mark.parametrize("kwargs", [{"bpm": 0}, {"bpm": 120, "fps": 0}, {"bpm": 120, "beats_per_bar": 0}])
def test_make_grid_errors(kwargs):
    args = {"bpm": 120, "duration": 4.0, **kwargs}
    with pytest.raises(ValueError):
        make_grid(**args)
    with pytest.raises(ValueError):
        make_grid(120, -1)


def test_frame_index_and_beat_times():
    assert frame_index(0.5, 60) == 30
    g = make_grid(120, 2.0)
    assert beat_times(g) == [0.0, 0.5, 1.0, 1.5, 2.0]
    assert beat_times([0.1, {"t": 0.2}]) == [0.1, 0.2]


def test_snap_to_grid():
    beats = [0.0, 0.5, 1.0]
    assert snap_to_grid(0.52, beats) == 0.5
    assert snap_to_grid(0.52, beats, reach=0.05) == 0.5
    assert snap_to_grid(0.7, beats, reach=0.05) is None
    assert snap_to_grid(0.7, []) is None


def test_with_half_beats():
    assert with_half_beats([0.0, 1.0, 2.0]) == [0.0, 0.5, 1.0, 1.5, 2.0]
    assert with_half_beats([1.0]) == [1.0]


def test_save_and_load_grid(tmp_path):
    g = make_grid(100, 3.0)
    save_grid(g, tmp_path / "g.json")
    assert load_grid(tmp_path / "g.json") == g
    (tmp_path / "bad.json").write_text(json.dumps({"x": 1}))
    with pytest.raises(ValueError):
        load_grid(tmp_path / "bad.json")


# ---- align ---------------------------------------------------------------------------


def test_align_reports_hits_and_misses():
    beats = make_grid(120, 10)
    r = align([0.0, 0.51, 1.2, 2.0], beats, tolerance_ms=17)
    assert [x.ok for x in r.results] == [True, True, False, True]
    assert r.misses == 1 and not r.all_ok
    assert r.results[1].delta_ms == pytest.approx(10.0)
    assert r.results[2].nearest == 1.0


def test_align_accepts_cut_formats():
    assert cut_times({"cuts": [{"t": 1.0}, 2.0]}) == [1.0, 2.0]
    r = align({"cuts": [0.5]}, [0.0, 0.5, 1.0])
    assert r.all_ok


def test_align_half_beats():
    beats = [0.0, 1.0, 2.0]
    assert not align([0.5], beats).all_ok
    assert align([0.5], beats, half=True).all_ok


def test_align_auto_offset_removes_constant_lag():
    beats = make_grid(120, 10)
    cuts = [t + 0.040 for t in (0.5, 1.5, 2.5, 3.5)]
    assert not align(cuts, beats, tolerance_ms=17).all_ok
    r = align(cuts, beats, tolerance_ms=17, auto_offset=True)
    assert r.all_ok and r.offset_ms == pytest.approx(40.0)
    assert all(abs(x.residual_ms) < 1e-6 for x in r.results)


def test_align_errors():
    with pytest.raises(ValueError):
        align([1.0], [])
    with pytest.raises(ValueError):
        align([1.0], [0.0], tolerance_ms=-1)
    assert align([], [0.0]).all_ok


def test_snap_cuts():
    beats = [0.0, 0.5, 1.0]
    assert snap_cuts([0.46, 0.9, 3.0], beats) == [0.5, 1.0, 1.0]
    assert snap_cuts([0.46, 3.0], beats, reach=0.1) == [0.5, 3.0]
    assert snap_cuts([0.26], beats, half=True) == [0.25]
    with pytest.raises(ValueError):
        snap_cuts([1.0], [])


# ---- detection (synthetic click tracks, numpy backend) ----------------------------------------


@pytest.mark.parametrize("bpm,offset", [(120, 0.13), (90, 0.31), (140, 0.05)])
def test_fit_tempo_on_click_track(bpm, offset):
    env, hs = onset_envelope(click_track(bpm, offset=offset), SR)
    fit = fit_tempo(env, hs)
    assert fit["bpm"] == pytest.approx(bpm, abs=0.5)
    assert phase_error_ms(fit["phase"], fit["period"], offset) < 15
    assert fit["stable"]


def test_fit_tempo_bpm_hint_fixes_octave():
    env, hs = onset_envelope(click_track(120), SR)
    fit = fit_tempo(env, hs, bpm_hint=60)
    assert fit["bpm"] == pytest.approx(60, abs=0.5)
    assert len(fit["octaves"]) == 1


def test_fit_tempo_rejects_short_envelope():
    with pytest.raises(ValueError):
        fit_tempo(np.zeros(3), 0.003)


def test_detect_beats_downbeats_and_onsets():
    y = click_track(120, seconds=24, offset=0.13)
    r = detect(y, sr=SR)
    assert r["tempo"] == pytest.approx(120, abs=0.5)
    assert r["backend"] == "fit"
    first_down = r["downbeats"][0]
    # accents are on clicks 0, 4, 8...: downbeats sit at 0.13 + 2.0 * k
    assert phase_error_ms(first_down, 2.0, 0.13) < 20
    assert len(r["beats"]) in (47, 48)
    assert len(r["energy"]) == len(r["beats"]) and max(r["energy"]) == 1.0
    assert len(r["onsets"]) >= 40
    assert r["duration"] == pytest.approx(24.0, abs=0.01)


def test_pick_downbeat_phase_votes_on_strength():
    env = np.zeros(2000)
    hs = 0.01
    beats = np.arange(0, 16) * 0.5 + 0.1
    for i, b in enumerate(beats):
        env[int(round(b / hs))] = 3.0 if i % 4 == 2 else 1.0
    assert pick_downbeat_phase(env, hs, beats, 4) == 2
    assert pick_downbeat_phase(env, hs, beats[:2], 4) == 0


def test_detect_from_wav_file(tmp_path):
    y = click_track(100, seconds=16, offset=0.2)
    path = tmp_path / "clicks.wav"
    write_wav24(path, y, SR)
    r = detect(path)
    assert r["tempo"] == pytest.approx(100, abs=0.5)
    assert r["audio"] == "clicks.wav"


def test_detect_errors(tmp_path):
    with pytest.raises(ValueError):
        detect(np.zeros(1000))
    with pytest.raises(ValueError):
        detect(np.zeros(1000), sr=SR, backend="bogus")
    with pytest.raises(ValueError):
        onset_envelope(np.zeros(100), SR)


def test_grid_from_detection_numbers_bars_from_downbeat():
    r = {"tempo": 120.0, "beats": [0.1, 0.6, 1.1, 1.6, 2.1, 2.6], "beats_per_bar": 4, "downbeat_phase": 1,
         "downbeats": [0.6, 2.6], "duration": 3.0}
    g = grid_from_detection(r, fps=60)
    assert g["beats"][0]["bar"] == 0 and g["beats"][1]["bar"] == 1 and g["beats"][1]["beat"] == 1
    assert g["beats"][5]["bar"] == 2 and g["beats"][1]["frame"] == 36


def test_read_audio_roundtrip_and_sample_widths(tmp_path):
    x = np.array([0.0, 0.5, -0.5, 0.99])
    write_wav24(tmp_path / "a.wav", x, 8000)
    y, sr = read_audio(tmp_path / "a.wav")
    assert sr == 8000 and np.allclose(y, x, atol=1e-6)
    with wave.open(str(tmp_path / "b.wav"), "wb") as w:  # 16-bit stereo averages to mono
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(np.array([[16384, 0], [-16384, 0]], dtype="<i2").tobytes())
    y2, _ = read_audio(tmp_path / "b.wav")
    assert np.allclose(y2, [0.25, -0.25])


def test_read_audio_other_format_needs_soundfile(tmp_path):
    p = tmp_path / "x.flac"
    p.write_bytes(b"not audio")
    try:
        import soundfile  # noqa: F401
    except ImportError:
        with pytest.raises(ImportError, match="soundfile"):
            read_audio(p)
    else:
        with pytest.raises(Exception):
            read_audio(p)


def test_write_wav24_rejects_bad_shape(tmp_path):
    with pytest.raises(ValueError):
        write_wav24(tmp_path / "x.wav", np.zeros((4, 3)), 8000)


# ---- bed ------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def small_bed():
    return generate_bed(120, "intro:1,build:1,drop:2,outro:1", seed=3, fps=60, rate=16000)


def test_bed_grid_is_frame_indexed_and_exact(small_bed):
    g = small_bed.grid
    assert len(g["beats"]) == 5 * 4
    assert g["beats"][0] == {"i": 0, "t": 0.0, "bar": 1, "beat": 1, "frame": 0, "section": "intro"}
    assert g["beats"][5]["t"] == 2.5 and g["beats"][5]["frame"] == 150
    assert g["downbeats"] == [0.0, 2.0, 4.0, 6.0, 8.0]
    assert [s["name"] for s in g["sections"]] == ["intro", "build", "drop", "outro"]
    assert g["frames_per_beat"] == 30.0


def test_bed_audio_length_level_and_stems(small_bed):
    bed = small_bed
    assert len(bed.mix) == int((10.0 + 0.5) * 16000)
    assert np.abs(bed.mix).max() == pytest.approx(10 ** (-6 / 20), rel=1e-6)
    assert set(bed.stems) == {"drums", "bass", "pad", "arp"}
    assert np.allclose(sum(bed.stems.values()), bed.mix)
    assert np.abs(bed.stems["arp"][: 16000 * 4]).max() == 0  # no arp before the drop
    assert np.abs(bed.stems["arp"]).max() > 0
    assert np.isfinite(bed.mix).all()


def test_bed_is_deterministic_and_seed_sensitive():
    a = generate_bed(100, "groove:1", seed=5, rate=8000)
    b = generate_bed(100, "groove:1", seed=5, rate=8000)
    c = generate_bed(100, "groove:1", seed=6, rate=8000)
    assert np.array_equal(a.mix, b.mix) and a.grid == b.grid
    assert not np.array_equal(a.mix, c.mix)


def test_bed_kick_lands_on_beats():
    bed = generate_bed(120, "groove:2", rate=16000)
    kick = np.abs(bed.stems["drums"])
    for t in (0.0, 0.5, 1.0, 1.5):
        i = int(t * 16000)
        assert kick[i : i + 400].max() > 0.05


def test_bed_end_hit_and_gaps():
    bed = generate_bed(120, "drop:2", end_hit=True, gaps=[4], rate=8000)
    g = bed.grid
    assert g["beats"][-1]["section"] == "end-hit" and g["beats"][-1]["t"] == 4.0
    assert g["duration"] == 6.5 and g["music_end"] == 4.0
    assert len(bed.mix) == int(6.5 * 8000)
    # half a beat before beat 4 (1.75 s to 2.0 s) is silent in every stem
    lo, hi = int(1.76 * 8000), int(1.99 * 8000)
    assert all(np.abs(s[lo:hi]).max() == 0 for s in bed.stems.values())
    assert np.abs(bed.mix[int(4.0 * 8000) :]).max() > 0  # the ring-out


def test_bed_styles_and_keys_differ():
    base = generate_bed(120, "drop:1", rate=8000)
    lofi = generate_bed(120, "drop:1", style="lofi", rate=8000)
    major = generate_bed(120, "drop:1", scale="major", key="C", rate=8000)
    assert not np.array_equal(base.mix, lofi.mix) and not np.array_equal(base.mix, major.mix)
    assert major.grid["key"] == "C major"


@pytest.mark.parametrize(
    "kwargs",
    [{"bpm": 0}, {"style": "x"}, {"scale": "x"}, {"key": "H"}, {"fps": 0}, {"gaps": [99]}, {"sections": "intro:0"},
     {"sections": "nobars"}, {"sections": ""}],
)
def test_bed_errors(kwargs):
    args = {"bpm": 120, "sections": "intro:1", "rate": 8000, **kwargs}
    with pytest.raises(ValueError):
        generate_bed(**args)


def test_parse_sections():
    assert parse_sections("intro:2, drop:4") == [("intro", 2), ("drop", 4)]


def test_write_bed_files(tmp_path):
    bed = generate_bed(120, "intro:1,drop:1", rate=8000)
    paths = write_bed(bed, tmp_path / "bed")
    assert (tmp_path / "bed.wav").exists() and (tmp_path / "bed.grid.json").exists()
    for stem in ("drums", "bass", "pad", "arp"):
        assert (tmp_path / "bed.stems" / f"{stem}.wav").exists()
    with wave.open(paths["mix"]) as w:
        assert w.getnchannels() == 2 and w.getsampwidth() == 3 and w.getframerate() == 8000
    assert load_grid(paths["grid"])["bpm"] == 120
    again = write_bed(bed, tmp_path / "bed2")
    assert (tmp_path / "bed.wav").read_bytes() == (tmp_path / "bed2.wav").read_bytes()
    assert again["mix"].endswith("bed2.wav")


def test_detect_finds_the_tempo_of_a_bed():
    bed = generate_bed(128, "groove:8", rate=22050)
    r = detect(bed.mix, sr=22050)
    assert r["tempo"] == pytest.approx(128, abs=1.0)


# ---- cli ----------------------------------------------------------------------------------------


def test_cli_grid_and_align(tmp_path, capsys):
    gp = tmp_path / "g.json"
    assert cli_main(["grid", "--bpm", "120", "--duration", "5", "--json", str(gp)]) == 0
    cuts = tmp_path / "c.json"
    cuts.write_text(json.dumps({"cuts": [0.5, 1.0, 1.7]}))
    assert cli_main(["align", "--cuts", str(cuts), "--beats", str(gp)]) == 1
    assert "MISS" in capsys.readouterr().out
    cuts.write_text(json.dumps([0.5, 1.01]))
    assert cli_main(["align", "--cuts", str(cuts), "--beats", str(gp)]) == 0


def test_cli_detect_and_bed(tmp_path, capsys):
    wav = tmp_path / "c.wav"
    write_wav24(wav, click_track(110, seconds=16), SR)
    out = tmp_path / "d.json"
    assert cli_main(["detect", str(wav), "--json", str(out)]) == 0
    assert load_grid(out)["bpm"] == pytest.approx(110, abs=0.5)
    assert cli_main(["bed", "--bpm", "120", "--sections", "intro:1", "--out", str(tmp_path / "b")]) == 0
    assert cli_main(["bed", "--bpm", "120", "--sections", "oops", "--out", str(tmp_path / "b")]) == 2


def test_cli_detect_missing_librosa_message(tmp_path, capsys):
    pytest.importorskip("numpy")
    try:
        import librosa  # noqa: F401
    except ImportError:
        assert cli_main(["detect", str(tmp_path / "x.wav"), "--backend", "librosa"]) == 2
        assert "librosa" in capsys.readouterr().err


def test_librosa_backend_when_installed():
    pytest.importorskip("librosa")
    r = detect(click_track(120, seconds=20), sr=SR, backend="librosa")
    assert r["tempo"] == pytest.approx(120, abs=3) or r["tempo"] == pytest.approx(60, abs=2) or r["tempo"] == pytest.approx(240, abs=5)
    assert len(r["beats"]) > 10
