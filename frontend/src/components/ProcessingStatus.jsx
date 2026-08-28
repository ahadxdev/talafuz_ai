const PIPELINE_STEPS = [
  { key: "video", label: "Video uploaded" },
  { key: "extracting_audio", label: "Extracting audio" },
  { key: "transcribing", label: "Transcribing" },
  { key: "completed", label: "Transcript ready" },
];

// Phase 3 extends the pipeline with romanization and subtitle generation.
const FULL_PIPELINE_STEPS = [
  { key: "video", label: "Video uploaded" },
  { key: "extracting_audio", label: "Extracting audio" },
  { key: "transcribing", label: "Transcribing" },
  { key: "romanizing", label: "Romanizing" },
  { key: "subtitles_ready", label: "Subtitles ready" },
];

function stepState(stepKey, processingStatus) {
  if (processingStatus === "extracting_audio") {
    return stepKey === "video" ? "done" : stepKey === "extracting_audio" ? "active" : "pending";
  }
  if (processingStatus === "transcribing") {
    return stepKey === "video" || stepKey === "extracting_audio"
      ? "done"
      : stepKey === "transcribing"
      ? "active"
      : "pending";
  }
  if (processingStatus === "romanizing") {
    return stepKey === "romanizing"
      ? "active"
      : stepKey === "subtitles_ready"
      ? "pending"
      : "done";
  }
  if (processingStatus === "subtitles_ready") return "done";
  if (processingStatus === "completed") {
    // In the full (Phase 3) stepper "Transcript ready" maps to the
    // romanizing boundary: ASR steps done, romanization not started yet.
    return stepKey === "romanizing" || stepKey === "subtitles_ready"
      ? "pending"
      : "done";
  }
  return "pending"; // queued / starting
}

export function ProcessingStatus({
  jobId,
  isSuccess,
  isError,
  errorMessage,
  processingStatus,
  processingStage,
  processingErrorCode,
  phase3Status,
}) {
  // phase3Status: null (not started) | "romanizing" | "subtitles_ready"
  const steps =
    phase3Status === "subtitles_ready" || phase3Status === "romanizing"
      ? FULL_PIPELINE_STEPS
      : PIPELINE_STEPS;
  // Processing pipeline states take priority over the upload banner.
  if (processingStatus === "failed") {
    const asrMissing = processingErrorCode === "ASR_NOT_CONFIGURED";
    return (
      <div
        className={`w-full max-w-2xl border rounded-lg p-4 ${
          asrMissing
            ? "bg-amber-900/40 border-amber-700"
            : "bg-red-900 border-red-700"
        }`}
      >
        <p
          className={`font-medium ${
            asrMissing ? "text-amber-200" : "text-red-200"
          }`}
        >
          {asrMissing ? "Speech recognition is not configured yet" : "Processing failed"}
        </p>
        <p
          className={`text-sm mt-1 ${
            asrMissing ? "text-amber-300" : "text-red-300"
          }`}
        >
          {errorMessage}
        </p>
      </div>
    );
  }

  const isPipelineActive =
    processingStatus === "starting" ||
    processingStatus === "running" ||
    processingStatus === "completed";

  if (isPipelineActive) {
    const backendStatus =
      processingStatus === "completed"
        ? phase3Status === "subtitles_ready"
          ? "subtitles_ready"
          : phase3Status === "romanizing"
          ? "romanizing"
          : "completed"
        : processingStatus === "running"
        ? processingStage || "queued"
        : "queued";

    const heading =
      backendStatus === "subtitles_ready"
        ? "Subtitles ready"
        : backendStatus === "romanizing"
        ? "Generating subtitles…"
        : backendStatus === "completed"
        ? "Transcript ready"
        : "Processing video…";

    return (
      <div className="w-full max-w-2xl bg-gray-800 border border-gray-700 rounded-lg p-4">
        <p className="text-white font-medium mb-3">{heading}</p>
        <ol className="space-y-2">
          {steps.map((step) => {
            const state = stepState(step.key, backendStatus);
            return (
              <li key={step.key} className="flex items-center gap-3">
                {state === "done" && (
                  <span className="w-5 h-5 rounded-full bg-green-600 text-white text-xs flex items-center justify-center">
                    ✓
                  </span>
                )}
                {state === "active" && (
                  <span className="w-5 h-5 rounded-full border-2 border-blue-400 border-t-transparent animate-spin" />
                )}
                {state === "pending" && (
                  <span className="w-5 h-5 rounded-full border border-gray-600" />
                )}
                <span
                  className={
                    state === "done"
                      ? "text-green-300 text-sm"
                      : state === "active"
                      ? "text-blue-300 text-sm font-medium"
                      : "text-gray-500 text-sm"
                  }
                >
                  {step.label}
                </span>
              </li>
            );
          })}
        </ol>
      </div>
    );
  }

  // Phase 1 behavior: upload error / success banners.
  if (!jobId && !isError) return null;

  if (isError) {
    return (
      <div className="w-full max-w-2xl bg-red-900 border border-red-700 rounded-lg p-4">
        <p className="text-red-200 font-medium">Upload failed</p>
        <p className="text-red-300 text-sm mt-1">{errorMessage}</p>
      </div>
    );
  }

  if (isSuccess) {
    return (
      <div className="w-full max-w-2xl bg-green-900 border border-green-700 rounded-lg p-4">
        <p className="text-green-200 font-medium">✓ Video uploaded successfully</p>
        <p className="text-green-300 text-sm mt-1">
          Your video is ready for the next step.
        </p>
      </div>
    );
  }

  return null;
}
