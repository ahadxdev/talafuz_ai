import { useState } from "react";
import { secondsToTimestamp, timestampToSeconds, isValidTimestamp } from "../utils/timeUtils";

/**
 * Phase 4 — Individual subtitle item component with editing capability.
 *
 * Displays a single subtitle with times and text, and allows inline editing
 * of all fields with validation.
 */
export function SubtitleItem({
  subtitle,
  isActive = false,
  onEdit = () => {},
  onClick = () => {},
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [editData, setEditData] = useState({
    start: secondsToTimestamp(subtitle.start),
    end: secondsToTimestamp(subtitle.end),
    romanized_text: subtitle.romanized_text,
    english_text: subtitle.english_text || "",
  });
  const [errors, setErrors] = useState({});

  const handleEdit = () => {
    setIsEditing(true);
    setErrors({});
  };

  const handleCancel = () => {
    setIsEditing(false);
    setEditData({
      start: secondsToTimestamp(subtitle.start),
      end: secondsToTimestamp(subtitle.end),
      romanized_text: subtitle.romanized_text,
      english_text: subtitle.english_text || "",
    });
    setErrors({});
  };

  const validateForm = () => {
    const newErrors = {};

    // Validate timestamps
    if (!isValidTimestamp(editData.start)) {
      newErrors.start = "Invalid format (use MM:SS.mmm)";
    }
    if (!isValidTimestamp(editData.end)) {
      newErrors.end = "Invalid format (use MM:SS.mmm)";
    }

    if (!newErrors.start && !newErrors.end) {
      const startSecs = timestampToSeconds(editData.start);
      const endSecs = timestampToSeconds(editData.end);

      if (startSecs < 0) {
        newErrors.start = "Start time cannot be negative";
      }
      if (endSecs < 0) {
        newErrors.end = "End time cannot be negative";
      }
      if (startSecs >= endSecs) {
        newErrors.end = "End must be after start";
      }
    }

    // Validate text
    if (!editData.romanized_text || !editData.romanized_text.trim()) {
      newErrors.romanized_text = "Romanized text cannot be empty";
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSave = () => {
    if (!validateForm()) {
      return;
    }

    const startSecs = timestampToSeconds(editData.start);
    const endSecs = timestampToSeconds(editData.end);

    onEdit({
      id: subtitle.id,
      start: startSecs,
      end: endSecs,
      original_text: subtitle.original_text,
      romanized_text: editData.romanized_text.trim(),
      english_text: editData.english_text.trim() || null,
    });

    setIsEditing(false);
    setErrors({});
  };

  const handleInputChange = (field, value) => {
    setEditData((prev) => ({
      ...prev,
      [field]: value,
    }));
    // Clear error for this field when user starts typing
    if (errors[field]) {
      setErrors((prev) => ({
        ...prev,
        [field]: undefined,
      }));
    }
  };

  if (isEditing) {
    return (
      <li className="bg-gray-800 border border-gray-600 rounded-md p-4 space-y-3">
        <div className="flex justify-between items-center mb-3">
          <span className="text-sm font-mono text-gray-400">#{subtitle.id}</span>
          <div className="flex gap-2">
            <button
              onClick={handleSave}
              className="px-3 py-1 bg-green-600 hover:bg-green-700 text-white text-sm rounded transition"
            >
              Save
            </button>
            <button
              onClick={handleCancel}
              className="px-3 py-1 bg-gray-600 hover:bg-gray-700 text-white text-sm rounded transition"
            >
              Cancel
            </button>
          </div>
        </div>

        {/* Timestamps */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs text-gray-400 mb-1">Start</label>
            <input
              type="text"
              value={editData.start}
              onChange={(e) => handleInputChange("start", e.target.value)}
              placeholder="MM:SS.mmm"
              className={`w-full px-2 py-1 bg-gray-700 text-white rounded text-sm font-mono border ${
                errors.start ? "border-red-500" : "border-gray-600"
              }`}
            />
            {errors.start && (
              <p className="text-xs text-red-400 mt-1">{errors.start}</p>
            )}
          </div>

          <div>
            <label className="block text-xs text-gray-400 mb-1">End</label>
            <input
              type="text"
              value={editData.end}
              onChange={(e) => handleInputChange("end", e.target.value)}
              placeholder="MM:SS.mmm"
              className={`w-full px-2 py-1 bg-gray-700 text-white rounded text-sm font-mono border ${
                errors.end ? "border-red-500" : "border-gray-600"
              }`}
            />
            {errors.end && (
              <p className="text-xs text-red-400 mt-1">{errors.end}</p>
            )}
          </div>
        </div>

        {/* Romanized Text */}
        <div>
          <label className="block text-xs text-gray-400 mb-1">Romanized Text</label>
          <textarea
            value={editData.romanized_text}
            onChange={(e) => handleInputChange("romanized_text", e.target.value)}
            className={`w-full px-2 py-1 bg-gray-700 text-white rounded text-sm border resize-none rows-2 ${
              errors.romanized_text ? "border-red-500" : "border-gray-600"
            }`}
            rows="2"
          />
          {errors.romanized_text && (
            <p className="text-xs text-red-400 mt-1">{errors.romanized_text}</p>
          )}
        </div>

        {/* English Text */}
        <div>
          <label className="block text-xs text-gray-400 mb-1">
            English Text (optional)
          </label>
          <textarea
            value={editData.english_text}
            onChange={(e) => handleInputChange("english_text", e.target.value)}
            className="w-full px-2 py-1 bg-gray-700 text-white rounded text-sm border border-gray-600 resize-none rows-2"
            rows="2"
          />
        </div>
      </li>
    );
  }

  return (
    <li
      onClick={onClick}
      className={`cursor-pointer rounded-md p-4 border transition ${
        isActive
          ? "bg-blue-900/30 border-blue-500 shadow-lg"
          : "bg-gray-900/60 border-gray-700 hover:border-gray-600"
      }`}
    >
      <div className="flex justify-between items-start mb-2">
        <span className="text-xs font-mono text-gray-500">
          #{subtitle.id} · {secondsToTimestamp(subtitle.start)} – {secondsToTimestamp(subtitle.end)}
        </span>
        <button
          onClick={(e) => {
            e.stopPropagation();
            handleEdit();
          }}
          className="px-2 py-1 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded transition"
        >
          Edit
        </button>
      </div>

      {/* Romanized text (primary) */}
      <p className="text-white font-medium leading-relaxed mb-1">
        {subtitle.romanized_text}
      </p>

      {/* Original text (secondary) */}
      <p className="text-xs text-gray-400 leading-relaxed">
        {subtitle.original_text}
      </p>

      {/* English text (optional) */}
      {subtitle.english_text && (
        <p className="text-xs text-sky-300 italic mt-1 leading-relaxed">
          {subtitle.english_text}
        </p>
      )}
    </li>
  );
}
