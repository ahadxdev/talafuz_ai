from pydantic import BaseModel
from typing import Optional, List

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


class TranscriptSegment(BaseModel):
    id: int
    start: float         # seconds
    end: float           # seconds
    text: str            # original ASR text, untranslated and unmodified


class TranscriptResponse(BaseModel):
    job_id: str
    segments: List[TranscriptSegment]
