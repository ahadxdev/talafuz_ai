import { useState, useEffect, useCallback, useRef } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { VideoPlayer } from "../components/VideoPlayer";
import { SubtitleEditor } from "../components/SubtitleEditor";
import { SubtitleModeSelector } from "../components/SubtitleModeSelector";
import { api } from "../services/api";

/**
 * Phase 4 — Subtitle Editor Page
 *
 * Provides a professional creator-focused workspace for editing and exporting subtitles.
 */
export function EditorPage() {
  const navigate = useNavigate();
  const { jobId } = useParams();

  // State
  const [videoUrl, setVideoUrl] = useState("");
  const [subtitles, setSubtitles] = useState([]);
  const [originalSubtitles, setOriginalSubtitles] = useState([]);
  const [subtitleMode, setSubtitleMode] = useState("romanized");
  const [activeSubtitleId, setActiveSubtitleId] = useState(null);
  const [hasEnglish, setHasEnglish] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const videoPlayerRef = useRef(null);

  // Load subtitles on mount
  useEffect(() => {
    if (!jobId) {
      setError("No job ID provided");
      return;
    }

    const loadSubtitles = async () => {
      try {
        setError("");
        setMessage("Loading subtitles...");

        // Load subtitles
        const data = await api.getSubtitles(jobId);
        const subs = data.subtitles || [];
        setSubtitles(subs);
        setOriginalSubtitles(JSON.parse(JSON.stringify(subs))); // Deep copy

        // Check for English
        const hasEng = subs.some((s) => s.english_text);
        setHasEnglish(hasEng);

        // Set video URL
        const url = api.getVideoUrl(jobId);
        setVideoUrl(url);

        setMessage("");
      } catch (err) {
        setError(err.message || "Failed to load subtitles");
      }
    };

    loadSubtitles();
  }, [jobId]);

  const handleEditSubtitle = useCallback((editedSubtitle) => {
    setSubtitles((prev) =>
      prev.map((sub) => (sub.id === editedSubtitle.id ? editedSubtitle : sub))
    );
  }, []);

  const handleSubtitleClick = (subtitle) => {
    setActiveSubtitleId(subtitle.id);
  };

  const handleSeekTo = (time) => {
    if (videoPlayerRef.current) {
      videoPlayerRef.current.seek(time);
    }
  };

  const handleSaveChanges = async () => {
    if (!jobId || subtitles.length === 0) {
      setError("No subtitles to save");
      return;
    }

    setIsSaving(true);
    setError("");
    setMessage("");

    try {
      const response = await api.saveSubtitles(jobId, subtitles);
      setMessage(response.message || `Saved ${response.subtitles_saved} subtitles`);
      setOriginalSubtitles(JSON.parse(JSON.stringify(subtitles)));
    } catch (err) {
      setError(err.message || "Failed to save subtitles");
    } finally {
      setIsSaving(false);
    }
  };

  const handleExportSRT = async (mode) => {
    if (!jobId) {
      setError("No job ID provided");
      return;
    }

    setIsExporting(true);
    setError("");
    setMessage("");

    try {
      await api.exportSRT(jobId, mode);
      setMessage(`Exported ${mode} SRT successfully`);
    } catch (err) {
      setError(err.message || `Failed to export ${mode} SRT`);
    } finally {
      setIsExporting(false);
    }
  };

  const hasChanges =
    JSON.stringify(subtitles) !== JSON.stringify(originalSubtitles);

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      {/* Header */}
      <header className="border-b border-gray-800 py-6">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex justify-between items-center">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">TALAFUZ AI</h1>
            <p className="text-gray-400 text-sm mt-1">Subtitle Editor</p>
          </div>
          <button
            onClick={() => navigate("/")}
            className="px-4 py-2 text-sm bg-gray-700 hover:bg-gray-600 text-gray-100 rounded-lg transition"
          >
            ← Back
          </button>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Messages */}
        {message && (
          <div className="mb-6 p-4 bg-green-900/30 border border-green-700 rounded-lg text-green-300 text-sm">
            {message}
          </div>
        )}
        {error && (
          <div className="mb-6 p-4 bg-red-900/30 border border-red-700 rounded-lg text-red-300 text-sm">
            {error}
          </div>
        )}

        {subtitles.length > 0 && (
          <div className="grid grid-cols-3 gap-6">
            {/* Left Column: Video Player & Mode Selector */}
            <div className="col-span-2 space-y-6">
              <VideoPlayer
                ref={videoPlayerRef}
                videoUrl={videoUrl}
                subtitles={subtitles}
                subtitleMode={subtitleMode}
                onSubtitleClick={handleSubtitleClick}
                onTimeUpdate={(time, subtitle) => {
                  if (subtitle) {
                    setActiveSubtitleId(subtitle.id);
                  }
                }}
              />

              <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
                <SubtitleModeSelector
                  mode={subtitleMode}
                  onModeChange={setSubtitleMode}
                  hasEnglish={hasEnglish}
                />
              </div>
            </div>

            {/* Right Column: Controls */}
            <div className="space-y-4">
              <div className="bg-gray-800 border border-gray-700 rounded-lg p-4 space-y-3">
                <h3 className="font-semibold text-white">Actions</h3>

                <button
                  onClick={handleSaveChanges}
                  disabled={!hasChanges || isSaving}
                  className={`w-full px-4 py-2 rounded-lg font-medium transition text-sm ${
                    hasChanges
                      ? "bg-green-600 hover:bg-green-700 text-white"
                      : "bg-gray-600 text-gray-400 cursor-not-allowed"
                  }`}
                >
                  {isSaving ? "Saving..." : "Save Changes"}
                </button>

                <div className="pt-2 border-t border-gray-700 space-y-2">
                  <p className="text-xs text-gray-400">Export as SRT:</p>
                  <button
                    onClick={() => handleExportSRT("romanized")}
                    disabled={isExporting}
                    className="w-full px-3 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded-lg transition disabled:opacity-50"
                  >
                    Romanized
                  </button>
                  {hasEnglish && (
                    <>
                      <button
                        onClick={() => handleExportSRT("english")}
                        disabled={isExporting}
                        className="w-full px-3 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded-lg transition disabled:opacity-50"
                      >
                        English
                      </button>
                      <button
                        onClick={() => handleExportSRT("dual")}
                        disabled={isExporting}
                        className="w-full px-3 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded-lg transition disabled:opacity-50"
                      >
                        Dual
                      </button>
                    </>
                  )}
                </div>
              </div>

              <div className="text-xs text-gray-400 bg-gray-800 border border-gray-700 rounded-lg p-3">
                <p className="font-semibold text-white mb-2">Info</p>
                <p>{subtitles.length} subtitles</p>
                {hasChanges && <p className="text-yellow-400 mt-1">Unsaved changes</p>}
              </div>
            </div>
          </div>
        )}

        {/* Bottom: Subtitle List */}
        {subtitles.length > 0 && (
          <div className="mt-8">
            <SubtitleEditor
              subtitles={subtitles}
              activeSubtitleId={activeSubtitleId}
              onEditSubtitle={handleEditSubtitle}
              onSeekTo={handleSeekTo}
            />
          </div>
        )}

        {subtitles.length === 0 && !error && (
          <div className="text-center py-12 text-gray-400">
            <p>Loading subtitles...</p>
          </div>
        )}
      </main>
    </div>
  );
}
