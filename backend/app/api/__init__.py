from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from ..services import VideoService
from ..models import VideoUploadResponse

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
