import os
from pathlib import Path

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
JOBS_DIR = DATA_DIR / "jobs"

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
