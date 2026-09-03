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
# Hard cap for a single subtitle cue (seconds). Used by the audio alignment
# layer so one cue never spans an excessively long stretch of speech.
SUBTITLE_MAX_DURATION = float(os.getenv("SUBTITLE_MAX_DURATION", "6.0"))

# ---------------------------------------------------------------------------
# Subtitle timing alignment — local audio VAD (FFmpeg silencedetect)
#
# Qwen3-ASR filetrans returns SENTENCE-level timestamps only. Long sentences
# are split into several cues; instead of distributing the whole sentence
# duration proportionally (which spans real pauses and overlaps boundary
# cues), the alignment service detects speech/silence inside each ASR
# segment from the already-extracted audio.wav and places cues inside
# detected speech regions. Everything runs locally through the existing
# FFmpeg dependency — no extra cloud API key, no heavy ML dependency.
# Any failure falls back to the proportional distributor.
# ---------------------------------------------------------------------------
SUBTITLE_ALIGNMENT_ENABLED = os.getenv("SUBTITLE_ALIGNMENT_ENABLED", "true").strip().lower() == "true"
# FFmpeg silencedetect noise floor in dB: audio below this level is silence.
VAD_THRESHOLD = float(os.getenv("VAD_THRESHOLD", "-35"))
# Speech regions shorter than this are discarded as false positives (seconds).
VAD_MIN_SPEECH_DURATION = float(os.getenv("VAD_MIN_SPEECH_DURATION", "0.15"))
# Silence must last at least this long to count as a meaningful pause
# (seconds). Shorter gaps are merged into the surrounding speech region.
VAD_MIN_SILENCE_DURATION = float(os.getenv("VAD_MIN_SILENCE_DURATION", "0.4"))
# Padding added around each detected speech region (seconds).
VAD_PADDING = float(os.getenv("VAD_PADDING", "0.05"))
# Timeout for one FFmpeg silence-analysis pass (seconds).
VAD_TIMEOUT = int(os.getenv("VAD_TIMEOUT", "300"))

# ---------------------------------------------------------------------------
# Word-level timing alignment — local whisper.cpp (pywhispercpp) DTW
#
# Qwen3-ASR gives SENTENCE-level timestamps and the VAD layer above refines
# them to CUE-level timing, but neither provides WORD-level timing. Without
# it the editor highlight (subtitleUtils.getActiveWordIndex) and the burn-in
# (video_export_service._word_timings) ESTIMATE each word's slot from its
# character length, so the highlight lags behind the spoken word.
#
# This layer produces REAL audio-derived word timings, fully offline:
# whisper.cpp transcribes the already-extracted audio.wav ONCE per job with
# DTW token timestamps (biased toward the known romanized transcript via
# initial_prompt so it "reads along" the real words); the recognised tokens
# are matched to the cue words with an order-preserving character alignment
# and the matched acoustic times are fitted into each cue window. No cloud
# call and no torch — a ~147 MB ggml model on CPU.
#
# Honesty / gating: word timings are persisted ONLY for cues that pass a
# strict quality gate. Every other cue keeps words=None and falls back to the
# existing proportional estimate — estimated timings are never presented or
# stored as real audio-derived word timings. This is recognition + matching
# (like WhisperX), not phoneme-level forced alignment; it is the lightest
# offline mechanism that yields genuine acoustic word timings here.
# ---------------------------------------------------------------------------
WORD_ALIGNMENT_ENABLED = os.getenv("WORD_ALIGNMENT_ENABLED", "true").strip().lower() == "true"
# ggml whisper model file, downloaded separately into backend/models/ (see
# backend/models/README). Word alignment is skipped when it is absent.
WORD_ALIGNMENT_MODEL_PATH = os.getenv(
    "WORD_ALIGNMENT_MODEL_PATH", str(BASE_DIR / "models" / "ggml-base.bin")
)
# Recognition language hint for whisper ("hi" suits Hindi/Hinglish speech).
WORD_ALIGNMENT_LANGUAGE = os.getenv("WORD_ALIGNMENT_LANGUAGE", "hi").strip() or "hi"
# Inference threads for the single per-job whisper pass.
WORD_ALIGNMENT_THREADS = int(os.getenv("WORD_ALIGNMENT_THREADS", "4"))
# Per-cue quality gate: minimum fraction of a cue's words that must match a
# recognised token before the cue's DTW anchors are trusted (used only by the
# opt-in "dtw" interior method below).
WORD_ALIGNMENT_MIN_MATCH = float(os.getenv("WORD_ALIGNMENT_MIN_MATCH", "0.6"))
# Interior word-boundary method WITHIN each cue window. Cue start/end always
# come from the (accurate) audio VAD and are never changed by this setting:
#   "proportional" (DEFAULT, hackathon build) — split the cue across its words
#       in proportion to each word's character length. Robust and deterministic;
#       it does NOT trust the whisper DTW anchors, which ggml-base distorts when
#       it mis-hears the Roman-Urdu prompt (interior words squeezed to 20-84ms).
#   "dtw" (opt-in) — affine-fit the whisper DTW anchors into the cue, but fall
#       back to the proportional split for any cue whose match coverage is below
#       WORD_ALIGNMENT_MIN_MATCH or whose DTW fit squeezes a word under
#       WORD_ALIGNMENT_MIN_TRUSTED_WORD seconds.
WORD_ALIGNMENT_METHOD = (
    os.getenv("WORD_ALIGNMENT_METHOD", "proportional").strip().lower()
    or "proportional"
)
# A DTW-fitted word shorter than this (seconds) means the anchors are not
# trustworthy for that cue — fall back to the proportional interior split.
WORD_ALIGNMENT_MIN_TRUSTED_WORD = float(
    os.getenv("WORD_ALIGNMENT_MIN_TRUSTED_WORD", "0.04")
)
# Cached per-job recognition result (whisper DTW tokens) inside the job dir.
WORD_ALIGNMENT_CACHE_FILENAME = "word_alignment.json"

# Legacy Alibaba Cloud access-key variables — kept for reference only;
# NOT used by the Model Studio Qwen3-ASR integration.
ALIBABA_CLOUD_ACCESS_KEY_ID = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
ALIBABA_CLOUD_ACCESS_KEY_SECRET = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                