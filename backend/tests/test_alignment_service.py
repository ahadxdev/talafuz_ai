"""
Tests for the audio-based subtitle alignment layer (alignment_service).

Covers the spec's 8 cases:
  1. continuous speech stays continuous
  2. speech → long pause → speech produces a subtitle gap
  3. multiple pauses produce multiple gaps
  4. no speech / unusable VAD → caller falls back (None returned)
  5. very short speech region → no invalid zero-length subtitle
  6. normalization removes overlaps between adjacent cues
  7. long ASR segment with many chunks → chunks distributed inside speech
     regions, never across the whole segment
  8. overlapping ASR sentence boundaries → chronological, non-overlapping
     final subtitles

Plus: real FFmpeg silencedetect integration over a synthesized WAV
(tone / silence / tone) and the disabled-flag path.
"""
import subprocess
from types import SimpleNamespace

import pytest

from app import config
from app.services import alignment_service
from app.services.alignment_service import (
    AlignmentError,
    align_chunk_times,
    detect_speech_regions,
    normalize_subtitle_timing,
)


EPS = 1e-6


def _patch_regions(monkeypatch, regions):
    """Replace audio analysis with fixed synthetic speech regions."""
    monkeypatch.setattr(
        alignment_service,
        "detect_speech_regions",
        lambda audio_path, start_time=0.0, end_time=None: list(regions),
    )


def _weights(n, w=10.0):
    return [w] * n


def _assert_valid_cues(times):
    prev_end = None
    for start, end in times:
        assert start >= 0.0
        assert end > start
        if prev_end is not None:
            assert start >= prev_end - EPS, f"overlap at {start} after {prev_end}"
        prev_end = end


def _cue_inside_regions(times, regions):
    for start, end in times:
        assert any(
            r_start - EPS <= start and end <= r_end + EPS
            for r_start, r_end in regions
        ), f"cue ({start}, {end}) crosses a silence gap in {regions}"


# ---------------------------------------------------------------------------
# TEST 1 — continuous speech remains approximately continuous
# ---------------------------------------------------------------------------

def test_continuous_speech_stays_continuous(monkeypatch):
    regions = [(0.0, 10.0)]
    _patch_regions(monkeypatch, regions)
    result = align_chunk_times(_weights(5), "unused.wav", 0.0, 10.0)
    assert result is not None and result.mode == "vad"
    times = result.times
    assert len(times) == 5
    _assert_valid_cues(times)
    assert times[0][0] == pytest.approx(0.0, abs=EPS)
    assert times[-1][1] == pytest.approx(10.0, abs=EPS)
    # Cues are back-to-back inside one continuous region.
    for i in range(len(times) - 1):
        assert times[i][1] == pytest.approx(times[i + 1][0], abs=EPS)


# ---------------------------------------------------------------------------
# TEST 2 — speech, long pause, speech → subtitle gap during silence
# ---------------------------------------------------------------------------

def test_pause_produces_subtitle_gap(monkeypatch):
    regions = [(0.0, 5.0), (6.5, 10.0)]
    _patch_regions(monkeypatch, regions)
    result = align_chunk_times(_weights(4), "unused.wav", 0.0, 10.0)
    times = result.times
    assert len(times) == 4
    _assert_valid_cues(times)
    _cue_inside_regions(times, regions)
    # A real gap must exist between the two speech regions.
    gaps = [
        times[i + 1][0] - times[i][1] for i in range(len(times) - 1)
    ]
    assert max(gaps) >= 1.5 - EPS
    # No cue may be visible during the silence (5.0 → 6.5).
    for start, end in times:
        assert not (start < 5.0 - EPS and end > 6.5 + EPS)


# ---------------------------------------------------------------------------
# TEST 3 — multiple pauses → multiple gaps
# ---------------------------------------------------------------------------

def test_multiple_pauses_multiple_gaps(monkeypatch):
    regions = [(0.0, 4.0), (5.0, 9.0), (10.5, 14.0)]
    _patch_regions(monkeypatch, regions)
    result = align_chunk_times(_weights(6), "unused.wav", 0.0, 14.0)
    times = result.times
    assert len(times) == 6
    _assert_valid_cues(times)
    _cue_inside_regions(times, regions)
    gaps = [times[i + 1][0] - times[i][1] for i in range(len(times) - 1)]
    significant_gaps = [g for g in gaps if g > 0.5]
    assert len(significant_gaps) >= 2


# ---------------------------------------------------------------------------
# TEST 4 — no speech / unusable VAD → None (caller falls back)
# ---------------------------------------------------------------------------

def test_no_speech_regions_returns_none(monkeypatch):
    _patch_regions(monkeypatch, [])
    assert align_chunk_times(_weights(3), "unused.wav", 0.0, 10.0) is None


def test_alignment_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(config, "SUBTITLE_ALIGNMENT_ENABLED", False)
    assert align_chunk_times(_weights(3), "unused.wav", 0.0, 10.0) is None


def test_missing_audio_file_raises_alignment_error(tmp_path):
    # The caller catches AlignmentError and falls back to proportional timing.
    with pytest.raises(AlignmentError):
        align_chunk_times(_weights(3), tmp_path / "does_not_exist.wav", 0.0, 10.0)


def test_silent_wav_yields_no_regions(tmp_path):
    wav = tmp_path / "silence.wav"
    subprocess.run(
        [config.FFMPEG_BIN, "-y", "-f", "lavfi",
         "-i", "anullsrc=r=16000:cl=mono:d=4", str(wav)],
        capture_output=True, check=True,
    )
    assert detect_speech_regions(wav, 0.0, 4.0) == []


# ---------------------------------------------------------------------------
# TEST 5 — very short speech region → no invalid zero-length subtitle
# ---------------------------------------------------------------------------

def test_very_short_region_no_zero_length_cue(monkeypatch):
    _patch_regions(monkeypatch, [(3.0, 3.1)])
    result = align_chunk_times([8.0], "unused.wav", 0.0, 10.0)
    times = result.times
    assert len(times) == 1
    start, end = times[0]
    assert end > start
    assert end - start >= alignment_service.MIN_CUE_DURATION - EPS
    _assert_valid_cues(times)


def test_short_region_many_chunks_all_valid(monkeypatch):
    _patch_regions(monkeypatch, [(2.0, 2.12)])
    result = align_chunk_times(_weights(3), "unused.wav", 0.0, 10.0)
    _assert_valid_cues(result.times)


# ---------------------------------------------------------------------------
# TEST 6 — normalization: adjacent cues never overlap
# ---------------------------------------------------------------------------

def test_normalize_removes_overlaps_and_sorts():
    subs = [
        SimpleNamespace(start=5.0, end=7.0),
        SimpleNamespace(start=-0.4, end=1.2),
        SimpleNamespace(start=6.8, end=6.7),   # inverted + overlapping
        SimpleNamespace(start=1.0, end=2.5),
    ]
    normalize_subtitle_timing(subs)
    starts = [s.start for s in subs]
    assert starts == sorted(starts)
    for s in subs:
        assert s.start >= 0.0
        assert s.end > s.start
    for i in range(len(subs) - 1):
        assert subs[i].end <= subs[i + 1].start + EPS


# ---------------------------------------------------------------------------
# TEST 7 — long segment, many chunks: distributed inside speech regions
# ---------------------------------------------------------------------------

def test_long_segment_chunks_confined_to_speech_regions(monkeypatch):
    regions = [(0.5, 10.0), (12.0, 20.0), (23.0, 33.0)]
    _patch_regions(monkeypatch, regions)
    weights = [float(5 + (i % 4)) for i in range(24)]
    result = align_chunk_times(weights, "unused.wav", 0.0, 33.108)
    times = result.times
    assert len(times) == 24
    _assert_valid_cues(times)
    _cue_inside_regions(times, regions)
    # Chunks actually spread over all three regions (not piled in one).
    per_region = []
    for r_start, r_end in regions:
        per_region.append(sum(
            1 for s, e in times if s >= r_start - EPS and e <= r_end + EPS
        ))
    assert all(count > 0 for count in per_region)
    # No cue spans the whole segment (the old proportional behaviour did).
    assert max(e - s for s, e in times) <= config.SUBTITLE_MAX_DURATION + EPS


# ---------------------------------------------------------------------------
# TEST 8 — overlapping ASR boundaries → chronological, non-overlapping cues
# ---------------------------------------------------------------------------

def test_overlapping_asr_boundaries_normalized(monkeypatch):
    # Sentence 0: 0.000 → 33.108 ; Sentence 1: 32.716 → 38.848 (overlap).
    _patch_regions(monkeypatch, [(0.2, 32.9), (33.2, 38.8)])
    seg1 = align_chunk_times(_weights(3), "unused.wav", 0.0, 33.108)
    # Second call: regions are clipped to the second segment window by the
    # real detector; emulate by patching per-segment regions.
    _patch_regions(monkeypatch, [(33.2, 38.8)])
    seg2 = align_chunk_times(_weights(2), "unused.wav", 32.716, 38.848)

    subs = [
        SimpleNamespace(start=s, end=e)
        for s, e in (seg1.times + seg2.times)
    ]
    normalize_subtitle_timing(subs)
    for s in subs:
        assert s.start >= 0.0 and s.end > s.start
    for i in range(len(subs) - 1):
        assert subs[i].start <= subs[i + 1].start + EPS
        assert subs[i].end <= subs[i + 1].start + EPS


# ---------------------------------------------------------------------------
# Integration — real FFmpeg silencedetect over a synthesized WAV
# ---------------------------------------------------------------------------

def test_detect_speech_regions_real_ffmpeg(tmp_path):
    wav = tmp_path / "tone_silence_tone.wav"
    # 2 s tone + 1.5 s digital silence + 2 s tone (16 kHz mono PCM).
    subprocess.run(
        [config.FFMPEG_BIN, "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=2",
         "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=1.5",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=2",
         "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1[a]",
         "-map", "[a]", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
         str(wav)],
        capture_output=True, check=True,
    )
    regions = detect_speech_regions(wav, 0.0, 5.5)
    assert len(regions) == 2
    (s1, e1), (s2, e2) = regions
    # First tone: ~0–2 s, second tone: ~3.5–5.5 s (± padding/tolerance).
    assert s1 <= 0.3
    assert 1.7 <= e1 <= 2.4
    assert 3.3 <= s2 <= 3.9
    assert e2 >= 5.2
    # The meaningful silence between tones stays a gap.
    assert s2 - e1 >= 1.0


def test_detect_regions_clip_to_window(tmp_path):
    wav = tmp_path / "tone.wav"
    subprocess.run(
        [config.FFMPEG_BIN, "-y", "-f", "lavfi",
         "-i", "sine=frequency=440:sample_rate=16000:duration=6", str(wav)],
        capture_output=True, check=True,
    )
    regions = detect_speech_regions(wav, 2.0, 4.0)
    assert len(regions) == 1
    s, e = regions[0]
    assert s >= 2.0 - EPS and e <= 4.0 + EPS
