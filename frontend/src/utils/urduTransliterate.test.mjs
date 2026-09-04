// Focused tests for the display-only Urdu script conversion (task STEP 8).
// Run: node frontend/src/utils/urduTransliterate.test.mjs
//
// Scope is intentionally narrow: it proves the new "Urdu" option converts
// Devanagari → Urdu script correctly and, critically, that it is ISOLATED from
// the timing/alignment pipeline — ids, cue start/end, word timings, Romanized
// output and English output are all asserted unchanged.
import { devanagariToUrdu } from "./urduTransliterate.js";
import {
  LANGUAGES,
  getSubtitleText,
  getActiveWordIndex,
} from "./subtitleUtils.js";

let failed = 0;
function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) failed += 1;
  console.log(
    `${ok ? "PASS" : "FAIL"} ${name}${ok ? "" : ` — got ${JSON.stringify(actual)}, expected ${JSON.stringify(expected)}`}`
  );
}

// (1) Hindi → Urdu conversion — the user's canonical examples + core rules.
check("convert example 1", devanagariToUrdu("अगर आप पाकिस्तान में हैं"), "اگر آپ پاکستان میں ہیں");
check("convert example 2", devanagariToUrdu("मैं पाकिस्तान में रहता हूँ"), "میں پاکستان میں رہتا ہوں");
check("convert aspiration (do-chashmi-he)", devanagariToUrdu("ठीक"), "ٹھیک");
check("convert aspirated set", devanagariToUrdu("घर खाना भी"), "گھر کھانا بھی");
check("convert nasal (anusvara→noon)", devanagariToUrdu("इंसान"), "انسان");

// (3) Numbers — Devanagari digits become ASCII; ASCII digits pass through.
check("convert devanagari digits", devanagariToUrdu("२०२६"), "2026");
check("convert mixed digits", devanagariToUrdu("साल 2026 है"), "سال 2026 ہے");

// (4) Punctuation — danda→Urdu full stop; ASCII punctuation preserved.
check("convert danda", devanagariToUrdu("राम।"), "رام۔");
check("convert exclamation", devanagariToUrdu("नमस्ते!"), "نمستے!");
check("convert comma + danda", devanagariToUrdu("हाँ, ठीक।"), "ہاں, ٹھیک۔");
check("convert question mark", devanagariToUrdu("क्या?"), "کیا?");

// (5) Hindi + English code-switch — Latin words/abbreviations preserved.
check("convert code-switch", devanagariToUrdu("मैं AI और Node.js उपयोग करता हूँ"), "میں AI اور Node.js اپیوگ کرتا ہوں");

// (6) Empty / nullish input.
check("convert empty string", devanagariToUrdu(""), "");
check("convert null", devanagariToUrdu(null), "");
check("convert undefined", devanagariToUrdu(undefined), "");

// (7) Never throws / falls back — adversarial non-string inputs return a string
//     instead of breaking the job (STEP 7: "do not fail the entire job").
let threw = null;
let robustTypes;
try {
  robustTypes = [0, 123, {}, [], true].map((v) => typeof devanagariToUrdu(v));
} catch (err) {
  threw = String(err);
}
check("convert never throws on odd input", threw, null);
check("convert returns string on odd input", robustTypes, ["string", "string", "string", "string", "string"]);
// Pure-Latin cue has nothing to convert → the source is preserved verbatim,
// which is exactly the per-cue "fall back to the original text" behaviour.
check("convert latin-only passthrough", devanagariToUrdu("Node.js"), "Node.js");

// ---------------------------------------------------------------------------
// Fixtures for the cue-level tests (2, 8–12) and the highlight timing test.
// cue[0].words carries real romanized timings that span the whole cue (0→5).
// ---------------------------------------------------------------------------
const cues = [
  {
    id: 1, start: 0.0, end: 5.0,
    original_text: "अगर आप पाकिस्तान में हैं",
    romanized_text: "agar aap pakistan mein hain",
    english_text: "If you are in Pakistan",
    words: [
      { word: "agar", start: 0.0, end: 0.5 },
      { word: "aap", start: 0.5, end: 1.0 },
      { word: "pakistan", start: 1.0, end: 1.4 },
      { word: "mein", start: 1.4, end: 4.0 },
      { word: "hain", start: 4.0, end: 5.0 },
    ],
  },
  {
    id: 2, start: 5.0, end: 7.0,
    original_text: "मैं AI और Node.js उपयोग करता हूँ",
    romanized_text: "main AI aur Node.js upyog karta hoon",
    english_text: "I use AI and Node.js",
    words: null,
  },
  {
    id: 3, start: 7.0, end: 8.0,
    original_text: "हाँ, ठीक।",
    romanized_text: "haan, theek.",
    english_text: "Yes, fine.",
    words: null,
  },
];

// Snapshot the authoritative id/timing fields BEFORE any Urdu display call.
const idsBefore = cues.map((c) => c.id);
const timesBefore = cues.map((c) => [c.start, c.end]);
const wordsBefore = JSON.stringify(cues.map((c) => c.words));

// (2) Multiple cues each convert independently through getSubtitleText.
check("multi-cue urdu [0]", getSubtitleText(cues[0], "urdu"), "اگر آپ پاکستان میں ہیں");
check("multi-cue urdu [1]", getSubtitleText(cues[1], "urdu"), "میں AI اور Node.js اپیوگ کرتا ہوں");
check("multi-cue urdu [2]", getSubtitleText(cues[2], "urdu"), "ہاں, ٹھیک۔");

// (11)(12) Romanized / English / Original display are byte-for-byte unchanged.
check("romanized unchanged", cues.map((c) => getSubtitleText(c, "romanized")), cues.map((c) => c.romanized_text));
check("english unchanged", cues.map((c) => getSubtitleText(c, "english")), cues.map((c) => c.english_text));
check("original unchanged", cues.map((c) => getSubtitleText(c, "original")), cues.map((c) => c.original_text));

// (8)(9)(10) Displaying Urdu never mutates ids, cue times, or word timings.
check("ids unchanged", cues.map((c) => c.id), idsBefore);
check("cue times unchanged", cues.map((c) => [c.start, c.end]), timesBefore);
check("word timings unchanged", JSON.stringify(cues.map((c) => c.words)), wordsBefore);

// ---------------------------------------------------------------------------
// STEP 5 — Selector order: Original | Urdu | Romanized | English.
// ---------------------------------------------------------------------------
check("LANGUAGES values order", LANGUAGES.map((l) => l.value), ["original", "urdu", "romanized", "english"]);
check("LANGUAGES labels order", LANGUAGES.map((l) => l.label), ["Original", "Urdu", "Romanized", "English"]);

// ---------------------------------------------------------------------------
// STEP 6 — Word highlighting REUSES the existing timing; Urdu adds none.
// Romanized tokens line up with subtitle.words → real timings drive the index
// (index 3 at t=3.0, inside "mein" [1.4, 4.0]). Urdu/English tokens differ →
// getActiveWordIndex falls back to the SAME proportional estimate Original and
// English already use. No new Urdu timestamps, no re-alignment.
// ---------------------------------------------------------------------------
const timed = cues[0];
const romanizedTokens = getSubtitleText(timed, "romanized").split(/\s+/).filter(Boolean);
const urduTokens = getSubtitleText(timed, "urdu").split(/\s+/).filter(Boolean);
const englishTokens = getSubtitleText(timed, "english").split(/\s+/).filter(Boolean);
check("romanized uses real word timing", getActiveWordIndex(romanizedTokens, timed, 3.0), 3);
check("urdu reuses estimate fallback", getActiveWordIndex(urduTokens, timed, 3.0), 2);
check("english uses estimate fallback", getActiveWordIndex(englishTokens, timed, 3.0), 3);
// Highlighting Urdu must not mutate the stored timings or add timing fields.
check("word timings intact after urdu highlight", timed.words.length, 5);
check("no urdu timing field added to cue", Object.keys(timed).filter((k) => /urdu/i.test(k)), []);
check("cue span intact after urdu highlight", [timed.start, timed.end], [0.0, 5.0]);

if (failed) {
  console.log(`\n${failed} CHECK(S) FAILED`);
  process.exit(1);
}
console.log("\nALL URDU CHECKS PASSED");
