import { useState, useEffect, useRef, useCallback } from "react";
import { VideoUploader } from "../components/VideoUploader";
import { VideoPreview } from "../components/VideoPreview";
import { ProcessingStatus } from "../components/ProcessingStatus";
import { TranscriptPanel } from "../components/TranscriptPanel";
import { SubtitlePanel } from "../components/SubtitlePanel";
import { api } from "../services/api";

const STATUS_POLL_INTERVAL_MS = 1500;
const STATUS_POLL_TIMEOUT_MS = 10 * 60 * 1000;
const MAX_CONSECUTIVE_POLL_ERRORS = 3;

export function Home() {
  const [uploadedVideo, setUploadedVideo] = useState(null);
  const [isError, setIsError] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [isUploading, setIsUploading] = useState(false);

  // Phase 2 — processing state
  const [processing, setProcessing] = useState("idle"); // idle | starting | running | completed | failed
  const [processingStage, setProcessingStage] = useState(null);
  const [processingErrorCode, setProcessingErrorCode] = useState(null);
  const [processingError, setProcessingError] = useState("");
  const [transcript, setTranscript] = useState(null);
  const pollStopRef = useRef(() => {});

  // Phase 3 — romanized subtitles state
  const [subtitles, setSubtitles] = useState(null);
  const [subtitlesModel, setSubtitlesModel] = useState(null);
  const [romanization, setRomanization] = useState("idle"); // idle | running | done | failed
  const [romanError, setRomanError] = useState("");
  const [includeEnglish, setIncludeEnglish] = useState(false);
  const [showEnglish, setShowEnglish] = useState(false);

  const stopPolling = useCallback(() => {
    pollStopRef.current();
  }, []);

  const handleVideoSelect = (file) => {
    setIsError(false);
    setErrorMessage("");
  };

  const handleUploadStart = () => {
    setIsUploading(true);
    setIsError(false);
  };

  const handleUploadComplete = (response) => {
    stopPolling();
    setUploadedVideo(response);
    setIsUploading(false);
    setProcessing("idle");
    setProcessingStage(null);
    setProcessingErrorCode(null);
    setProcessingError("");
    setTranscript(null);
    resetPhase3();
  };

  const resetPhase3 = () => {
    setSubtitles(null);
    setSubtitlesModel(null);
    setRomanization("idle");
    setRomanError("");
    setShowEnglish(false);
  };

  const handleError = (message) => {
    setIsError(true);
    setErrorMessage(message);
    setIsUploading(false);
  };

  const handleReset = () => {
    stopPolling();
    setUploadedVideo(null);
    setIsError(false);
    setErrorMessage("");
    setIsUploading(false);
    setProcessing("idle");
    setProcessingStage(null);
    setProcessingErrorCode(null);
    setProcessingError("");
    setTranscript(null);
    resetPhase3();
  };

  // Poll GET /status until the job completes, fails, or times out.
  useEffect(() => {
    if (processing !== "running" || !uploadedVideo?.job_id) return undefined;

    let cancelled = false;
    let interval = null;
    let errorCount = 0;
    const startedAt = Date.now();

    const poll = async () => {
      try {
        const status = await api.getJobStatus(uploadedVideo.job_id);
        if (cancelled) return;
        errorCount = 0;

        if (status.status === "completed") {
          try {
            const data = await api.getTranscript(uploadedVideo.job_id);
            if (cancelled) return;
            setTranscript(data.segments ?? []);
            setProcessingStage("completed");
            setProcessing("completed");
            // Restore previously generated subtitles, if any. The status
            // endpoint reports subtitles_available, so no 404 probe is made.
            if (status.subtitles_available) {
              try {
                const subs = await api.getSubtitles(uploadedVideo.job_id);
                if (cancelled) return;
                setSubtitles(subs.subtitles ?? []);
                setSubtitlesModel(subs.model ?? null);
                setShowEnglish(!!subs.include_english);
                setRomanization("done");
              } catch {
                // Subtitles vanished between checks — safe to ignore.
              }
            }
          } catch (err) {
            if (cancelled) return;
            setProcessingErrorCode("TRANSCRIPT_FETCH");
            setProcessingError(err.message || "Failed to load transcript.");
            setProcessing("failed");
          }
        } else if (status.status === "failed") {
          setProcessingErrorCode(status.error_code ?? null);
          setProcessingError(
            status.error || "Processing failed. Please try again."
          );
          setProcessing("failed");
        } else {
          setProcessingStage(status.status);
          if (Date.now() - startedAt > STATUS_POLL_TIMEOUT_MS) {
            setProcessingErrorCode("TIMEOUT");
            setProcessingError("Processing is taking too long. Please try again.");
            setProcessing("failed");
          }
        }
      } catch (err) {
        if (cancelled) return;
        errorCount += 1;
        if (errorCount >= MAX_CONSECUTIVE_POLL_ERRORS) {
          setProcessingErrorCode("NETWORK");
          setProcessingError(
            err.message || "Could not reach the server. Check that the backend is running."
          );
          setProcessing("failed");
        }
      }
    };

    poll();
    interval = setInterval(poll, STATUS_POLL_INTERVAL_MS);
    pollStopRef.current = () => {
      cancelled = true;
      if (interval) clearInterval(interval);
    };

    return () => {
      cancelled = true;
      if (interval) clearInterval(interval);
    };
  }, [processing, uploadedVideo]);

  const handleStartProcessing = async () => {
    if (!uploadedVideo?.job_id) return;

    setProcessing("starting");
    setProcessingError("");
    setProcessingErrorCode(null);
    setTranscript(null);

    try {
      await api.startProcessing(uploadedVideo.job_id);
      setProcessingStage("queued");
      setProcessing("running");
    } catch (error) {
      if (error.status === 409) {
        // Job already started (or completed) on the backend — resume polling.
        setProcessingStage(null);
        setProcessing("running");
        return;
      }
      setProcessingErrorCode("START_FAILED");
      setProcessingError(error.message || "Failed to start processing.");
      setProcessing("failed");
    }
  };

  // Client-side failures (network, transcript fetch) only need re-polling;
  // backend-side failures are restarted through POST /process.
  const CLIENT_SIDE_ERROR_CODES = ["NETWORK", "TRANSCRIPT_FETCH", "TIMEOUT"];
  const handleRetryProcessing = () => {
    if (CLIENT_SIDE_ERROR_CODES.includes(processingErrorCode)) {
      setProcessingError("");
      setProcessingErrorCode(null);
      setProcessing("running");
    } else {
      handleStartProcessing();
    }
  };

  const showStartButton = uploadedVideo && processing === "idle";
  const showTranscript = processing === "completed" && transcript;

  // Phase 3 — generate Romanized subtitles from the completed ASR transcript.
  const handleRomanize = async () => {
    if (!uploadedVideo?.job_id) return;
    setRomanization("running");
    setRomanError("");
    setSubtitles(null);
    try {
      const data = await api.romanize(uploadedVideo.job_id, includeEnglish);
      setSubtitles(data.subtitles ?? []);
      setSubtitlesModel(data.model ?? null);
      setShowEnglish(!!data.include_english);
      setRomanization("done");
    } catch (error) {
      setRomanError(error.message || "Failed to generate subtitles.");
      setRomanization("failed");
    }
  };

  const phase3Status =
    romanization === "running"
      ? "romanizing"
      : romanization === "done" && subtitles
      ? "subtitles_ready"
      : null;
  const hasEnglish = (subtitles || []).some((s) => s.english_text);

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      {/* Header */}
      <header className="border-b border-gray-800 py-8">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
          <h1 className="text-4xl font-bold tracking-tight">TALAFUZ AI</h1>
          <p className="text-gray-400 mt-2">
            AI subtitles for South Asian short-form content.
          </p>
        </div>
      </header>

      {/* Main content */}
      <main className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        <div className="space-y-8">
          {/* Upload section */}
          {!uploadedVideo && (
            <div className="flex justify-center">
              <VideoUploader
                onVideoSelect={handleVideoSelect}
                onUploadStart={handleUploadStart}
                onUploadComplete={handleUploadComplete}
                onError={handleError}
              />
            </div>
          )}

          {/* Status messages */}
          {(isError || uploadedVideo) && (
            <div className="flex justify-center">
              <ProcessingStatus
                jobId={uploadedVideo?.job_id}
                isSuccess={!!uploadedVideo}
                isError={isError}
                errorMessage={processing === "failed" ? processingError : errorMessage}
                processingStatus={processing === "idle" ? null : processing}
                processingStage={processingStage}
                processingErrorCode={processingErrorCode}
                phase3Status={phase3Status}
              />
            </div>
          )}

          {/* Processing retry (not offered when ASR is simply not configured) */}
          {processing === "failed" &&
            processingErrorCode !== "ASR_NOT_CONFIGURED" &&
            processingError && (
              <div className="flex justify-center">
                <button
                  onClick={handleRetryProcessing}
                  className="px-6 py-2 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-lg transition"
                >
                  Retry Processing
                </button>
              </div>
            )}

          {/* Video preview */}
          {uploadedVideo && (
            <div className="space-y-6">
              <div className="flex justify-center">
                <VideoPreview
                  jobId={uploadedVideo.job_id}
                  filename={uploadedVideo.filename}
                />
              </div>

              {/* Start processing */}
              {showStartButton && (
                <div className="flex justify-center">
                  <button
                    onClick={handleStartProcessing}
                    className="px-8 py-3 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-lg transition"
                  >
                    Start Processing
                  </button>
                </div>
              )}

              {/* Transcript panel */}
              {showTranscript && (
                <div className="flex justify-center">
                  <TranscriptPanel
                    jobId={uploadedVideo.job_id}
                    segments={transcript}
                  />
                </div>
              )}

              {/* Phase 3 — romanization controls */}
              {showTranscript && romanization !== "running" && (
                <div className="flex flex-col items-center gap-3">
                  <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={includeEnglish}
                      onChange={(e) => setIncludeEnglish(e.target.checked)}
                      className="w-4 h-4 accent-blue-500"
                    />
                    Include English translation (optional)
                  </label>
                  <button
                    onClick={handleRomanize}
                    className="px-8 py-3 bg-purple-600 hover:bg-purple-700 text-white font-semibold rounded-lg transition"
                  >
                    {romanization === "done"
                      ? "Regenerate Roman Urdu Subtitles"
                      : "Generate Roman Urdu Subtitles"}
                  </button>
                </div>
              )}

              {/* Phase 3 — romanization error */}
              {romanization === "failed" && romanError && (
                <div className="flex justify-center">
                  <div className="w-full max-w-2xl bg-red-900 border border-red-700 rounded-lg p-4">
                    <p className="text-red-200 font-medium">
                      Subtitle generation failed
                    </p>
                    <p className="text-red-300 text-sm mt-1">{romanError}</p>
                    <button
                      onClick={handleRomanize}
                      className="mt-3 px-4 py-1.5 bg-red-700 hover:bg-red-600 text-white text-sm font-medium rounded transition"
                    >
                      Retry
                    </button>
                  </div>
                </div>
              )}

              {/* Phase 3 — romanized subtitles (primary output) */}
              {showTranscript && subtitles && romanization === "done" && (
                <div className="flex justify-center">
                  <SubtitlePanel
                    subtitles={subtitles}
                    hasEnglish={hasEnglish}
                    showEnglish={showEnglish}
                    onToggleEnglish={setShowEnglish}
                  />
                </div>
              )}
              {subtitlesModel && (
                <p className="text-center text-xs text-gray-500">
                  Romanization model: {subtitlesModel}
                </p>
              )}

              {/* Reset button */}
              <div className="flex justify-center">
                <button
                  onClick={handleReset}
                  className="px-6 py-2 bg-gray-800 hover:bg-gray-700 text-white font-medium rounded-lg transition border border-gray-700"
                >
                  Upload Another Video
                </button>
              </div>
            </div>
          )}

          {/* Error state with retry */}
          {isError && !uploadedVideo && (
            <div className="flex justify-center">
              <button
                onClick={handleReset}
                className="px-6 py-2 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-lg transition"
              >
                Try Again
              </button>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
