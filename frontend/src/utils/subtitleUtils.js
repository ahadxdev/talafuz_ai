/**
 * Pure helpers for the Phase 4 subtitle editor.
 *
 * Subtitles use the backend snake_case shape produced by the romanization
 * pipeline: { id, start, end, original_text, romanized_text, english_text }.
 * The original ASR text is treated as read-only — no editor operation
 * modifies the stored transcript.
 */

export const LANGUAGES = [
  { value: "romanized", label: "Romanized" },
  { value: "english", label: "English" },
  { value: "original", label: "Original" },
];

/** Text shown for a subtitle in the requested display language. */
export function getSubtitleText(subtitle, language) {
  if (!subtitle) return "";
  if (language === "english") return subtitle.english_text || "";
  if (language === "original") return subtitle.original_text || "";
  return subtitle.romanized_text || "";
}

/** The subtitle active at `time` (seconds), or null. */
export function findActiveSubtitle(subtitles, time) {
  if (!Array.isArray(subtitles)) return null;
  return subtitles.find((s) => time >= s.start && time <= s.end) || null;
}

export function hasEnglishTranslations(subtitles) {
  return (subtitles || []).some((s) => s.english_text && s.english_text.trim());
}

/** Renumber ids 1..n keeping order. */
export function resequenceSubtitles(subtitles) {
  return subtitles.map((sub, index) => ({ ...sub, id: index + 1 }));
}

function splitTextAtRatio(text, ratio) {
  const words = (text || "").trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return ["", ""];
  const cut = Math.min(
    words.length - 1,
    Math.max(1, Math.round(words.length * ratio))
  );
  return [words.slice(0, cut).join(" "), words.slice(cut).join(" ")];
}

/**
 * Split a subtitle at `time` seconds.
 * Texts are split at a proportional word position (cue timing is known,
 * word-level ASR timing is not). Returns [first, second] or null when the
 * playhead is not strictly inside the cue.
 */
export function splitSubtitleAt(subtitle, time) {
  if (!subtitle) return null;
  if (time <= subtitle.start + 0.05 || time >= subtitle.end - 0.05) return null;
  const ratio = (time - subtitle.start) / (subtitle.end - subtitle.start);
  const [romA, romB] = splitTextAtRatio(subtitle.romanized_text, ratio);
  const [engA, engB] = splitTextAtRatio(subtitle.english_text, ratio);
  const [origA, origB] = splitTextAtRatio(subtitle.original_text, ratio);
  return [
    {
      ...subtitle,
      end: time,
      romanized_text: romA,
      english_text: engA || null,
      original_text: origA,
    },
    {
      ...subtitle,
      start: time,
      romanized_text: romB,
      english_text: engB || null,
      original_text: origB,
    },
  ];
}

/** Merge two adjacent subtitles into a single cue. */
export function mergeSubtitles(first, second) {
  if (!first || !second) return null;
  const join = (a, b) => {
    const parts = [a, b].filter((v) => v && v.trim());
    return parts.length ? parts.join(" ") : "";
  };
  return {
    ...first,
    start: Math.min(first.start, second.start),
    end: Math.max(first.end, second.end),
    romanized_text: join(first.romanized_text, second.romanized_text),
    english_text: join(first.english_text, second.english_text) || null,
    original_text: join(first.original_text, second.original_text),
  };
}

/** Blank cue inserted after `subtitle` (or at the start when null). */
export function createSubtitleAfter(subtitle, duration) {
  const fallbackEnd = (subtitle ? subtitle.end : 0) + 2;
  const maxEnd =
    Number.isFinite(duration) && duration > 0 ? duration : fallbackEnd;
  const start = subtitle
    ? Math.min(subtitle.end, Math.max(0, maxEnd - 0.2))
    : 0;
  const end = Math.min(start + 2, Math.max(start + 0.2, maxEnd));
  return {
    id: 0,
    start: Number(start.toFixed(3)),
    end: Number(end.toFixed(3)),
    original_text: "",
    romanized_text: "",
    english_text: null,
  };
}

/**
 * Index of the word that should be highlighted at `time`.
 *
 * Real word-level timestamps (`subtitle.words`, a future ASR upgrade) always
 * take precedence. Without them the active word is estimated by distributing
 * the words evenly across the cue — a display-only estimate that is never
 * persisted or exported as subtitle data.
 */
export function getActiveWordIndex(words, subtitle, time) {
  if (!subtitle || !Array.isArray(words) || words.length === 0) return -1;
  const realWords = Array.isArray(subtitle.words) ? subtitle.words : null;
  if (realWords && realWords.length === words.length) {
    for (let i = 0; i < realWords.length; i += 1) {
      const wStart = realWords[i].start ?? subtitle.start;
      const wEnd = realWords[i].end ?? subtitle.end;
      if (time >= wStart && time <= wEnd) return i;
      if (time < wStart) return i > 0 ? i - 1 : 0;
    }
    return realWords.length - 1;
  }
  const span = Math.max(subtitle.end - subtitle.start, 0.001);
  const ratio = Math.min(Math.max((time - subtitle.start) / span, 0), 0.999);
  return Math.floor(ratio * words.length);
}

/** Ids of subtitles matching `query` across any language field, or null. */
export function searchSubtitles(subtitles, query) {
  const q = (query || "").trim().toLowerCase();
  if (!q) return null;
  const matches = new Set();
  for (const sub of subtitles || []) {
    const haystack = [
      sub.romanized_text,
      sub.english_text,
      sub.original_text,
    ]
      .filter(Boolean)
      .join("\n")
      .toLowerCase();
    if (haystack.includes(q)) matches.add(sub.id);
  }
  return matches;
}

/** Short ruler label, e.g. 0:05 / 1:12. */
export function formatRulerTime(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, "0")}`;
}

/** Structural validation before saving to the backend. */
export function validateSubtitlesForSave(subtitles) {
  for (const sub of subtitles || []) {
    if (!(sub.end > sub.start)) {
      return `Subtitle #${sub.id}: end time must be after start time.`;
    }
    if (!(sub.romanized_text || "").trim()) {
      return `Subtitle #${sub.id}: romanized text cannot be empty.`;
    }
  }
  return null;
}
