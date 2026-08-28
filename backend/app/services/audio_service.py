"""
Phase 2 — Audio extraction service (FFmpeg).

Extracts ASR-suitable audio (mono, 16-bit PCM WAV) from the uploaded video
inside a job directory. All FFmpeg invocation is isolated here; API routes
never call FFmpeg directly.
"""
import subprocess
from pathlib import Path

from ..config import FFMPEG_BIN, AUDIO_FILENAME, AUDIO_SAMPLE_RATE, AUDIO_EXTRACTION_TIMEOUT


class FFmpegNotFoundError(RuntimeError):
    """The ffmpeg executable is not installed / not on PATH."""


class AudioExtractionError(RuntimeError):
    """FFmpeg failed to extract audio from the video."""


def is_ffmpeg_installed() -> bool:
    """Check whether the ffmpeg executable is available."""
    try:
        result = subprocess.run(
            [FFMPEG_BIN, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ffmpeg_version() -> str:
    """Return the first line of `ffmpeg -version` for diagnostics."""
    try:
        result = subprocess.run(
            [FFMPEG_BIN, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return (result.stdout or "").splitlines()[0] if result.stdout else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def _format_output(result: subprocess.CompletedProcess, limit: int = 800) -> str:
    """Extract the tail of FFmpeg stderr for error reporting."""
    text = (result.stderr or result.stdout or "").strip()
    return text[-limit:] if text else "no output from ffmpeg"


def extract_audio(video_path: Path, output_path: Path) -> Path:
    """
    Extract audio from `video_path` into `output_path` (mono 16-bit PCM WAV,
    sample rate from configuration — suitable for speech recognition).

    Raises:
        FFmpegNotFoundError: ffmpeg executable missing.
        FileNotFoundError: the video file does not exist.
        AudioExtractionError: ffmpeg failed or produced no usable output.
    """
    if not is_ffmpeg_installed():
        raise FFmpegNotFoundError(
            f"FFmpeg ('{FFMPEG_BIN}') is not installed on this system. Install FFmpeg "
            "(e.g. 'sudo apt install ffmpeg') and restart the backend."
        )

    video_path = Path(video_path)
    output_path = Path(output_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path.name}")

    cmd = [
        FFMPEG_BIN,
        "-y",                        # overwrite any previous extraction
        "-i", str(video_path),
        "-vn",                       # drop the video stream
        "-acodec", "pcm_s16le",      # 16-bit PCM, widely supported by ASR
        "-ar", str(AUDIO_SAMPLE_RATE),
        "-ac", "1",                  # mono
        str(output_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=AUDIO_EXTRACTION_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise AudioExtractionError(
            f"Audio extraction timed out after {AUDIO_EXTRACTION_TIMEOUT} seconds."
        )
    except OSError as e:
        raise AudioExtractionError(f"Failed to run FFmpeg: {e}")

    if result.returncode != 0:
        raise AudioExtractionError(
            "Audio extraction failed (the video may be corrupt or have no "
            f"audio track). FFmpeg output: {_format_output(result)}"
        )

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise AudioExtractionError(
            "Audio extraction produced no output (the video may have no audio track)."
        )

    return output_path


def get_audio_path(job_dir: Path) -> Path:
    """Standard audio output location inside a job directory."""
    return Path(job_dir) / AUDIO_FILENAME
