"""
Word-level timing alignment — local whisper.cpp (pywhispercpp) DTW tokens.

Problem
-------
Qwen3-ASR returns SENTENCE-level timestamps and the VAD layer
(alignment_service) refines them to CUE-level timing, but neither provides
WORD-level timing. Without it the editor highlight
(subtitleUtils.getActiveWordIndex) and the burn-in
(video_export_service._word_timings) ESTIMATE each word's slot from its
character length, so the highlight visibly lags behind the spoken word.

Approach
--------
This module produces REAL audio-derived word timings, fully offline:

1. whisper.cpp (ggml-base, ~147 MB, CPU) recognises the already-extracted
   audio.wav ONCE per job with DTW token timestamps. The known romanized
   transcript is passed as `initial_prompt` so whisper "reads along" the real
   words instead of free-transcribing Hinglish (which ggml-base does poorly).
2. The recognised token stream is expanded into a per-character acoustic
   timeline and matched to every cue word with ONE order-preserving
   (monotonic) character alignment across the whole job. Matching globally —
   not per cue — makes the result immune to the absolute drift between the
   whisper timeline and the VAD cue timeline.
3. Cue start/end come from the audio VAD and are already accurate, so they are
   kept EXACTLY. Only the INTERIOR word boundaries are estimated, and how they
   are estimated is chosen by config.WORD_ALIGNMENT_METHOD:
     * "audio" (DEFAULT, hackathon build) — read the extracted audio.wav energy
       envelope, find the low-energy gaps between voiced runs inside each cue,
       and anchor the interior boundaries to those gaps (a word starts at the
       acoustic onset of its voiced run). Junctions with no reliable gap fall
       back to the proportional split. Pure stdlib DSP — no whisper/torch.
     * "proportional" — split the cue window across its words in proportion to
       each word's character length. Robust and deterministic.
     * "dtw" (opt-in, SUBTITLE_DTW_ALIGNMENT_ENABLED) — affinely fit each cue's
       matched acoustic anchors into its [start, end] window, interpolating
       unmatched words between their matched neighbours, and fall back to the
       proportional split for any cue whose anchors are not trustworthy.
   Either way the boundaries are repaired to be contiguous, monotonic and
   inside the cue, so the words tile the VAD window exactly.

The recognition result (the DTW tokens) is cached in the job directory
(word_alignment.json) keyed by the audio file identity + model + language, so
whisper runs at most once per audio/job and is reused on re-romanization.

Why proportional is the default
-------------------------------
ggml-base frequently MIS-HEARS the Roman-Urdu initial_prompt (e.g. decoding
"Agar main" as "aagar moadho"), so the DTW anchors — and the interior word
durations affine-fitted from them — are unreliable: measured interior words
spanned 20ms-768ms inside a single cue ("Agar" squeezed to 84ms, "main" pinned
to the 20ms floor) even though the cue-level VAD boundaries were exact. Because
the cue window is already correct, a proportional character-length split gives
a far saner interior distribution without depending on misheard anchors. The
DTW fit stays available behind WORD_ALIGNMENT_METHOD="dtw", where it is still
gated per cue (WORD_ALIGNMENT_MIN_MATCH coverage plus a minimum trusted word
length) and falls back to proportional whenever a cue looks unreliable. Empty
or zero/negative-span cues keep words=None and the caller's own estimate.

Fallback
--------
recognize_word_tokens() returns [] whenever alignment is disabled, the model
or audio is missing, pywhispercpp is unavailable, or recognition fails;
align_subtitle_words() then attaches nothing and every cue keeps the
proportional fallback. Subtitle generation never fails because of word
alignment. All pywhispercpp imports are lazy so the module (and therefore the
romanization service) imports fine without the dependency installed.
"""
import array
import bisect
import difflib
import json
import logging
import math
import re
import sys
import threading
import wave
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .. import config

logger = logging.getLogger(__name__)

# Fraction of a word's normalized characters that must match a recognised
# token before the word counts as "matched" (has usable real timing).
_WORD_CHAR_COVERAGE = 0.5
# Absolute floor so no fitted word is zero-length (seconds).
MIN_WORD_DURATION = 0.02
# Tolerance (seconds) for float rounding when validating word boundaries.
_EPS = 1e-3

# --- Audio-driven interior-boundary tuning (config.AUDIO_BOUNDARY_*) --------
_AUDIO_FRAME_MS = 10.0        # short-time energy frame length
_AUDIO_SPEECH_FRAC = 0.45     # speech floor = p20 + frac*(p90 - p20)
_AUDIO_DEV_DB_PER_SEC = 40.0  # penalty for moving a boundary off its proportional prior
_AUDIO_SHORT_PENALTY = 60.0   # penalty scale for a word below AUDIO_BOUNDARY_MIN_WORD_MS
_AUDIO_MAX_DEPTH_DB = 50.0    # cap so absolute silence does not dominate scoring

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")


class WordAlignmentError(RuntimeError):
    """Word alignment is unavailable or failed; the caller keeps the
    proportional fallback."""


# ---------------------------------------------------------------------------
# Recognition cache — whisper runs at most once per audio/job
# ---------------------------------------------------------------------------

# In-memory cache of recognised tokens keyed by (audio identity, model, lang).
_token_cache: Dict[Tuple, List[Dict]] = {}
_cache_lock = threading.Lock()
# Single shared whisper model (loaded lazily, reused across calls).
_model_holder: Dict[str, object] = {}
_model_lock = threading.Lock()
# In-memory cache of the audio energy envelope keyed by file identity, so the
# DSP runs at most once per audio even if alignment is invoked repeatedly.
_audio_ctx_cache: Dict[Tuple, object] = {}


def _normalize(text: str) -> str:
    """Lowercase and strip everything that is not a-z0-9 (script/punct/space
    agnostic) so cue words and whisper tokens compare on the same footing."""
    return _NON_ALNUM_RE.sub("", (text or "").lower())


def _cache_key(audio_path: Path, stat) -> Tuple:
    model_name = Path(config.WORD_ALIGNMENT_MODEL_PATH).name
    return (
        str(audio_path), round(stat.st_mtime, 3), int(stat.st_size),
        model_name, config.WORD_ALIGNMENT_LANGUAGE,
    )


def _cache_path(job_dir: Path) -> Path:
    return Path(job_dir) / config.WORD_ALIGNMENT_CACHE_FILENAME


def _read_disk_cache(job_dir: Path, key: Tuple) -> Optional[List[Dict]]:
    """Load a still-valid cached token list, or None (stale/absent/corrupt)."""
    path = _cache_path(job_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    tokens = data.get("tokens")
    if not isinstance(tokens, list):
        return None
    _audio_path, mtime, size, model_name, lang = key
    if (
        data.get("audio_mtime") != mtime
        or data.get("audio_size") != size
        or data.get("model") != model_name
        or data.get("language") != lang
    ):
        return None  # audio/model/language changed — recompute
    return tokens


def _write_disk_cache(job_dir: Path, key: Tuple, tokens: List[Dict]) -> None:
    _audio_path, mtime, size, model_name, lang = key
    payload = {
        "model": model_name,
        "language": lang,
        "audio_mtime": mtime,
        "audio_size": size,
        "tokens": tokens,
    }
    try:
        _cache_path(job_dir).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as e:  # never fail romanization over a cache write
        logger.warning("Word alignment: could not write cache (%s).", e)


def _load_model():
    """Return the shared whisper.cpp model, loading it on first use.

    Raises WordAlignmentError when pywhispercpp is unavailable or the ggml
    model file is missing — the model path is passed explicitly so whisper
    never attempts a network download.
    """
    with _model_lock:
        model = _model_holder.get("model")
        if model is not None:
            return model
        model_path = Path(config.WORD_ALIGNMENT_MODEL_PATH)
        if not model_path.exists():
            raise WordAlignmentError(
                f"whisper model not found: {model_path.name} "
                "(download it into backend/models/ to enable word timing)."
            )
        try:
            from pywhispercpp.model import Model
        except Exception as e:  # ImportError or native-library failure
            raise WordAlignmentError(f"pywhispercpp unavailable: {e}")
        # context_params enables DTW token timestamps; logs go to devnull so
        # the background romanization thread stays quiet.
        model = Model(
            str(model_path),
            redirect_whispercpp_logs_to=None,
            context_params={"dtw_token_timestamps": True},
        )
        _model_holder["model"] = model
        return model


def _extract_tokens(model) -> List[Dict]:
    """Read DTW token timings straight from the whisper context.

    pywhispercpp's Segment object exposes only t0/t1/text, so per-token
    timings are read from the underlying context. Special tokens are skipped
    and token text that fails to decode (partial multibyte BPE pieces) is
    ignored rather than crashing the whole pass.
    """
    import _pywhispercpp as pw

    ctx = model._ctx
    special_beg = pw.whisper_token_beg(ctx)
    tokens: List[Dict] = []
    n_seg = pw.whisper_full_n_segments(ctx)
    for i in range(n_seg):
        n_tok = pw.whisper_full_n_tokens(ctx, i)
        for j in range(n_tok):
            data = pw.whisper_full_get_token_data(ctx, i, j)
            if data.id >= special_beg:
                continue  # special/control token — no readable text
            try:
                text = pw.whisper_full_get_token_text(ctx, i, j)
            except (UnicodeDecodeError, ValueError):
                continue
            if not text or not text.strip():
                continue
            t0 = data.t0 / 100.0  # centiseconds → seconds
            t1 = data.t1 / 100.0
            if t1 < t0:
                t1 = t0
            tokens.append({"w": text, "s": round(t0, 3), "e": round(t1, 3)})
    return tokens


def recognize_word_tokens(audio_path, prompt_text: str = "") -> List[Dict]:
    """
    Recognise the audio ONCE and return DTW token timings: [{w, s, e}, ...]
    (w = token text, s/e = start/end seconds).

    Results are cached in memory and in word_alignment.json inside the audio's
    job directory, keyed by audio identity + model + language, so repeated
    romanization/reload operations reuse the same recognition. Returns [] on
    any unavailability/failure — the caller then keeps proportional timing.
    """
    if not config.WORD_ALIGNMENT_ENABLED:
        return []
    path = Path(audio_path).resolve()
    if not path.exists():
        return []
    try:
        stat = path.stat()
    except OSError:
        return []

    key = _cache_key(path, stat)
    job_dir = path.parent

    with _cache_lock:
        cached = _token_cache.get(key)
    if cached is not None:
        return cached

    disk = _read_disk_cache(job_dir, key)
    if disk is not None:
        with _cache_lock:
            if len(_token_cache) > 8:
                _token_cache.clear()
            _token_cache[key] = disk
        logger.info("Word alignment: reused cached recognition for %s.", path.name)
        return disk

    try:
        model = _load_model()
        # Serialize inference: the model context is shared state.
        with _model_lock:
            model.transcribe(
                str(path),
                token_timestamps=True,
                language=config.WORD_ALIGNMENT_LANGUAGE,
                n_threads=config.WORD_ALIGNMENT_THREADS,
                initial_prompt=(prompt_text or "")[:2000],
                print_progress=False,
                print_realtime=False,
            )
            tokens = _extract_tokens(model)
    except WordAlignmentError as e:
        logger.info("Word alignment skipped: %s", e)
        return []
    except Exception as e:  # noqa: BLE001 — never break romanization
        logger.warning(
            "Word alignment: whisper recognition failed (%s) — cues keep "
            "proportional timing.", e,
        )
        return []

    if not tokens:
        return []
    _write_disk_cache(job_dir, key, tokens)
    with _cache_lock:
        if len(_token_cache) > 8:
            _token_cache.clear()
        _token_cache[key] = tokens
    return tokens


# ---------------------------------------------------------------------------
# Global monotonic matching (cue words ↔ recognised tokens)
# ---------------------------------------------------------------------------

def _char_timeline(tokens: List[Dict]) -> Tuple[str, List[Tuple[float, float]]]:
    """Expand recognised tokens into a per-character acoustic timeline.

    Returns (char_string, per_char_times) where per_char_times[k] is the
    (start, end) of char_string[k], linearly interpolated inside its token's
    DTW span. Token boundaries do not matter — only the character stream.
    """
    chars: List[str] = []
    times: List[Tuple[float, float]] = []
    for tok in tokens:
        nt = _normalize(tok.get("w", ""))
        if not nt:
            continue
        s = float(tok.get("s", 0.0))
        e = float(tok.get("e", 0.0))
        if e < s:
            e = s
        length = len(nt)
        for k, ch in enumerate(nt):
            f0 = k / length
            f1 = (k + 1) / length
            chars.append(ch)
            times.append((s + (e - s) * f0, s + (e - s) * f1))
    return "".join(chars), times


def _global_word_match(
    cue_words: List[str], whisper_chars: str, whisper_times: List[Tuple[float, float]]
) -> List[Optional[Tuple[float, float]]]:
    """Match every cue word to acoustic (start, end) via ONE monotonic
    character alignment across the whole job.

    Returns a list aligned to `cue_words`; each entry is None (unmatched) or
    (start, end) in the whisper acoustic timeline. A word is matched only when
    at least _WORD_CHAR_COVERAGE of its normalized characters align.
    """
    matched: List[Optional[Tuple[float, float]]] = [None] * len(cue_words)
    cue_chars: List[str] = []
    word_of_char: List[int] = []
    word_norm_len: List[int] = []
    for wi, cw in enumerate(cue_words):
        ncw = _normalize(cw)
        word_norm_len.append(len(ncw))
        for ch in ncw:
            cue_chars.append(ch)
            word_of_char.append(wi)
    cue_stream = "".join(cue_chars)
    if not cue_stream or not whisper_chars:
        return matched

    sm = difflib.SequenceMatcher(a=cue_stream, b=whisper_chars, autojunk=False)
    per_word: Dict[int, List[Tuple[float, float]]] = {}
    for block in sm.get_matching_blocks():
        for k in range(block.size):
            ci = block.a + k
            wj = block.b + k
            per_word.setdefault(word_of_char[ci], []).append(whisper_times[wj])

    for wi, times in per_word.items():
        norm_len = word_norm_len[wi] or 1
        if len(times) / norm_len >= _WORD_CHAR_COVERAGE:
            matched[wi] = (
                min(t[0] for t in times),
                max(t[1] for t in times),
            )
    return matched


# ---------------------------------------------------------------------------
# Per-cue fit into the established cue window
# ---------------------------------------------------------------------------

def _repair_boundaries(
    raw: List[Optional[float]], cue_start: float, cue_end: float, n: int
) -> List[float]:
    """Turn raw boundary estimates into n+1 strictly-increasing boundaries with
    b[0] == cue_start and b[n] == cue_end, all inside the cue window."""
    span = cue_end - cue_start
    b: List[float] = [cue_start]
    for k in range(1, n):
        v = raw[k]
        if v is None:
            v = cue_start + span * k / n
        b.append(min(max(v, cue_start), cue_end))
    b.append(cue_end)

    min_gap = max(min(span / (n * 4), MIN_WORD_DURATION), 1e-4)
    # Forward pass: enforce a strictly-increasing minimum gap.
    for k in range(1, n + 1):
        if b[k] < b[k - 1] + min_gap:
            b[k] = b[k - 1] + min_gap
    # If the forward pass overshot the cue end, compress proportionally.
    if b[n] > cue_end + 1e-9:
        total = b[n] - cue_start
        if total > 0:
            for k in range(1, n + 1):
                b[k] = cue_start + (b[k] - cue_start) * (span / total)
    b[0] = cue_start
    b[n] = cue_end

    out = [round(x, 3) for x in b]
    for k in range(1, n + 1):
        if out[k] <= out[k - 1]:
            out[k] = round(out[k - 1] + 0.001, 3)
    # Degenerate (rounding pushed past the cue end): even distribution.
    if out[n] > round(cue_end, 3) + 1e-9:
        step = span / n if n else span
        out = [round(cue_start + step * k, 3) for k in range(n + 1)]
        out[0] = round(cue_start, 3)
        out[n] = round(cue_end, 3)
        for k in range(1, n + 1):
            if out[k] <= out[k - 1]:
                out[k] = round(out[k - 1] + 0.001, 3)
    return out


def _proportional_word_split(
    cue_words: List[str], cue_start: float, cue_end: float
) -> Optional[List[Dict]]:
    """Split one cue window across its words in proportion to character length.

    This is the robust DEFAULT interior split. The cue endpoints come from the
    audio VAD and are already accurate, so they are kept exactly; only the
    INTERIOR boundaries are estimated, by giving each word a slice of the window
    proportional to its normalized character length (a cheap proxy for speaking
    time). Unlike the DTW-anchored fit it never trusts the whisper token times —
    which ggml-base distorts when it mis-hears the Roman-Urdu prompt — so no word
    is squeezed to a near-zero slot. Boundaries still pass through
    _repair_boundaries, so they tile [cue_start, cue_end] exactly, stay monotonic
    and respect the MIN_WORD_DURATION floor. Returns None for an empty or
    zero/negative-span cue.
    """
    n = len(cue_words)
    span = cue_end - cue_start
    if n <= 0 or span <= 0:
        return None
    if n == 1:
        return [{
            "word": cue_words[0],
            "start": round(cue_start, 3),
            "end": round(cue_end, 3),
        }]
    weights = [max(len(_normalize(w)), 1) for w in cue_words]
    total = sum(weights)
    raw: List[float] = [cue_start]
    acc = 0
    for k in range(1, n):
        acc += weights[k - 1]
        raw.append(cue_start + span * acc / total)
    raw.append(cue_end)
    b = _repair_boundaries(raw, cue_start, cue_end, n)
    return [
        {"word": cue_words[i], "start": b[i], "end": b[i + 1]}
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Audio-driven interior boundaries — energy envelope, gap detection, DP fit
#
# Cue start/end come from the (accurate) audio VAD and are NEVER changed here;
# this only refines the INTERIOR boundaries between a cue's words. Pure stdlib
# DSP over the already-extracted audio.wav (no numpy/whisper/torch).
# ---------------------------------------------------------------------------

def _audio_cfg() -> Dict:
    """Resolve the audio-boundary tuning from config (with safe defaults)."""
    min_gap_s = getattr(config, "AUDIO_BOUNDARY_MIN_GAP_MS", 40.0) / 1000.0
    return {
        "frame_s": _AUDIO_FRAME_MS / 1000.0,
        "smooth_ms": getattr(config, "AUDIO_BOUNDARY_SMOOTH_MS", 30.0),
        "min_gap_s": min_gap_s,
        "min_run_s": max(min_gap_s, 0.04),
        "depth_db": getattr(config, "AUDIO_BOUNDARY_GAP_DEPTH_DB", 12.0),
        "margin_s": getattr(config, "AUDIO_BOUNDARY_EDGE_MARGIN_MS", 40.0) / 1000.0,
        "pref_s": getattr(config, "AUDIO_BOUNDARY_MIN_WORD_MS", 40.0) / 1000.0,
        "dev_w": _AUDIO_DEV_DB_PER_SEC,
        "short_pen": _AUDIO_SHORT_PENALTY,
    }


def _audio_envelope(
    audio_path, frame_ms: float = _AUDIO_FRAME_MS, smooth_ms: float = 30.0
) -> Optional[Tuple[List[float], List[float]]]:
    """Return (db_frames, frame_times) for a 16-bit PCM wav, or None.

    Pure stdlib (wave + math): 10 ms RMS frames → dB → moving-average smooth.
    Stereo is mono-mixed. Never raises — any problem returns None so the caller
    falls back to the proportional split.
    """
    try:
        wf = wave.open(str(audio_path), "rb")
    except Exception:
        return None
    try:
        nchan = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        sr = wf.getframerate()
        nframes = wf.getnframes()
        if sampwidth != 2 or sr <= 0 or nframes <= 0:
            return None
        raw = wf.readframes(nframes)
    except Exception:
        return None
    finally:
        wf.close()
    if not raw:
        return None
    pcm = array.array("h")
    pcm.frombytes(raw[: (len(raw) // 2) * 2])
    if sys.byteorder == "big":
        pcm.byteswap()
    if nchan >= 2:
        mono = [(pcm[i] + pcm[i + 1]) * 0.5 for i in range(0, len(pcm) - 1, 2)]
    else:
        mono = list(pcm)
    if not mono:
        return None
    hop = max(1, int(round(sr * frame_ms / 1000.0)))
    scale = 1.0 / 32768.0
    db: List[float] = []
    times: List[float] = []
    for i in range(0, len(mono) - hop + 1, hop):
        ss = 0.0
        for k in range(i, i + hop):
            v = mono[k] * scale
            ss += v * v
        rms = math.sqrt(ss / hop)
        db.append(20.0 * math.log10(rms + 1e-9))
        times.append((i + hop * 0.5) / sr)
    if len(db) < 3:
        return None
    w = max(1, int(round(smooth_ms / frame_ms)))
    if w > 1:
        half = w // 2
        smoothed: List[float] = []
        for i in range(len(db)):
            lo = i - half if i - half > 0 else 0
            hi = i + half + 1 if i + half + 1 < len(db) else len(db)
            smoothed.append(sum(db[lo:hi]) / (hi - lo))
        db = smoothed
    return db, times


def _speech_threshold(db: List[float]) -> float:
    """Adaptive speech/silence floor from the envelope's own distribution
    (p20 + 0.45*(p90 - p20)) — no hardcoded dBFS."""
    if not db:
        return 0.0
    s = sorted(db)
    n = len(s)
    lo = s[int(0.20 * (n - 1))]
    hi = s[int(0.90 * (n - 1))]
    return lo + _AUDIO_SPEECH_FRAC * (hi - lo)


def _voiced_runs(db, times, thr, min_gap_s, min_run_s, frame_s):
    """Merge frames above `thr` into (start, end, peak_db) voiced runs, bridging
    gaps shorter than min_gap_s and dropping runs shorter than min_run_s (noise).
    Gaps BETWEEN the returned runs are therefore >= min_gap_s."""
    runs: List[List[float]] = []
    n = len(db)
    i = 0
    while i < n:
        if db[i] > thr:
            j = i
            peak = db[i]
            while j + 1 < n and db[j + 1] > thr:
                j += 1
                if db[j] > peak:
                    peak = db[j]
            runs.append([times[i] - frame_s * 0.5, times[j] + frame_s * 0.5, peak])
            i = j + 1
        else:
            i += 1
    merged: List[List[float]] = []
    for r in runs:
        if merged and (r[0] - merged[-1][1]) < min_gap_s:
            merged[-1][1] = r[1]
            if r[2] > merged[-1][2]:
                merged[-1][2] = r[2]
        else:
            merged.append([r[0], r[1], r[2]])
    return [tuple(r) for r in merged if (r[1] - r[0]) >= min_run_s]


def _min_db_between(db, times, t0, t1) -> float:
    """Minimum envelope dB for frames whose centre lies in [t0, t1]."""
    i = bisect.bisect_left(times, t0)
    j = bisect.bisect_right(times, t1)
    if j <= i:
        return db[i] if i < len(db) else (db[-1] if db else 0.0)
    return min(db[i:j])


def _interior_candidates(db, times, thr, frame_s, cue_start, cue_end, cfg):
    """Candidate interior boundaries in ONE cue: (time, depth_db) for each
    low-energy gap between two voiced runs lying inside the cue.

    The boundary time is the ONSET of the following voiced run, so an anchored
    word starts exactly when it is spoken. depth_db is how far the gap floor
    sits below the quieter of the two neighbouring runs (separation strength).
    """
    runs = _voiced_runs(db, times, thr, cfg["min_gap_s"], cfg["min_run_s"], frame_s)
    min_gap_s = cfg["min_gap_s"]
    depth_db = cfg["depth_db"]
    margin_s = cfg["margin_s"]
    out: List[Tuple[float, float]] = []
    for a, b in zip(runs, runs[1:]):
        gap_start, gap_end = a[1], b[0]
        if (gap_end - gap_start) < min_gap_s:
            continue
        if gap_start < cue_start - 1e-6 or gap_end > cue_end + 1e-6:
            continue
        t = gap_end
        if t < cue_start + margin_s or t > cue_end - margin_s:
            continue
        floor = _min_db_between(db, times, gap_start, gap_end)
        depth = min(a[2], b[2]) - floor
        if depth < depth_db:
            continue
        out.append((round(t, 4), min(depth, _AUDIO_MAX_DEPTH_DB)))
    return out


def _load_audio_context(audio_path):
    """(db, times, thr, frame_s) for the audio, cached per file identity, or
    None when unreadable — callers then fall back to the proportional split."""
    if audio_path is None:
        return None
    p = Path(audio_path)
    try:
        st = p.stat()
    except OSError:
        return None
    key = (str(p.resolve()), round(st.st_mtime, 3), int(st.st_size))
    with _cache_lock:
        if key in _audio_ctx_cache:
            return _audio_ctx_cache[key]
    cfg = _audio_cfg()
    env = _audio_envelope(p, _AUDIO_FRAME_MS, cfg["smooth_ms"])
    if env is None:
        ctx = None
    else:
        db, times = env
        ctx = (db, times, _speech_threshold(db), cfg["frame_s"])
    with _cache_lock:
        if len(_audio_ctx_cache) > 8:
            _audio_ctx_cache.clear()
        _audio_ctx_cache[key] = ctx
    return ctx


def _cue_candidates(ctx, cue_start, cue_end):
    """Interior candidate boundaries for one cue from a loaded audio context."""
    if ctx is None:
        return None
    db, times, thr, frame_s = ctx
    return _interior_candidates(
        db, times, thr, frame_s, cue_start, cue_end, _audio_cfg()
    )


def _assign_interior_boundaries(prop_bounds, candidates, cue_start, cue_end, cfg):
    """Choose one boundary per interior junction — either an ACOUSTIC candidate
    or the PROPORTIONAL prior — maximising (gap depth − deviation from the
    proportional prior − short-word penalty), subject to strictly increasing
    boundaries and the MIN_WORD_DURATION floor.

    A small DP over junctions x candidates; because candidate times are sorted,
    enforcing increasing boundary VALUES automatically keeps candidate usage in
    order (monotonic). Returns (bounds, sources) where sources[k] is 'ACOUSTIC'
    or 'PROPORTIONAL'. Falls back to the proportional priors if no valid
    increasing assignment exists.
    """
    J = len(prop_bounds)
    if J == 0:
        return [], []
    cands = candidates
    M = len(cands)
    pref = cfg["pref_s"]
    short_pen = cfg["short_pen"]
    dev_w = cfg["dev_w"]
    floor = MIN_WORD_DURATION
    n_opts = M + 1  # option 0 = proportional, options 1..M = candidate (o-1)

    def value(k, o):
        return prop_bounds[k] if o == 0 else cands[o - 1][0]

    def base(k, o):
        if o == 0:
            return 0.0
        t, depth = cands[o - 1]
        return depth - dev_w * abs(t - prop_bounds[k])

    def seg(d):
        if d < floor:
            return None
        if d < pref:
            return -short_pen * (pref - d) / pref
        return 0.0

    prop_fallback = (list(prop_bounds), ["PROPORTIONAL"] * J)
    layers: List[Dict[int, Tuple[float, Optional[int]]]] = []
    cur: Dict[int, Tuple[float, Optional[int]]] = {}
    for o in range(n_opts):
        v = value(0, o)
        if v <= cue_start or v >= cue_end:
            continue
        s0 = seg(v - cue_start)
        if s0 is None:
            continue
        cur[o] = (base(0, o) + s0, None)
    if not cur:
        return prop_fallback
    layers.append(cur)
    for k in range(1, J):
        nxt: Dict[int, Tuple[float, Optional[int]]] = {}
        for po, (pscore, _) in cur.items():
            pv = value(k - 1, po)
            for o in range(n_opts):
                v = value(k, o)
                if v <= pv or v >= cue_end:
                    continue
                sd = seg(v - pv)
                if sd is None:
                    continue
                tot = pscore + base(k, o) + sd
                if o not in nxt or tot > nxt[o][0]:
                    nxt[o] = (tot, po)
        if not nxt:
            return prop_fallback
        cur = nxt
        layers.append(cur)
    best_o, best_tot = None, float("-inf")
    for o, (score, _) in cur.items():
        sd = seg(cue_end - value(J - 1, o))
        if sd is None:
            continue
        if score + sd > best_tot:
            best_tot, best_o = score + sd, o
    if best_o is None:
        return prop_fallback
    opts = [0] * J
    o = best_o
    for k in range(J - 1, -1, -1):
        opts[k] = o
        o = layers[k][o][1]
    bounds = [round(value(k, opts[k]), 4) for k in range(J)]
    sources = ["ACOUSTIC" if opts[k] != 0 else "PROPORTIONAL" for k in range(J)]
    return bounds, sources


def _audio_word_split(cue_words, cue_start, cue_end, candidates):
    """Audio-driven / hybrid interior split for ONE cue.

    CASE A: enough reliable gaps → every junction anchored (ACOUSTIC).
    CASE B: some gaps → acoustic anchors + proportional fill (hybrid).
    CASE C: no gaps → proportional character split.

    Returns (words, sources) or None. sources[k] ∈ {ACOUSTIC, PROPORTIONAL} is
    the origin of the boundary that STARTS word k+1 (word 0 always starts at the
    cue start). Never returns invalid or non-monotonic timings — everything
    passes through _repair_boundaries, so the words tile [cue_start, cue_end]
    exactly and cue endpoints are preserved.
    """
    n = len(cue_words)
    span = cue_end - cue_start
    if n <= 0 or span <= 0:
        return None
    if n == 1:
        return ([{"word": cue_words[0], "start": round(cue_start, 3),
                  "end": round(cue_end, 3)}], [])
    prop = _proportional_word_split(cue_words, cue_start, cue_end)
    if prop is None:
        return None
    prop_bounds = [prop[i]["end"] for i in range(n - 1)]
    if not candidates:
        return prop, ["PROPORTIONAL"] * (n - 1)
    cands = sorted((t, d) for (t, d) in candidates if cue_start < t < cue_end)
    if not cands:
        return prop, ["PROPORTIONAL"] * (n - 1)
    bounds, sources = _assign_interior_boundaries(
        prop_bounds, cands, cue_start, cue_end, _audio_cfg()
    )
    raw = [cue_start] + list(bounds) + [cue_end]
    b = _repair_boundaries(raw, cue_start, cue_end, n)
    words = [{"word": cue_words[i], "start": b[i], "end": b[i + 1]} for i in range(n)]
    return words, sources


def _fit_cue_words(
    cue_words: List[str],
    matched: List[Optional[Tuple[float, float]]],
    cue_start: float,
    cue_end: float,
    min_match: float,
    candidates: Optional[List[Tuple[float, float]]] = None,
) -> Optional[List[Dict]]:
    """Return word timings tiling one cue window, or None when it is degenerate.

    Word i spans [b[i], b[i+1]] so timings are contiguous and non-overlapping by
    construction, and b[0]/b[n] are exactly the (VAD-accurate) cue start/end. A
    single-word cue always maps to the whole window; an empty or zero-span cue
    returns None.

    Interior boundaries follow config.WORD_ALIGNMENT_METHOD:
      * "audio" (DEFAULT, needs SUBTITLE_AUDIO_BOUNDARY_ALIGNMENT_ENABLED and
        acoustic `candidates`): anchor interior boundaries to the low-energy
        gaps between voiced runs (_audio_word_split), proportionally filling any
        junction with no reliable gap. With no candidates it degrades to the
        proportional split.
      * "proportional": character-length split via _proportional_word_split —
        robust, ignores the DTW anchors entirely.
      * "dtw" (opt-in, needs SUBTITLE_DTW_ALIGNMENT_ENABLED): affine-fit the
        matched acoustic anchors into the cue, but fall back to the proportional
        split for THIS cue when the anchors are not trustworthy — coverage below
        `min_match`, or a fit that squeezes any word under
        WORD_ALIGNMENT_MIN_TRUSTED_WORD seconds.
    """
    n = len(cue_words)
    if n == 0:
        return None
    if n == 1:
        return [{
            "word": cue_words[0],
            "start": round(cue_start, 3),
            "end": round(cue_end, 3),
        }]
    span = cue_end - cue_start
    if span <= 0:
        return None

    audio_on = getattr(config, "SUBTITLE_AUDIO_BOUNDARY_ALIGNMENT_ENABLED", True)
    dtw_on = getattr(config, "SUBTITLE_DTW_ALIGNMENT_ENABLED", False)
    method = getattr(config, "WORD_ALIGNMENT_METHOD", "audio")
    use_dtw = method == "dtw" and dtw_on
    use_audio = (not use_dtw) and audio_on and method in ("audio", "dtw")

    if use_audio:
        # DEFAULT: acoustic interior boundaries (hybrid with proportional).
        if candidates:
            res = _audio_word_split(cue_words, cue_start, cue_end, candidates)
            if res and res[0]:
                return res[0]
        return _proportional_word_split(cue_words, cue_start, cue_end)

    if not use_dtw:
        # WORD_ALIGNMENT_METHOD="proportional" (or audio disabled): the robust
        # character-length interior split.
        return _proportional_word_split(cue_words, cue_start, cue_end)

    # OPT-IN DTW path (SUBTITLE_DTW_ALIGNMENT_ENABLED + WORD_ALIGNMENT_METHOD=
    # "dtw") — affine-fit the acoustic anchors, with a per-cue proportional
    # fallback whenever the anchors cannot be trusted.
    matched_idx = [i for i, m in enumerate(matched) if m is not None]
    if not matched_idx or len(matched_idx) / n < min_match:
        return _proportional_word_split(cue_words, cue_start, cue_end)

    # Affine-map the acoustic window spanned by the matched words onto the cue.
    a0 = matched[matched_idx[0]][0]
    a1 = matched[matched_idx[-1]][1]
    acoustic_span = a1 - a0
    scale = (span / acoustic_span) if acoustic_span > 1e-6 else 0.0

    def to_cue(t: float) -> float:
        return cue_start + (t - a0) * scale

    # Boundary k is the start of word k; estimate it from matched neighbours.
    raw: List[Optional[float]] = [cue_start] + [None] * (n - 1) + [cue_end]
    for k in range(1, n):
        if matched[k] is not None:
            raw[k] = to_cue(matched[k][0])
    # Interpolate the unmatched boundaries linearly between known anchors.
    last_k, last_v = 0, cue_start
    for k in range(1, n + 1):
        if raw[k] is None:
            continue
        gap = k - last_k
        for m in range(last_k + 1, k):
            raw[m] = last_v + (raw[k] - last_v) * (m - last_k) / gap
        last_k, last_v = k, raw[k]

    b = _repair_boundaries(raw, cue_start, cue_end, n)
    words = [
        {"word": cue_words[i], "start": b[i], "end": b[i + 1]}
        for i in range(n)
    ]
    # Confidence guard: a DTW fit that squeezes any word below the trust floor
    # mis-heard this cue — use the proportional split for it instead.
    min_trusted = getattr(config, "WORD_ALIGNMENT_MIN_TRUSTED_WORD", 0.04)
    if any((w["end"] - w["start"]) < min_trusted for w in words):
        return _proportional_word_split(cue_words, cue_start, cue_end)
    return words


# ---------------------------------------------------------------------------
# Shared validation (aligner, save endpoint, exporter)
# ---------------------------------------------------------------------------

def validate_word_timings(
    words, romanized_text: str, start: float, end: float
) -> Optional[List[Dict]]:
    """Return a cleaned word-timing list when `words` are valid for this cue,
    else None.

    Valid means: exactly one entry per whitespace token of `romanized_text`,
    each entry's `word` equal to that token, timings inside [start, end], each
    with end > start, non-overlapping and monotonically increasing. Shared by
    the aligner (before persisting), the editor save endpoint (to drop stale
    timings after a manual edit) and the exporter (before burn-in) so an
    estimate is never mistaken for a real audio-derived timing.
    """
    if not isinstance(words, list) or not words:
        return None
    tokens = (romanized_text or "").split()
    if len(words) != len(tokens):
        return None
    try:
        cue_start = float(start)
        cue_end = float(end)
    except (TypeError, ValueError):
        return None

    cleaned: List[Dict] = []
    prev_end: Optional[float] = None
    for entry, token in zip(words, tokens):
        if not isinstance(entry, dict):
            return None
        word_text = entry.get("word")
        if not isinstance(word_text, str) or word_text.strip() != token.strip():
            return None
        try:
            ws = float(entry.get("start"))
            we = float(entry.get("end"))
        except (TypeError, ValueError):
            return None
        if we <= ws:
            return None
        if ws < cue_start - _EPS or we > cue_end + _EPS:
            return None
        if prev_end is not None and ws < prev_end - _EPS:
            return None
        cleaned.append({"word": token, "start": round(ws, 3), "end": round(we, 3)})
        prev_end = we
    return cleaned


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def align_subtitle_words(subtitles: List, audio_path) -> int:
    """Attach word timings to cues; return the number of cues given words.

    The interior word boundaries are produced by config.WORD_ALIGNMENT_METHOD:
      * "audio" (DEFAULT, needs SUBTITLE_AUDIO_BOUNDARY_ALIGNMENT_ENABLED) —
        reads the extracted audio.wav ONCE (cached energy envelope), finds the
        low-energy gaps between voiced runs inside each cue, and anchors the
        interior boundaries to them (proportionally filling any junction with no
        reliable gap). Whisper is NOT run — this path is pure stdlib DSP.
      * "proportional" — character-length split of the VAD-accurate cue window.
      * "dtw" (opt-in, needs SUBTITLE_DTW_ALIGNMENT_ENABLED) — recognises the
        audio ONCE with whisper.cpp, matches every cue word to the token stream
        with one global monotonic alignment, and affine-fits the anchors.
    Empty or zero-span cues keep words=None and the caller's own proportional
    estimate. `subtitles` are objects with mutable .start/.end/.romanized_text/
    .words (romanization_service.Subtitle). Never raises — any problem leaves
    the cues untouched.
    """
    if not config.WORD_ALIGNMENT_ENABLED or audio_path is None or not subtitles:
        return 0

    audio_on = getattr(config, "SUBTITLE_AUDIO_BOUNDARY_ALIGNMENT_ENABLED", True)
    dtw_on = getattr(config, "SUBTITLE_DTW_ALIGNMENT_ENABLED", False)
    method = getattr(config, "WORD_ALIGNMENT_METHOD", "audio")
    use_dtw = method == "dtw" and dtw_on
    use_audio = (not use_dtw) and audio_on and method in ("audio", "dtw")

    # Flatten all cue words once (shared by every method).
    flat_words: List[str] = []
    spans: List[Tuple[int, int]] = []
    for s in subtitles:
        cue_words = (getattr(s, "romanized_text", "") or "").split()
        spans.append((len(flat_words), len(flat_words) + len(cue_words)))
        flat_words.extend(cue_words)
    if not flat_words:
        return 0

    min_match = config.WORD_ALIGNMENT_MIN_MATCH
    matched_flat: Optional[List[Optional[Tuple[float, float]]]] = None
    audio_ctx = None
    matched_words = 0

    if use_dtw:
        prompt = " ".join(
            (getattr(s, "romanized_text", "") or "") for s in subtitles
        )
        tokens = recognize_word_tokens(audio_path, prompt)
        if not tokens:
            return 0
        whisper_chars, whisper_times = _char_timeline(tokens)
        matched_flat = _global_word_match(flat_words, whisper_chars, whisper_times)
        matched_words = sum(1 for m in matched_flat if m is not None)
    elif use_audio:
        audio_ctx = _load_audio_context(audio_path)

    aligned = 0
    for s, (a, b) in zip(subtitles, spans):
        cue_words = flat_words[a:b]
        if not cue_words:
            continue
        matched = (
            matched_flat[a:b] if matched_flat is not None else [None] * len(cue_words)
        )
        candidates = (
            _cue_candidates(audio_ctx, float(s.start), float(s.end))
            if audio_ctx is not None else None
        )
        fitted = _fit_cue_words(
            cue_words, matched, float(s.start), float(s.end), min_match,
            candidates=candidates,
        )
        if not fitted:
            continue
        # Only persist timings that pass the shared validator.
        validated = validate_word_timings(
            fitted, s.romanized_text, s.start, s.end
        )
        if validated:
            s.words = validated
            aligned += 1

    logger.info(
        "Word timing (%s): %d/%d cues aligned%s.",
        "dtw" if use_dtw else ("audio" if use_audio else "proportional"),
        aligned, len(subtitles),
        (
            f" ({matched_words}/{len(flat_words)} words matched)"
            if use_dtw and flat_words else ""
        ),
    )
    return aligned
