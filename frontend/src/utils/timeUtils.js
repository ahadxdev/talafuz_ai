/**
 * Time utility functions for subtitle editing.
 *
 * Handles conversion between seconds (internal) and MM:SS.mmm format (display).
 */

/**
 * Convert seconds (float) to timestamp format MM:SS.mmm.
 *
 * @param {number} seconds - Time in seconds (e.g., 0.0, 3.5, 125.75)
 * @returns {string} Formatted timestamp (e.g., "00:03.500", "02:05.750")
 *
 * @example
 * secondsToTimestamp(0.0) // "00:00.000"
 * secondsToTimestamp(3.5) // "00:03.500"
 * secondsToTimestamp(125.75) // "02:05.750"
 */
export function secondsToTimestamp(seconds) {
  // Validate input
  if (typeof seconds !== "number" || !isFinite(seconds) || seconds < 0) {
    seconds = 0;
  }

  // Split into integer and fractional parts
  const totalSeconds = Math.floor(seconds);
  const milliseconds = Math.round((seconds - totalSeconds) * 1000);

  // Calculate minutes and seconds
  const minutes = Math.floor(totalSeconds / 60);
  const secs = totalSeconds % 60;

  // Format: MM:SS.mmm
  return `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}.${String(milliseconds).padStart(3, "0")}`;
}

/**
 * Convert timestamp format MM:SS.mmm to seconds (float).
 *
 * @param {string} timestamp - Formatted timestamp (e.g., "00:03.500")
 * @returns {number} Time in seconds, or null if invalid
 *
 * @example
 * timestampToSeconds("00:00.000") // 0.0
 * timestampToSeconds("00:03.500") // 3.5
 * timestampToSeconds("02:05.750") // 125.75
 * timestampToSeconds("invalid") // null
 */
export function timestampToSeconds(timestamp) {
  if (typeof timestamp !== "string") {
    return null;
  }

  // Match MM:SS.mmm or MM:SS format
  const match = timestamp.match(/^(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?$/);
  if (!match) {
    return null;
  }

  const minutes = parseInt(match[1], 10);
  const seconds = parseInt(match[2], 10);
  const millis = match[3] ? parseInt(match[3].padEnd(3, "0"), 10) : 0;

  // Validate ranges
  if (minutes < 0 || seconds < 0 || seconds > 59 || millis < 0 || millis > 999) {
    return null;
  }

  return minutes * 60 + seconds + millis / 1000;
}

/**
 * Validate that a timestamp is in valid MM:SS.mmm format.
 *
 * @param {string} timestamp - Timestamp to validate
 * @returns {boolean} True if valid, false otherwise
 */
export function isValidTimestamp(timestamp) {
  return timestampToSeconds(timestamp) !== null;
}

/**
 * Format a number as MM:SS (without milliseconds, for display purposes).
 *
 * @param {number} seconds - Time in seconds
 * @returns {string} Formatted as MM:SS
 */
export function secondsToMinutesSeconds(seconds) {
  if (typeof seconds !== "number" || !isFinite(seconds) || seconds < 0) {
    seconds = 0;
  }

  const totalSeconds = Math.floor(seconds);
  const minutes = Math.floor(totalSeconds / 60);
  const secs = totalSeconds % 60;

  return `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}
