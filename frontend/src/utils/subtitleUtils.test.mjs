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

// word highlight: 4 words over 0–4s; word timestamps absent → even distribution
const words4 = ["Agar", "main", "AI", "parhna"];
check("activeWord mid cue", getActiveWordIndex(words4, sub, 1.0), 1);
check("activeWord end of cue", getActiveWordIndex(words4, sub, 3.9), 3);
check("activeWord disabled empty", getActiveWordIndex([], sub, 1), -1);
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

if (failed) {
  console.log(`\n${failed} CHECK(S) FAILED`);
  process.exit(1);
}
console.log("\nALL UTIL CHECKS PASSED");
