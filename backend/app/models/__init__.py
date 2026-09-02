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

class RomanizeRequest(BaseModel):
    # English translation is optional and generated separately from
    # romanization. Romanized subtitles are always produced.
    include_english: bool = False


class RomanizedSubtitle(BaseModel):
    id: int
    start: float                 # seconds
    end: float                   # seconds
    original_text: str           # original ASR text, never overwritten
    romanized_text: str          # Latin-script (Roman Urdu/Hindi) version
    english_text: Optional[str] = None  # only present when requested


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
