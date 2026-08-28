const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export const api = {
  /**
   * Health check
   */
  async health() {
    const response = await fetch(`${API_BASE_URL}/api/health`);
    if (!response.ok) throw new Error("Health check failed");
    return response.json();
  },

  /**
   * Upload a video file
   */
  async uploadVideo(file) {
    const formData = new FormData();
    formData.append("file", file);

    const response = await fetch(`${API_BASE_URL}/api/videos/upload`, {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Upload failed");
    }

    return response.json();
  },

  /**
   * Get video file URL for a job
   */
  getVideoUrl(jobId) {
    return `${API_BASE_URL}/api/videos/${jobId}/file`;
  },

  /**
   * Start Phase 2 processing (audio extraction + transcription) for a job
   */
  async startProcessing(jobId) {
    const response = await fetch(
      `${API_BASE_URL}/api/videos/${jobId}/process`,
      { method: "POST" }
    );

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.detail || "Failed to start processing");
      error.status = response.status;
      throw error;
    }
    return data;
  },

  /**
   * Get processing status for a job
   */
  async getJobStatus(jobId) {
    const response = await fetch(`${API_BASE_URL}/api/videos/${jobId}/status`);

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.detail || "Failed to fetch job status");
      error.status = response.status;
      throw error;
    }
    return data;
  },

  /**
   * Get the timestamped transcript for a completed job
   */
  async getTranscript(jobId) {
    const response = await fetch(
      `${API_BASE_URL}/api/videos/${jobId}/transcript`
    );

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.detail || "Failed to fetch transcript");
      error.status = response.status;
      throw error;
    }
    return data;
  },
};
