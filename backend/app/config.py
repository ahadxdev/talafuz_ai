import os
from pathlib import Path

from dotenv import load_dotenv

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent
# The project root contains backend, frontend, and the root .env file.
ROOT_DIR = BASE_DIR.parent
DATA_DIR = BASE_DIR / "data"
JOBS_DIR = DATA_DIR / "jobs"

# Load environment variables from the project root .env (preferred), while
# keeping a backward-compatible fallback for backend-local .env files.
for env_path in (ROOT_DIR / ".env", BASE_DIR / ".env"):
	load_dotenv(env_path)

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
JOBS_DIR.mkdir(exist_ok=True)

# Upload configuration
MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500 MB in bytes
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}

# CORS configuration
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

# API configuration
API_PREFIX = "/api"

# ---------------------------------------------------------------------------
# Phase 2 — Audio extraction + ASR configuration
# ---------------------------------------------------------------------------

# FFmpeg audio extraction
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
AUDIO_FILENAME = "audio.wav"
AUDIO_SAMPLE_RATE = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
AUDIO_EXTRACTION_TIMEOUT = int(os.getenv("AUDIO_EXTRACTION_TIMEOUT", "600"))  # seconds

# ---------------------------------------------------------------------------
# Phase 5 — Video export (caption burn-in via FFmpeg + ASS subtitles)
# ---------------------------------------------------------------------------
# Output name of the rendered (captioned) video inside the job directory.
EXPORTED_VIDEO_FILENAME = "captioned_video.mp4"
# Hard cap for a single render (seconds) — re-encoding a long video is slow.
VIDEO_EXPORT_TIMEOUT = int(os.getenv("VIDEO_EXPORT_TIMEOUT", "1800"))

# ASR provider selection: "none" (not configured) | "alibaba"
ASR_PROVIDER = os.getenv("ASR_PROVIDER", "none").strip().lower()

# ---------------------------------------------------------------------------
# Alibaba Cloud Model Studio — Qwen3-ASR configuration
#
# Authentication uses a Model Studio API key (DASHSCOPE_API_KEY).
# The legacy ALIBABA_CLOUD_ACCESS_KEY_ID / _SECRET variables are NOT used
# by this integration.
#
# To enable transcription:
#   1. ASR_PROVIDER=alibaba
#   2. DASHSCOPE_API_KEY=<your Model Studio API key>
#   3. ALIBABA_ASR_REGION=singapore | beijing (must match the API key region)
#   4. Optional: ALIBABA_WORKSPACE_ID to use the workspace-specific domain
#      https://{WorkspaceId}.<region-domain>; when omitted the official
#      standard DashScope domain for the region is used.
#
# Models (official Alibaba Cloud Model Studio):
#   - qwen3-asr-flash-filetrans: async file transcription, returns
#     sentence-level timestamps (required for Phase 2 timestamped segments).
#     Needs a file URL: the local audio.wav is uploaded through the official
#     temporary-upload API to obtain an oss:// URL.
#   - qwen3-asr-flash: synchronous recognition of base64/local audio.
#     Returns plain text WITHOUT timestamps — only use as fallback; the
#     provider emits a single segment covering the whole audio.
# ---------------------------------------------------------------------------
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
ALIBABA_ASR_MODEL = os.getenv("ALIBABA_ASR_MODEL", "qwen3-asr-flash-filetrans").strip()
ALIBABA_ASR_REGION = os.getenv("ALIBABA_ASR_REGION", "singapore").strip().lower()
ALIBABA_WORKSPACE_ID = os.getenv("ALIBABA_WORKSPACE_ID", "").strip()
# Optional single language hint (e.g. "hi" or "en"). Leave empty for
# automatic language detection — recommended for Hindi/English code-switched
# speech. Only sent to the API when non-empty (the API accepts one language).
ALIBABA_ASR_LANGUAGE_HINTS = os.getenv("ALIBABA_ASR_LANGUAGE_HINTS", "").strip()
# Word-level timestamps (filetrans enable_words) are only reliable for a
# limited language set (Hindi is not in it); default to sentence-level.
ALIBABA_ASR_ENABLE_WORDS = os.getenv("ALIBABA_ASR_ENABLE_WORDS", "false").strip().lower() == "true"
# Polling limits for the async filetrans task
ASR_TASK_POLL_INTERVAL = float(os.getenv("ASR_TASK_POLL_INTERVAL", "2"))
ASR_TASK_TIMEOUT = int(os.getenv("ASR_TASK_TIMEOUT", "1800"))  # seconds
ASR_HTTP_TIMEOUT = int(os.getenv("ASR_HTTP_TIMEOUT", "120"))  # seconds per HTTP call
# Sync qwen3-asr-flash accepts base64 audio up to 10 MB encoded
ASR_SYNC_MAX_AUDIO_BYTES = 7 * 1024 * 1024  # stay safely under the 10 MB limit

# ---------------------------------------------------------------------------
# Phase 3 — Romanization / English translation (Qwen text model)
#
# Uses the SAME Model Studio API key (DASHSCOPE_API_KEY) and region as the
# ASR integration, but a Qwen TEXT model through the official
# OpenAI-compatible chat/completions endpoint (compatible-mode/v1).
# Qwen3-ASR is NOT used here — ASR already happened in Phase 2.
# ---------------------------------------------------------------------------
# Official Model Studio text models: qwen-flash, qwen-turbo, qwen-plus, qwen-max
ROMANIZATION_MODEL = os.getenv("ROMANIZATION_MODEL", "qwen-plus").strip()
# Transcript segments are sent to the model in batches of this size
ROMANIZATION_BATCH_SIZE = int(os.getenv("ROMANIZATION_BATCH_SIZE", "8"))
ROMANIZATION_HTTP_TIMEOUT = int(os.getenv("ROMANIZATION_HTTP_TIMEOUT", "180"))  # seconds per call
ROMANIZATION_MAX_TOKENS = int(os.getenv("ROMANIZATION_MAX_TOKENS", "8192"))
# Subtitle segmentation targets (Phase 3)
SUBTITLE_MAX_CHARS_PER_LINE = int(os.getenv("SUBTITLE_MAX_CHARS_PER_LINE", "42"))
SUBTITLE_MAX_LINES = int(os.getenv("SUBTITLE_MAX_LINES", "2"))
# Creator-style cue length: romanized segments are split into cues of at
# most this many words (~3–5 word captions). SUBTITLE_MAX_CHARS_PER_LINE *
# SUBTITLE_MAX_LINES stays the hard character ceiling.
SUBTITLE_MAX_WORDS_PER_CUE = int(os.getenv("SUBTITLE_MAX_WORDS_PER_CUE", "5"))
SUBTITLE_MIN_DURATION = float(os.getenv("SUBTITLE_MIN_DURATION", "0.8"))  # seconds

# Legacy Alibaba Cloud access-key variables — kept for reference only;
# NOT used by the Model Studio Qwen3-ASR integration.
ALIBABA_CLOUD_ACCESS_KEY_ID = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
ALIBABA_CLOUD_ACCESS_KEY_SECRET = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                