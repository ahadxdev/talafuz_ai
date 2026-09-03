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
     * "proportional" (DEFAULT, hackathon build) — split the cue window across
       its words in proportion to each word's character length. Robust and
       deterministic; it does not trust the whisper DTW anchors.
     * "dtw" (opt-in) — affinely fit each cue's matched acoustic anchors into
       its [start, end] window, interpolating unmatched words between their
       matched neighbours, and fall back to the proportional split for any cue
       whose anchors are not trustworthy.
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
import difflib
import json
import logging
import re
import threading
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


def _fit_cue_words(
    cue_words: List[str],
    matched: List[Optional[Tuple[float, float]]],
    cue_start: float,
    cue_end: float,
    min_match: float,
) -> Optional[List[Dict]]:What changed (C + D fix — frontend only)
The word-highlight path is now decoupled from the throttled React state round-trip. Two files, no alignment-engine or EditorPage changes.VideoStage.jsx — one rAF loop, two paths:
Coarse path (unchanged): TIME_REPORT_INTERVAL = 1/15 gate → onCurrentTime(t) → EditorPage state. Byte-identical logic, just nested inside if (video). Still drives SubtitleList / Timeline / StylePanel / time display / seek slider.
New un-throttled path: playheadRef.current = video.currentTime written every frame, plus a stable getPlayheadTime = useCallback(() => videoRef.current.currentTime) passed to CaptionOverlay (replacing the currentTime prop).
CaptionOverlay.jsx — now reads the live playhead in its own rAF:
Runs getActiveWordIndex(words, subtitle, getPlayheadTime()) every frame (lookup logic untouched).
Keeps only activeWordIndex in local state, set solely when it changes → CaptionOverlay re-renders a few times/sec (on word change), and EditorPage never re-renders for highlighting.
Latest words/subtitle/highlightOn are mirrored into refs so the persistent rAF always sees fresh data without re-subscribing.
Why lag → ~0: the value fed to getActiveWordIndex is videoRef.current.currentTime read at that instant — no 66.7 ms throttle quantization, no setState → re-render → prop-drill round-trip in the path.
Acceptance criteria — measured (same live diagnostic, job 087a46b2)
Metric	Before	After	Verdict
max lag	103.6 ms	2.2 ms	✅ low single-digit, bounded by rAF
avg lag	47.3 ms (fixed)	0.11 ms	✅ tracks near 0
p95 lag	83.4 ms	0.22 ms	✅
min lag	—	0 ms	✅
highlight path rate	~15 Hz ceiling	60 Hz (samplerHz: 60)	✅
EditorPage coarse rate	~15 Hz	coarseHz: 12.9 (≈15 Hz ceiling)	✅ unchanged
Raw samples confirm used tracks live to sub-ms: {live: 34.475256, used: 34.475147} → 0.11 ms. The highlight still renders/advances correctly (STEP 7: one word highlighted, "ho", at t≈34.5 s).
✅ EditorPage render structure untouched — not in git status; Timeline still binds its 15 Hz currentTime.
✅ TIME_REPORT_INTERVAL value unchanged (1/15); the throttled branch is byte-identical.
✅ No alignment-service file touched.
✅ getActiveWordIndex lookup logic unchanged.
✅ Step 5 verified: CaptionOverlay is rendered only by VideoStage, and used currentTime only for the highlight — nothing depended on its 15 Hz cadence.
✅ All frontend tests pass (ALL UTIL CHECKS PASSED) and production build succeeds (50 modules, no errors).
✅ All temporary instrumentation removed (0 residual markers); servers stopped; dist/ is gitignored.
Separate remaining issue (A/F — NOT fixed here, as instructed)
Perceptual sync is now bounded only by the ~16 ms frame budget, so the C/D lag is eliminated. However, individual words can still highlight at the wrong instant because of the already-diagnosed A/F intra-cue distortion, which is independent of this fix:
ggml-base DTW mishears the Roman-Urdu prompt, and the per-cue affine fit emits inter-onset intervals, so interior word boundaries are wrong even though cue endpoints are VAD-accurate.
Concrete example from cue #1: Agar = 0.000→0.08
4 (84 ms) — it will still visibly flash far shorter than it is spoken; word durations range 20–768 ms.
This lives entirely in word_alignment_service.py / the DTW data and was explicitly out of scope. It is the next thing to address only if words still look mistimed after this frontend fix — and it requires an alignment-engine change, not a frontend one

    """Return word timings tiling one cue window, or None when it is degenerate.

    Word i spans [b[i], b[i+1]] so timings are contiguous and non-overlapping by
    construction, and b[0]/b[n] are exactly the (VAD-accurate) cue start/end. A
    single-word cue always maps to the whole window; an empty or zero-span cue
    returns None.

    Interior boundaries follow config.WORD_ALIGNMENT_METHOD:
      * "proportional" (DEFAULT): character-length split via
        _proportional_word_split — robust, ignores the DTW anchors entirely.
      * "dtw" (opt-in): affine-fit the matched acoustic anchors into the cue,
        but fall back to the proportional split for THIS cue when the anchors
        are not trustworthy — coverage below `min_match`, or a fit that squeezes
        any word under WORD_ALIGNMENT_MIN_TRUSTED_WORD seconds.
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

    method = getattr(config, "WORD_ALIGNMENT_METHOD", "proportional")
    if method != "dtw":
        # DEFAULT: robust proportional character-length interior split.
        return _proportional_word_split(cue_words, cue_start, cue_end)

    # OPT-IN DTW path — affine-fit the acoustic anchors, with a per-cue
    # proportional fallback whenever the anchors cannot be trusted.
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

    Recognises the job's audio ONCE (cached) and matches every cue word to the
    recognised token stream with one global monotonic alignment. Each cue's
    interior word boundaries are then produced by config.WORD_ALIGNMENT_METHOD
    (default "proportional": a character-length split of the VAD-accurate cue
    window; opt-in "dtw": the acoustic-anchor fit with a per-cue proportional
    fallback). Empty or zero-span cues keep words=None and the caller's own
    proportional estimate. `subtitles` are objects with mutable
    .start/.end/.romanized_text/.words (romanization_service.Subtitle). Never
    raises — any problem leaves the cues untouched.
    """
    if not config.WORD_ALIGNMENT_ENABLED or audio_path is None or not subtitles:
        return 0

    prompt = " ".join((getattr(s, "romanized_text", "") or "") for s in subtitles)
    tokens = recognize_word_tokens(audio_path, prompt)
    if not tokens:
        return 0

    whisper_chars, whisper_times = _char_timeline(tokens)

    # Flatten all cue words and match them globally (drift-immune).
    flat_words: List[str] = []
    spans: List[Tuple[int, int]] = []
    for s in subtitles:
        cue_words = (getattr(s, "romanized_text", "") or "").split()
        spans.append((len(flat_words), len(flat_words) + len(cue_words)))
        flat_words.extend(cue_words)
    if not flat_words:
        return 0

    matched_flat = _global_word_match(flat_words, whisper_chars, whisper_times)
    matched_words = sum(1 for m in matched_flat if m is not None)
    min_match = config.WORD_ALIGNMENT_MIN_MATCH

    aligned = 0
    for s, (a, b) in zip(subtitles, spans):
        cue_words = flat_words[a:b]
        if not cue_words:
            continue
        fitted = _fit_cue_words(
            cue_words, matched_flat[a:b], float(s.start), float(s.end), min_match
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
        "Word timing: %d/%d cues aligned to audio (%d/%d words matched, %.0f%%).",
        aligned, len(subtitles), matched_words, len(flat_words),
        (100.0 * matched_words / len(flat_words)) if flat_words else 0.0,
    )
    return aligned
