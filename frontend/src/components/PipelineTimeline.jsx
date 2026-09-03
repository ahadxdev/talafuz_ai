import { IconCheck, IconFilm, IconRefresh, IconX } from "./editor/icons";

/**
 * Phase 5 — Transcription pipeline timeline for the home page.
 *
 * Shows every stage of the automatic flow — upload → extract audio →
 * transcribe → generate subtitles → open editor — as a vertical stepper
 * with the active stage spinning, completed stages checked, a progress bar
 * and the elapsed time. Failures freeze the timeline at the failed stage
 * and offer retry / start-over actions.
 */

const STAGES = [
  { key: "upload", label: "Upload video", hint: "Sending your file to the server" },
  { key: "extract", label: "Extract audio", hint: "Isolating the speech track with FFmpeg" },
  { key: "transcribe", label: "Transcribe speech", hint: "Qwen3-ASR recognizes every word" },
  { key: "subtitles", label: "Generate subtitles", hint: "Romanizing speech and translating to English" },
  { key: "editor", label: "Open editor", hint: "Style, edit and export captions" },
];

// Order index of the stage the flow is currently sitting on.
function frontierFor(phase, stage, errorStep) {
  if (phase === "uploading" || (phase === "error" && errorStep === "upload")) return 0;
  if (phase === "processing" || (phase === "error" && errorStep === "process")) {
    return stage === "transcribing" ? 2 : 1;
  }
  if (phase === "romanizing" || (phase === "error" && errorStep === "romanize")) return 3;
  if (phase === "redirecting") return 4;
  return -1;
}

const PROGRESS_BY_FRONTIER = [8, 35, 65, 85, 100];

function formatElapsed(totalSeconds) {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function PipelineTimeline({
  fileName,
  fileSizeMB,
  jobId,
  phase, // uploading | processing | romanizing | redirecting | error
  stage, // queued | extracting_audio | transcribing (last backend stage seen)
  elapsedSeconds,
  errorMessage,
  errorCode,
  errorStep,
  canRetry,
  onRetry,
  onStartOver,
}) {
  const frontier = frontierFor(phase, stage, errorStep);
  const isError = phase === "error";
  const percent = PROGRESS_BY_FRONTIER[frontier] ?? 0;
  const asrMissing = errorCode === "ASR_NOT_CONFIGURED";

  const stepState = (idx) => {
    if (idx < frontier) return "done";
    if (isError && idx === frontier) return "error";
    if (!isError && idx === frontier) return "active";
    return "pending";
  };

  return (
    <div className="w-full max-w-2xl bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
      {/* File header */}
      <div className="flex items-center gap-3 p-4 border-b border-gray-800">
        <span className="w-10 h-10 rounded-lg bg-gray-950 border border-gray-800 flex items-center justify-center text-emerald-400 shrink-0">
          <IconFilm size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-gray-100 truncate">{fileName}</p>
          <p className="text-[11px] text-gray-500">
            {fileSizeMB} MB
            {jobId && <> · job {jobId.slice(0, 8)}</>}
          </p>
        </div>
        {elapsedSeconds != null && (
          <span className="text-xs font-mono text-gray-500 shrink-0">
            {formatElapsed(elapsedSeconds)}
          </span>
        )}
      </div>

      {/* Steps */}
      <div className="p-4 space-y-4">
        <ol>
          {STAGES.map((s, i) => {
            const state = stepState(i);
            return (
              <li key={s.key} className="relative flex gap-3 pb-5 last:pb-0">
                {i < STAGES.length - 1 && (
                  <span
                    className={`absolute left-[13px] top-8 bottom-0 w-px ${
                      state === "done" ? "bg-emerald-500/50" : "bg-gray-800"
                    }`}
                  />
                )}
                <span
                  className={`relative z-10 w-7 h-7 rounded-full flex items-center justify-center shrink-0 border ${
                    state === "done"
                      ? "bg-emerald-500 border-emerald-500 text-gray-950"
                      : state === "active"
                      ? "bg-gray-950 border-emerald-500 text-emerald-400"
                      : state === "error"
                      ? "bg-red-950 border-red-500 text-red-400"
                      : "bg-gray-950 border-gray-700 text-gray-600"
                  }`}
                >
                  {state === "done" ? (
                    <IconCheck size={14} />
                  ) : state === "active" ? (
                    <span className="w-3 h-3 border-2 border-emerald-400 border-t-transparent rounded-full animate-spin" />
                  ) : state === "error" ? (
                    <IconX size={13} />
                  ) : (
                    <span className="w-1.5 h-1.5 rounded-full bg-gray-700" />
                  )}
                </span>
                <div className="min-w-0 pt-0.5">
                  <p
                    className={`text-sm font-medium ${
                      state === "pending"
                        ? "text-gray-500"
                        : state === "error"
                        ? "text-red-300"
                        : "text-gray-100"
                    }`}
                  >
                    {s.label}
                  </p>
                  {state === "active" && (
                    <p className="text-[11px] text-emerald-400/80 mt-0.5">
                      {s.hint}…
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ol>

        {/* Progress bar */}
        {phase === "uploading" ? (
          <div className="h-1.5 rounded-full bg-gray-800 overflow-hidden">
            <div className="h-full w-full bg-emerald-500/60 rounded-full animate-pulse" />
          </div>
        ) : (
          <div className="h-1.5 rounded-full bg-gray-800 overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                isError ? "bg-red-500" : "bg-emerald-500"
              }`}
              style={{ width: `${percent}%` }}
            />
          </div>
        )}
      </div>

      {/* Error + actions */}
      {isError && errorMessage && (
        <div className="mx-4 mb-4 rounded-lg border border-red-700 bg-red-950/60 px-3 py-2.5">
          <p className="text-xs font-semibold text-red-300">
            {asrMissing
              ? "Speech recognition is not configured"
              : "Something went wrong"}
          </p>
          <p className="text-[11px] text-red-300/80 mt-0.5 break-words">
            {errorMessage}
          </p>
          <div className="flex gap-2 mt-2.5">
            {canRetry && (
              <button
                type="button"
                onClick={onRetry}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-md bg-red-600 hover:bg-red-500 text-white transition"
              >
                <IconRefresh size={12} /> Retry
              </button>
            )}
            <button
              type="button"
              onClick={onStartOver}
              className="px-3 py-1.5 text-xs rounded-md bg-gray-800 hover:bg-gray-700 text-gray-300 transition"
            >
              Start over
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
