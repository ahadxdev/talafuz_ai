// Phase 4 verification — pure subtitleUtils logic under Node.
// Run: node frontend/src/utils/subtitleUtils.test.mjs
import {
  getSubtitleText,
  findActiveSubtitle,
  hasEnglishTranslations,
  resequenceSubtitles,
  splitSubtitleAt,
  mergeSubtitles,
  createSubtitleAfter,
  getActiveWordIndex,
  searchSubtitles,
  formatRulerTime,
  validateSubtitlesForSave,
  shortenSubtitles,
  resegmentSubtitles,
} from "./subtitleUtils.js";

let failed = 0;
function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) failed += 1;
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${ok ? "" : ` — got ${JSON.stringify(actual)}, expected ${JSON.stringify(expected)}`}`);
}

const sub = {
  id: 1, start: 0, end: 4,
  original_text: "अगर मैं AI पढ़ना",
  romanized_text: "Agar main AI parhna",
  english_text: "If I learn AI",
};

check("getSubtitleText romanized", getSubtitleText(sub, "romanized"), "Agar main AI parhna");
check("getSubtitleText english", getSubtitleText(sub, "english"), "If I learn AI");
check("getSubtitleText original", getSubtitleText(sub, "original"), "अगर मैं AI पढ़ना");
check("getSubtitleText missing english", getSubtitleText({ ...sub, english_text: null }, "english"), "");
check("getSubtitleText null", getSubtitleText(null, "romanized"), "");

const subs = [
  sub,
  { id: 2, start: 4, end: 8, original_text: "b", romanized_text: "second cue", english_text: null },
];
check("findActiveSubtitle hit", findActiveSubtitle(subs, 2)?.id, 1);
check("findActiveSubtitle gap", findActiveSubtitle(subs, 8.5), null);
check("hasEnglishTranslations mixed", hasEnglishTranslations(subs), true);
check("hasEnglishTranslations none", hasEnglishTranslations([subs[1]]), false);

check("resequenceSubtitles", resequenceSubtitles([{ id: 9 }, { id: 4 }]).map(s => s.id), [1, 2]);

// split at midpoint 2s → ratio .5 of 3 words → 2/1
const [a, b] = splitSubtitleAt(sub, 2);
check("split first", [a.start, a.end, a.romanized_text], [0, 2, "Agar main"]);
check("split second", [b.start, b.end, b.romanized_text, b.english_text], [2, 4, "AI parhna", "learn AI"]);
check("split english", [a.english_text, b.english_text], ["If I", "learn AI"]);
check("split too early", splitSubtitleAt(sub, 0.02), null);
check("split too late", splitSubtitleAt(sub, 3.99), null);

const merged = mergeSubtitles(subs[0], subs[1]);
check("merge timing", [merged.start, merged.end], [0, 8]);
check("merge text", merged.romanized_text, "Agar main AI parhna second cue");
check("merge english null", merged.english_text, "If I learn AI");

const added = createSubtitleAfter(subs[1], 10);
check("createSubtitleAfter", [added.start, added.end, added.romanized_text], [8, 10, ""]);
const firstCue = createSubtitleAfter(null, 10);
check("createSubtitleAfter first", [firstCue.start, firstCue.end], [0, 2]);

// word highlight: 4 words over 0–4s; word timestamps absent → speech-weighted
const words4 = ["Agar", "main", "AI", "parhna"]; // weights 6, 6, 4, 8 — total 24
// at 1.0s (ratio 0.25, target 6) cumulative reaches 6 at word 0 → 6<6 false; 12 → 6<12 → index 1
check("activeWord mid cue", getActiveWordIndex(words4, sub, 1.0), 1);
// at 3.9s (ratio 0.975, target 23.4) cumulative 24 → 23.4<24 → index 3
check("activeWord end of cue", getActiveWordIndex(words4, sub, 3.9), 3);
check("activeWord disabled empty", getActiveWordIndex([], sub, 1), -1);
// weighted: short vs long word — "a" (weight 3) and "extraordinary" (weight 15) over 2s
const skewed = { id: 9, start: 0, end: 2, romanized_text: "a extraordinary", original_text: "x", english_text: null };
// at 1.0s (ratio 0.5, target 9) — 3 → 9<3 false; 18 → 9<18 → index 1
check("activeWord weighted long", getActiveWordIndex(["a", "extraordinary"], skewed, 1.0), 1);
// at 0.2s (ratio 0.1, target 1.8) — 3 → 1.8<3 → index 0
check("activeWord weighted short", getActiveWordIndex(["a", "extraordinary"], skewed, 0.2), 0);
// with real word timestamps (future ASR) they take precedence
const timed = { ...sub, words: [{ start: 0, end: 1 }, { start: 1, end: 2 }, { start: 2, end: 3 }, { start: 3, end: 4 }] };
check("activeWord real timestamps", getActiveWordIndex(words4, timed, 2.5), 2);

check("searchSubtitles hit", [...searchSubtitles(subs, "ai")], [1]);
check("searchSubtitles case", [...searchSubtitles(subs, "SECOND")], [2]);
check("searchSubtitles none", searchSubtitles(subs, "zzz"), new Set());
check("searchSubtitles empty", searchSubtitles(subs, "  "), null);

check("formatRulerTime", [formatRulerTime(5), formatRulerTime(72)], ["0:05", "1:12"]);

check("validate ok", validateSubtitlesForSave(subs), null);
check("validate empty text", validateSubtitlesForSave([{ id: 1, start: 0, end: 1, romanized_text: "" }]), "Subtitle #1: romanized text cannot be empty.");
check("validate bad timing", validateSubtitlesForSave([{ id: 2, start: 5, end: 5, romanized_text: "x" }]), "Subtitle #2: end time must be after start time.");

// shortenSubtitles: 12 romanized words over 0–3.2s → 3 chunks of ≤4 words,
// timing now weighted by estimated speaking time (word length + punct pauses).
// Word weights: Agar=6 main=6 AI=4 parhna=8 chahta=8 hoon=6 to=4 kya=5 karna=7 chahiye=9 batao=7 zara=6 → total 76.
// Chunk weights: 6+6+4+8=24, 8+6+4+5=23, 7+9+7+6=29.
// Boundaries: 3.2*24/76≈1.011, 3.2*47/76≈1.979, 3.2.
const longCue = {
  id: 1, start: 0, end: 3.2,
  original_text: "अगर मैं AI पढ़ना चाहता हूँ",
  romanized_text: "Agar main AI parhna chahta hoon to kya karna chahiye batao zara",
  english_text: "If I want to learn AI what should I do tell me",
};
const shortened = shortenSubtitles([longCue]);
check("shorten count", shortened.length, 3);
check("shorten texts", shortened.map((s) => s.romanized_text), [
  "Agar main AI parhna",
  "chahta hoon to kya",
  "karna chahiye batao zara",
]);
check("shorten timing", shortened.map((s) => [s.start, s.end]), [
  [0, 1.011],
  [1.011, 1.979],
  [1.979, 3.2],
]);
check("shorten contiguous", shortened.every((s, i) => i === 0 || s.start === shortened[i - 1].end), true);
check("shorten english pieces", shortened.map((s) => s.english_text), [
  "If I want to",
  "learn AI what should",
  "I do tell me",
]);
check("shorten resequence ids", shortenSubtitles([longCue, { ...subs[1], id: 9 }]).map((s) => s.id), [1, 2, 3, 4]);

// Already-short cues pass through untouched.
check("shorten keeps short cue", shortenSubtitles([{ id: 1, start: 4, end: 6, romanized_text: "hello world", original_text: "x", english_text: null }]).length, 1);

// Fast cues merge pieces to respect the minimum duration: 10 words in 0.8s
// would give 3 pieces of 0.27s → the smallest pair merges → 2 pieces.
// Weights: one=5 two=5 three=7 four=6 | five=6 six=5 seven=7 eight=7 nine=6 ten=5
// chunk1=23, chunk2=36; boundary 0.8*23/59≈0.312.
const fastCue = {
  id: 1, start: 0, end: 0.8,
  original_text: "x", romanized_text: "one two three four five six seven eight nine ten", english_text: null,
};
const fastened = shortenSubtitles([fastCue]);
check("shorten min duration", fastened.map((s) => s.romanized_text), [
  "one two three four",
  "five six seven eight nine ten",
]);
check("shorten fast timing", fastened.map((s) => [s.start, s.end]), [[0, 0.312], [0.312, 0.8]]);

// resegmentSubtitles — word mode: one word per caption.
const worded = resegmentSubtitles([longCue], "word");
check("resegment word count", worded.length, 12);
check("resegment word first", worded[0].romanized_text, "Agar");
check("resegment word last", worded[worded.length - 1].romanized_text, "zara");
// Word boundary: Agar (weight 6) → 3.2*6/76≈0.253
check("resegment word first boundary", [worded[0].start, worded[0].end], [0, 0.253]);

// resegmentSubtitles — short mode: 3–5 words per caption.
const shorted = resegmentSubtitles([longCue], "short");
check("resegment short count", shorted.length, 3);
check("resegment short first", shorted[0].romanized_text, "Agar main AI parhna chahta");
check("resegment short last", shorted[shorted.length - 1].romanized_text, "batao zara");

// resegmentSubtitles — sentence mode: no sentence-ending punct in longCue,
// 12 words < 14, text 63 chars < 84 → merged into one cue.
const sentenced = resegmentSubtitles([longCue], "sentence");
check("resegment sentence count", sentenced.length, 1);
check("resegment sentence timing", [sentenced[0].start, sentenced[0].end], [0, 3.2]);

// Sentence mode merges at sentence-ending punctuation.
const multiCues = [
  { id: 1, start: 0, end: 1, romanized_text: "yeh kaam ho gaya.", original_text: "x", english_text: "this work is done." },
  { id: 2, start: 1, end: 2, romanized_text: "ab agla step", original_text: "y", english_text: "now the next step" },
  { id: 3, start: 2, end: 3, romanized_text: "shuru karte hain.", original_text: "z", english_text: "we start." },
];
const sentenced2 = resegmentSubtitles(multiCues, "sentence");
check("resegment sentence multi cue 1", sentenced2[0].romanized_text, "yeh kaam ho gaya.");
check("resegment sentence multi cue 2", sentenced2[1].romanized_text, "ab agla step shuru karte hain.");

if (failed) {
  console.log(`\n${failed} CHECK(S) FAILED`);
  process.exit(1);
}
console.log("\nALL UTIL CHECKS PASSED");
