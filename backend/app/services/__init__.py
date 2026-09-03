import os
import uuid
from pathlib import Path
from typing import Tuple, Optional
from fastapi import UploadFile
from ..config import JOBS_DIR, MAX_UPLOAD_SIZE, ALLOWED_VIDEO_EXTENSIONS, EXPORTED_VIDEO_FILENAME


class VideoService:
    """Service for handling video file operations."""

    @staticmethod
    def generate_job_id() -> str:
        """Generate a unique job ID."""
        return str(uuid.uuid4())

    @staticmethod
    def validate_video_file(filename: str, file_size: int) -> Tuple[bool, Optional[str]]:
        """
        Validate video file.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check file extension
        _, ext = os.path.splitext(filename)
        if ext.lower() not in ALLOWED_VIDEO_EXTENSIONS:
            return False, f"Unsupported file type. Supported formats: {', '.join(ALLOWED_VIDEO_EXTENSIONS)}"
        
        # Check file size
        if file_size == 0:
            return False, "File is empty."
        
        if file_size > MAX_UPLOAD_SIZE:
            max_mb = MAX_UPLOAD_SIZE // (1024 * 1024)
            return False, f"File is too large. Maximum size is {max_mb}MB."
        
        return True, None

    @staticmethod
    async def save_video(job_id: str, file: UploadFile) -> Tuple[bool, str, Optional[str]]:
        """
        Save uploaded video to job directory.
        
        Returns:
            Tuple of (success, file_path or error_message, error_detail)
        """
        try:
            # Create job directory
            job_dir = JOBS_DIR / job_id
            job_dir.mkdir(exist_ok=True)
            
            # Sanitize filename (remove path separators)
            filename = os.path.basename(file.filename or "video.mp4")
            file_path = job_dir / filename
            
            # Save file
            contents = await file.read()
            with open(file_path, "wb") as f:
                f.write(contents)
            
            return True, str(file_path), None
        except Exception as e:
            return False, "", f"Failed to save file: {str(e)}"

    @staticmethod
    def get_video_path(job_id: str) -> Optional[Path]:
        """
        Get the path to the uploaded video file for a job.
    
        Returns:
            Path to video file or None if not found
        """
        job_dir = JOBS_DIR / job_id
    
        if not job_dir.exists():
            return None
    
        # Find the first video file in the job directory, skipping the
        # caption burn-in export — it is a render output, not the upload,
        # and would otherwise shadow the original video (and get deleted
        # by the next export run).
        for ext in ALLOWED_VIDEO_EXTENSIONS:
            for file in job_dir.glob(f"*{ext}"):
                if file.name == EXPORTED_VIDEO_FILENAME:
                    continue
                return file
    
        return None
