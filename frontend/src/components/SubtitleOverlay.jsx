/**
 * Phase 4 — Subtitle overlay component.
 *
 * Displays the current active subtitle at the bottom center of the video,
 * with support for different display modes (romanized, english, dual).
 */
export function SubtitleOverlay({ subtitle, mode = "romanized" }) {
  if (!subtitle) {
    return null;
  }

  let displayText = "";

  if (mode === "romanized") {
    displayText = subtitle.romanized_text || "";
  } else if (mode === "english") {
    displayText = subtitle.english_text || "";
  } else if (mode === "dual") {
    const romanized = subtitle.romanized_text || "";
    const english = subtitle.english_text || "";
    displayText = english ? `${romanized}\n${english}` : romanized;
  }

  if (!displayText) {
    return null;
  }

  return (
    <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black to-transparent pt-12 pb-4 px-4">
      <div className="text-center">
        <p className="text-white font-medium text-lg leading-relaxed max-w-2xl mx-auto drop-shadow-lg whitespace-pre-line">
          {displayText}
        </p>
      </div>
    </div>
  );
}
