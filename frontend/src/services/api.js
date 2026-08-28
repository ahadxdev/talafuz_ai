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
};
