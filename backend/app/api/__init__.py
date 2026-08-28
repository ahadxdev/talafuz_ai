import asyncio
import logging
from functools import partial

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from ..services import VideoService
from ..services.pipeline import run_processing_job
from ..services.job_manager import job_manager, resolve_job_state
from ..services.asr_service import load_transcript, get_asr_provider, ASRNotConfiguredError
from ..services.romanization_service import (
    QwenRomanizationService,
    RomanizationError,
    RomanizationNotConfiguredError,
    ROMANIZED_SUBTITLES_FILENAME,
    load_romanized_subtitles,
    save_romanized_subtitles,
)
from ..config import JOBS_DIR
from ..models import (
    VideoUploadResponse,
    ProcessResponse,
    JobStatusResponse,
    TranscriptResponse,
    RomanizeRequest,
    RomanizeResponse,
    RomanizedSubtitle,
)

router = APIRouter(prefix="/videos", tags=["videos"])


@router.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    """
    Upload a video file.
    
    Returns a job ID, filename, file size, and status.
    """
    # Read file size
    contents = await file.read()
    file_size = len(contents)
    await file.seek(0)  # Reset file pointer
    
    # Validate file
    is_valid, error_msg = VideoService.validate_video_file(file.filename, file_size)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)
    
    # Generate job ID
    job_id = VideoService.generate_job_id()
    
    # Save video
    success, result, error_detail = await VideoService.save_video(job_id, file)
    if not success:
        raise HTTPException(status_code=500, detail=error_detail)
    
    return VideoUploadResponse(
        job_id=job_id,
        filename=file.filename,
        size=file_size,
        status="uploaded"
    )


@router.get("/{job_id}/file")
async def get_video_file(job_id: str):
    """
    Retrieve the uploaded video file for a job.
    """
    video_path = VideoService.get_video_path(job_id)
    
    if not video_path or not video_path.exists():
        raise HTTPException(status_code=404, detail="Video not found for this job ID.")
    
    return FileResponse(
        path=video_path,
        media_type="video/mp4",
        filename=video_path.name
    )


# ---------------------------------------------------------------------------
# Phase 2 — Processing, status and transcript endpoints
# ---------------------------------------------------------------------------

# HTTP status used when a requested resource exists but processing state
# does not allow it yet (not started / still running).
_CONFLICT = 409

logger = logging.getLogger(__name__)


def _log_pipeline_result(job_id: str, future: "asyncio.Future") -> None:
    """Log unexpected pipeline exceptions (normally the pipeline never raises)."""
    error = future.exception()
    if error is not None:
        logger.exception("Pipeline crashed for job %s: %s", job_id, error)
        job_manager.mark_failed(job_id, f"Processing failed: {error}")


@router.post("/{job_id}/process", response_model=ProcessResponse)
async def start_processing(job_id: str):
    """
    Start Phase 2 processing (audio extraction → transcription) for a job.

    Security: only the job_id is accepted; all files are resolved through
    the job storage system — no filesystem paths come from the client.
    """
    job_dir = JOBS_DIR / job_id
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Unknown job ID.")

    if VideoService.get_video_path(job_id) is None:
        raise HTTPException(
            status_code=404,
            detail="No uploaded video found for this job ID.",
        )

    existing = job_manager.get(job_id)
    if existing is not None:
        if existing["status"] in ("queued", "extracting_audio", "transcribing"):
            raise HTTPException(
                status_code=_CONFLICT,
                detail="Processing is already in progress for this job.",
            )
        if existing["status"] == "completed":
            raise HTTPException(
                status_code=_CONFLICT,
                detail="Processing has already completed. Fetch the transcript instead.",
            )
        # status == "failed": allow restarting the pipeline

    # Fail fast with a clear configuration error instead of extracting audio
    # and failing mid-pipeline (ASR provider not configured yet).
    try:
        get_asr_provider()
    except ASRNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))

    job_manager.register(job_id)
    # Runs in a worker thread: the request completes immediately and the
    # client follows progress via GET /status. This keeps the pipeline
    # isolated so background/queued processing can replace it later.
    future = asyncio.get_running_loop().run_in_executor(None, run_processing_job, job_id)
    # Ensure unexpected scheduling errors are logged instead of being
    # silently dropped by the executor.
    future.add_done_callback(partial(_log_pipeline_result, job_id))

    return ProcessResponse(
        job_id=job_id,
        status="queued",
        message="Processing started: audio extraction and transcription.",
    )


@router.get("/{job_id}/status", response_model=JobStatusResponse)
async def get_status(job_id: str):
    """
    Return the current processing status of a job.

    Progress values are stage milestones (never faked mid-stage):
    0 uploaded · 5 queued · 25 extracting audio · 50 transcribing ·
    100 completed.
    """
    state = resolve_job_state(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Unknown job ID.")

    return JobStatusResponse(
        job_id=job_id,
        subtitles_available=(JOBS_DIR / job_id / ROMANIZED_SUBTITLES_FILENAME).exists(),
        **state,
    )


@router.get("/{job_id}/transcript", response_model=TranscriptResponse)
async def get_transcript(job_id: str):
    """
    Return the timestamped transcript for a completed job. The transcript
    contains the original recognized speech — untranslated and unmodified.
    """
    job_dir = JOBS_DIR / job_id
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Unknown job ID.")

    state = resolve_job_state(job_id)
    if state["status"] == "uploaded":
        raise HTTPException(
            status_code=_CONFLICT,
            detail="Processing has not been started for this job yet.",
        )
    if state["status"] in ("queued", "extracting_audio", "transcribing"):
        raise HTTPException(
            status_code=_CONFLICT,
            detail="Processing is still in progress. Check the status endpoint.",
        )
    if state["status"] == "failed":
        status_code = 503 if state.get("error_code") == "ASR_NOT_CONFIGURED" else 500
        raise HTTPException(
            status_code=status_code,
            detail=state.get("error") or "Processing failed for this job.",
        )

    data = load_transcript(job_dir)
    if data is None:
        raise HTTPException(status_code=404, detail="Transcript not available for this job.")

    return TranscriptResponse(job_id=job_id, segments=data["segments"])


# ---------------------------------------------------------------------------
# Phase 3 — Romanization + optional English translation endpoints
# ---------------------------------------------------------------------------

def _run_romanization(job_id: str, segments, include_english: bool) -> dict:
    """Blocking romanization work — always runs in a worker thread."""
    service = QwenRomanizationService()
    subtitles = service.romanize_segments(segments, include_english=include_english)
    save_romanized_subtitles(
        JOBS_DIR / job_id, job_id, subtitles,
        model=service._model,
        include_english=include_english,
    )
    return {
        "job_id": job_id,
        "model": service._model,
        "include_english": include_english,
        "subtitles": [RomanizedSubtitle(**s.to_dict()) for s in subtitles],
    }


@router.post("/{job_id}/romanize", response_model=RomanizeResponse)
async def romanize_transcript(job_id: str, body: RomanizeRequest = RomanizeRequest()):
    """
    Generate Romanized subtitles (Latin-script Roman Urdu/Hindi) from the
    existing ASR transcript of a completed job. English translation is
    optional and generated separately.

    The original transcript.json is never modified.
    """
    job_dir = JOBS_DIR / job_id
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Unknown job ID.")

    transcript = load_transcript(job_dir)
    if transcript is None or not transcript.get("segments"):
        raise HTTPException(
            status_code=_CONFLICT,
            detail="No transcript available. Run processing first to "
            "generate the ASR transcript.",
        )

    # Fail fast on missing configuration before doing any work.
    try:
        QwenRomanizationService()
    except RomanizationNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))

    try:
        result = await asyncio.get_running_loop().run_in_executor(
            None, _run_romanization, job_id, transcript["segments"],
            body.include_english,
        )
    except RomanizationError as e:
        logger.error("Romanization failed for job %s: %s", job_id, e)
        raise HTTPException(status_code=502, detail=str(e))
    return RomanizeResponse(**result)


@router.get("/{job_id}/subtitles", response_model=RomanizeResponse)
async def get_subtitles(job_id: str):
    """Return previously generated romanized subtitles for a job."""
    job_dir = JOBS_DIR / job_id
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Unknown job ID.")

    data = load_romanized_subtitles(job_dir)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail="Romanized subtitles have not been generated for this job yet.",
        )
    return RomanizeResponse(
        job_id=data.get("job_id", job_id),
        model=data.get("model", ""),
        include_english=bool(data.get("include_english", False)),
        subtitles=[RomanizedSubtitle(**s) for s in data["subtitles"]],
    )
