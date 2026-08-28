from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .config import FRONTEND_URL, API_PREFIX
from .api import router as videos_router

app = FastAPI(
    title="Talafuz AI",
    description="AI-powered subtitles for South Asian short-form videos",
    version="0.1.0"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health check endpoint
@app.get(f"{API_PREFIX}/health")
def health():
    """Health check endpoint."""
    return {"status": "ok"}

# Include routers
app.include_router(videos_router, prefix=f"{API_PREFIX}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
