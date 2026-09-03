import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { VideoStage } from "../components/editor/VideoStage";
import { SubtitleList } from "../components/editor/SubtitleList";
import { Timeline } from "../components/editor/Timeline";
import { StylePanel } from "../components/editor/StylePanel";
import {
  IconArrowLeft,
  IconChevronDown,
  IconDownload,
  IconFileText,
  IconRedo,
  IconSave,
  IconUndo,
} from "../components/editor/icons";
import "../components/editor/editor.css";
import { useEditorHistory } from "../hooks/useEditorHistory";
import { api } from "../services/api";
import { DEFAULT_CAPTION_STYLE, mergeCaptionStyle } from "../utils/captionStyles";
import {
  LANGUAGES,
  createSubtitleAfter,
  findActiveSubtitle,
  hasEnglishTranslations,
  mergeSubtitles,
  resequenceSubtitles,
  resegmentSubtitles,
  searchSubtitles,
  splitSubtitleAt,
  validateSubtitlesForSave,
} from "../utils/subtitleUtils";

/**
 * Phase 4 — Talafuz AI Subtitle Editor.
 *
 * Three-panel creator workspace:
 *   left   — caption list (search, inline editing, add/split/merge/delete)
 *   center — video preview with styled caption overlay + timeline
 *   right  — caption tools (text, style, position, animation)
 *
 * Undo/redo covers the whole editor document (subtitles + language + style).
 * Editor state autosaves to localStorage (`talafuz_editor_{job_id}`) so a
 * page refresh never loses work; "Save" persists to the backend.
 */

const DRAFT_PREFIX = "talafuz_editor_";
const DRAFT_DEBOUNCE_MS = 400;
const TOAST_TIMEOUT_MS = 3500;

const SRT_MODES = [
  { value: "romanized", label: "Romanized" },
  { value: "english", label: "English", needsEnglish: true },
  { value: "dual", label: "Dual (Roman + English)", needsEnglish: true },
  { value: "original", label: "Original (ASR text)" },
];

function loadDraft(jobId) {
  try {
    const raw = localStorage.getItem(DRAFT_PREFIX + jobId);
    if (!raw) return null;
    const draft = JSON.parse(raw);
    if (draft && Array.isArray(draft.subtitles) && draft.subtitles.length > 0) {
      return draft;
    }
  } catch {
    /* corrupted draft — fall through to server data */
  }
  return null;
}

export function EditorPage() {
  const navigate = useNavigate();
  const { jobId } = useParams();
  const playerRef = useRef(null);
  const interactionRef = useRef(false);

  const [status, setStatus] = useState("loading"); // loading | ready | error
  const [loadError, setLoadError] = useState("");
  const [hasLocalDraft, setHasLocalDraft] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [selectedId, setSelectedId] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [showSafeZone, setShowSafeZone] = useState(false);
  const [saveState, setSaveState] = useState("idle"); // idle | saving | saved
  const [exportState, setExportState] = useState("idle"); // idle | exporting | downloading
  const [srtMenuOpen, setSrtMenuOpen] = useState(false);
  const [srtState, setSrtState] = useState("idle"); // idle | exporting
  const [toast, setToast] = useState(null);

  const editor = useEditorHistory({
    subtitles: [],
    language: "romanized",
    style: DEFAULT_CAPTION_STYLE,
  });
  const {
    state: { subtitles, language, style },
    update,
    pushHistory,
    updateLive,
    undo,
    redo,
    reset,
    canUndo,
    canRedo,
  } = editor;

  // ------------------------------------------------------------------
  // Load: local draft first, then the backend (edited → generated)
  // ------------------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    setLoadError("");

    const draft = loadDraft(jobId);
    if (draft) {
      reset({
        subtitles: draft.subtitles,
        language: draft.language || "romanized",
        style: mergeCaptionStyle(draft.style),
      });
      setHasLocalDraft(true);
      setStatus("ready");
      return undefined;
    }

    (async () => {
      try {
        const data = await api.getSubtitles(jobId);
        if (cancelled) return;
        const loadedSubs = data.subtitles || [];
        reset({
          subtitles: loadedSubs,
          language: data.language || "romanized",
          style: mergeCaptionStyle(data.style),
        });
        // Auto-segment long cues into 3–5 word groups on the first load so
        // the default matches the creator-style short cues and the word
        // highlight stays in sync with the estimated speech timing.
        const hasLongCue = loadedSubs.some((s) => {
          const wc = ((s.romanized_text || "").trim().split(/\s+/).filter(Boolean)).length;
          return wc > 5;
        });
        if (hasLongCue && loadedSubs.length > 0) {
          update((doc) => ({
            ...doc,
            subtitles: resegmentSubtitles(doc.subtitles, "short"),
          }));
          setToast({
            type: "info",
            text: "Captions auto-segmented into short 3\u20135 word groups \u2014 Ctrl+Z to undo.",
          });
        }
        setStatus("ready");
      } catch (err) {
        if (cancelled) return;
        setLoadError(
          err.message ||
            "Failed to load subtitles. Generate them on the home page first."
        );
        setStatus("error");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [jobId, reset]);

  // ------------------------------------------------------------------
  // Autosave to localStorage (debounced)
  // ------------------------------------------------------------------
  useEffect(() => {
    if (status !== "ready" || subtitles.length === 0) return undefined;
    const id = setTimeout(() => {
      try {
        localStorage.setItem(
          DRAFT_PREFIX + jobId,
          JSON.stringify({
            version: 1,
            subtitles,
            language,
            style,
            savedAt: Date.now(),
          })
        );
        setHasLocalDraft(true);
      } catch {
        /* storage unavailable — autosave silently skipped */
      }
    }, DRAFT_DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [status, jobId, subtitles, language, style]);

  // ------------------------------------------------------------------
  // Derived state
  // ------------------------------------------------------------------
  const activeSubtitle = useMemo(
    () => findActiveSubtitle(subtitles, currentTime),
    [subtitles, currentTime]
  );
  const selectedSubtitle = useMemo(
    () => subtitles.find((s) => s.id === selectedId) || null,
    [subtitles, selectedId]
  );
  const hasEnglish = useMemo(
    () => hasEnglishTranslations(subtitles),
    [subtitles]
  );
  const matchIds = useMemo(
    () => searchSubtitles(subtitles, searchQuery),
    [subtitles, searchQuery]
  );

  const selectedIndex = subtitles.findIndex((s) => s.id === selectedId);
  const hasSelection = selectedId != null;
  const canSplit =
    !!selectedSubtitle &&
    currentTime > selectedSubtitle.start + 0.05 &&
    currentTime < selectedSubtitle.end - 0.05;
  const canMerge = selectedIndex >= 0 && selectedIndex < subtitles.length - 1;

  // ------------------------------------------------------------------
  // Live interaction pattern: one undo entry per interaction (drag/typing)
  // ------------------------------------------------------------------
  const beginInteraction = useCallback(() => {
    if (!interactionRef.current) {
      pushHistory();
      interactionRef.current = true;
    }
  }, [pushHistory]);

  const endInteraction = useCallback(() => {
    interactionRef.current = false;
  }, []);

  // ------------------------------------------------------------------
  // Selection / playback
  // ------------------------------------------------------------------
  const handleSelect = useCallback((sub) => {
    setSelectedId(sub.id);
    playerRef.current?.seek(sub.start);
    setCurrentTime(sub.start);
  }, []);

  const handleSeek = useCallback((time) => {
    playerRef.current?.seek(time);
    setCurrentTime(time);
  }, []);

  const handleCurrentTime = useCallback((time) => setCurrentTime(time), []);
  const handleDuration = useCallback((d) => setDuration(d), []);

  // ------------------------------------------------------------------
  // Structural operations (add / delete / split / merge)
  // ------------------------------------------------------------------
  const handleAdd = useCallback(() => {
    const base = selectedSubtitle || subtitles[subtitles.length - 1] || null;
    const effectiveDuration =
      duration || (base ? base.end : 0) + 2;
    const fresh = createSubtitleAfter(base, effectiveDuration);
    update((doc) => {
      const subs = [...doc.subtitles];
      const insertAt = base
        ? subs.findIndex((s) => s.id === base.id) + 1
        : subs.length;
      subs.splice(insertAt, 0, fresh);
      return { ...doc, subtitles: resequenceSubtitles(subs) };
    });
    const newId = base ? selectedIndex + 2 : subtitles.length + 1;
    setSelectedId(newId);
    playerRef.current?.seek(fresh.start);
    setCurrentTime(fresh.start);
  }, [
    selectedSubtitle,
    subtitles,
    duration,
    selectedIndex,
    update,
  ]);

  const handleDelete = useCallback(() => {
    if (selectedId == null) return;
    const idx = selectedIndex;
    update((doc) => ({
      ...doc,
      subtitles: resequenceSubtitles(
        doc.subtitles.filter((s) => s.id !== selectedId)
      ),
    }));
    const remaining = subtitles.length - 1;
    if (remaining === 0) {
      setSelectedId(null);
    } else {
      const nextIndex = Math.min(idx, remaining - 1);
      setSelectedId(subtitles[nextIndex]?.id ?? null);
    }
  }, [selectedId, selectedIndex, subtitles, update]);

  const handleSplit = useCallback(() => {
    if (!canSplit || !selectedSubtitle) return;
    update((doc) => {
      const subs = [...doc.subtitles];
      const idx = subs.findIndex((s) => s.id === selectedId);
      const parts = splitSubtitleAt(subs[idx], currentTime);
      if (!parts) return doc;
      subs.splice(idx, 1, parts[0], parts[1]);
      return { ...doc, subtitles: resequenceSubtitles(subs) };
    });
    setSelectedId(selectedId + 1); // the second half follows the split
  }, [canSplit, selectedSubtitle, selectedId, currentTime, update]);

  const handleMerge = useCallback(() => {
    if (!canMerge) return;
    update((doc) => {
      const subs = [...doc.subtitles];
      const idx = subs.findIndex((s) => s.id === selectedId);
      const merged = mergeSubtitles(subs[idx], subs[idx + 1]);
      if (!merged) return doc;
      subs.splice(idx, 2, merged);
      return { ...doc, subtitles: resequenceSubtitles(subs) };
    });
  }, [canMerge, selectedId, update]);

  // ------------------------------------------------------------------
  // Timeline trimming (live drag → single undo entry)
  // ------------------------------------------------------------------
  const handleTrim = useCallback(
    (id, edge, time, phase) => {
      if (phase === "start") beginInteraction();

      const apply = (doc) => {
        const subs = doc.subtitles;
        const idx = subs.findIndex((s) => s.id === id);
        if (idx === -1) return doc;
        const sub = subs[idx];
        const prevEnd = idx > 0 ? subs[idx - 1].end : 0;
        const nextStart =
          idx < subs.length - 1 ? subs[idx + 1].start : Infinity;

        let { start, end } = sub;
        if (edge === "start") {
          const candidate = Math.max(0, Math.min(time, sub.end - 0.1));
          if (candidate >= prevEnd - 0.001 && candidate < sub.end - 0.05) {
            start = candidate;
          }
        } else {
          const candidate = Math.max(time, sub.start + 0.1);
          if (candidate <= nextStart + 0.001 && candidate > sub.start + 0.05) {
            end = candidate;
          }
        }

        if (start === sub.start && end === sub.end) return doc;
        const next = [...subs];
        next[idx] = {
          ...sub,
          start: Number(start.toFixed(3)),
          end: Number(end.toFixed(3)),
        };
        return { ...doc, subtitles: next };
      };

      updateLive(apply);
      if (phase === "end") endInteraction();
    },
    [beginInteraction, endInteraction, updateLive]
  );

  // ------------------------------------------------------------------
  // Text / style / language changes
  // ------------------------------------------------------------------
  const handleEditText = useCallback(
    (subId, text) => {
      beginInteraction();
      updateLive((doc) => ({
        ...doc,
        subtitles: doc.subtitles.map((s) => {
          if (s.id !== subId) return s;
          if (doc.language === "english") {
            return { ...s, english_text: text.trim() ? text : null };
          }
          if (doc.language === "romanized") {
            return { ...s, romanized_text: text };
          }
          return s; // original is read-only
        }),
      }));
    },
    [beginInteraction, updateLive]
  );

  const handleStyleChange = useCallback(
    (patch) => {
      beginInteraction();
      updateLive((doc) => ({
        ...doc,
        style: { ...doc.style, ...patch },
      }));
    },
    [beginInteraction, updateLive]
  );

  const handleLanguageChange = useCallback(
    (lang) => {
      if (lang === "english" && !hasEnglish) return;
      update((doc) => ({ ...doc, language: lang }));
    },
    [hasEnglish, update]
  );

  // ------------------------------------------------------------------
  // Save / draft management
  // ------------------------------------------------------------------
  const handleSave = useCallback(async () => {
    const problem = validateSubtitlesForSave(subtitles);
    if (problem) {
      setToast({ type: "error", text: problem });
      return;
    }
    setSaveState("saving");
    try {
      await api.saveSubtitles(jobId, subtitles, { language, style });
      setSaveState("saved");
      setToast({ type: "info", text: "Subtitles saved." });
      setTimeout(() => setSaveState("idle"), 2500);
    } catch (err) {
      setToast({ type: "error", text: err.message || "Failed to save." });
    }
  }, [jobId, subtitles, language, style]);

  const handleDownloadVideo = useCallback(async () => {
    if (exportState !== "idle") return;
    const problem = validateSubtitlesForSave(subtitles);
    if (problem) {
      setToast({ type: "error", text: problem });
      return;
    }
    setExportState("exporting");
    try {
      // Persist the current editor state so the render includes every edit.
      await api.saveSubtitles(jobId, subtitles, { language, style });
      await api.startVideoExport(jobId);
      // FFmpeg burn-in runs on the backend — poll until it finishes.
      const MAX_POLLS = 600; // ~15 minutes at 1.5s intervals
      for (let i = 0; i < MAX_POLLS; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        const state = await api.getVideoExportStatus(jobId);
        if (state.status === "ready") break;
        if (state.status === "failed") {
          throw new Error(state.error || "Video export failed.");
        }
        if (state.status === "idle") {
          throw new Error("Export was interrupted — please try again.");
        }
        if (i === MAX_POLLS - 1) {
          throw new Error("Video export timed out — please try again.");
        }
      }
      setExportState("downloading");
      await api.downloadExportedVideo(jobId);
      setToast({ type: "info", text: "Captioned video downloaded." });
    } catch (err) {
      setToast({ type: "error", text: err.message || "Video export failed." });
    } finally {
      setExportState("idle");
    }
  }, [exportState, jobId, subtitles, language, style]);

  const handleResegment = useCallback(
    (mode) => {
      update((doc) => ({
        ...doc,
        subtitles: resegmentSubtitles(doc.subtitles, mode),
      }));
      setToast({
        type: "info",
        text:
          mode === "word"
            ? "One word per caption — Ctrl+Z undoes it."
            : mode === "sentence"
            ? "Captions merged into sentence-length cues — Ctrl+Z undoes it."
            : "Captions split into short 3–5 word segments — Ctrl+Z undoes it.",
      });
    },
    [update]
  );

  const handleExportSRT = useCallback(
    async (mode) => {
      if (srtState !== "idle") return;
      const problem = validateSubtitlesForSave(subtitles);
      if (problem) {
        setToast({ type: "error", text: problem });
        return;
      }
      setSrtMenuOpen(false);
      setSrtState("exporting");
      try {
        // Persist the current editor state so the file matches the screen.
        await api.saveSubtitles(jobId, subtitles, { language, style });
        await api.exportSRT(jobId, mode);
        setToast({ type: "info", text: `SRT (${mode}) downloaded.` });
      } catch (err) {
        setToast({ type: "error", text: err.message || "SRT export failed." });
      } finally {
        setSrtState("idle");
      }
    },
    [srtState, jobId, subtitles, language, style]
  );

  const handleDiscardDraft = useCallback(async () => {
    try {
      localStorage.removeItem(DRAFT_PREFIX + jobId);
    } catch {
      /* ignore */
    }
    setHasLocalDraft(false);
    setStatus("loading");
    try {
      const data = await api.getSubtitles(jobId);
      reset({
        subtitles: data.subtitles || [],
        language: data.language || "romanized",
        style: mergeCaptionStyle(data.style),
      });
      setSelectedId(null);
      setStatus("ready");
      setToast({ type: "info", text: "Local draft discarded." });
    } catch (err) {
      setLoadError(err.message || "Failed to reload subtitles.");
      setStatus("error");
    }
  }, [jobId, reset]);

  useEffect(() => {
    if (!toast) return undefined;
    const id = setTimeout(() => setToast(null), TOAST_TIMEOUT_MS);
    return () => clearTimeout(id);
  }, [toast]);

  // ------------------------------------------------------------------
  // Keyboard shortcuts
  // ------------------------------------------------------------------
  useEffect(() => {
    const onKeyDown = (e) => {
      const target = e.target;
      const typing =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable);
      if (typing) return;

      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
        return;
      }
      if (mod && e.key.toLowerCase() === "y") {
        e.preventDefault();
        redo();
        return;
      }
      if (
        (e.key === "Delete" || e.key === "Backspace") &&
        selectedId != null
      ) {
        e.preventDefault();
        handleDelete();
        return;
      }
      if (e.key === " " && target?.tagName !== "BUTTON") {
        e.preventDefault();
        playerRef.current?.togglePlay();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedId, handleDelete, undo, redo]);

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------
  if (status === "loading") {
    return (
      <div className="h-screen bg-gray-950 text-white flex flex-col items-center justify-center gap-3">
        <div className="w-8 h-8 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
        <p className="text-sm text-gray-400">Loading subtitle editor…</p>
      </div>
    );
  }

  if (status === "error") {
    return (
      <div className="h-screen bg-gray-950 text-white flex flex-col items-center justify-center gap-4 px-4">
        <p className="text-red-400 text-sm max-w-md text-center">
          {loadError}
        </p>
        <button
          type="button"
          onClick={() => navigate("/")}
          className="px-6 py-2 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-lg text-sm"
        >
          ← Back to home
        </button>
      </div>
    );
  }

  return (
    <div className="min-h-screen lg:h-screen flex flex-col bg-gray-950 text-white overflow-hidden">
      {/* Header */}
      <header className="h-14 shrink-0 flex items-center justify-between gap-3 px-4 border-b border-gray-800 bg-gray-900">
        <div className="flex items-center gap-3 min-w-0">
          <button
            type="button"
            onClick={() => navigate("/")}
            className="text-gray-400 hover:text-white transition"
            title="Back to home"
          >
            <IconArrowLeft size={18} />
          </button>
          <div className="min-w-0">
            <h1 className="text-sm font-bold tracking-wide leading-tight">
              TALAFUZ <span className="text-emerald-400">AI</span>
            </h1>
            <p className="text-[10px] text-gray-500 truncate">
              Subtitle Editor · {jobId}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* Language switch */}
          <div className="flex rounded-lg overflow-hidden border border-gray-700">
            {LANGUAGES.map((l) => {
              const disabled = l.value === "english" && !hasEnglish;
              return (
                <button
                  key={l.value}
                  type="button"
                  disabled={disabled}
                  onClick={() => handleLanguageChange(l.value)}
                  title={
                    disabled
                      ? "No English translations were generated for this job"
                      : `Show ${l.label}`
                  }
                  className={`px-3 py-1.5 text-xs font-medium transition ${
                    language === l.value
                      ? "bg-emerald-500 text-gray-950"
                      : "bg-gray-800 text-gray-300 hover:bg-gray-700"
                  } ${disabled ? "opacity-40 cursor-not-allowed" : ""}`}
                >
                  {l.label}
                </button>
              );
            })}
          </div>

          <button
            type="button"
            onClick={undo}
            disabled={!canUndo}
            className="w-8 h-8 flex items-center justify-center rounded-lg bg-gray-800 text-gray-300 hover:bg-gray-700 transition disabled:opacity-40 disabled:cursor-not-allowed"
            title="Undo (Ctrl+Z)"
          >
            <IconUndo size={15} />
          </button>
          <button
            type="button"
            onClick={redo}
            disabled={!canRedo}
            className="w-8 h-8 flex items-center justify-center rounded-lg bg-gray-800 text-gray-300 hover:bg-gray-700 transition disabled:opacity-40 disabled:cursor-not-allowed"
            title="Redo (Ctrl+Shift+Z)"
          >
            <IconRedo size={15} />
          </button>

          {/* SRT export menu */}
          <div className="relative">
            <button
              type="button"
              onClick={() => setSrtMenuOpen((v) => !v)}
              disabled={srtState !== "idle"}
              className="flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-lg transition bg-gray-800 border border-gray-700 text-gray-200 hover:bg-gray-700 disabled:opacity-60 disabled:cursor-not-allowed"
              title="Download subtitles as an SRT file"
            >
              {srtState === "exporting" ? (
                <span className="w-3.5 h-3.5 border-2 border-emerald-400 border-t-transparent rounded-full animate-spin" />
              ) : (
                <IconFileText size={14} />
              )}
              SRT
              <IconChevronDown size={12} />
            </button>
            {srtMenuOpen && (
              <>
                <button
                  type="button"
                  aria-label="Close menu"
                  className="fixed inset-0 z-40 cursor-default"
                  onClick={() => setSrtMenuOpen(false)}
                />
                <div className="absolute right-0 top-full mt-1.5 z-50 w-52 bg-gray-900 border border-gray-700 rounded-lg shadow-xl py-1">
                  {SRT_MODES.map((m) => {
                    const disabled = m.needsEnglish && !hasEnglish;
                    return (
                      <button
                        key={m.value}
                        type="button"
                        disabled={disabled}
                        onClick={() => handleExportSRT(m.value)}
                        title={
                          disabled
                            ? "No English translations were generated for this job"
                            : `Download ${m.label} SRT`
                        }
                        className="w-full text-left px-3 py-2 text-xs text-gray-200 hover:bg-gray-800 transition disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        {m.label}
                      </button>
                    );
                  })}
                </div>
              </>
            )}
          </div>

          <button
            type="button"
            onClick={handleDownloadVideo}
            disabled={exportState !== "idle" || saveState === "saving"}
            className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-semibold rounded-lg transition bg-gray-800 border border-gray-700 text-gray-200 hover:bg-gray-700 disabled:opacity-60 disabled:cursor-not-allowed"
            title="Save and render the current captions into the video, then download it"
          >
            {exportState === "idle" ? (
              <IconDownload size={14} />
            ) : (
              <span className="w-3.5 h-3.5 border-2 border-emerald-400 border-t-transparent rounded-full animate-spin" />
            )}
            {exportState === "exporting"
              ? "Rendering…"
              : exportState === "downloading"
              ? "Downloading…"
              : "Download Video"}
          </button>

          <button
            type="button"
            onClick={handleSave}
            className={`flex items-center gap-1.5 px-4 py-1.5 text-xs font-semibold rounded-lg transition ${
              saveState === "saved"
                ? "bg-emerald-600 text-white"
                : "bg-emerald-500 hover:bg-emerald-600 text-gray-950"
            }`}
          >
            <IconSave size={14} />
            {saveState === "saving"
              ? "Saving…"
              : saveState === "saved"
              ? "Saved"
              : "Save"}
          </button>
        </div>
      </header>

      {/* Workspace */}
      <main className="flex-1 min-h-0 flex flex-col lg:flex-row gap-3 p-3 overflow-y-auto lg:overflow-hidden">
        <SubtitleList
          className="w-full lg:w-[340px] shrink-0 h-[420px] lg:h-auto"
          subtitles={subtitles}
          language={language}
          activeSubtitleId={activeSubtitle?.id ?? null}
          selectedId={selectedId}
          query={searchQuery}
          onQueryChange={setSearchQuery}
          matchIds={matchIds}
          hasSelection={hasSelection}
          canSplit={canSplit}
          canMerge={canMerge}
          onSelect={handleSelect}
          onEditText={handleEditText}
          onEditCommit={endInteraction}
          onAdd={handleAdd}
          onDelete={handleDelete}
          onSplit={handleSplit}
          onMerge={handleMerge}
          onResegment={handleResegment}
        />

        <section className="flex-1 min-w-0 min-h-0 flex flex-col gap-3">
          <VideoStage
            ref={playerRef}
            videoUrl={api.getVideoUrl(jobId)}
            activeSubtitle={activeSubtitle}
            language={language}
            style={style}
            currentTime={currentTime}
            onCurrentTime={handleCurrentTime}
            onDuration={handleDuration}
            showSafeZone={showSafeZone}
          />

          <Timeline
            subtitles={subtitles}
            duration={duration}
            currentTime={currentTime}
            selectedId={selectedId}
            onSeek={handleSeek}
            onSelect={(sub) => setSelectedId(sub.id)}
            onTrim={handleTrim}
          />
        </section>

        <StylePanel
          className="w-full lg:w-[320px] shrink-0 h-[520px] lg:h-auto"
          style={style}
          onStyleChange={handleStyleChange}
          onStyleCommit={endInteraction}
          selectedSubtitle={selectedSubtitle}
          language={language}
          onEditText={handleEditText}
          onEditCommit={endInteraction}
          showSafeZone={showSafeZone}
          onToggleSafeZone={setShowSafeZone}
        />
      </main>

      {/* Local draft indicator */}
      {hasLocalDraft && (
        <button
          type="button"
          onClick={handleDiscardDraft}
          className="fixed bottom-3 left-3 z-40 text-[11px] px-3 py-1.5 rounded-full bg-gray-800/95 border border-gray-700 text-gray-400 hover:text-white transition"
          title="Discard the local draft and reload from the server"
        >
          Local draft active · discard
        </button>
      )}

      {/* Toast */}
      {toast && (
        <div
          className={`fixed bottom-4 right-4 z-50 px-4 py-2.5 rounded-lg text-sm shadow-xl border max-w-sm ${
            toast.type === "error"
              ? "bg-red-950/95 border-red-700 text-red-200"
              : "bg-gray-800/95 border-gray-600 text-gray-100"
          }`}
        >
          {toast.text}
        </div>
      )}
    </div>
  );
}
