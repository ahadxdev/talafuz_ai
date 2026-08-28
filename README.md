# Talafuz AI

**AI-powered subtitles for South Asian short-form videos containing Urdu, Roman Urdu, Hindi, and English code-switched speech.**

## Phase 1: Application Foundation + Video Upload

In this phase, you can:

1. ✅ Open the Talafuz web application
2. ✅ Drag and drop a short video or select one using a file picker
3. ✅ See the selected video's filename and file size
4. ✅ Upload the video to the FastAPI backend
5. ✅ See upload progress/status
6. ✅ Receive a unique job ID
7. ✅ Have the backend save the video under the job's directory
8. ✅ See the uploaded video in a browser video preview
9. ✅ Receive clear errors for unsupported files or failed uploads

**The application does NOT process the video yet.** Future phases will add transcription, translation, subtitle generation, and video rendering.

---

## Tech Stack

**Frontend:**
- React 18
- Vite 5
- Tailwind CSS 3

**Backend:**
- Python 3.9+
- FastAPI 0.104+
- Uvicorn (ASGI server)

**Storage:**
- Local filesystem only

---

## Project Structure

```
talafuz-ai/
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── VideoUploader.jsx     # Drag-drop + file picker
│   │   │   ├── VideoPreview.jsx      # Video player
│   │   │   └── ProcessingStatus.jsx  # Status messages
│   │   ├── pages/
│   │   │   └── Home.jsx              # Main page
│   │   ├── services/
│   │   │   └── api.js                # API client
│   │   ├── App.jsx
│   │   ├── App.css
│   │   └── main.jsx
│   ├── index.html
│   ├── package.json
│   ├── vite.config.js
│   ├── tailwind.config.js
│   └── postcss.config.js
│
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                   # FastAPI app + routes
│   │   ├── config.py                 # Configuration
│   │   ├── api/
│   │   │   └── __init__.py           # Video endpoints
│   │   ├── services/
│   │   │   └── __init__.py           # Video service logic
│   │   └── models/
│   │       └── __init__.py           # Pydantic models
│   ├── data/
│   │   └── jobs/                     # Uploaded videos stored here
│   └── requirements.txt
│
├── .env.example
├── .gitignore
└── README.md (this file)
```

---

## Prerequisites

- **Node.js** 16+ (for frontend)
- **Python** 3.9+ (for backend)
- **npm** or **yarn** (for frontend package management)

---

## Backend Installation

### 1. Install dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 2. Set environment variables (optional)

Create a `.env` file in the project root:

```env
FRONTEND_URL=http://localhost:5173
```

Or use defaults:
- `FRONTEND_URL` defaults to `http://localhost:5173`

### 3. Start the FastAPI server

```bash
cd backend
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The backend will be available at: **http://localhost:8000**

---

## Frontend Installation

### 1. Install dependencies

```bash
cd frontend
npm install
```

### 2. Set environment variables (optional)

Create a `.env.local` file in the `frontend/` directory:

```env
VITE_API_BASE_URL=http://localhost:8000
```

Or use the default: `http://localhost:8000`

### 3. Start the development server

```bash
cd frontend
npm run dev
```

The frontend will be available at: **http://localhost:5173**

---

## Usage

1. Open your browser and navigate to **http://localhost:5173**
2. **Drag and drop** a video file, or click **Choose Video**
3. Supported formats: **MP4, MOV, WEBM**
4. Click **Upload Video**
5. Wait for the upload to complete
6. View the uploaded video in the preview player
7. See the job ID and upload status

---

## API Endpoints

All endpoints are prefixed with `/api`.

### Health Check

```
GET /api/health
```

**Response:**

```json
{
  "status": "ok"
}
```

---

### Upload Video

```
POST /api/videos/upload
```

**Request:**
- Method: `POST`
- Content-Type: `multipart/form-data`
- Body: `file` (video file)

**Response (Success - 200):**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "filename": "my_video.mp4",
  "size": 12345678,
  "status": "uploaded"
}
```

**Response (Errors):**

| Status | Error |
|--------|-------|
| 400 | Unsupported file type, file too large, or empty file |
| 500 | Server-side upload failure |

---

### Get Video File

```
GET /api/videos/{job_id}/file
```

**Path Parameters:**
- `job_id` (required): The job ID from the upload response

**Response:**
- Returns the video file for browser playback

**Error (404):**
```json
{
  "detail": "Video not found for this job ID."
}
```

---

## Supported Video Formats

- **MP4** (.mp4)
- **MOV** (.mov)
- **WebM** (.webm)

---

## Configuration

### Backend Configuration

Edit `backend/app/config.py` to customize:

- `MAX_UPLOAD_SIZE`: Maximum file size in bytes (default: 500 MB)
- `ALLOWED_VIDEO_EXTENSIONS`: Supported file extensions
- `FRONTEND_URL`: CORS origin for frontend requests

### Frontend Configuration

Environment variables in `frontend/.env.local`:

- `VITE_API_BASE_URL`: Backend API base URL (default: `http://localhost:8000`)

---

## Storage

Uploaded videos are stored in:

```
backend/data/jobs/{job_id}/
```

Each job gets its own directory. The original filename is preserved.

---

## Error Handling

### Frontend Errors

The frontend displays user-friendly messages:

- "Unsupported file type. Supported formats: .mp4, .mov, .webm"
- "File is too large. Maximum size is 500MB."
- "File is empty."
- "Upload failed. Please try again."

### Backend Errors

The backend returns appropriate HTTP status codes:

- **400 Bad Request**: Validation errors (unsupported type, too large, empty)
- **404 Not Found**: Video file not found for job ID
- **500 Internal Server Error**: Server-side failures

---

## Current Limitations

- No database (all state is ephemeral)
- No authentication or user accounts
- No video processing (transcription, translation, subtitle generation)
- No cloud storage (local filesystem only)
- Videos are not validated for corruption
- No automatic cleanup of old uploads
- No resumable uploads for large files

---

## Future Phases

**Phase 2:** Audio Extraction & Speech Recognition
- Extract audio from video
- Implement timestamped speech-to-text
- Support Urdu, Roman Urdu, Hindi, and English

**Phase 3:** Translation & Processing
- Translate recognized speech to English
- Handle code-switched content intelligently
- Generate natural English subtitles

**Phase 4:** Subtitle Editor & Styling
- Interactive subtitle timeline editor
- Subtitle customization (fonts, colors, positioning)
- SRT file export

**Phase 5:** Video Rendering
- Burn subtitles into video file
- MP4 export with styled subtitles
- Quality and format options

**Phase 6+:** Advanced Features
- User accounts and projects
- Subtitle templates
- Community subtitle sharing
- Multi-language subtitle generation

---

## Testing

### Backend Testing

1. Start the FastAPI server:
   ```bash
   cd backend
   python -m uvicorn app.main:app --reload
   ```

2. Test health endpoint:
   ```bash
   curl http://localhost:8000/api/health
   ```

3. Test video upload:
   ```bash
   curl -X POST -F "file=@sample_video.mp4" http://localhost:8000/api/videos/upload
   ```

4. Verify job directory:
   ```bash
   ls backend/data/jobs/{job_id}/
   ```

5. Test video serving:
   ```bash
   curl http://localhost:8000/api/videos/{job_id}/file -o downloaded_video.mp4
   ```

### Frontend Testing

1. Start the development server:
   ```bash
   cd frontend
   npm run dev
   ```

2. Open browser: **http://localhost:5173**

3. Test scenarios:
   - ✅ Drag and drop a video
   - ✅ Select a video using file picker
   - ✅ See filename and file size
   - ✅ Upload succeeds with valid video
   - ✅ Upload fails with unsupported format (test with .txt)
   - ✅ Upload fails with oversized file
   - ✅ Video preview plays after upload
   - ✅ Job ID is displayed
   - ✅ "Upload Another Video" button works

---

## Development

### Adding New Features

Keep concerns separated:

- **Frontend business logic** → `frontend/src/services/api.js`
- **Backend business logic** → `backend/app/services/`
- **API routes** → `backend/app/api/`
- **React components** → `frontend/src/components/` and `frontend/src/pages/`

### Extending Video Processing

When adding transcription/translation in Phase 2:

1. Create `backend/app/services/transcription_service.py`
2. Add new endpoints in `backend/app/api/`
3. Add React components for transcript display
4. Keep the upload flow unchanged

---

## Troubleshooting

### CORS Errors

**Error:** "Access to XMLHttpRequest has been blocked by CORS policy"

**Solution:** Ensure `FRONTEND_URL` in `backend/app/config.py` matches your frontend origin (default: `http://localhost:5173`)

### Upload Fails

**Error:** "Upload failed. Please try again."

**Causes:**
- Backend not running
- File path is incorrect
- Disk space issue
- File is corrupted

**Solution:** Check backend logs for detailed error messages

### Video Won't Play

**Error:** Video player shows loading but no playback

**Causes:**
- Browser doesn't support WebM or MOV in HTML5 video player
- Job ID is incorrect

**Solution:** Ensure you're uploading MP4 files for best compatibility

---

## License

TBD

---

## Support

For issues or questions, please create an issue in the repository.

---

**Built with ❤️ for content creators in South Asia.**
