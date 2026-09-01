/**
 * Phase 4 — Subtitle mode selector component.
 *
 * Allows users to choose between Romanized, English, and Dual subtitle display modes.
 */
export function SubtitleModeSelector({
  mode = "romanized",
  onModeChange = () => {},
  hasEnglish = false,
}) {
  return (
    <div className="space-y-2">
      <label className="block text-sm font-medium text-gray-300 mb-3">
        Subtitle Mode
      </label>
      <div className="flex flex-col gap-2">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="radio"
            name="subtitle-mode"
            value="romanized"
            checked={mode === "romanized"}
            onChange={(e) => onModeChange(e.target.value)}
            className="w-4 h-4 accent-blue-600"
          />
          <span className="text-sm text-gray-300">
            ● Romanized (Roman Urdu/Hindi)
          </span>
        </label>

        {hasEnglish && (
          <>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="radio"
                name="subtitle-mode"
                value="english"
                checked={mode === "english"}
                onChange={(e) => onModeChange(e.target.value)}
                className="w-4 h-4 accent-blue-600"
              />
              <span className="text-sm text-gray-300">● English</span>
            </label>

            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="radio"
                name="subtitle-mode"
                value="dual"
                checked={mode === "dual"}
                onChange={(e) => onModeChange(e.target.value)}
                className="w-4 h-4 accent-blue-600"
              />
              <span className="text-sm text-gray-300">● Dual (Romanized + English)</span>
            </label>
          </>
        )}
      </div>
    </div>
  );
}
