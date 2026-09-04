import { useEffect, useRef, useState } from "react";
import { getSubtitleText } from "../../utils/subtitleUtils";
import { secondsToTimestamp } from "../../utils/timeUtils";
import {
  IconChevronDown,
  IconMerge,
  IconPencil,
  IconPlus,
  IconScissors,
  IconSearch,
  IconTrash,
  IconWand,
  IconX,
} from "./icons";

/** Caption length modes offered by the Length menu. */
const LENGTH_MODES = [
  { value: "word", label: "Word by word", hint: "One word per caption (pop style)" },
  { value: "short", label: "Short · 3–5 words", hint: "Creator-style caption groups" },
  { value: "sentence", label: "Sentence", hint: "Full sentence-length cues" },
];

/**
 * Phase 4 — Left panel: caption list with search and inline text editing.
 *
 * Clicking a caption selects it and seeks the video to its start time.
 * Double-click (or the pencil button) edits the text of the current display
 * language. The original ASR text is read-only.
 */
export function SubtitleList({
  className = "",
  subtitles,
  language,
  activeSubtitleId,
  selectedId,
  query,
  onQueryChange,
  matchIds,
  hasSelection,
  canSplit,
  canMerge,
  onSelect,
  onEditText,
  onEditCommit,
  onAdd,
  onDelete,
  onSplit,
  onMerge,
  onResegment,
}) {
  const [editingId, setEditingId] = useState(null);
  const [draft, setDraft] = useState("");
  const [originalDraft, setOriginalDraft] = useState("");
  const [lengthMenuOpen, setLengthMenuOpen] = useState(false);
  const activeItemRef = useRef(null);

  // Keep the active (playing) caption in view.
  useEffect(() => {
    if (activeSubtitleId != null) {
      activeItemRef.current?.scrollIntoView({
        block: "nearest",
        behavior: "smooth",
      });
    }
  }, [activeSubtitleId]);

  const startEdit = (sub) => {
    if (language === "original" || language === "urdu") return; // read-only views (original ASR / Urdu script)
    const text = getSubtitleText(sub, language);
    setEditingId(sub.id);
    setDraft(text);
    setOriginalDraft(text);
  };

  const commitEdit = () => {
    if (editingId != null) {
      onEditText(editingId, draft);
      onEditCommit();
    }
    setEditingId(null);
  };

  const cancelEdit = () => {
    if (editingId != null && draft !== originalDraft) {
      onEditText(editingId, originalDraft);
      onEditCommit();
    }
    setEditingId(null);
  };

  const isSearching = !!matchIds;
  const matchCount = matchIds ? matchIds.size : 0;

  const toolbarButton =
    "flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded-md transition disabled:opacity-40 disabled:cursor-not-allowed";

  return (
    <aside
      className={`${className} flex flex-col min-h-0 bg-gray-900 border border-gray-800 rounded-xl`}
    >
      {/* Header */}
      <div className="shrink-0 p-3 border-b border-gray-800 space-y-2.5">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-semibold text-gray-300 uppercase tracking-wider">
            Captions
          </h2>
          <span className="text-xs text-gray-500">{subtitles.length} cues</span>
        </div>

        {/* Search */}
        <div className="relative">
          <IconSearch
            size={14}
            className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-500"
          />
          <input
            type="text"
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder="Search captions…"
            className="w-full bg-gray-950 border border-gray-700 rounded-lg pl-8 pr-7 py-1.5 text-sm text-gray-100 placeholder-gray-600 focus:outline-none focus:border-emerald-500"
          />
          {query && (
            <button
              type="button"
              onClick={() => onQueryChange("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
              title="Clear search"
            >
              <IconX size={13} />
            </button>
          )}
        </div>
        {isSearching && (
          <p className="text-[11px] text-gray-500">
            {matchCount} match{matchCount === 1 ? "" : "es"} · non-matching
            captions dimmed
          </p>
        )}

        {/* Toolbar */}
        <div className="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            onClick={onAdd}
            className={`${toolbarButton} bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20`}
            title="Add caption after selection"
          >
            <IconPlus size={13} /> Add
          </button>
          <button
            type="button"
            onClick={onSplit}
            disabled={!canSplit}
            className={`${toolbarButton} bg-gray-800 text-gray-300 hover:bg-gray-700`}
            title="Split caption at playhead"
          >
            <IconScissors size={13} /> Split
          </button>
          <button
            type="button"
            onClick={onMerge}
            disabled={!canMerge}
            className={`${toolbarButton} bg-gray-800 text-gray-300 hover:bg-gray-700`}
            title="Merge with next caption"
          >
            <IconMerge size={13} /> Merge
          </button>
          {/* Caption length — re-segment into word / short / sentence cues */}
          <div className="relative">
            <button
              type="button"
              onClick={() => setLengthMenuOpen((v) => !v)}
              disabled={subtitles.length === 0}
              className={`${toolbarButton} bg-gray-800 text-gray-300 hover:bg-gray-700`}
              title="Re-segment captions into word, short or sentence cues — undo with Ctrl+Z"
            >
              <IconWand size={13} /> Length <IconChevronDown size={12} />
            </button>
            {lengthMenuOpen && (
              <>
                <button
                  type="button"
                  aria-label="Close menu"
                  className="fixed inset-0 z-40 cursor-default"
                  onClick={() => setLengthMenuOpen(false)}
                />
                <div className="absolute left-0 top-full mt-1.5 z-50 w-60 bg-gray-900 border border-gray-700 rounded-lg shadow-xl py-1">
                  {LENGTH_MODES.map((m) => (
                    <button
                      key={m.value}
                      type="button"
                      onClick={() => {
                        setLengthMenuOpen(false);
                        onResegment(m.value);
                      }}
                      className="w-full text-left px-3 py-2 text-xs text-gray-200 hover:bg-gray-800 transition"
                    >
                      {m.label}
                      <span className="block text-[10px] text-gray-500">
                        {m.hint}
                      </span>
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
          <button
            type="button"
            onClick={onDelete}
            disabled={!hasSelection}
            className={`${toolbarButton} ml-auto bg-red-500/10 text-red-400 hover:bg-red-500/20`}
            title="Delete caption (Del)"
          >
            <IconTrash size={13} />
          </button>
        </div>
      </div>

      {/* List */}
      {subtitles.length === 0 ? (
        <div className="flex-1 flex items-center justify-center p-6 text-sm text-gray-500">
          No captions yet — click Add to create one.
        </div>
      ) : (
        <ul className="tf-scroll flex-1 min-h-0 overflow-y-auto p-2 space-y-1.5">
          {subtitles.map((sub) => {
            const isSelected = sub.id === selectedId;
            const isActive = sub.id === activeSubtitleId;
            const isDimmed = isSearching && !matchIds.has(sub.id);
            const isEditing = sub.id === editingId;
            const isEmpty = !(sub.romanized_text || "").trim();

            return (
              <li
                key={sub.id}
                ref={isActive ? activeItemRef : null}
                onClick={() => onSelect(sub)}
                onDoubleClick={() => startEdit(sub)}
                className={`relative rounded-lg border p-2.5 cursor-pointer transition ${
                  isSelected
                    ? "bg-blue-950/60 border-blue-500 shadow-lg shadow-blue-950/50"
                    : isActive
                    ? "bg-gray-800/80 border-emerald-500/60"
                    : "bg-gray-950/60 border-gray-800 hover:border-gray-600"
                } ${isDimmed ? "opacity-35" : ""}`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-[11px] font-mono text-gray-500">
                    #{sub.id}
                  </span>
                  <span className="text-[11px] font-mono text-gray-500 truncate">
                    {secondsToTimestamp(sub.start)} →{" "}
                    {secondsToTimestamp(sub.end)}
                  </span>
                  {language !== "original" && language !== "urdu" && !isEditing && (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        startEdit(sub);
                      }}
                      className="ml-auto text-gray-500 hover:text-emerald-400 transition"
                      title="Edit text"
                    >
                      <IconPencil size={13} />
                    </button>
                  )}
                </div>

                {isEditing ? (
                  <textarea
                    autoFocus
                    value={draft}
                    rows={2}
                    onChange={(e) => {
                      setDraft(e.target.value);
                      onEditText(sub.id, e.target.value);
                    }}
                    onBlur={commitEdit}
                    onClick={(e) => e.stopPropagation()}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        commitEdit();
                      } else if (e.key === "Escape") {
                        e.preventDefault();
                        cancelEdit();
                      }
                    }}
                    className="w-full bg-gray-950 border border-emerald-500 rounded-md px-2 py-1.5 text-sm text-gray-100 resize-none focus:outline-none"
                  />
                ) : (
                  <p className="text-sm text-gray-100 leading-snug break-words">
                    {getSubtitleText(sub, language) || (
                      <em className="text-gray-500 text-xs">
                        {language === "english"
                          ? "English translation not available"
                          : "— empty —"}
                      </em>
                    )}
                  </p>
                )}

                {isEmpty && !isEditing && (
                  <p className="text-[10px] text-amber-400 mt-1">
                    Empty text blocks saving
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
}
