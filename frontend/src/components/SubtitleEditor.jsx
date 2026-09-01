import { useState, useRef, useEffect } from "react";
import { SubtitleItem } from "./SubtitleItem";

/**
 * Phase 4 — Subtitle editor component.
 *
 * Displays all subtitles in a scrollable list and allows editing with
 * automatic highlighting of the currently active subtitle during playback.
 */
export function SubtitleEditor({
  subtitles = [],
  activeSubtitleId = null,
  onEditSubtitle = () => {},
  onSeekTo = () => {},
}) {
  const containerRef = useRef(null);
  const activeRef = useRef(null);

  // Auto-scroll to active subtitle
  useEffect(() => {
    if (activeRef.current && containerRef.current) {
      activeRef.current.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
      });
    }
  }, [activeSubtitleId]);

  if (!subtitles || subtitles.length === 0) {
    return (
      <div className="w-full bg-gray-800 border border-gray-700 rounded-lg p-6 text-center text-gray-400">
        No subtitles available.
      </div>
    );
  }

  return (
    <div className="w-full bg-gray-800 border border-gray-700 rounded-lg p-6">
      <div className="flex items-center justify-between gap-4 mb-4">
        <h2 className="text-lg font-semibold text-white">Subtitle Editor</h2>
        <span className="text-sm text-gray-400">
          {subtitles.length} subtitle{subtitles.length === 1 ? "" : "s"}
        </span>
      </div>

      <ul
        ref={containerRef}
        className="space-y-3 max-h-96 overflow-y-auto pr-2"
      >
        {subtitles.map((sub) => (
          <div
            key={sub.id}
            ref={sub.id === activeSubtitleId ? activeRef : null}
          >
            <SubtitleItem
              subtitle={sub}
              isActive={sub.id === activeSubtitleId}
              onEdit={onEditSubtitle}
              onClick={() => onSeekTo(sub.start)}
            />
          </div>
        ))}
      </ul>
    </div>
  );
}
