"""
Phase 2 — Processing pipeline: video → audio → timestamped transcript.

Runs in a worker thread (invoked via asyncio.to_thread from the API layer),
so the event loop stays responsive and background/queue-based processing
can be introduced later without changing this code.
"""
import logging

from ..config import JOBS_DIR
from . import VideoService
from .audio_service import extract_audio, get_audio_path, FFmpegNotFoundError, AudioExtractionError
from .asr_service import (
    get_asr_provider,
    save_transcript,
    ASRNotConfiguredError,
    ASRError,
)
from .job_manager import job_manager

logger = logging.getLogger(__name__)


def run_processing_job(job_id: str) -> None:
    """
    Execute the full Phase 2 pipeline for a job:
      1. Locate the uploaded video by job_id.
      2. Extract audio to audio.wav in the job directory.
      3. Transcribe through the configured ASR provider.
      4. Store the timestamped transcript as transcript.json.

    All failures are recorded in the job manager — this function never
    raises into the caller.
    """
    job_dir = JOBS_DIR / job_id
    try:
        # Stage 1: audio extraction
        job_manager.update(job_id, "extracting_audio", "Extracting audio from video")
        video_path = VideoService.get_video_path(job_id)
        if video_path is None:
            raise FileNotFoundError("Uploaded video not found for this job.")
        audio_path = extract_audio(video_path, get_audio_path(job_dir))

        # Stage 2: speech recognition
        job_manager.update(job_id, "transcribing", "Transcribing audio")
        provider = get_asr_provider()
        result = provider.transcribe(audio_path)
        save_transcript(
            job_dir,
            job_id,
            result["segments"],
            provider.name,
            raw=result.get("raw"),
        )

        job_manager.update(job_id, "completed", "Transcript ready")
    except ASRNotConfiguredError as e:
        logger.warning("ASR not configured for job %s: %s", job_id, e)
        job_manager.mark_failed(job_id, str(e), error_code="ASR_NOT_CONFIGURED")
    except ASRError as e:
        # Provider-level failures (API key, network, timeout, bad response).
        logger.error("ASR failed for job %s: %s", job_id, e)
        job_manager.mark_failed(job_id, str(e), error_code="ASR_FAILED")
    except FFmpegNotFoundError as e:
        logger.error("FFmpeg missing for job %s: %s", job_id, e)
        job_manager.mark_failed(job_id, str(e), error_code="FFMPEG_NOT_FOUND")
    except (AudioExtractionError, FileNotFoundError) as e:
        logger.error("Processing failed for job %s: %s", job_id, e)
        job_manager.mark_failed(job_id, str(e))
    except Exception as e:  # noqa: BLE001 — record any unexpected failure
        logger.exception("Unexpected processing failure for job %s", job_id)
        job_manager.mark_failed(job_id, f"Processing failed: {e}")
