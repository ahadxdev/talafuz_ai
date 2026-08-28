"""
Phase 2 — Job processing state manager.

MVP: in-memory job state (no database, per Phase 2 requirements). State is
thread-safe because the processing pipeline runs in worker threads. If a
server restart loses in-memory state, status is reconstructed from the job
directory artifacts (transcript.json / audio.wav).
"""
import threading
from pathlib import Path
from typing import Dict, Optional

from ..config import JOBS_DIR
from . import VideoService
from .audio_service import get_audio_path
from .asr_service import TRANSCRIPT_FILENAME

# Stage-based progress milestones (never faked mid-stage).
STAGE_PROGRESS = {
    "queued": 5,
    "extracting_audio": 25,
    "transcribing": 50,
    "completed": 100,
}


class JobManager:
    """In-memory registry of processing jobs."""

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict] = {}

    def register(self, job_id: str) -> Dict:
        """Register a newly started processing job."""
        state = {
            "status": "queued",
            "stage": "Queued",
            "progress": STAGE_PROGRESS["queued"],
            "error": None,
            "error_code": None,
        }
        with self._lock:
            self._jobs[job_id] = state
        return dict(state)

    def update(self, job_id: str, status: str, stage: str) -> None:
        """Advance a job to a new processing stage."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.update({
                    "status": status,
                    "stage": stage,
                    "progress": STAGE_PROGRESS.get(status, job["progress"]),
                    "error": None,
                    "error_code": None,
                })

    def mark_failed(self, job_id: str, error: str, error_code: Optional[str] = None) -> None:
        """Mark a job as failed with a human-readable reason."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.update({"status": "failed", "error": error, "error_code": error_code})

    def get(self, job_id: str) -> Optional[Dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job is not None else None

    def is_registered(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._jobs


job_manager = JobManager()


def resolve_job_state(job_id: str) -> Optional[Dict]:
    """
    Resolve the current state for a job ID.

    Prefers live in-memory state; otherwise reconstructs a meaningful state
    from job directory artifacts (survives backend restarts). Security:
    only exact job_id names under JOBS_DIR are considered.
    """
    state = job_manager.get(job_id)
    if state is not None:
        return state

    job_dir = JOBS_DIR / job_id
    if not job_dir.is_dir():
        return None

    if (job_dir / TRANSCRIPT_FILENAME).exists():
        return {
            "status": "completed",
            "stage": "Transcript ready",
            "progress": 100,
            "error": None,
            "error_code": None,
        }
    if get_audio_path(job_dir).exists():
        return {
            "status": "transcribing",
            "stage": "Audio extracted; previous transcription did not finish",
            "progress": STAGE_PROGRESS["transcribing"],
            "error": None,
            "error_code": None,
        }
    if VideoService.get_video_path(job_id) is not None:
        return {
            "status": "uploaded",
            "stage": "Uploaded",
            "progress": 0,
            "error": None,
            "error_code": None,
        }
    return {
        "status": "failed",
        "stage": None,
        "progress": 0,
        "error": "Job directory exists but the uploaded video is missing.",
        "error_code": "VIDEO_MISSING",
    }
