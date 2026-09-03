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

  /**
   * Phase 3 — generate Romanized subtitles from the ASR transcript.
   * The English translation is always generated alongside romanization.
   */
  async romanize(jobId, includeEnglish = true) {
    const response = await fetch(
      `${API_BASE_URL}/api/videos/${jobId}/romanize`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ include_english: includeEnglish }),
      }
    );

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.detail || "Failed to generate subtitles");
      error.status = response.status;
      throw error;
    }
    return data;
  },

  /**
   * Phase 3 — fetch previously generated romanized subtitles for a job
   */
  async getSubtitles(jobId) {
    const response = await fetch(
      `${API_BASE_URL}/api/videos/${jobId}/subtitles`
    );

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.detail || "Failed to fetch subtitles");
      error.status = response.status;
      throw error;
    }
    return data;
  },

  /**
   * Phase 4 — save edited subtitles for a job.
   * `options` may carry the selected display language and the caption
   * style configuration so the editor state round-trips through the backend.
   */
  async saveSubtitles(jobId, subtitles, options = {}) {
    const payload = { subtitles };
    if (options.language) payload.language = options.language;
    if (options.style) payload.style = options.style;

    const response = await fetch(
      `${API_BASE_URL}/api/videos/${jobId}/subtitles/save`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }
    );

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.detail || "Failed to save subtitles");
      error.status = response.status;
      throw error;
    }
    return data;
  },

  /**
   * Phase 4 — export subtitles as SRT file
   */
  async exportSRT(jobId, mode = "romanized") {
    const response = await fetch(
      `${API_BASE_URL}/api/videos/${jobId}/export/srt?mode=${encodeURIComponent(mode)}`
    );

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const error = new Error(data.detail || "Failed to export SRT");
      error.status = response.status;
      throw error;
    }

    // Get the file blob and trigger download
    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `subtitles_${mode}_${jobId}.srt`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
  },

  /**
   * Phase 5 — start rendering the edited captions into the video
   * (FFmpeg burn-in). Returns immediately; poll getVideoExportStatus()
   * until the state is "ready".
   */
  async startVideoExport(jobId) {
    const response = await fetch(
      `${API_BASE_URL}/api/videos/${jobId}/export/video`,
      { method: "POST" }
    );

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.detail || "Failed to start video export");
      error.status = response.status;
      throw error;
    }
    return data;
  },

  /**
   * Phase 5 — poll the video export state (idle | exporting | ready | failed)
   */
  async getVideoExportStatus(jobId) {
    const response = await fetch(
      `${API_BASE_URL}/api/videos/${jobId}/export/video/status`
    );

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.detail || "Failed to fetch export status");
      error.status = response.status;
      throw error;
    }
    return data;
  },

  /**
   * Drafts — list all saved jobs with metadata
   */
  async getDrafts() {
    const response = await fetch(`${API_BASE_URL}/api/videos/drafts`);

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.detail || "Failed to fetch drafts");
      error.status = response.status;
      throw error;
    }
    return data;
  },

  /**
   * Drafts — delete a job and all its data
   */
  async deleteDraft(jobId) {
    const response = await fetch(
      `${API_BASE_URL}/api/videos/${jobId}`,
      { method: "DELETE" }
    );

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.detail || "Failed to delete job");
      error.status = response.status;
      throw error;
    }
    return data;
  },

  /**
   * Phase 5 — download the captioned (burn-in) video once the export is ready
   */
  async downloadExportedVideo(jobId) {
    const response = await fetch(
      `${API_BASE_URL}/api/videos/${jobId}/export/video/file`
    );

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const error = new Error(data.detail || "Failed to download video");
      error.status = response.status;
      throw error;
    }

    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `talafuz_captions_${jobId.slice(0, 8)}.mp4`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
  },
};

