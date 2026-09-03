from pydantic import BaseModel
from typing import Optional, List, Dict, Any

class VideoUploadResponse(BaseModel):
    job_id: str
    filename: str
    size: int
    status: str

class VideoErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None


# ---------------------------------------------------------------------------
# Phase 2 — Processing status & transcript models
# ---------------------------------------------------------------------------

class ProcessResponse(BaseModel):
    job_id: str
    status: str
    message: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str          # uploaded | extracting_audio | transcribing | completed | failed
    stage: Optional[str] = None
    progress: int        # stage-based milestone (never faked mid-stage)
    error: Optional[str] = None
    error_code: Optional[str] = None
    subtitles_available: bool = False  # Phase 3: romanized_subtitles.json exists


class TranscriptSegment(BaseModel):
    id: int
    start: float         # seconds
    end: float           # seconds
    text: str            # original ASR text, untranslated and unmodified


class TranscriptResponse(BaseModel):
    job_id: str
    segments: List[TranscriptSegment]


# ---------------------------------------------------------------------------
# Phase 3 — Romanized subtitle models
# ---------------------------------------------------------------------------

class WordTiming(BaseModel):
    """One real audio-derived word timing inside a cue (seconds).

    Produced by the offline whisper.cpp DTW aligner and persisted only for
    cues that pass its quality gate. A cue without validated word timings
    omits `words` entirely, signalling every consumer (editor highlight,
    SRT, burn-in) to fall back to proportional estimation — estimated
    timings are never stored or served as real ones.
    """
    word: str                    # exact whitespace token of romanized_text
    start: float                 # seconds, >= subtitle.start
    end: float                   # seconds, <= subtitle.end, > start


class RomanizeRequest(BaseModel):
    # The English translation is generated alongside romanization by
    # default — the editor, SRT and video exports all use it. Romanized
    # subtitles are always produced.
    include_english: bool = True


class RomanizedSubtitle(BaseModel):
    id: int
    start: float                 # seconds
    end: float                   # seconds
    original_text: str           # original ASR text, never overwritten
    romanized_text: str          # Latin-script (Roman Urdu/Hindi) version
    english_text: Optional[str] = None  # only present when requested
    words: Optional[List[WordTiming]] = None  # real word timings when aligned


class RomanizeResponse(BaseModel):
    job_id: str
    model: str
    include_english: bool
    subtitles: List[RomanizedSubtitle]
    language: Optional[str] = None                # Phase 4: editor display language
    style: Optional[Dict[str, Any]] = None        # Phase 4: caption style config


# ---------------------------------------------------------------------------
# Phase 4 — Subtitle editor & export models
# ---------------------------------------------------------------------------

class EditedSubtitle(BaseModel):
    """A subtitle that has been edited by the user."""
    id: int
    start: float                 # seconds (user-editable)
    end: float                   # seconds (user-editable)
    original_text: str           # original ASR text, never changed
    romanized_text: str          # user-editable romanized text
    english_text: Optional[str] = None  # user-editable English text
    # Word timings carried over from generation. On save they are re-validated
    # against the (possibly edited) romanized_text and cue window; stale or
    # mismatched timings are dropped so a manual edit never keeps fake words.
    words: Optional[List[WordTiming]] = None


class SubtitleSaveRequest(BaseModel):
    """Request to save edited subtitles."""
    subtitles: List[EditedSubtitle]
    language: Optional[str] = None                # selected display language
    style: Optional[Dict[str, Any]] = None        # caption style configuration


class SubtitleSaveResponse(BaseModel):
    """Confirmation response after saving edited subtitles."""
    job_id: str
    message: str
    subtitles_saved: int


# ---------------------------------------------------------------------------
# Phase 5 — Video export (caption burn-in) models
# ---------------------------------------------------------------------------

class VideoExportStartResponse(BaseModel):
    """Confirmation that a background render has been started."""
    job_id: str
    status: str
    message: str


class VideoExportStatusResponse(BaseModel):
    """Render state for a job's captioned video."""
    job_id: str
    status: str          # idle | exporting | ready | failed
    error: Optional[str] = None
    filename: Optional[str] = None


# ---------------------------------------------------------------------------
# Drafts — saved job listing for the Home page
# ---------------------------------------------------------------------------

class DraftItem(BaseModel):
    """A single saved job shown in the drafts list."""
    job_id: str
    video_filename: Optional[str] = None
    created_at: str               # ISO-8601 timestamp
    status: str                   # uploaded | completed | failed | …
    subtitles_saved: bool = False # True when user-edited subtitles.json exists
    has_export: bool = False      # True when captioned_video.mp4 exists


class DraftsListResponse(BaseModel):
    """Response for GET /drafts."""
    drafts: List[DraftItem]
    total: int
