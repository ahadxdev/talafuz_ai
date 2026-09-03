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

/**
 * Estimated speaking-time weight for one word: longer words take longer
 * to say, and trailing sentence/clause punctuation adds a natural pause.
 * Mirrors _speech_weight() in the backend video export so the preview
 * highlight, cue splitting and burn-in all use identical timing.
 */
function speechWeight(word) {
  let weight = Math.max(word.length, 1) + 2;
  if (/[.!…]$/.test(word)) weight += 5;
  else if (/[,;:—–]$/.test(word)) weight += 3;
  return weight;
}

/**
 * Distribute a text field's words across `chunkCount` pieces, proportional
 * to each chunk's slot (some pieces can be empty for short fields).
 */
function splitFieldIntoChunks(text, chunkCount) {
  const trimmed = (text || "").trim();
  if (chunkCount <= 1) return [trimmed];
  const words = trimmed ? trimmed.split(/\s+/).filter(Boolean) : [];
  if (words.length === 0) return Array(chunkCount).fill("");
  const pieces = [];
  for (let k = 0; k < chunkCount; k += 1) {
    const from = Math.round((words.length * k) / chunkCount);
    const to =
      k === chunkCount - 1
        ? words.length
        : Math.round((words.length * (k + 1)) / chunkCount);
    pieces.push(words.slice(from, to).join(" "));
  }
  return pieces;
}

/**
 * Re-segment captions into short, creator-style cues: every cue is split
 * into word groups of at most `maxWords` words (and roughly `maxChars`
 * characters), with the timing distributed in proportion to each piece's
 * estimated speaking time (word length + punctuation pauses) — the same
 * speech-weighted estimate the word-highlight preview uses.
 *
 * Pieces shorter than `minDuration` are merged with their smallest
 * neighbour so fast cues don't collapse into unreadable flashes. All
 * language fields are split proportionally by their own word counts;
 * already-short cues are left untouched.
 */
export function shortenSubtitles(
  subtitles,
  { maxWords = 4, maxChars = 28, minDuration = 0.3 } = {}
) {
  const out = [];
  for (const sub of subtitles || []) {
    const words = (sub.romanized_text || "").trim().split(/\s+/).filter(Boolean);
    const isShort =
      words.length <= maxWords &&
      words.join(" ").length <= maxChars;
    if (isShort || words.length === 0) {
      out.push(sub);
      continue;
    }

    // Greedy word grouping.
    const chunks = [];
    let current = [];
    for (const word of words) {
      const next = current.length ? [...current, word] : [word];
      if (
        current.length &&
        (next.length > maxWords || next.join(" ").length > maxChars)
      ) {
        chunks.push(current);
        current = [word];
      } else {
        current = next;
      }
    }
    if (current.length) chunks.push(current);

    // Merge the smallest adjacent groups while pieces would be too short.
    const duration = sub.end - sub.start;
    while (chunks.length > 1 && duration / chunks.length < minDuration) {
      let best = 0;
      let bestSize = Infinity;
      for (let i = 0; i < chunks.length - 1; i += 1) {
        const size = chunks[i].length + chunks[i + 1].length;
        if (size < bestSize) {
          bestSize = size;
          best = i;
        }
      }
      chunks.splice(best, 2, [...chunks[best], ...chunks[best + 1]]);
    }

    if (chunks.length <= 1) {
      out.push(sub);
      continue;
    }

    // Timing: boundaries proportional to the cumulative estimated speaking
    // time of each chunk's words, so cue splits and word highlighting stay
    // in sync with each other.
    const romPieces = chunks.map((c) => c.join(" "));
    const engPieces = splitFieldIntoChunks(sub.english_text, chunks.length);
    const origPieces = splitFieldIntoChunks(sub.original_text, chunks.length);
    const weights = words.map(speechWeight);
    const totalWeight = weights.reduce((sum, w) => sum + w, 0);
    const round3 = (v) => Number(v.toFixed(3));
    let weightCursor = 0;
    let wordCursor = 0;
    let start = sub.start;
    for (let i = 0; i < chunks.length; i += 1) {
      const isLast = i === chunks.length - 1;
      for (let k = 0; k < chunks[i].length; k += 1) {
        weightCursor += weights[wordCursor];
        wordCursor += 1;
      }
      const boundary = isLast
        ? sub.end
        : sub.start + (duration * weightCursor) / totalWeight;
      out.push({
        ...sub,
        start: round3(start),
        end: round3(boundary),
        romanized_text: romPieces[i],
        english_text: engPieces[i] || null,
        original_text: origPieces[i],
        words: undefined, // word timestamps no longer line up after splitting
      });
      start = boundary;
    }
  }
  return resequenceSubtitles(out);
}

/** Length ceilings for "sentence" re-segmentation mode. */
const SENTENCE_MAX_WORDS = 14;
const SENTENCE_MAX_CHARS = 84;

/**
 * Merge short cues back into sentence-length cues: cues accumulate until
 * the romanized text ends a sentence (. ! ? … ।) or the group hits the
 * length ceiling. All language fields merge together.
 */
function mergeIntoSentences(subtitles) {
  const out = [];
  let group = null;
  for (const sub of subtitles || []) {
    group = group ? mergeSubtitles(group, sub) : { ...sub };
    const text = (group.romanized_text || "").trim();
    const wordCount = text ? text.split(/\s+/).length : 0;
    const endsSentence = /[.!…।]$/.test(
      (sub.romanized_text || "").trim()
    );
    if (
      endsSentence ||
      wordCount >= SENTENCE_MAX_WORDS ||
      text.length >= SENTENCE_MAX_CHARS
    ) {
      out.push(group);
      group = null;
    }
  }
  if (group) out.push(group);
  return resequenceSubtitles(out);
}

/**
 * Re-segment every caption into the requested length mode:
 *   "word"     — one word per caption (pop-caption style)
 *   "short"    — 3–5 word groups (creator style, the default)
 *   "sentence" — full sentence-length cues
 *
 * Timing follows the estimated speaking time of each piece, so the word
 * highlight stays in sync after re-segmentation. Treat the result as one
 * undoable editor step.
 */
export function resegmentSubtitles(subtitles, mode = "sentence") {
  if (mode === "word") {
    return shortenSubtitles(subtitles, { maxWords: 1, minDuration: 0 });
  }
  if (mode === "sentence") {
    return mergeIntoSentences(subtitles);
  }
  return shortenSubtitles(subtitles, { maxWords: 5, maxChars: 42 });
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
 * take precedence. Without them the active word is estimated by spreading
 * the cue across its words in proportion to each word's estimated speaking
 * time — longer words and punctuation pauses hold the highlight longer,
 * matching how the words are actually spoken. This is a display-only
 * estimate that is never persisted or exported as subtitle data.
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
  const ratio = Math.min(Math.max((time - subtitle.start) / span, 0), 1);
  const weights = words.map(speechWeight);
  const total = weights.reduce((sum, w) => sum + w, 0);
  const target = ratio * total;
  let cumulative = 0;
  for (let i = 0; i < words.length; i += 1) {
    cumulative += weights[i];
    if (target < cumulative) return i;
  }
  return words.length - 1;
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
