function formatTime(seconds) {
  const total = Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
  const mins = Math.floor(total / 60);
  const secs = total - mins * 60;
  return `${String(mins).padStart(2, "0")}:${secs.toFixed(2).padStart(5, "0")}`;
}

/**
 * Phase 3 — Romanized subtitle preview.
 *
 * The Romanized (Latin-script) text is the primary output and rendered
 * prominently; the original ASR text is shown as a secondary reference.
 * English translation (when generated) is opt-in via a toggle.
 */
export function SubtitlePanel({ subtitles, hasEnglish, showEnglish, onToggleEnglish }) {
  return (
    <div className="w-full max-w-3xl bg-gray-800 border border-gray-700 rounded-lg p-6">
      <div className="flex items-center justify-between gap-4 mb-4">
        <h2 className="text-lg font-semibold text-white">
          Roman Urdu Subtitles
        </h2>
        <div className="flex items-center gap-4">
          <span className="text-sm text-gray-400">
            {subtitles.length} subtitle{subtitles.length === 1 ? "" : "s"}
          </span>
          {hasEnglish && (
            <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={showEnglish}
                onChange={(e) => onToggleEnglish(e.target.checked)}
                className="w-4 h-4 accent-blue-500"
              />
              English Translation
            </label>
          )}
        </div>
      </div>

      <ul className="space-y-4 max-h-[28rem] overflow-y-auto pr-1">
        {subtitles.map((sub) => (
          <li
            key={sub.id}
            className="bg-gray-900/60 border border-gray-700 rounded-md p-4"
          >
            <div className="text-xs font-mono text-gray-500 mb-2">
              {formatTime(sub.start)} – {formatTime(sub.end)}
            </div>

            {/* Primary output: romanized text */}
            <p className="text-lg text-white font-medium leading-relaxed">
              {sub.romanized_text}
            </p>

            {/* Secondary reference: original ASR text */}
            <p className="text-sm text-gray-400 mt-2 leading-relaxed">
              {sub.original_text}
            </p>

            {/* Optional English translation */}
            {showEnglish && sub.english_text && (
              <p className="text-sm text-sky-300 mt-2 italic leading-relaxed">
                {sub.english_text}
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
