/**
 * Lightweight Devanagari (Hindi) → Urdu (Perso-Arabic) SCRIPT conversion.
 *
 * This powers the editor's display-only "Urdu" language option: it renders the
 * read-only `original_text` (Devanagari ASR output) in Urdu script. It is a
 * pure, dependency-free, deterministic transliterator — NOT a translation and
 * NOT a rewrite of meaning.
 *
 * Guarantees that keep this isolated from the timing/alignment pipeline:
 *   • Operates ONLY on a string. It never touches subtitle ids, start/end,
 *     `words`, `romanized_text`, or `english_text`.
 *   • Never throws: on any unexpected input it returns the source text so the
 *     caller can fall back to the original Devanagari for that cue.
 *   • Latin letters, ASCII digits and punctuation pass through unchanged, so
 *     code-switched English words and numbers are preserved.
 *
 * Urdu orthography is an abjad: short vowels (ि / ु) are omitted, long vowels
 * and matras map to their Urdu letters, and the final "e/ai" uses bari-ye (ے)
 * while a nasalised ending uses chhoti-ye + noon-ghunna (ی…ں). Nukta forms map
 * to their Perso-Arabic equivalents (क़→ق, ख़→خ, ग़→غ, ज़→ز, ड़→ड़, फ़→ف).
 */

// Urdu letters are declared by code point so the output can never be confused
// with look-alike Arabic forms (e.g. Urdu ک U+06A9 vs Arabic ك U+0643, Urdu
// ہ U+06C1 vs Arabic ه U+0647, Urdu گ U+06AF, ٹ U+0679, ڈ U+0688, ڑ U+0691).
const A = "\u0627"; // ا  alif
const AA = "\u0622"; // آ  alif-madda
const B = "\u0628"; // ب
const P = "\u067E"; // پ
const T = "\u062A"; // ت
const TT = "\u0679"; // ٹ
const J = "\u062C"; // ج
const CH = "\u0686"; // چ
const D = "\u062F"; // د
const DD = "\u0688"; // ڈ
const R = "\u0631"; // ر
const RR = "\u0691"; // ڑ
const S = "\u0633"; // س
const SH = "\u0634"; // ش
const F = "\u0641"; // ف
const Q = "\u0642"; // ق
const K = "\u06A9"; // ک  (Urdu keheh)
const G = "\u06AF"; // گ  (Urdu gaf)
const L = "\u0644"; // ل
const M = "\u0645"; // م
const N = "\u0646"; // ن
const NG = "\u06BA"; // ں  noon-ghunna (nasalisation)
const H = "\u06C1"; // ہ  (Urdu heh-goal — the standalone "h" letter)
const DH = "\u06BE"; // ھ  do-chashmi-he (aspiration only: کھ گھ ٹھ تھ …)
const W = "\u0648"; // و  vao
const Y = "\u06CC"; // ی  chhoti-ye
const YE = "\u06D2"; // ے  bari-ye (word-final -e/-ai)
const KH = "\u062E"; // خ
const GH = "\u063A"; // غ
const Z = "\u0632"; // ز
const DOT = "\u06D4"; // ۔  Urdu full stop

// Devanagari control marks.
const NUKTA = "\u093C"; // combining nukta
const VIRAMA = "\u094D"; // halant / virama
const ANUSVARA = "\u0902"; // ं
const CHANDRA = "\u0901"; // ँ
const VISARGA = "\u0903"; // ः
const E_MATRA = "\u0947"; // े
const AI_MATRA = "\u0948"; // ै
const DANDA = "\u0964"; // ।
const DOUBLE_DANDA = "\u0965"; // ॥

// Independent vowels ("swar").
const VOWELS = {
  "अ": A, // a
  "आ": AA, // aa
  "इ": A, // i
  "ई": A + Y, // ii
  "उ": A, // u
  "ऊ": A + W, // uu
  "ऋ": R, // ri
  "ए": A + Y, // e  (word-initial → ای, e.g. एक → ایک)
  "ऐ": A + Y, // ai
  "ओ": A + W, // o
  "औ": A + W, // au (और → اور)
};

// Dependent vowel signs ("matras"). े / ै are handled specially below (bari vs
// chhoti ye), so they are intentionally not listed here.
const MATRAS = {
  "ा": A, // aa
  "ि": "", // short i — omitted in Urdu
  "ी": Y, // ii
  "ु": "", // short u — omitted in Urdu
  "ू": W, // uu
  "ृ": R, // ri
  "ॆ": Y, // short e
  "ॅ": Y, // candra e
  "ॊ": W, // short o
  "ो": W, // o
  "ॉ": W, // candra o
  "ौ": W, // au
};

// Consonants ("vyanjan"). The inherent schwa is not written in Urdu, so each
// consonant maps to its bare letter. Aspirated stops (ख घ छ झ ठ ढ थ ध फ भ) are
// digraphs built with do-chashmi-he (ھ U+06BE), never heh-goal (ہ U+06C1).
const CONSONANTS = {
  "क": K,
  "ख": K + DH,
  "ग": G,
  "घ": G + DH,
  "ङ": N + G,
  "च": CH,
  "छ": CH + DH,
  "ज": J,
  "झ": J + DH,
  "ञ": N + Y,
  "ट": TT,
  "ठ": TT + DH,
  "ड": DD,
  "ढ": DD + DH,
  "ण": N,
  "त": T,
  "थ": T + DH,
  "द": D,
  "ध": D + DH,
  "न": N,
  "प": P,
  "फ": P + DH,
  "ब": B,
  "भ": B + DH,
  "म": M,
  "य": Y,
  "र": R,
  "ल": L,
  "व": W,
  "श": SH,
  "ष": SH,
  "स": S,
  "ह": H,
};

// Precomposed nukta consonants (U+0958–U+095F) — Perso-Arabic-origin sounds.
const NUKTA_CONSONANTS = {
  "क़": Q, // qa
  "ख़": KH, // kha
  "ग़": GH, // gha
  "ज़": Z, // za
  "ड़": RR, // da
  "ढ़": RR, // rha
  "फ़": F, // fa
  "य़": Y, // yya
};

// Base consonant followed by a combining nukta (U+093C) → same Perso-Arabic
// letter. Handles text that stores the nukta separately from the base.
const NUKTA_BASE = {
  "क": Q,
  "ख": KH,
  "ग": GH,
  "ज": Z,
  "ड": RR,
  "ढ": RR,
  "फ": F,
  "य": Y,
};

/** True when `c` is a Devanagari consonant (used for context-sensitive rules). */
function isDevConsonant(c) {
  if (!c) return false;
  const cp = c.codePointAt(0);
  return (cp >= 0x0915 && cp <= 0x0939) || (cp >= 0x0958 && cp <= 0x095f);
}

/**
 * Convert Devanagari (Hindi) text to Urdu script.
 *
 * @param {string} text  source text (usually `subtitle.original_text`)
 * @returns {string} Urdu-script text; the source text unchanged on any error,
 *   or "" for null/empty input. Latin words, digits and punctuation are kept.
 */
export function devanagariToUrdu(text) {
  if (text == null) return "";
  if (typeof text !== "string") return String(text);
  if (!text) return "";
  try {
    const chars = Array.from(text);
    let out = "";
    for (let i = 0; i < chars.length; i += 1) {
      const ch = chars[i];
      const nx = chars[i + 1];

      // base consonant + combining nukta → single Perso-Arabic letter
      if (nx === NUKTA && NUKTA_BASE[ch] !== undefined) {
        out += NUKTA_BASE[ch];
        i += 1; // consume the nukta
        continue;
      }
      if (ch === NUKTA) continue; // stray nukta — drop
      if (ch === VIRAMA) continue; // halant — Urdu writes no explicit virama

      // nasalisation: noon (ن) before a consonant, else noon-ghunna (ں)
      if (ch === ANUSVARA || ch === CHANDRA) {
        out += isDevConsonant(nx) ? N : NG;
        continue;
      }
      if (ch === VISARGA) {
        out += H;
        continue;
      }

      // e / ai matra: chhoti-ye (ی) when nasalised or medial (में → میں,
      // मेरे → میرے); bari-ye (ے) at a word boundary (है → ہے, के → के,
      // से → से) — i.e. end of string, whitespace, or any non-Devanagari char.
      if (ch === E_MATRA || ch === AI_MATRA) {
        if (nx === ANUSVARA || nx === CHANDRA || isDevConsonant(nx)) {
          out += Y;
        } else {
          out += YE;
        }
        continue;
      }

      // Devanagari digits → ASCII digits (numeric value preserved)
      const cp = ch.codePointAt(0);
      if (cp >= 0x0966 && cp <= 0x096f) {
        out += String(cp - 0x0966);
        continue;
      }
      // danda / double-danda → Urdu full stop
      if (ch === DANDA || ch === DOUBLE_DANDA) {
        out += DOT;
        continue;
      }

      const mapped =
        CONSONANTS[ch] ?? NUKTA_CONSONANTS[ch] ?? MATRAS[ch] ?? VOWELS[ch];
      if (mapped !== undefined) {
        out += mapped;
        continue;
      }

      // Latin letters, ASCII digits, punctuation, spaces, and anything
      // unmapped: pass through unchanged (code-switched English preserved).
      out += ch;
    }
    return out;
  } catch (err) {
    // Never break the UI: fall back to the source text and log clearly.
    // eslint-disable-next-line no-console
    console.warn(
      "[urdu] Devanagari→Urdu conversion failed; falling back to source text.",
      err
    );
    return text;
  }
}

export default devanagariToUrdu;
