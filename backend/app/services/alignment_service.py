"""
Subtitle timing alignment — local audio-based speech/silence detection.

Problem
-------
Qwen3-ASR filetrans returns SENTENCE-level timestamps only. A single ASR
sentence can span tens of seconds including real pauses. Splitting such a
sentence into short cues and distributing the whole sentence duration
proportionally (romanization_service.distribute_timestamps) makes cues span
silence and overlap at slightly-overlapping ASR sentence boundaries.

Approach
--------
This module is the timing layer; the LLM never decides timestamps.

- Speech/silence detection runs LOCALLY through the existing FFmpeg
  dependency (`silencedetect` audio filter) over the already-extracted
  audio.wav — no extra cloud API key and no heavy ML dependency (Silero
  VAD would require torch, which is disproportionate for this MVP).
- Silence intervals are inverted into speech regions, false positives are
  dropped, regions are padded and merged, then clipped to the ASR segment
  being aligned (ASR timestamps stay the outer boundaries).
- Subtitle chunks are allocated across speech regions proportionally to
  their speaking-time weights, then distributed WITHIN each continuous
  speech region. A cue therefore never crosses a meaningful pause. VAD is
  used for macro timing only — one speech region can hold many cues.
- normalize_subtitle_timing() is a final validation pass guaranteeing
  chronological order, non-negative timestamps, end > start and no
  overlap between adjacent cues (handles overlapping ASR boundaries).

Fallback
--------
align_chunk_times() returns None whenever alignment is disabled or the
audio analysis yields no usable speech regions, and raises AlignmentError
on analysis failures. The caller (romanization_service) falls back to the
proportional distributor in both cases, so subtitle generation never fails
because of alignment.
"""
import logging
import re
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

from .. import config

logger = logging.getLogger(__name__)

# Absolute floor so no cue ever has zero/negative length (seconds).
MIN_CUE_DURATION = 0.05

_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


class AlignmentError(RuntimeError):
    """Audio analysis or alignment failed; the caller should fall back."""


class AlignmentResult(NamedTuple):
    """Outcome of a successful VAD-based alignment for one ASR segment."""

    times: List[Tuple[float, float]]          # per-chunk (start, end), seconds
    speech_regions: List[Tuple[float, float]]  # detected regions in the segment
    mode: str                                  # "vad"


# ---------------------------------------------------------------------------
# FFmpeg silence detection (cached per audio file)
# ---------------------------------------------------------------------------

# One silencedetect pass per audio file; results are shared across the ASR
# segments of the same job so FFmpeg is never run per subtitle/segment.
_silence_cache: Dict[Tuple, Tuple[float, List[Tuple[float, float]]]] = {}
_cache_lock = threading.Lock()


def _run_silencedetect(audio_path: Path) -> Tuple[float, List[Tuple[float, float]]]:
    """
    Run `ffmpeg -af silencedetect` over the audio file and return
    (duration_seconds, [(silence_start, silence_end), ...]).
    """
    cmd = [
        config.FFMPEG_BIN,
        "-hide_banner",
        "-nostats",
        "-i", str(audio_path),
        "-af", (
            f"silencedetect=noise={config.VAD_THRESHOLD}dB"
            f":d={config.VAD_MIN_SILENCE_DURATION}"
        ),
        "-f", "null",
        "-",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=config.VAD_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise AlignmentError(
            f"FFmpeg silence detection timed out after {config.VAD_TIMEOUT}s."
        )
    except OSError as e:
        raise AlignmentError(f"Failed to run FFmpeg for silence detection: {e}")

    stderr = result.stderr or ""
    if result.returncode != 0:
        # Never leak paths/env details beyond FFmpeg's own tail output.
        raise AlignmentError(
            "FFmpeg silence detection failed: " + stderr.strip()[-400:]
        )

    duration_match = _DURATION_RE.search(stderr)
    if not duration_match:
        raise AlignmentError("Could not determine audio duration from FFmpeg output.")
    h, m, s = duration_match.groups()
    duration = int(h) * 3600 + int(m) * 60 + float(s)

    silences: List[Tuple[float, float]] = []
    pending_start: Optional[float] = None
    for line in stderr.splitlines():
        start_match = _SILENCE_START_RE.search(line)
        if start_match:
            pending_start = float(start_match.group(1))
            continue
        end_match = _SILENCE_END_RE.search(line)
        if end_match and pending_start is not None:
            silences.append((pending_start, float(end_match.group(1))))
            pending_start = None
    # Audio that ends in silence reports silence_start with no silence_end.
    if pending_start is not None:
        silences.append((pending_start, duration))

    return duration, silences


def _detect_silences_cached(audio_path: Path) -> Tuple[float, List[Tuple[float, float]]]:
    """Cache wrapper around _run_silencedetect (per file state + params)."""
    path = Path(audio_path).resolve()
    if not path.exists():
        raise AlignmentError(f"Audio file not found for alignment: {path.name}")
    stat = path.stat()
    key = (
        str(path), stat.st_mtime, stat.st_size,
        config.VAD_THRESHOLD, config.VAD_MIN_SILENCE_DURATION,
    )
    with _cache_lock:
        cached = _silence_cache.get(key)
    if cached is not None:
        return cached
    result = _run_silencedetect(path)
    with _cache_lock:
        # Bound the cache: hackathon MVP with one job at a time.
        if len(_silence_cache) > 8:
            _silence_cache.clear()
        _silence_cache[key] = result
    return result


# ---------------------------------------------------------------------------
# Speech region detection
# ---------------------------------------------------------------------------

def detect_speech_regions(
    audio_path: Path,
    start_time: float = 0.0,
    end_time: Optional[float] = None,
) -> List[Tuple[float, float]]:
    """
    Detect speech regions inside [start_time, end_time] of the audio file.

    Silence intervals (from FFmpeg silencedetect) are inverted into speech
    regions over the whole file, then:
    - regions shorter than VAD_MIN_SPEECH_DURATION are dropped (false
      positives),
    - remaining regions are expanded by VAD_PADDING and merged where they
      touch (tiny gaps never split a region — silencedetect already merges
      gaps shorter than VAD_MIN_SILENCE_DURATION natively),
    - finally regions are clipped to the requested window, which is the
      ASR segment: audio outside the segment never affects alignment.

    Returns a sorted list of (start, end) tuples in seconds; empty when no
    speech was detected in the window.
    """
    duration, silences = _detect_silences_cached(Path(audio_path))

    lo = max(0.0, float(start_time))
    hi = duration if end_time is None else min(float(end_time), duration)
    if hi <= lo:
        return []

    # Invert silences into speech regions over [0, duration].
    speech: List[Tuple[float, float]] = []
    cursor = 0.0
    for sil_start, sil_end in sorted(silences):
        sil_start = max(0.0, sil_start)
        sil_end = min(sil_end, duration)
        if sil_start > cursor:
            speech.append((cursor, sil_start))
        cursor = max(cursor, sil_end)
    if cursor < duration:
        speech.append((cursor, duration))

    # Drop too-short false positives.
    speech = [
        (s, e) for s, e in speech
        if (e - s) >= config.VAD_MIN_SPEECH_DURATION
    ]

    # Pad and merge touching regions.
    pad = max(0.0, config.VAD_PADDING)
    padded: List[Tuple[float, float]] = []
    for s, e in speech:
        s2, e2 = max(0.0, s - pad), min(duration, e + pad)
        if padded and s2 <= padded[-1][1]:
            padded[-1] = (padded[-1][0], max(padded[-1][1], e2))
        else:
            padded.append((s2, e2))

    # Clip to the ASR segment window.
    regions: List[Tuple[float, float]] = []
    for s, e in padded:
        cs, ce = max(s, lo), min(e, hi)
        if ce > cs:
            regions.append((round(cs, 3), round(ce, 3)))
    return regions


# ---------------------------------------------------------------------------
# Chunk → speech region allocation
# ---------------------------------------------------------------------------

def align_chunk_times(
    weights: Sequence[float],
    audio_path: Path,
    segment_start: float,
    segment_end: float,
) -> Optional[AlignmentResult]:
    """
    Compute (start, end) times for subtitle chunks inside one ASR segment
    using detected speech regions.

    `weights` are the per-chunk speaking-time weights (same order as the
    chunks). Allocation:

    1. Detect speech regions inside the ASR segment.
    2. Map each chunk onto the concatenated "virtual speech timeline" of
       those regions by cumulative weight — a chunk belongs to the region
       containing its midpoint. One region can hold many chunks; regions
       without chunks are skipped (never produce empty subtitles).
    3. Inside each region, chunks are distributed proportionally to their
       weights, capped at SUBTITLE_MAX_DURATION. Cues never cross a
       meaningful silence gap because chunk boundaries only fall inside
       regions.

    Returns None when alignment is disabled or no usable speech regions
    were found — the caller must then fall back to proportional timing.
    Raises AlignmentError when the audio analysis itself fails.
    """
    if not config.SUBTITLE_ALIGNMENT_ENABLED:
        return None
    n = len(weights)
    if n == 0:
        return AlignmentResult(times=[], speech_regions=[], mode="vad")

    regions = detect_speech_regions(audio_path, segment_start, segment_end)
    if not regions:
        logger.info(
            "Alignment: no speech regions detected in %.3f → %.3f — "
            "caller should fall back to proportional timing.",
            segment_start, segment_end,
        )
        return None

    total_speech = sum(e - s for s, e in regions)
    total_weight = sum(weights) or 1.0
    if total_speech <= 0:
        return None

    # Virtual timeline: concatenate region durations; map chunk weight
    # midpoints onto regions.
    virtual_spans: List[Tuple[float, float, int]] = []
    cum = 0.0
    for idx, (s, e) in enumerate(regions):
        virtual_spans.append((cum, cum + (e - s), idx))
        cum += e - s

    assignment: List[List[int]] = [[] for _ in regions]
    weight_cursor = 0.0
    for i, w in enumerate(weights):
        mid_virtual = ((weight_cursor + w / 2.0) / total_weight) * total_speech
        weight_cursor += w
        region_idx = len(regions) - 1
        for v0, v1, idx in virtual_spans:
            if mid_virtual < v1:
                region_idx = idx
                break
        assignment[region_idx].append(i)

    # Distribute chunks within each region.
    max_duration = config.SUBTITLE_MAX_DURATION
    times: List[Optional[Tuple[float, float]]] = [None] * n
    for idx, chunk_ids in enumerate(assignment):
        if not chunk_ids:
            continue
        region_start, region_end = regions[idx]
        region_len = region_end - region_start
        region_weights = [max(float(weights[i]), 0.001) for i in chunk_ids]
        weight_sum = sum(region_weights)

        prev_end = region_start
        for j, chunk_i in enumerate(chunk_ids):
            if j == 0:
                cue_start = region_start
            else:
                cue_start = prev_end
            if j == len(chunk_ids) - 1:
                cue_end = region_end
            else:
                cum_share = sum(region_weights[: j + 1]) / weight_sum
                cue_end = region_start + region_len * cum_share
            # Hard readable-duration floor so no cue is zero-length.
            cue_end = max(cue_end, cue_start + MIN_CUE_DURATION)
            # Cap overly long cues (long region + few chunks).
            if cue_end - cue_start > max_duration:
                cue_end = cue_start + max_duration
            times[chunk_i] = (round(cue_start, 3), round(cue_end, 3))
            prev_end = cue_end

    if any(t is None for t in times):
        # Should be unreachable; be safe rather than emit broken timing.
        raise AlignmentError("Internal alignment error: a chunk got no time span.")

    return AlignmentResult(
        times=[(s, e) for s, e in times],  # type: ignore[misc]
        speech_regions=regions,
        mode="vad",
    )


# ---------------------------------------------------------------------------
# Final normalization / validation pass
# ---------------------------------------------------------------------------

def normalize_subtitle_timing(subtitles: List) -> List:
    """
    In-place final validation pass over subtitle objects exposing mutable
    numeric `.start` / `.end` attributes (romanization_service.Subtitle).

    Guarantees:
    1. start >= 0
    2. end > start (at least MIN_CUE_DURATION)
    3. subtitles sorted chronologically
    4. adjacent subtitles never overlap: sub[i].end <= sub[i+1].start
       (also absorbs slightly overlapping ASR sentence boundaries — the
       later cue is clamped to start at the previous cue's end; no text is
       lost)
    5. timestamps rounded to milliseconds
    """
    if not subtitles:
        return subtitles

    subtitles.sort(key=lambda s: (float(s.start), float(s.end)))
    prev_end: Optional[float] = None
    for sub in subtitles:
        start = max(0.0, float(sub.start))
        if prev_end is not None and start < prev_end:
            start = prev_end
        end = float(sub.end)
        start = round(start, 3)
        end = round(end, 3)
        if end <= start:
            end = round(start + MIN_CUE_DURATION, 3)
        sub.start = start
        sub.end = end
        prev_end = end
    return subtitles
