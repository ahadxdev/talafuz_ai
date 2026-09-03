import asyncio
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, UploadFile, File, HTTPException, Query
from fastapi.responses import FileResponse, Response
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
from ..services.srt_service import generate_srt
from ..services.audio_service import is_ffmpeg_installed
from ..services.video_export_service import (
    VideoExportError,
    get_export_state,
    get_exported_video_path,
    start_export,
)
from ..config import JOBS_DIR, EXPORTED_VIDEO_FILENAME
from ..models import (
    VideoUploadResponse,
    ProcessResponse,
    JobStatusResponse,
    TranscriptResponse,
    RomanizeRequest,
    RomanizeResponse,
    RomanizedSubtitle,
    EditedSubtitle,
    SubtitleSaveRequest,
    SubtitleSaveResponse,
    VideoExportStartResponse,
    VideoExportStatusResponse,
    DraftItem,
    DraftsListResponse,
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


# ---------------------------------------------------------------------------
# Drafts — list saved jobs and delete them
# ---------------------------------------------------------------------------


@router.get("/drafts", response_model=DraftsListResponse)
async def list_drafts():
    """
    List all saved jobs (drafts) with metadata.

    Each job directory under JOBS_DIR is inspected for its video filename,
    creation timestamp, processing status, and whether user-edited subtitles
    or an exported video exist.
    """
    drafts = []
    if not JOBS_DIR.is_dir():
        return DraftsListResponse(drafts=[], total=0)

    for entry in sorted(JOBS_DIR.iterdir(), key=lambda p: p.stat().st_ctime, reverse=True):
        if not entry.is_dir():
            continue
        job_id = entry.name

        # Resolve video filename (skip exported captioned_video.mp4)
        video_filename = None
        video_path = VideoService.get_video_path(job_id)
        if video_path is not None:
            video_filename = video_path.name

        # Creation time from directory
        created_iso = datetime.fromtimestamp(entry.stat().st_ctime, tz=timezone.utc).isoformat()

        # Processing status (reconstruct from artifacts if not in memory)
        state = resolve_job_state(job_id)
        status = state["status"] if state else "unknown"

        # Subtitles saved?
        subtitles_saved = (entry / EDITED_SUBTITLES_FILENAME).exists()

        # Export exists?
        has_export = (entry / EXPORTED_VIDEO_FILENAME).exists()

        drafts.append(DraftItem(
            job_id=job_id,
            video_filename=video_filename,
            created_at=created_iso,
            status=status,
            subtitles_saved=subtitles_saved,
            has_export=has_export,
        ))

    return DraftsListResponse(drafts=drafts, total=len(drafts))


@router.delete("/{job_id}")
async def delete_job(job_id: str):
    """
    Delete a job and all its data permanently.
    """
    job_dir = JOBS_DIR / job_id
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Unknown job ID.")

    try:
        shutil.rmtree(job_dir)
    except OSError as e:
        logger.error("Failed to delete job directory for %s: %s", job_id, e)
        raise HTTPException(
            status_code=500,
            detail="Failed to delete job data. Please try again.",
        )

    # Also clean up in-memory state if present
    # (JobManager has no delete method, but the state is ephemeral anyway)

    logger.info("Deleted job %s and its data directory", job_id)
    return {"message": "Job deleted successfully.", "job_id": job_id}


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


# User-edited subtitles (Phase 4 editor) are stored separately from the
# generated romanized output and take precedence when loading.
EDITED_SUBTITLES_FILENAME = "subtitles.json"


def _load_edited_subtitles(job_dir: Path) -> Optional[Dict[str, Any]]:
    """Load user-edited subtitles.json; None when absent or invalid."""
    path = Path(job_dir) / EDITED_SUBTITLES_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("subtitles"), list):
        return None
    return data


@router.get("/{job_id}/subtitles", response_model=RomanizeResponse)
async def get_subtitles(job_id: str):
    """
    Return subtitles for a job.

    User-edited subtitles (subtitles.json, saved from the editor) take
    precedence over the generated romanized_subtitles.json. The Phase 4
    editor state (display language and caption style) is returned when
    present.
    """
    job_dir = JOBS_DIR / job_id
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Unknown job ID.")

    data = _load_edited_subtitles(job_dir)
    if data is None:
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
        language=data.get("language"),
        style=data.get("style"),
        subtitles=[RomanizedSubtitle(**s) for s in data["subtitles"]],
    )


# ---------------------------------------------------------------------------
# Phase 4 — Subtitle editor endpoints
# ---------------------------------------------------------------------------


@router.post("/{job_id}/subtitles/save", response_model=SubtitleSaveResponse)
async def save_edited_subtitles(job_id: str, request: SubtitleSaveRequest):
    """
    Save user-edited subtitles to a separate file (subtitles.json).
    
    The edited subtitles are stored separately from the original romanized
    output (romanized_subtitles.json), preserving the original generated
    data.
    
    Validation:
    - start >= 0
    - end > start
    - timestamps must be numeric
    - subtitle text cannot be empty
    """
    job_dir = JOBS_DIR / job_id
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Unknown job ID.")

    # Validate subtitles before saving
    if not request.subtitles:
        raise HTTPException(status_code=400, detail="No subtitles provided.")

    if request.language and request.language not in ("romanized", "english", "original"):
        raise HTTPException(
            status_code=400,
            detail="Invalid language. Must be 'romanized', 'english', or 'original'.",
        )

    for sub in request.subtitles:
        if not isinstance(sub.start, (int, float)) or not isinstance(sub.end, (int, float)):
            raise HTTPException(
                status_code=400,
                detail=f"Subtitle {sub.id}: start and end must be numeric.",
            )
        if sub.start < 0 or sub.end < 0:
            raise HTTPException(
                status_code=400,
                detail=f"Subtitle {sub.id}: timestamps cannot be negative.",
            )
        if sub.start >= sub.end:
            raise HTTPException(
                status_code=400,
                detail=f"Subtitle {sub.id}: start must be before end.",
            )
        if not sub.romanized_text or not sub.romanized_text.strip():
            raise HTTPException(
                status_code=400,
                detail=f"Subtitle {sub.id}: romanized_text cannot be empty.",
            )

    # Ensure directory exists
    job_dir.mkdir(parents=True, exist_ok=True)

    # Save to subtitles.json (language + caption style are part of the
    # editor state and round-trip back through GET /subtitles)
    subtitles_path = job_dir / EDITED_SUBTITLES_FILENAME
    data = {
        "job_id": job_id,
        "language": request.language,
        "style": request.style,
        "subtitles": [
            {
                "id": sub.id,
                "start": round(sub.start, 3),
                "end": round(sub.end, 3),
                "original_text": sub.original_text,
                "romanized_text": sub.romanized_text,
                "english_text": sub.english_text,
            }
            for sub in request.subtitles
        ],
    }

    with open(subtitles_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info("Saved edited subtitles for job %s to %s", job_id, subtitles_path)

    return SubtitleSaveResponse(
        job_id=job_id,
        message="Subtitles saved successfully.",
        subtitles_saved=len(request.subtitles),
    )


@router.get("/{job_id}/export/srt")
async def export_srt(
    job_id: str,
    mode: str = Query("romanized", pattern="^(romanized|english|dual|original)$"),
):
    """
    Export subtitles as SRT file.

    Args:
        job_id: The job identifier.
        mode: Display mode for SRT:
            - "romanized": romanized text only
            - "english": English text only
            - "dual": romanized text followed by English text
            - "original": original ASR transcription text

    Returns:
        SRT file as downloadable response.

    Raises:
        400: Invalid mode or subtitle data issues.
        404: Job not found or no subtitles available.
    """
    job_dir = JOBS_DIR / job_id
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Unknown job ID.")

    # Try to load edited subtitles first; fall back to romanized_subtitles
    subtitles_path = job_dir / EDITED_SUBTITLES_FILENAME
    romanized_path = job_dir / ROMANIZED_SUBTITLES_FILENAME

    data = None
    if subtitles_path.exists():
        try:
            with open(subtitles_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error("Failed to load edited subtitles for job %s: %s", job_id, e)
            raise HTTPException(
                status_code=500,
                detail="Failed to load edited subtitles.",
            )
    elif romanized_path.exists():
        try:
            with open(romanized_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error("Failed to load romanized subtitles for job %s: %s", job_id, e)
            raise HTTPException(
                status_code=500,
                detail="Failed to load romanized subtitles.",
            )

    if not data or not data.get("subtitles"):
        raise HTTPException(
            status_code=404,
            detail="No subtitles available for this job. Generate subtitles first.",
        )

    # Generate SRT
    try:
        srt_content = generate_srt(data["subtitles"], mode=mode)
    except ValueError as e:
        logger.warning("SRT generation failed for job %s (mode=%s): %s", job_id, mode, e)
        raise HTTPException(status_code=400, detail=str(e))

    # Return as downloadable file (generated text — not a file on disk,
    # so a plain Response is used instead of FileResponse)
    return Response(
        content=srt_content.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="subtitles_{mode}_{job_id}.srt"'},
    )


# ---------------------------------------------------------------------------
# Phase 5 — Video export (caption burn-in) endpoints
# ---------------------------------------------------------------------------


@router.post("/{job_id}/export/video", response_model=VideoExportStartResponse)
async def export_video(job_id: str):
    """
    Start rendering the edited captions into the video (FFmpeg burn-in).

    The render always uses the persisted editor state — subtitles.json when
    present (saved from the Phase 4 editor), otherwise the generated
    romanized_subtitles.json — so clients must save their edits before
    exporting. The render itself runs on a background thread; follow
    progress through GET /export/video/status.
    """
    job_dir = JOBS_DIR / job_id
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Unknown job ID.")

    video_path = VideoService.get_video_path(job_id)
    if video_path is None:
        raise HTTPException(
            status_code=404,
            detail="No uploaded video found for this job ID.",
        )

    data = _load_edited_subtitles(job_dir) or load_romanized_subtitles(job_dir)
    if data is None or not data.get("subtitles"):
        raise HTTPException(
            status_code=404,
            detail="No subtitles available for this job. Generate subtitles first.",
        )

    if not is_ffmpeg_installed():
        raise HTTPException(
            status_code=503,
            detail="FFmpeg is not installed on this system; video export is unavailable.",
        )

    try:
        start_export(job_id, job_dir, video_path, data)
    except VideoExportError as e:
        raise HTTPException(status_code=_CONFLICT, detail=str(e))

    return VideoExportStartResponse(
        job_id=job_id,
        status="exporting",
        message="Video export started. Poll the status endpoint until it is ready.",
    )


@router.get("/{job_id}/export/video/status", response_model=VideoExportStatusResponse)
async def get_video_export_status(job_id: str):
    """Video export state for a job: idle | exporting | ready | failed."""
    job_dir = JOBS_DIR / job_id
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Unknown job ID.")

    state = get_export_state(job_id)
    return VideoExportStatusResponse(
        job_id=job_id,
        status=state.get("status", "idle"),
        error=state.get("error"),
        filename=state.get("filename"),
    )


@router.get("/{job_id}/export/video/file")
async def download_exported_video(job_id: str):
    """Download the captioned (burn-in) video for a job."""
    job_dir = JOBS_DIR / job_id
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Unknown job ID.")

    state = get_export_state(job_id)
    if state.get("status") == "exporting":
        raise HTTPException(
            status_code=_CONFLICT,
            detail="Video export is still in progress.",
        )

    video_path = get_exported_video_path(job_id)
    if video_path is None:
        raise HTTPException(
            status_code=404,
            detail="Exported video not found. Start an export first.",
        )

    return FileResponse(
        path=video_path,
        media_type="video/mp4",
        filename=f"talafuz_captions_{job_id[:8]}.mp4",
    )

