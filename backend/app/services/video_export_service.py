r"""
Phase 5 — Video export service (FFmpeg caption burn-in).

Renders the user's edited captions into the uploaded video: the subtitle
track + caption style are converted into an ASS (Advanced SubStation Alpha)
file and burned into the pixels with FFmpeg's `subtitles` filter, producing
a standalone MP4 that can be downloaded straight from the editor.

Style mapping notes (CSS preview -> ASS):
- fontSize is px at 1080p video height, so PlayResY is fixed at 1080 and
  PlayResX preserves the video aspect ratio — sizes and positions map 1:1.
- The caption box width becomes ASS side margins (the wrap width) with the
  anchor point shifted for left/right text alignment, mirroring the CSS
  overlay (box centered on posX, vertically centered on posY).
- Word highlighting emits one Dialogue event per word: exactly one word is
  colour-overridden to the highlight colour at any moment (matching the
  preview's single active word) instead of ASS karaoke, which would fill
  words cumulatively and leave the whole line highlighted by the cue's end.
  Event boundaries spread the cue across words in proportion to each word's
  estimated speaking time (length + punctuation pauses) — the same
  speech-weighted estimate the editor preview uses.
- Backgrounds use BorderStyle=3 (opaque box); text outlines use
  BorderStyle=1. Only one can be burned in per ASS style, so a background
  wins when both are configured.
- Entrance animations map to \fad / \t scale / \move effects and are
  applied once per cue (the first word event), not replayed per word.

All FFmpeg invocation is isolated here; API routes never call FFmpeg
directly. Exports run on a background thread — the HTTP request that starts
one returns immediately and clients poll the status endpoint.
"""
import logging
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..config import (
    EXPORTED_VIDEO_FILENAME,
    FFMPEG_BIN,
    JOBS_DIR,
    VIDEO_EXPORT_TIMEOUT,
)
from .audio_service import FFmpegNotFoundError, _format_output, is_ffmpeg_installed
from .word_alignment_service import validate_word_timings

logger = logging.getLogger(__name__)

# ffprobe ships alongside ffmpeg — derive its name from FFMPEG_BIN so a
# custom FFMPEG_BIN path keeps working.
_FFMPEG_PATH = Path(FFMPEG_BIN)
FFPROBE_BIN = str(_FFMPEG_PATH.with_name("ffprobe" + _FFMPEG_PATH.suffix))

# Intermediate subtitle file written into the job directory. The burn-in
# filter references it by bare filename with cwd=job_dir, which sidesteps
# Windows drive-letter escaping inside the FFmpeg filtergraph.
ASS_FILENAME = "captions.ass"

_VALID_LANGUAGES = ("romanized", "english", "original")

# Python mirror of the frontend DEFAULT_CAPTION_STYLE (captionStyles.js).
# Persisted styles are partial; this fills in every missing field.
DEFAULT_CAPTION_STYLE: Dict[str, Any] = {
    "fontFamily": "Inter",
    "fontSize": 45,
    "fontWeight": 700,
    "uppercase": False,
    "textColor": "#FFFFFF",
    "backgroundColor": "#000000",
    "backgroundOpacity": 0.0,
    "outlineColor": "#000000",
    "outlineWidth": 0.0,
    "shadow": True,
    "shadowOpacity": 0.6,
    "alignment": "center",
    "position": "bottom",
    "posX": 50.0,
    "posY": 82.9,
    "animation": "none",
    "letterSpacing": 0.0,
    "lineHeight": 1.2,
    "wordHighlight": True,
    "highlightColor": "#22C55E",
    "boxWidth": 95.0,
}


class VideoExportError(RuntimeError):
    """FFmpeg failed to render the captioned video."""


# ---------------------------------------------------------------------------
# In-memory export state (job_id -> status snapshot)
# ---------------------------------------------------------------------------

_exports: Dict[str, Dict[str, Any]] = {}
_exports_lock = threading.Lock()


def get_exported_video_path(job_id: str) -> Optional[Path]:
    """Path of the finished captioned video, or None when not rendered yet."""
    path = JOBS_DIR / job_id / EXPORTED_VIDEO_FILENAME
    return path if path.exists() else None


def get_export_state(job_id: str) -> Dict[str, Any]:
    """Current export status snapshot: idle | exporting | ready | failed."""
    with _exports_lock:
        state = _exports.get(job_id)
        snapshot = dict(state) if state else None
    if snapshot is not None:
        return snapshot
    # No in-memory state (server restarted) — a finished render on disk is
    # still valid to download.
    if get_exported_video_path(job_id) is not None:
        return {"status": "ready", "error": None, "filename": EXPORTED_VIDEO_FILENAME}
    return {"status": "idle", "error": None, "filename": None}


def start_export(job_id: str, job_dir: Path, video_path: Path, data: Dict[str, Any]) -> None:
    """
    Kick off a background render for a job. Returns immediately; follow
    progress through get_export_state().

    Raises:
        VideoExportError: an export is already running for this job.
    """
    with _exports_lock:
        current = (_exports.get(job_id) or {}).get("status")
        if current == "exporting":
            raise VideoExportError("A video export is already in progress for this job.")
        _exports[job_id] = {"status": "exporting", "error": None, "filename": None}
    thread = threading.Thread(
        target=_run_export, args=(job_id, job_dir, video_path, data), daemon=True
    )
    thread.start()


def _run_export(job_id: str, job_dir: Path, video_path: Path, data: Dict[str, Any]) -> None:
    try:
        output_path = render_captioned_video(job_dir, video_path, data)
        with _exports_lock:
            _exports[job_id] = {
                "status": "ready",
                "error": None,
                "filename": output_path.name,
            }
    except Exception as e:  # surfaced to the client via the status endpoint
        logger.error("Video export failed for job %s: %s", job_id, e)
        with _exports_lock:
            _exports[job_id] = {"status": "failed", "error": str(e), "filename": None}


# ---------------------------------------------------------------------------
# ASS subtitle generation
# ---------------------------------------------------------------------------


def _ass_color(hex_color: Any, alpha: float = 0.0) -> str:
    """Convert '#RRGGBB' to an ASS &HAABBGGRR colour (alpha 0=opaque)."""
    h = str(hex_color or "#000000").lstrip("#")
    if len(h) != 6:
        h = "000000"
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        r, g, b = 0, 0, 0
    a = round(255 * min(1.0, max(0.0, alpha)))
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"


def _ass_rgb(hex_color: Any) -> str:
    """Convert '#RRGGBB' to an ASS &HBBGGRR colour for inline \\c overrides."""
    return "&H" + _ass_color(hex_color)[-6:]


def _ass_time(seconds: float) -> str:
    """Format seconds as ASS H:MM:SS.cc."""
    cs = max(0, round(float(seconds) * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _cue_text(sub: Dict[str, Any], language: str) -> str:
    """Display text for a cue in the requested language."""
    if language == "english":
        return (sub.get("english_text") or "").strip()
    if language == "original":
        return (sub.get("original_text") or "").strip()
    return (sub.get("romanized_text") or "").strip()


def _escape_ass_word(word: str) -> str:
    """Neutralize ASS override braces inside a word."""
    return word.replace("{", "(").replace("}", ")").replace("\n", " ")


def _speech_weight(word: str) -> float:
    """
    Estimated speaking-time weight for one word: longer words take longer
    to say, and trailing sentence/clause punctuation adds a natural pause.
    Mirrors speechWeight() in the frontend subtitleUtils so the preview
    highlight and the burn-in use identical timing.
    """
    weight = max(len(word), 1) + 2
    if word[-1] in ".!?…":
        weight += 5
    elif word[-1] in ",;:—–":
        weight += 3
    return weight


def _get_valid_word_timings(sub: Dict[str, Any], displayed_text: str) -> Optional[list]:
    """Real per-word (start, end) seconds for the DISPLAYED text, or None.

    Uses the persisted subtitle['words'] (whisper.cpp DTW timings) only when
    they validate against the displayed tokens and the cue window — i.e. the
    words on screen are exactly the romanized words the timings were aligned
    to. Any mismatch (English/original display, a manual text edit, missing or
    invalid timings) returns None so the caller falls back to the
    proportional _word_timings() estimate. This is the SAME persisted data the
    editor preview highlights from, so preview and burn-in agree.
    """
    words = sub.get("words")
    if not words:
        return None
    try:
        start = float(sub.get("start", 0) or 0)
        end = float(sub.get("end", 0) or 0)
    except (TypeError, ValueError):
        return None
    cleaned = validate_word_timings(words, displayed_text, start, end)
    if not cleaned:
        return None
    return [(w["start"], w["end"]) for w in cleaned]


def _boundaries_from_word_times(word_times: list, start: float, end: float) -> list:
    """Centisecond event boundaries from real per-word (start, end) seconds.

    Boundary k is the start of word k and the last boundary is the cue end.
    Values are clamped into the cue and forced strictly increasing (mirroring
    _word_timings' rounding guard) so ASS events never collapse or overlap.
    """
    n = len(word_times)
    start_cs = round(float(start) * 100)
    end_cs = max(round(float(end) * 100), start_cs + n)
    boundaries = [start_cs]
    for k in range(1, n):
        cs = round(float(word_times[k][0]) * 100)
        boundaries.append(min(max(cs, start_cs), end_cs))
    boundaries.append(end_cs)
    for k in range(1, n + 1):
        if boundaries[k] <= boundaries[k - 1]:
            boundaries[k] = boundaries[k - 1] + 1
    boundaries[-1] = max(boundaries[-1], end_cs)
    return boundaries


def _word_events(
    words: list,
    start: float,
    end: float,
    text_rgb: str,
    highlight_rgb: str,
    word_times: Optional[list] = None,
) -> list:
    """
    Build one Dialogue body per word: the active word is wrapped in an
    inline \\c override to the highlight colour and restored afterwards, so
    exactly one word is highlighted at a time (mirroring the preview).

    Event boundaries come from the real per-word timings (`word_times`) when
    they are valid and line up 1:1 with the displayed words; otherwise they
    fall back to the proportional _word_timings() estimate. Returns a list of
    (start_cs, end_cs, body) tuples with centisecond boundaries.
    """
    if word_times is not None and len(word_times) == len(words):
        boundaries = _boundaries_from_word_times(word_times, start, end)
    else:
        boundaries = _word_timings(words, start, end)
    events = []
    for k, word in enumerate(words):
        parts = []
        for j, w in enumerate(words):
            if j == k:
                parts.append(f"{{\\c{highlight_rgb}}}{w}{{\\c{text_rgb}}}")
            else:
                parts.append(w)
        events.append((boundaries[k], boundaries[k + 1], " ".join(parts)))
    return events


def _word_timings(words: list, start: float, end: float) -> list:
    """
    Centisecond event boundaries that split [start, end] across the words
    in proportion to each word's estimated speaking time — longer words and
    punctuation pauses get longer slots (the same speech-weighted estimate
    the preview's active-word logic uses). The last word absorbs the
    rounding remainder.
    """
    word_count = len(words)
    start_cs = round(start * 100)
    end_cs = round(end * 100)
    span_cs = max(end_cs - start_cs, word_count)
    weights = [_speech_weight(w) for w in words]
    total = sum(weights)

    boundaries = [start_cs]
    cumulative = 0.0
    for i, weight in enumerate(weights):
        cumulative += weight
        if i == word_count - 1:
            boundaries.append(start_cs + span_cs)
        else:
            boundaries.append(start_cs + round(span_cs * cumulative / total))
    boundaries[-1] = max(boundaries[-1], end_cs)  # keep the cue end exact
    # Guard against rounding collapses (very fast cues): strictly increasing.
    for i in range(1, len(boundaries)):
        if boundaries[i] <= boundaries[i - 1]:
            boundaries[i] = boundaries[i - 1] + 1
    return boundaries


def _override_tags(ass_align: int, x: int, y: int, animation: str, phase: str) -> str:
    """
    Positioning + entrance-animation override block for one event.

    `phase` is "only" for single-event cues, or "first"/"mid"/"last" for
    per-word events: entrance animations play once per cue (on the first
    word event) instead of replaying on every word swap; a fade cue also
    fades out on the last word event.
    """
    tags = [f"\\an{ass_align}"]
    if animation == "slide-up" and phase == "first":
        tags.append(f"\\move({x},{y + 60},{x},{y},0,220)")
    elif animation == "slide-down" and phase == "first":
        tags.append(f"\\move({x},{y - 60},{x},{y},0,220)")
    else:
        tags.append(f"\\pos({x},{y})")
    if animation == "fade":
        fade_in = 200 if phase in ("first", "only") else 0
        fade_out = 150 if phase in ("last", "only") else 0
        if fade_in or fade_out:
            tags.append(f"\\fad({fade_in},{fade_out})")
    elif animation == "pop" and phase in ("first", "only"):
        tags.append("\\fscx70\\fscy70\\t(0,180,\\fscx100\\fscy100)")
    return "{" + "".join(tags) + "}"


def generate_ass_content(
    subtitles: list,
    language: str,
    style: Dict[str, Any],
    video_width: int,
    video_height: int,
) -> str:
    """Render the subtitle track + caption style as a complete ASS document."""
    style = {**DEFAULT_CAPTION_STYLE, **(style or {})}

    # fontSize is defined at 1080p height — pin PlayResY and derive PlayResX
    # from the real video aspect so percentages map directly.
    play_h = 1080
    try:
        ratio = float(video_width) / float(video_height)
    except (TypeError, ZeroDivisionError):
        ratio = 16 / 9
    play_w = max(320, min(7680, round(play_h * ratio)))

    font_size = max(8, round(float(style.get("fontSize", 45) or 45)))
    font_name = str(style.get("fontFamily", "Inter"))
    bold = -1 if int(style.get("fontWeight", 700) or 700) >= 600 else 0
    spacing = round(float(style.get("letterSpacing", 0) or 0))
    uppercase = bool(style.get("uppercase", False))
    word_highlight = bool(style.get("wordHighlight", True))
    text_color = style.get("textColor", "#FFFFFF")
    highlight_color = style.get("highlightColor", "#22C55E")

    # PrimaryColour is the text colour; the highlighted word overrides it
    # inline per event (see _word_events). SecondaryColour is unused.
    primary = _ass_color(text_color)
    secondary = _ass_color(text_color)
    highlight_rgb = _ass_rgb(highlight_color)
    text_rgb = _ass_rgb(text_color)

    background_opacity = min(1.0, max(0.0, float(style.get("backgroundOpacity", 0) or 0)))
    if background_opacity > 0:
        border_style = 3  # opaque box behind the text
        outline = max(1, round(font_size * 0.3))  # box padding
        outline_colour = _ass_color(
            style.get("backgroundColor", "#000000"), alpha=1 - background_opacity
        )
    else:
        border_style = 1  # outline + shadow around the glyphs
        outline = max(0, round(float(style.get("outlineWidth", 0) or 0)))
        outline_colour = _ass_color(style.get("outlineColor", "#000000"))

    shadow = bool(style.get("shadow", True))
    shadow_opacity = min(1.0, max(0.0, float(style.get("shadowOpacity", 0.6) or 0)))
    if shadow and shadow_opacity > 0:
        back_colour = _ass_color("#000000", alpha=1 - shadow_opacity)
        shadow_depth = 2
    else:
        back_colour = _ass_color("#000000", alpha=1.0)
        shadow_depth = 0

    box_width = min(100.0, max(10.0, float(style.get("boxWidth", 88) or 88)))
    margin = round(play_w * (100 - box_width) / 200)

    # Text alignment anchors the wrapped block: the CSS overlay centers the
    # box on posX with text aligned inside it, so shift the ASS anchor to
    # the matching box edge for left/right alignment.
    alignment = str(style.get("alignment", "center"))
    pos_x = min(100.0, max(0.0, float(style.get("posX", 50) or 50)))
    pos_y = min(100.0, max(0.0, float(style.get("posY", 82.9) or 82.9)))
    if alignment == "left":
        anchor_x = pos_x - box_width / 2
        ass_align = 4
    elif alignment == "right":
        anchor_x = pos_x + box_width / 2
        ass_align = 6
    else:
        anchor_x = pos_x
        ass_align = 5
    anchor_x = min(100.0, max(0.0, anchor_x))
    anchor_px = round(play_w * anchor_x / 100)
    anchor_py = round(play_h * pos_y / 100)
    animation = str(style.get("animation", "none"))

    header = (
        "[Script Info]\n"
        "; Generated by Talafuz AI — burned-in caption export\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {play_w}\n"
        f"PlayResY: {play_h}\n"
        "WrapStyle: 1\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Caption,{font_name},{font_size},{primary},{secondary},"
        f"{outline_colour},{back_colour},{bold},0,0,0,100,100,{spacing},0,"
        f"{border_style},{outline},{shadow_depth},5,{margin},{margin},0,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )

    lines = []
    for sub in subtitles:
        try:
            start = float(sub.get("start", 0) or 0)
            end = float(sub.get("end", 0) or 0)
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        raw_text = _cue_text(sub, language)
        if not raw_text:
            continue
        # Real word timings apply only when the words on screen are the
        # romanized words they were aligned to (validated against raw_text,
        # before any uppercase cosmetic transform, which preserves token
        # count and order).
        word_times = _get_valid_word_timings(sub, raw_text)
        text = raw_text.upper() if uppercase else raw_text
        words = [_escape_ass_word(w) for w in text.split()]
        if not words:
            continue
        if word_times is not None and len(word_times) != len(words):
            word_times = None  # display token count differs — fall back

        if word_highlight and len(words) > 1:
            # One event per word: only the spoken word carries the highlight
            # colour at any moment (entrance animation on the first event,
            # fade-out on the last).
            for k, (cs0, cs1, body) in enumerate(
                _word_events(words, start, end, text_rgb, highlight_rgb, word_times)
            ):
                phase = "first" if k == 0 else "last" if k == len(words) - 1 else "mid"
                override = _override_tags(ass_align, anchor_px, anchor_py, animation, phase)
                lines.append(
                    f"Dialogue: 0,{_ass_time(cs0 / 100)},{_ass_time(cs1 / 100)},"
                    f"Caption,,0,0,0,,{override}{body}"
                )
        else:
            # Plain cue (or a single word, which stays highlighted for its
            # whole duration — matching the preview's active-word estimate).
            if word_highlight:
                body = f"{{\\c{highlight_rgb}}}{words[0]}"
            else:
                body = " ".join(words)
            override = _override_tags(ass_align, anchor_px, anchor_py, animation, "only")
            lines.append(
                f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Caption,,0,0,0,,"
                f"{override}{body}"
            )

    return header + "\n".join(lines) + "\n"


def _probe_video_size(video_path: Path) -> Tuple[int, int]:
    """Video pixel dimensions via ffprobe; (1920, 1080) fallback."""
    default = (1920, 1080)
    try:
        result = subprocess.run(
            [
                FFPROBE_BIN,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return default
    if result.returncode != 0:
        return default
    parts = (result.stdout or "").strip().split(",")
    if len(parts) == 2:
        try:
            w, h = int(parts[0]), int(parts[1])
            if w > 0 and h > 0:
                return w, h
        except ValueError:
            pass
    return default


# ---------------------------------------------------------------------------
# FFmpeg render
# ---------------------------------------------------------------------------


def render_captioned_video(job_dir: Path, video_path: Path, data: Dict[str, Any]) -> Path:
    """
    Burn the caption track into the video and return the output path.

    Raises:
        FFmpegNotFoundError: ffmpeg executable missing.
        FileNotFoundError: the video file does not exist.
        VideoExportError: no subtitles, or ffmpeg failed / produced no output.
    """
    if not is_ffmpeg_installed():
        raise FFmpegNotFoundError(
            f"FFmpeg ('{FFMPEG_BIN}') is not installed on this system. Install FFmpeg "
            "(e.g. 'sudo apt install ffmpeg') and restart the backend."
        )

    job_dir = Path(job_dir)
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path.name}")

    subtitles = data.get("subtitles") or []
    if not subtitles:
        raise VideoExportError("No subtitles available to burn into the video.")

    language = data.get("language") or "romanized"
    if language not in _VALID_LANGUAGES:
        language = "romanized"
    style = {**DEFAULT_CAPTION_STYLE, **(data.get("style") or {})}

    width, height = _probe_video_size(video_path)
    ass_content = generate_ass_content(subtitles, language, style, width, height)
    ass_path = job_dir / ASS_FILENAME
    ass_path.write_text(ass_content, encoding="utf-8")

    # Render to a .part file and rename on success so a crash mid-render
    # never leaves a half-written video that looks "ready".
    output_path = job_dir / EXPORTED_VIDEO_FILENAME
    temp_path = job_dir / (EXPORTED_VIDEO_FILENAME + ".part")
    for stale in (temp_path, output_path):
        if stale.exists():
            stale.unlink()

    cmd = [
        FFMPEG_BIN,
        "-y",                       # overwrite any previous render
        "-i", video_path.name,      # relative to cwd=job_dir
        # Bare filename + cwd sidesteps Windows drive-letter escaping in
        # the filtergraph (colons would need double-escaping).
        "-vf", f"subtitles={ASS_FILENAME}",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",  # web-friendly MP4 (moov atom first)
        "-f", "mp4",                # temp name hides the extension from ffmpeg
        temp_path.name,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",       # ffmpeg may echo non-UTF-8 filenames
            timeout=VIDEO_EXPORT_TIMEOUT,
            cwd=str(job_dir),
        )
    except subprocess.TimeoutExpired:
        raise VideoExportError(
            f"Video export timed out after {VIDEO_EXPORT_TIMEOUT} seconds."
        )
    except OSError as e:
        raise VideoExportError(f"Failed to run FFmpeg: {e}")

    if result.returncode != 0:
        if temp_path.exists():
            temp_path.unlink()
        raise VideoExportError(
            "Video export failed (FFmpeg could not render the captions). "
            f"FFmpeg output: {_format_output(result)}"
        )

    if not temp_path.exists() or temp_path.stat().st_size == 0:
        raise VideoExportError("Video export produced no output.")

    temp_path.replace(output_path)
    logger.info("Rendered captioned video for job at %s", output_path)
    return output_path
