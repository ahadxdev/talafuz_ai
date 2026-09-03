import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { VideoDropzone } from "../components/VideoDropzone";
import { PipelineTimeline } from "../components/PipelineTimeline";
import { api } from "../services/api";

/**
 * Phase 5 — Home: upload → automatic pipeline → editor.
 *
 * Picking a video kicks off the whole flow without further clicks — upload,
 * audio extraction, ASR transcription and romanization run back to back,
 * each stage mirrored in the PipelineTimeline, and the browser is routed
 * straight into the subtitle editor when the captions are ready. Matching
 * the editor chrome keeps the app feeling like one tool.
 */

const STATUS_POLL_INTERVAL_MS = 1500;
const STATUS_POLL_TIMEOUT_MS = 10 * 60 * 1000;
const MAX_CONSECUTIVE_POLL_ERRORS = 3;
const REDIRECT_DELAY_MS = 900;

export function Home() {
  const navigate = useNavigate();

  // Active tab: upload | drafts
  const [tab, setTab] = useState("upload");

  // Pipeline state machine. `phase` drives the timeline; `errorStep` names
  // the stage a retry should restart from.
  const [phase, setPhase] = useState("idle"); // idle | uploading | processing | romanizing | redirecting | error
  const [errorStep, setErrorStep] = useState(null); // upload | process | romanize
  const [errorMessage, setErrorMessage] = useState("");
  const [errorCode, setErrorCode] = useState(null);
  const [fileName, setFileName] = useState("");
  const [fileSizeMB, setFileSizeMB] = useState(0);
  const [jobId, setJobId] = useState(null);
  const [backendStage, setBackendStage] = useState(null); // queued | extracting_audio | transcribing
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [file, setFile] = useState(null); // kept for upload retries

  // Drafts state
  const [drafts, setDrafts] = useState([]);
  const [draftsLoading, setDraftsLoading] = useState(false);
  const [draftsError, setDraftsError] = useState("");
  const [deletingId, setDeletingId] = useState(null);

  const cancelledRef = useRef(false);

  // ------------------------------------------------------------------
  // Drafts fetching
  // ------------------------------------------------------------------
  const fetchDrafts = useCallback(async () => {
    setDraftsLoading(true);
    setDraftsError("");
    try {
      const data = await api.getDrafts();
      setDrafts(data.drafts || []);
    } catch (err) {
      setDraftsError(err.message || "Could not load drafts.");
    } finally {
      setDraftsLoading(false);
    }
  }, []);

  // Fetch drafts when switching to the drafts tab
  useEffect(() => {
    if (tab === "drafts") {
      fetchDrafts();
    }
  }, [tab, fetchDrafts]);

  const handleDeleteDraft = useCallback(
    async (draftJobId) => {
      if (!window.confirm("Delete this draft permanently? This cannot be undone.")) return;
      setDeletingId(draftJobId);
      try {
        await api.deleteDraft(draftJobId);
        setDrafts((prev) => prev.filter((d) => d.job_id !== draftJobId));
      } catch (err) {
        alert("Failed to delete: " + (err.message || "Unknown error"));
      } finally {
        setDeletingId(null);
      }
    },
    []
  );

  const handleEditDraft = useCallback(
    (draftJobId) => {
      navigate(`/editor/${draftJobId}`);
    },
    [navigate]
  );

  // ------------------------------------------------------------------
  // Elapsed timer
  // ------------------------------------------------------------------
  useEffect(() => {
    if (phase === "idle" || phase === "error" || phase === "redirecting") {
      return undefined;
    }
    const id = setInterval(() => setElapsedSeconds((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [phase]);

  useEffect(() => {
    cancelledRef.current = false;
    return () => {
      cancelledRef.current = true;
    };
  }, []);

  const fail = useCallback((step, message, code = null) => {
    setErrorStep(step);
    setErrorCode(code);
    setErrorMessage(message || "Something went wrong. Please try again.");
    setPhase("error");
  }, []);

  const reset = useCallback(() => {
    cancelledRef.current = true;
    setPhase("idle");
    setErrorStep(null);
    setErrorMessage("");
    setErrorCode(null);
    setFileName("");
    setFileSizeMB(0);
    setJobId(null);
    setBackendStage(null);
    setElapsedSeconds(0);
    setFile(null);
  }, []);

  // ------------------------------------------------------------------
  // Stage 1 — upload
  // ------------------------------------------------------------------
  const runUpload = useCallback(
    async (videoFile) => {
      setPhase("uploading");
      setErrorMessage("");
      setErrorStep(null);
      setErrorCode(null);
      setElapsedSeconds(0);
      try {
        const response = await api.uploadVideo(videoFile);
        if (cancelledRef.current) return;
        setJobId(response.job_id);
        runProcessingRef.current(response.job_id);
      } catch (err) {
        if (cancelledRef.current) return;
        fail("upload", err.message || "Upload failed.");
      }
    },
    [fail]
  );

  const handleFile = useCallback(
    (picked) => {
      setFile(picked);
      setFileName(picked.name);
      setFileSizeMB((picked.size / (1024 * 1024)).toFixed(1));
      runUpload(picked);
    },
    [runUpload]
  );

  // ------------------------------------------------------------------
  // Stage 2 — processing (audio extraction + ASR), polled
  //
  // Declared before its dependents and reached through refs so the three
  // stage runners can call each other without ordering or staleness
  // issues (useCallback deps cannot reference a const declared later).
  // ------------------------------------------------------------------
  const runRomanizationRef = useRef(() => {});
  const runProcessingRef = useRef(() => {});

  const runProcessing = useCallback(
    async (id) => {
      setPhase("processing");
      setBackendStage(null);
      setErrorMessage("");
      setErrorStep(null);
      setErrorCode(null);
      try {
        await api.startProcessing(id);
      } catch (err) {
        if (err.status === 409) {
          // Already started (or completed) on the backend — just poll.
        } else {
          fail("process", err.message || "Failed to start processing.", err.status === 503 ? "ASR_NOT_CONFIGURED" : null);
          return;
        }
      }

      const startedAt = Date.now();
      let consecutiveErrors = 0;

      const pollOnce = async () => {
        try {
          const status = await api.getJobStatus(id);
          consecutiveErrors = 0;
          if (cancelledRef.current) return;

          if (status.status === "completed") {
            // Subtitles already generated (e.g. a retried job) skip
            // straight to the editor; otherwise romanize next.
            if (status.subtitles_available) {
              setPhase("redirecting");
              setTimeout(() => {
                if (!cancelledRef.current) navigate(`/editor/${id}`);
              }, REDIRECT_DELAY_MS);
            } else {
              runRomanizationRef.current(id);
            }
            return;
          }
          if (status.status === "failed") {
            fail(
              "process",
              status.error || "Processing failed. Please try again.",
              status.error_code ?? null
            );
            return;
          }
          setBackendStage(status.status);
          if (Date.now() - startedAt > STATUS_POLL_TIMEOUT_MS) {
            fail("process", "Processing is taking too long. Please try again.", "TIMEOUT");
            return;
          }
          scheduleNext();
        } catch (err) {
          consecutiveErrors += 1;
          if (consecutiveErrors >= MAX_CONSECUTIVE_POLL_ERRORS) {
            fail(
              "process",
              err.message || "Could not reach the server. Check that the backend is running.",
              "NETWORK"
            );
            return;
          }
          scheduleNext();
        }
      };

      const scheduleNext = () => {
        if (!cancelledRef.current) {
          setTimeout(pollOnce, STATUS_POLL_INTERVAL_MS);
        }
      };

      pollOnce();
    },
    [fail, navigate]
  );

  runProcessingRef.current = runProcessing;

  // ------------------------------------------------------------------
  // Stage 3 — romanization (+ English translation)
  //
  // The English translation is always generated alongside the romanized
  // captions — no opt-in needed.
  // ------------------------------------------------------------------
  const runRomanization = useCallback(
    async (id) => {
      setPhase("romanizing");
      setErrorMessage("");
      setErrorStep(null);
      setErrorCode(null);
      try {
        await api.romanize(id);
        if (cancelledRef.current) return;
        setPhase("redirecting");
        setTimeout(() => {
          if (!cancelledRef.current) navigate(`/editor/${id}`);
        }, REDIRECT_DELAY_MS);
      } catch (err) {
        if (cancelledRef.current) return;
        fail("romanize", err.message || "Failed to generate subtitles.");
      }
    },
    [fail, navigate]
  );

  runRomanizationRef.current = runRomanization;

  // ------------------------------------------------------------------
  // Retry / start over
  // ------------------------------------------------------------------
  const handleRetry = useCallback(() => {
    if (errorStep === "upload" && file) {
      runUpload(file);
    } else if (errorStep === "process" && jobId) {
      runProcessing(jobId);
    } else if (errorStep === "romanize" && jobId) {
      runRomanization(jobId);
    } else {
      reset();
    }
  }, [errorStep, file, jobId, reset, runProcessing, runRomanization, runUpload]);

  const busy = phase !== "idle" && phase !== "error";

  // Format ISO date to a short relative string
  const formatDate = (iso) => {
    try {
      const d = new Date(iso);
      return d.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return iso;
    }
  };

  // Status badge color
  const statusBadge = (status) => {
    const map = {
      completed: "bg-emerald-500/15 text-emerald-400",
      uploaded: "bg-yellow-500/15 text-yellow-400",
      failed: "bg-red-500/15 text-red-400",
      queued: "bg-blue-500/15 text-blue-400",
      extracting_audio: "bg-blue-500/15 text-blue-400",
      transcribing: "bg-blue-500/15 text-blue-400",
    };
    return map[status] || "bg-gray-500/15 text-gray-400";
  };

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------
  return (
    <div className="min-h-screen flex flex-col bg-gray-950 text-white">
      {/* Header — same chrome as the editor */}
      <header className="h-14 shrink-0 flex items-center justify-between gap-3 px-4 border-b border-gray-800 bg-gray-900">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-7 h-7 rounded-lg bg-emerald-500 flex items-center justify-center text-gray-950 font-black text-xs shrink-0">
            T
          </div>
          <div className="min-w-0">
            <h1 className="text-sm font-bold tracking-wide leading-tight">
              TALAFUZ <span className="text-emerald-400">AI</span>
            </h1>
            <p className="text-[10px] text-gray-500 truncate">
              AI subtitles for South Asian creators
            </p>
          </div>
        </div>
        {busy && (
          <span className="text-[11px] text-gray-500 hidden sm:block">
            Hang tight — everything runs automatically
          </span>
        )}
      </header>

      <main className="flex-1 flex flex-col items-center gap-8 px-4 py-10">
        {/* -------------------------------------------------------- */}
        {/* Tab switcher (only when idle or error)                    */}
        {/* -------------------------------------------------------- */}
        {(phase === "idle" || phase === "error") && (
          <div className="flex items-center gap-1 bg-gray-900 rounded-lg p-1 border border-gray-800">
            <button
              onClick={() => setTab("upload")}
              className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                tab === "upload"
                  ? "bg-emerald-500 text-gray-950"
                  : "text-gray-400 hover:text-white"
              }`}
            >
              Upload
            </button>
            <button
              onClick={() => setTab("drafts")}
              className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors flex items-center gap-1.5 ${
                tab === "drafts"
                  ? "bg-emerald-500 text-gray-950"
                  : "text-gray-400 hover:text-white"
              }`}
            >
              Drafts
              {drafts.length > 0 && tab !== "drafts" && (
                <span className="bg-emerald-500/20 text-emerald-400 text-[10px] font-bold px-1.5 py-0.5 rounded-full">
                  {drafts.length}
                </span>
              )}
            </button>
          </div>
        )}

        {/* -------------------------------------------------------- */}
        {/* Upload tab                                                */}
        {/* -------------------------------------------------------- */}
        {phase === "idle" && tab === "upload" && (
          <>
            <div className="text-center max-w-xl">
              <h2 className="text-2xl sm:text-3xl font-bold tracking-tight">
                Caption your video in one drop
              </h2>
              <p className="text-sm text-gray-400 mt-2 leading-relaxed">
                Upload a clip and Talafuz transcribes the speech, romanizes it
                and drops you straight into the caption editor — styled,
                timed and export-ready.
              </p>
            </div>
            <VideoDropzone onFile={handleFile} />
          </>
        )}

        {/* -------------------------------------------------------- */}
        {/* Drafts tab                                                */}
        {/* -------------------------------------------------------- */}
        {phase === "idle" && tab === "drafts" && (
          <div className="w-full max-w-2xl">
            {draftsLoading && (
              <div className="text-center py-16">
                <div className="inline-block w-6 h-6 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
                <p className="text-sm text-gray-500 mt-3">Loading drafts…</p>
              </div>
            )}

            {draftsError && !draftsLoading && (
              <div className="text-center py-16">
                <p className="text-sm text-red-400">{draftsError}</p>
                <button
                  onClick={fetchDrafts}
                  className="mt-3 text-xs text-emerald-400 hover:underline"
                >
                  Try again
                </button>
              </div>
            )}

            {!draftsLoading && !draftsError && drafts.length === 0 && (
              <div className="text-center py-16">
                <div className="w-14 h-14 mx-auto rounded-xl bg-gray-800 flex items-center justify-center mb-4">
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-7 h-7 text-gray-600">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z" />
                  </svg>
                </div>
                <h3 className="text-base font-semibold text-gray-300">No drafts yet</h3>
                <p className="text-sm text-gray-500 mt-1">
                  Upload a video and your work will be saved here automatically.
                </p>
                <button
                  onClick={() => setTab("upload")}
                  className="mt-4 px-4 py-2 bg-emerald-500 text-gray-950 text-sm font-medium rounded-lg hover:bg-emerald-400 transition-colors"
                >
                  Upload a video
                </button>
              </div>
            )}

            {!draftsLoading && !draftsError && drafts.length > 0 && (
              <div className="space-y-2">
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-lg font-semibold">Saved drafts</h2>
                  <button
                    onClick={fetchDrafts}
                    className="text-xs text-gray-500 hover:text-emerald-400 transition-colors"
                  >
                    Refresh
                  </button>
                </div>

                {drafts.map((draft) => {
                  const canEdit = draft.status === "completed" || draft.subtitles_saved;
                  return (
                    <div
                      key={draft.job_id}
                      className="group flex items-center gap-4 bg-gray-900 border border-gray-800 rounded-lg px-4 py-3 hover:border-gray-700 transition-colors"
                    >
                      {/* Video icon / thumbnail placeholder */}
                      <div className="w-10 h-10 rounded-lg bg-gray-800 flex items-center justify-center shrink-0">
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-5 h-5 text-gray-500">
                          <path strokeLinecap="round" strokeLinejoin="round" d="m15.75 10.5 4.72-4.72a.75.75 0 0 1 1.28.53v11.38a.75.75 0 0 1-1.28.53l-4.72-4.72M4.5 18.75h9a2.25 2.25 0 0 0 2.25-2.25v-9a2.25 2.25 0 0 0-2.25-2.25h-9A2.25 2.25 0 0 0 2.25 7.5v9a2.25 2.25 0 0 0 2.25 2.25Z" />
                        </svg>
                      </div>

                      {/* Info */}
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium truncate">
                          {draft.video_filename || "Unknown video"}
                        </p>
                        <div className="flex items-center gap-2 mt-0.5">
                          <span className="text-[11px] text-gray-500">
                            {formatDate(draft.created_at)}
                          </span>
                          <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-full ${statusBadge(draft.status)}`}>
                            {draft.status}
                          </span>
                          {draft.subtitles_saved && (
                            <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400">
                              saved
                            </span>
                          )}
                          {draft.has_export && (
                            <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-purple-500/15 text-purple-400">
                              exported
                            </span>
                          )}
                        </div>
                      </div>

                      {/* Actions */}
                      <div className="flex items-center gap-2 shrink-0">
                        <button
                          onClick={() => handleEditDraft(draft.job_id)}
                          disabled={!canEdit}
                          className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                            canEdit
                              ? "bg-emerald-500/15 text-emerald-400 hover:bg-emerald-500/25"
                              : "bg-gray-800 text-gray-600 cursor-not-allowed"
                          }`}
                          title={canEdit ? "Open in editor" : "Processing not complete"}
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => handleDeleteDraft(draft.job_id)}
                          disabled={deletingId === draft.job_id}
                          className="px-3 py-1.5 text-xs font-medium rounded-md bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-colors disabled:opacity-50"
                        >
                          {deletingId === draft.job_id ? "…" : "Delete"}
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* -------------------------------------------------------- */}
        {/* Pipeline timeline (visible during processing)             */}
        {/* -------------------------------------------------------- */}
        {phase !== "idle" && (
          <PipelineTimeline
            fileName={fileName}
            fileSizeMB={fileSizeMB}
            jobId={jobId}
            phase={phase}
            stage={backendStage}
            elapsedSeconds={elapsedSeconds}
            errorMessage={errorMessage}
            errorCode={errorCode}
            errorStep={errorStep}
            canRetry={errorCode !== "ASR_NOT_CONFIGURED"}
            onRetry={handleRetry}
            onStartOver={reset}
          />
        )}

        {phase === "redirecting" && (
          <p className="text-xs text-emerald-400 animate-pulse">
            Opening the editor…
          </p>
        )}
      </main>
    </div>
  );
}
