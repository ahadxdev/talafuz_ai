"""
Tests for offline word-level timing alignment (word_alignment_service) and its
integration into the video exporter and the persisted-subtitle contract.

No network and no whisper model are required:
  * the pure matching / fitting / validation functions are exercised directly;
  * align_subtitle_words() is driven with a synthetic recognised-token stream
    (monkeypatched recognize_word_tokens) so the whole "recognise once → map the
    global word stream onto the final cues" path runs deterministically;
  * the disk cache is exercised against tmp_path.

These cover the binding requirements: words match the whitespace tokens of
romanized_text in order, sit inside [start, end], are strictly increasing and
non-overlapping; a one-word cue maps to the whole window; interior boundaries
default to a proportional character-length split of the VAD-accurate cue window
(the opt-in DTW fit falls back to it per cue when anchors are untrustworthy);
the SAME persisted words drive the FFmpeg exporter; and old jobs without words
still load.
"""
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import config
from app.services import word_alignment_service as was
from app.services import video_export_service as ves
from app.services.word_alignment_service import (
    WordAlignmentError,
    _char_timeline,
    _fit_cue_words,
    _global_word_match,
    _normalize,
    _proportional_word_split,
    _repair_boundaries,
    align_subtitle_words,
    recognize_word_tokens,
    validate_word_timings,
)


EPS = 1e-6


@pytest.fixture(autouse=True)
def _isolate_caches():
    """Keep each test independent of the module-level recognition/model caches."""
    was._token_cache.clear()
    was._model_holder.clear()
    yield
    was._token_cache.clear()
    was._model_holder.clear()


def _sub(start, end, text, words=None):
    return SimpleNamespace(start=start, end=end, romanized_text=text, words=words)


def _assert_valid_words(words, text, start, end):
    """The persisted-word contract every consumer relies on."""
    tokens = text.split()
    assert words is not None, "expected real word timings, got None"
    assert len(words) == len(tokens)
    prev_end = None
    for w, tok in zip(words, tokens):
        assert w["word"] == tok
        assert w["end"] > w["start"]
        assert w["start"] >= start - EPS
        assert w["end"] <= end + EPS
        if prev_end is not None:
            assert w["start"] >= prev_end - EPS, "word timings overlap"
        prev_end = w["end"]


# ---------------------------------------------------------------------------
# 1. Normalization
# ---------------------------------------------------------------------------

def test_normalize_strips_non_alnum_and_lowercases():
    assert _normalize("Agar") == "agar"
    assert _normalize("AI,") == "ai"
    assert _normalize("  hello-world!  ") == "helloworld"
    assert _normalize("") == ""
    assert _normalize(None) == ""


def test_normalize_drops_non_latin_script():
    # Devanagari has no a-z0-9 → empty, so it never spuriously matches.
    assert _normalize("अगर") == ""


# ---------------------------------------------------------------------------
# 2. validate_word_timings — the shared persistence gate
# ---------------------------------------------------------------------------

def test_validate_accepts_wellformed():
    words = [
        {"word": "Agar", "start": 0.0, "end": 1.0},
        {"word": "main", "start": 1.0, "end": 2.0},
    ]
    assert validate_word_timings(words, "Agar main", 0.0, 2.0) == [
        {"word": "Agar", "start": 0.0, "end": 1.0},
        {"word": "main", "start": 1.0, "end": 2.0},
    ]


def test_validate_rounds_to_milliseconds():
    words = [{"word": "x", "start": 0.12345, "end": 0.98765}]
    assert validate_word_timings(words, "x", 0.0, 1.0) == [
        {"word": "x", "start": 0.123, "end": 0.988}
    ]


def test_validate_uses_token_not_raw_word_text():
    # A timing whose word carries stray spaces still matches the token, and the
    # cleaned output uses the canonical token.
    words = [{"word": "  x  ", "start": 0.0, "end": 1.0}]
    assert validate_word_timings(words, "x", 0.0, 1.0) == [
        {"word": "x", "start": 0.0, "end": 1.0}
    ]


def test_validate_rejects_count_mismatch():
    words = [{"word": "Agar", "start": 0.0, "end": 1.0}]
    assert validate_word_timings(words, "Agar main", 0.0, 2.0) is None


def test_validate_rejects_word_text_mismatch():
    words = [
        {"word": "Agar", "start": 0.0, "end": 1.0},
        {"word": "WRONG", "start": 1.0, "end": 2.0},
    ]
    assert validate_word_timings(words, "Agar main", 0.0, 2.0) is None


def test_validate_rejects_zero_or_negative_duration():
    assert validate_word_timings([{"word": "x", "start": 1.0, "end": 1.0}], "x", 0.0, 2.0) is None
    assert validate_word_timings([{"word": "x", "start": 1.0, "end": 0.5}], "x", 0.0, 2.0) is None


def test_validate_rejects_words_outside_cue_window():
    assert validate_word_timings([{"word": "x", "start": -0.5, "end": 1.0}], "x", 0.0, 2.0) is None
    assert validate_word_timings([{"word": "x", "start": 0.0, "end": 3.0}], "x", 0.0, 2.0) is None


def test_validate_rejects_overlap():
    words = [
        {"word": "a", "start": 0.0, "end": 1.5},
        {"word": "b", "start": 1.0, "end": 2.0},
    ]
    assert validate_word_timings(words, "a b", 0.0, 2.0) is None


def test_validate_rejects_non_monotonic_order():
    words = [
        {"word": "a", "start": 1.0, "end": 2.0},
        {"word": "b", "start": 0.0, "end": 1.0},
    ]
    assert validate_word_timings(words, "a b", 0.0, 2.0) is None


def test_validate_rejects_empty_and_non_list():
    assert validate_word_timings([], "x", 0.0, 1.0) is None
    assert validate_word_timings(None, "x", 0.0, 1.0) is None


def test_validate_rejects_non_dict_entry():
    assert validate_word_timings(["x"], "x", 0.0, 1.0) is None


def test_validate_rejects_non_numeric_word_times():
    assert validate_word_timings([{"word": "x", "start": "a", "end": 1.0}], "x", 0.0, 1.0) is None
    assert validate_word_timings([{"word": "x", "start": 0.0}], "x", 0.0, 1.0) is None


def test_validate_rejects_non_numeric_cue_window():
    words = [{"word": "x", "start": 0.0, "end": 1.0}]
    assert validate_word_timings(words, "x", "a", 1.0) is None


# ---------------------------------------------------------------------------
# 3. _char_timeline — recognised tokens → per-character acoustic timeline
# ---------------------------------------------------------------------------

def test_char_timeline_interpolates_within_token():
    chars, times = _char_timeline([{"w": "ab", "s": 0.0, "e": 1.0}])
    assert chars == "ab"
    assert times[0] == pytest.approx((0.0, 0.5))
    assert times[1] == pytest.approx((0.5, 1.0))


def test_char_timeline_normalizes_and_concatenates():
    chars, times = _char_timeline([
        {"w": "A1!", "s": 0.0, "e": 0.3},
        {"w": "b", "s": 0.3, "e": 0.6},
    ])
    assert chars == "a1b"  # punctuation dropped, tokens joined
    assert len(times) == 3


def test_char_timeline_skips_tokens_without_alnum():
    chars, times = _char_timeline([
        {"w": "!!", "s": 0.0, "e": 1.0},
        {"w": "hi", "s": 1.0, "e": 2.0},
    ])
    assert chars == "hi"
    assert len(times) == 2


def test_char_timeline_clamps_inverted_span():
    chars, times = _char_timeline([{"w": "ab", "s": 1.0, "e": 0.0}])
    assert chars == "ab"
    # e < s collapses to a zero-length span at s.
    assert times[0] == (1.0, 1.0)
    assert times[1] == (1.0, 1.0)


# ---------------------------------------------------------------------------
# 4. _global_word_match — one monotonic alignment across the whole job
# ---------------------------------------------------------------------------

def test_global_word_match_exact_stream():
    chars, times = _char_timeline([
        {"w": "agar", "s": 0.0, "e": 1.0},
        {"w": "main", "s": 1.0, "e": 2.0},
    ])
    matched = _global_word_match(["Agar", "main"], chars, times)
    assert matched[0] is not None and matched[1] is not None
    assert matched[0][0] == pytest.approx(0.0)
    assert matched[1][1] == pytest.approx(2.0)


def test_global_word_match_unmatched_word_is_none():
    chars, times = _char_timeline([{"w": "zzzz", "s": 0.0, "e": 1.0}])
    assert _global_word_match(["agar"], chars, times) == [None]


def test_global_word_match_empty_streams():
    assert _global_word_match(["agar"], "", []) == [None]
    assert _global_word_match([], "abc", [(0, 1), (1, 2), (2, 3)]) == []


def test_global_word_match_coverage_threshold():
    chars, times = _char_timeline([{"w": "ab", "s": 0.0, "e": 1.0}])
    # 2/5 = 0.4 < 0.5 coverage → unmatched.
    assert _global_word_match(["abcde"], chars, times) == [None]
    # 2/4 = 0.5 coverage → matched (the threshold is inclusive).
    assert _global_word_match(["abcd"], chars, times)[0] is not None


# ---------------------------------------------------------------------------
# 5. _repair_boundaries — contiguous, monotonic, inside the cue
# ---------------------------------------------------------------------------

def test_repair_boundaries_endpoints_and_monotonic():
    b = _repair_boundaries([1.0, None, 3.0], 1.0, 4.0, 3)
    assert len(b) == 4
    assert b[0] == pytest.approx(1.0)
    assert b[-1] == pytest.approx(4.0)
    for i in range(1, len(b)):
        assert b[i] > b[i - 1]


def test_repair_boundaries_interpolates_missing():
    assert _repair_boundaries([0.0, None, None, 3.0], 0.0, 3.0, 3) == pytest.approx(
        [0.0, 1.0, 2.0, 3.0]
    )


def test_repair_boundaries_clamps_out_of_range():
    b = _repair_boundaries([0.0, 99.0, -5.0, 4.0], 0.0, 4.0, 3)
    assert b[0] == pytest.approx(0.0)
    assert b[-1] == pytest.approx(4.0)
    for i in range(1, len(b)):
        assert b[i] > b[i - 1]
    for x in b:
        assert -EPS <= x <= 4.0 + EPS


# ---------------------------------------------------------------------------
# 6. _fit_cue_words — acoustic anchors → one cue window, with the quality gate
# ---------------------------------------------------------------------------

def test_fit_single_word_maps_whole_cue():
    assert _fit_cue_words(["Hello"], [(0.0, 1.0)], 2.0, 5.0, 0.6) == [
        {"word": "Hello", "start": 2.0, "end": 5.0}
    ]


def test_fit_single_word_without_anchor_still_maps_whole_cue():
    # A one-word cue needs no acoustic anchor — it always spans the window.
    assert _fit_cue_words(["Hello"], [None], 2.0, 5.0, 0.6) == [
        {"word": "Hello", "start": 2.0, "end": 5.0}
    ]


def test_fit_empty_returns_none():
    assert _fit_cue_words([], [], 0.0, 1.0, 0.6) is None


def test_fit_no_matched_falls_back_to_proportional():
    # Default (proportional) method ignores the anchors entirely, so a cue with
    # no matched anchors still gets a character-length split of its window.
    out = _fit_cue_words(["a", "b"], [None, None], 0.0, 2.0, 0.6)
    assert out == [
        {"word": "a", "start": 0.0, "end": 1.0},
        {"word": "b", "start": 1.0, "end": 2.0},
    ]


def test_fit_below_quality_gate_falls_back_to_proportional():
    # Only 1 of 4 words matched (0.25 < 0.6). The default proportional method
    # does not gate on coverage — it splits the window by character length, so
    # equal-length words get equal quarters instead of None.
    matched = [(0.0, 0.5), None, None, None]
    out = _fit_cue_words(["a", "b", "c", "d"], matched, 0.0, 4.0, 0.6)
    assert [w["word"] for w in out] == ["a", "b", "c", "d"]
    assert out[0]["start"] == pytest.approx(0.0)
    assert out[-1]["end"] == pytest.approx(4.0)
    for w in out:
        assert w["end"] - w["start"] == pytest.approx(1.0)


def test_fit_zero_span_returns_none():
    assert _fit_cue_words(["a", "b"], [(0.0, 1.0), (1.0, 2.0)], 2.0, 2.0, 0.6) is None


def test_fit_produces_contiguous_valid_timings():
    matched = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]
    out = _fit_cue_words(["a", "b", "c"], matched, 10.0, 13.0, 0.6)
    assert [w["word"] for w in out] == ["a", "b", "c"]
    assert out[0]["start"] == pytest.approx(10.0)
    assert out[-1]["end"] == pytest.approx(13.0)
    for i, w in enumerate(out):
        assert w["end"] > w["start"]
        if i:
            assert w["start"] >= out[i - 1]["end"] - EPS
    # The fit already satisfies the shared persistence validator.
    assert validate_word_timings(out, "a b c", 10.0, 13.0) is not None


def test_fit_interpolates_unmatched_between_anchors():
    # First and last matched, middle word interpolated — still contiguous.
    matched = [(0.0, 1.0), None, (2.0, 3.0)]
    out = _fit_cue_words(["a", "b", "c"], matched, 0.0, 3.0, 0.6)
    assert out is not None
    _assert_valid_words(out, "a b c", 0.0, 3.0)


# ---------------------------------------------------------------------------
# 6b. Interior split method — proportional default + opt-in DTW fallback
# ---------------------------------------------------------------------------

def test_proportional_split_even_for_equal_length_words():
    # The hackathon default: equal-length words tile the cue evenly, so no word
    # is squeezed to a near-zero slot by a misheard DTW anchor (Agar/main/2026
    # over 500 ms → ~166 ms each, per the task example).
    out = _fit_cue_words(["Agar", "main", "2026"], [None, None, None], 0.0, 0.5, 0.6)
    assert [w["word"] for w in out] == ["Agar", "main", "2026"]
    assert out[0]["start"] == pytest.approx(0.0)
    assert out[-1]["end"] == pytest.approx(0.5)
    for w in out:
        assert w["end"] - w["start"] == pytest.approx(0.5 / 3, abs=1e-3)


def test_proportional_split_weights_by_character_length():
    # "a"(1 char) vs "bcd"(3 chars) over 0.4 s → 0.1 s / 0.3 s (1:3 by length).
    out = _fit_cue_words(["a", "bcd"], [None, None], 0.0, 0.4, 0.6)
    assert out[0]["end"] - out[0]["start"] == pytest.approx(0.1)
    assert out[1]["end"] - out[1]["start"] == pytest.approx(0.3)


def test_proportional_split_tiles_cue_exactly():
    # The Part 5 structural guarantee (0 gaps / 0 overlaps, endpoints exact)
    # must hold for the proportional split too.
    words = ["Agar", "main", "2026", "mein", "IAML"]
    out = _fit_cue_words(words, [None] * 5, 1.0, 2.5, 0.6)
    assert out[0]["start"] == pytest.approx(1.0)
    assert out[-1]["end"] == pytest.approx(2.5)
    for i in range(1, len(out)):
        assert out[i]["start"] == pytest.approx(out[i - 1]["end"])
        assert out[i]["end"] > out[i]["start"]
    assert validate_word_timings(out, " ".join(words), 1.0, 2.5) is not None


def test_proportional_split_ignores_dtw_anchors():
    # Even with anchors present, the DEFAULT method does not use them: the split
    # depends only on character length and the cue window (anchors that would
    # squeeze word 1 to ~10 ms are ignored).
    anchored = [(0.0, 0.05), (0.05, 0.06), (0.06, 1.0)]
    out = _fit_cue_words(["aaa", "bbb", "ccc"], anchored, 0.0, 1.0, 0.6)
    for w in out:
        assert w["end"] - w["start"] == pytest.approx(1.0 / 3, abs=1e-3)


def test_proportional_helper_matches_fit_and_floors_short_words():
    # The helper is what _fit_cue_words delegates to; a very short cue still
    # respects the MIN_WORD_DURATION floor via _repair_boundaries.
    direct = _proportional_word_split(["a", "b"], 0.0, 0.03)
    assert direct[0]["start"] == pytest.approx(0.0)
    assert direct[-1]["end"] == pytest.approx(0.03)
    assert all(w["end"] > w["start"] for w in direct)
    assert _proportional_word_split([], 0.0, 1.0) is None
    assert _proportional_word_split(["a", "b"], 1.0, 1.0) is None


def test_dtw_mode_uses_anchors_when_confident(monkeypatch):
    # Opt-in DTW: with full coverage and no squeezed word, the acoustic anchors
    # drive the interior boundary — word "a" spans 0→2 (proportional would give
    # 0→1.333), proving the DTW path is actually exercised.
    monkeypatch.setattr(config, "WORD_ALIGNMENT_METHOD", "dtw")
    monkeypatch.setattr(config, "SUBTITLE_DTW_ALIGNMENT_ENABLED", True)
    matched = [(0.0, 2.0), (2.0, 3.0), (3.0, 4.0)]
    out = _fit_cue_words(["a", "b", "c"], matched, 0.0, 4.0, 0.6)
    _assert_valid_words(out, "a b c", 0.0, 4.0)
    assert out[0]["end"] == pytest.approx(2.0)
    assert out[1]["end"] == pytest.approx(3.0)


def test_dtw_mode_falls_back_when_a_word_is_squeezed(monkeypatch):
    # Opt-in DTW: a misheard fit that squeezes a word under the 40 ms trust
    # floor falls back to the proportional split for that cue (even thirds).
    monkeypatch.setattr(config, "WORD_ALIGNMENT_METHOD", "dtw")
    monkeypatch.setattr(config, "SUBTITLE_DTW_ALIGNMENT_ENABLED", True)
    monkeypatch.setattr(config, "WORD_ALIGNMENT_MIN_TRUSTED_WORD", 0.04)
    matched = [(0.0, 0.01), (0.01, 0.02), (0.02, 1.0)]
    out = _fit_cue_words(["aaa", "bbb", "ccc"], matched, 0.0, 1.0, 0.6)
    for w in out:
        assert w["end"] - w["start"] == pytest.approx(1.0 / 3, abs=1e-3)


def test_dtw_mode_falls_back_below_coverage_gate(monkeypatch):
    # Opt-in DTW: coverage below the gate no longer yields None — it falls back
    # to the proportional split so the cue still gets sane interior timings.
    monkeypatch.setattr(config, "WORD_ALIGNMENT_METHOD", "dtw")
    monkeypatch.setattr(config, "SUBTITLE_DTW_ALIGNMENT_ENABLED", True)
    matched = [(0.0, 0.5), None, None, None]  # 1/4 = 0.25 < 0.6
    out = _fit_cue_words(["a", "b", "c", "d"], matched, 0.0, 4.0, 0.6)
    assert [w["word"] for w in out] == ["a", "b", "c", "d"]
    for w in out:
        assert w["end"] - w["start"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 7. align_subtitle_words — recognise once, map the global stream to the cues
# ---------------------------------------------------------------------------

def _patch_tokens(monkeypatch, tokens):
    monkeypatch.setattr(
        was, "recognize_word_tokens",
        lambda audio_path, prompt_text="": list(tokens),
    )


def test_align_disabled_attaches_nothing(monkeypatch):
    monkeypatch.setattr(config, "WORD_ALIGNMENT_ENABLED", False)
    _patch_tokens(monkeypatch, [
        {"w": "Agar", "s": 0.0, "e": 1.0}, {"w": "main", "s": 1.0, "e": 2.0},
    ])
    subs = [_sub(0.0, 2.0, "Agar main")]
    assert align_subtitle_words(subs, "audio.wav") == 0
    assert subs[0].words is None


def test_align_without_audio_path_attaches_nothing(monkeypatch):
    monkeypatch.setattr(config, "WORD_ALIGNMENT_ENABLED", True)
    subs = [_sub(0.0, 2.0, "Agar main")]
    assert align_subtitle_words(subs, None) == 0
    assert subs[0].words is None


def test_align_empty_subtitles(monkeypatch):
    monkeypatch.setattr(config, "WORD_ALIGNMENT_ENABLED", True)
    assert align_subtitle_words([], "audio.wav") == 0


def test_align_no_tokens_attaches_nothing(monkeypatch):
    # DTW path: no recognised tokens → nothing is attached. (The default audio
    # path does not depend on whisper, so this token gate is DTW-specific.)
    monkeypatch.setattr(config, "WORD_ALIGNMENT_ENABLED", True)
    monkeypatch.setattr(config, "WORD_ALIGNMENT_METHOD", "dtw")
    monkeypatch.setattr(config, "SUBTITLE_DTW_ALIGNMENT_ENABLED", True)
    _patch_tokens(monkeypatch, [])
    subs = [_sub(0.0, 2.0, "Agar main")]
    assert align_subtitle_words(subs, "audio.wav") == 0
    assert subs[0].words is None


def test_align_attaches_valid_words(monkeypatch):
    monkeypatch.setattr(config, "WORD_ALIGNMENT_ENABLED", True)
    monkeypatch.setattr(config, "WORD_ALIGNMENT_MIN_MATCH", 0.6)
    _patch_tokens(monkeypatch, [
        {"w": "Agar", "s": 0.0, "e": 1.0}, {"w": "main", "s": 1.0, "e": 2.0},
        {"w": "AI", "s": 2.0, "e": 3.0}, {"w": "parhna", "s": 3.0, "e": 4.0},
    ])
    subs = [_sub(0.0, 2.0, "Agar main"), _sub(2.0, 4.0, "AI parhna")]
    assert align_subtitle_words(subs, "audio.wav") == 2
    for s in subs:
        _assert_valid_words(s.words, s.romanized_text, s.start, s.end)


def test_align_maps_drifted_global_stream_into_each_cue(monkeypatch):
    # The whisper timeline is offset ~100 s from the cue timeline; the global
    # affine fit still lands every word inside its own cue window.
    monkeypatch.setattr(config, "WORD_ALIGNMENT_ENABLED", True)
    monkeypatch.setattr(config, "WORD_ALIGNMENT_MIN_MATCH", 0.6)
    _patch_tokens(monkeypatch, [
        {"w": "one", "s": 100.0, "e": 101.0}, {"w": "two", "s": 101.0, "e": 102.0},
        {"w": "three", "s": 102.0, "e": 103.0}, {"w": "four", "s": 103.0, "e": 104.0},
    ])
    subs = [_sub(0.0, 2.0, "one two"), _sub(2.0, 4.0, "three four")]
    assert align_subtitle_words(subs, "audio.wav") == 2
    for s in subs:
        _assert_valid_words(s.words, s.romanized_text, s.start, s.end)
    assert subs[0].words[0]["start"] == pytest.approx(0.0)
    assert subs[1].words[-1]["end"] == pytest.approx(4.0)


def test_align_one_word_cue_maps_whole_window(monkeypatch):
    monkeypatch.setattr(config, "WORD_ALIGNMENT_ENABLED", True)
    _patch_tokens(monkeypatch, [{"w": "Hello", "s": 5.0, "e": 6.0}])
    subs = [_sub(1.0, 3.0, "Hello")]
    assert align_subtitle_words(subs, "audio.wav") == 1
    assert subs[0].words == [{"word": "Hello", "start": 1.0, "end": 3.0}]


def test_align_proportional_covers_every_nonempty_cue(monkeypatch):
    # Default proportional method: even a cue whose words are absent from the
    # whisper stream still gets a character-length split of its VAD window
    # (cue-level timing is trusted; only the DTW anchors are not).
    monkeypatch.setattr(config, "WORD_ALIGNMENT_ENABLED", True)
    monkeypatch.setattr(config, "WORD_ALIGNMENT_MIN_MATCH", 0.6)
    _patch_tokens(monkeypatch, [
        {"w": "Agar", "s": 0.0, "e": 1.0}, {"w": "main", "s": 1.0, "e": 2.0},
    ])
    subs = [_sub(0.0, 2.0, "Agar main"), _sub(2.0, 4.0, "zzz qqq xxx")]
    assert align_subtitle_words(subs, "audio.wav") == 2
    _assert_valid_words(subs[0].words, "Agar main", 0.0, 2.0)
    _assert_valid_words(subs[1].words, "zzz qqq xxx", 2.0, 4.0)


def test_align_skips_empty_text_cue(monkeypatch):
    monkeypatch.setattr(config, "WORD_ALIGNMENT_ENABLED", True)
    _patch_tokens(monkeypatch, [{"w": "hi", "s": 0.0, "e": 1.0}])
    subs = [_sub(0.0, 1.0, ""), _sub(1.0, 2.0, "hi")]
    assert align_subtitle_words(subs, "audio.wav") == 1
    assert subs[0].words is None
    _assert_valid_words(subs[1].words, "hi", 1.0, 2.0)


# ---------------------------------------------------------------------------
# 8. Disk cache — whisper runs at most once per audio/job and is reused
# ---------------------------------------------------------------------------

def test_disk_cache_round_trip(tmp_path):
    key = was._cache_key(tmp_path / "audio.wav", SimpleNamespace(st_mtime=1000.0, st_size=2048))
    tokens = [{"w": "hi", "s": 0.0, "e": 1.0}]
    was._write_disk_cache(tmp_path, key, tokens)
    assert (tmp_path / config.WORD_ALIGNMENT_CACHE_FILENAME).exists()
    assert was._read_disk_cache(tmp_path, key) == tokens


def test_disk_cache_stale_on_audio_change(tmp_path):
    tokens = [{"w": "hi", "s": 0.0, "e": 1.0}]
    k1 = was._cache_key(tmp_path / "a.wav", SimpleNamespace(st_mtime=1000.0, st_size=2048))
    was._write_disk_cache(tmp_path, k1, tokens)
    k2 = was._cache_key(tmp_path / "a.wav", SimpleNamespace(st_mtime=9999.0, st_size=2048))
    assert was._read_disk_cache(tmp_path, k2) is None


def test_disk_cache_stale_on_model_change(tmp_path):
    tokens = [{"w": "hi", "s": 0.0, "e": 1.0}]
    k1 = was._cache_key(tmp_path / "a.wav", SimpleNamespace(st_mtime=1000.0, st_size=2048))
    was._write_disk_cache(tmp_path, k1, tokens)
    k2 = (k1[0], k1[1], k1[2], "ggml-other.bin", k1[4])
    assert was._read_disk_cache(tmp_path, k2) is None


def test_disk_cache_missing_file(tmp_path):
    key = was._cache_key(tmp_path / "a.wav", SimpleNamespace(st_mtime=1.0, st_size=1))
    assert was._read_disk_cache(tmp_path, key) is None


def test_disk_cache_corrupt_json(tmp_path):
    (tmp_path / config.WORD_ALIGNMENT_CACHE_FILENAME).write_text("{not json", encoding="utf-8")
    key = was._cache_key(tmp_path / "a.wav", SimpleNamespace(st_mtime=1.0, st_size=1))
    assert was._read_disk_cache(tmp_path, key) is None


# ---------------------------------------------------------------------------
# 9. recognize_word_tokens — offline gating (never runs whisper in tests)
# ---------------------------------------------------------------------------

def test_recognize_disabled_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "WORD_ALIGNMENT_ENABLED", False)
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"x")
    assert recognize_word_tokens(audio) == []


def test_recognize_missing_audio_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "WORD_ALIGNMENT_ENABLED", True)
    assert recognize_word_tokens(tmp_path / "nope.wav") == []


def test_recognize_missing_model_returns_empty(monkeypatch, tmp_path):
    # No model file → WordAlignmentError inside _load_model → [] (silent skip).
    monkeypatch.setattr(config, "WORD_ALIGNMENT_ENABLED", True)
    monkeypatch.setattr(config, "WORD_ALIGNMENT_MODEL_PATH", str(tmp_path / "missing.bin"))
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"x")
    assert recognize_word_tokens(audio) == []


def test_recognize_reuses_disk_cache_without_rerunning_whisper(monkeypatch, tmp_path):
    # A valid disk cache is returned even though loading the model would fail,
    # proving whisper is NOT re-run once the result is cached for the job.
    monkeypatch.setattr(config, "WORD_ALIGNMENT_ENABLED", True)
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"real audio bytes")
    resolved = audio.resolve()
    key = was._cache_key(resolved, resolved.stat())
    tokens = [{"w": "cached", "s": 0.0, "e": 1.0}]
    was._write_disk_cache(resolved.parent, key, tokens)

    def _boom():
        raise AssertionError("whisper must not run when a valid cache exists")

    monkeypatch.setattr(was, "_load_model", _boom)
    assert recognize_word_tokens(audio, "prompt") == tokens


def test_load_model_raises_when_model_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "WORD_ALIGNMENT_MODEL_PATH", str(tmp_path / "missing.bin"))
    with pytest.raises(WordAlignmentError):
        was._load_model()


# ---------------------------------------------------------------------------
# 10. Persisted-subtitle contract (backward compatibility)
# ---------------------------------------------------------------------------

def test_subtitle_to_dict_omits_words_when_none():
    from app.services.romanization_service import Subtitle
    s = Subtitle(id=1, start=0.0, end=1.0, original_text="x", romanized_text="hi")
    assert "words" not in s.to_dict()


def test_subtitle_to_dict_includes_words_when_set():
    from app.services.romanization_service import Subtitle
    words = [{"word": "hi", "start": 0.0, "end": 1.0}]
    s = Subtitle(id=1, start=0.0, end=1.0, original_text="x",
                 romanized_text="hi", words=words)
    assert s.to_dict()["words"] == words


def test_models_load_old_cue_without_words():
    from app.models import RomanizedSubtitle, EditedSubtitle
    r = RomanizedSubtitle(id=1, start=0.0, end=1.0, original_text="x", romanized_text="hi")
    e = EditedSubtitle(id=1, start=0.0, end=1.0, original_text="x", romanized_text="hi")
    assert r.words is None and e.words is None


def test_models_parse_words():
    from app.models import RomanizedSubtitle
    r = RomanizedSubtitle(
        id=1, start=0.0, end=1.0, original_text="x", romanized_text="hi",
        words=[{"word": "hi", "start": 0.0, "end": 1.0}],
    )
    assert r.words is not None and r.words[0].word == "hi"


def test_edited_subtitle_dict_drops_stale_words():
    # A manual text edit invalidates the carried-over word timings; the save
    # serializer must drop them so consumers fall back to the estimate.
    from app.api import _edited_subtitle_dict
    from app.models import EditedSubtitle
    sub = EditedSubtitle(
        id=1, start=0.0, end=2.0, original_text="x", romanized_text="changed text",
        words=[
            {"word": "Agar", "start": 0.0, "end": 1.0},
            {"word": "main", "start": 1.0, "end": 2.0},
        ],
    )
    assert "words" not in _edited_subtitle_dict(sub)


def test_edited_subtitle_dict_keeps_valid_words():
    from app.api import _edited_subtitle_dict
    from app.models import EditedSubtitle
    sub = EditedSubtitle(
        id=1, start=0.0, end=2.0, original_text="x", romanized_text="Agar main",
        words=[
            {"word": "Agar", "start": 0.0, "end": 1.0},
            {"word": "main", "start": 1.0, "end": 2.0},
        ],
    )
    data = _edited_subtitle_dict(sub)
    assert data["words"] == [
        {"word": "Agar", "start": 0.0, "end": 1.0},
        {"word": "main", "start": 1.0, "end": 2.0},
    ]


# ---------------------------------------------------------------------------
# 11. Exporter consumes the SAME persisted words (FFmpeg burn-in)
# ---------------------------------------------------------------------------

def test_exporter_reads_persisted_words():
    sub = {
        "start": 0.0, "end": 2.0, "romanized_text": "Agar main",
        "words": [
            {"word": "Agar", "start": 0.0, "end": 1.0},
            {"word": "main", "start": 1.0, "end": 2.0},
        ],
    }
    assert ves._get_valid_word_timings(sub, "Agar main") == [(0.0, 1.0), (1.0, 2.0)]


def test_exporter_none_when_words_absent():
    sub = {"start": 0.0, "end": 2.0, "romanized_text": "Agar main"}
    assert ves._get_valid_word_timings(sub, "Agar main") is None


def test_exporter_none_on_display_mismatch():
    # English/original display → tokens differ from the aligned romanized words.
    sub = {
        "start": 0.0, "end": 2.0, "romanized_text": "Agar main",
        "words": [
            {"word": "Agar", "start": 0.0, "end": 1.0},
            {"word": "main", "start": 1.0, "end": 2.0},
        ],
    }
    assert ves._get_valid_word_timings(sub, "If I") is None


def test_exporter_boundaries_from_word_times():
    assert ves._boundaries_from_word_times([(0.0, 1.0), (1.0, 2.0)], 0.0, 2.0) == [0, 100, 200]


def test_exporter_word_events_uses_real_times():
    events = ves._word_events(
        ["Agar", "main"], 0.0, 4.0, "T", "H", [(0.0, 1.0), (1.0, 4.0)]
    )
    # Real timings put the first boundary at 1.0 s (100 cs), not the 2.0 s
    # proportional midpoint — proving the persisted words drive the events.
    assert (events[0][0], events[0][1]) == (0, 100)
    assert (events[1][0], events[1][1]) == (100, 400)


def test_exporter_word_events_falls_back_without_times():
    events = ves._word_events(["Agar", "main"], 0.0, 4.0, "T", "H", None)
    fb = ves._word_timings(["Agar", "main"], 0.0, 4.0)
    assert [(e[0], e[1]) for e in events] == [(fb[0], fb[1]), (fb[1], fb[2])]
    assert fb[1] == 200  # proportional midpoint, distinct from the real 100 cs


def test_exporter_word_events_ignores_mismatched_times():
    events = ves._word_events(["Agar", "main"], 0.0, 4.0, "T", "H", [(0.0, 1.0)])
    fb = ves._word_timings(["Agar", "main"], 0.0, 4.0)
    assert [(e[0], e[1]) for e in events] == [(fb[0], fb[1]), (fb[1], fb[2])]


def test_generate_ass_uses_persisted_words():
    subs = [{
        "id": 1, "start": 0.0, "end": 4.0, "romanized_text": "Agar main",
        "original_text": "x", "english_text": None,
        "words": [
            {"word": "Agar", "start": 0.0, "end": 1.0},
            {"word": "main", "start": 1.0, "end": 4.0},
        ],
    }]
    ass = ves.generate_ass_content(
        subs, "romanized", {"wordHighlight": True, "uppercase": False}, 1920, 1080
    )
    # The real 1.0 s word boundary is emitted (proportional would be 2.0 s).
    assert "0:00:01.00" in ass
    assert "0:00:02.00" not in ass


def test_generate_ass_uppercase_keeps_word_alignment():
    subs = [{
        "id": 1, "start": 0.0, "end": 4.0, "romanized_text": "Agar main",
        "original_text": "x", "english_text": None,
        "words": [
            {"word": "Agar", "start": 0.0, "end": 1.0},
            {"word": "main", "start": 1.0, "end": 4.0},
        ],
    }]
    ass = ves.generate_ass_content(
        subs, "romanized", {"wordHighlight": True, "uppercase": True}, 1920, 1080
    )
    # Uppercase is cosmetic (token count/order preserved) so real timings hold.
    assert "0:00:01.00" in ass


def test_generate_ass_without_words_falls_back():
    subs = [{
        "id": 1, "start": 0.0, "end": 4.0, "romanized_text": "Agar main",
        "original_text": "x", "english_text": None,
    }]
    ass = ves.generate_ass_content(
        subs, "romanized", {"wordHighlight": True}, 1920, 1080
    )
    # Backward compatible: an old cue without words uses the proportional split.
    assert "0:00:02.00" in ass


# ---------------------------------------------------------------------------
# 12. Dynamic job IDs — per-job cache isolation (regression)
#
# Word alignment must be driven ONLY by the audio path it is handed
# (JOBS_DIR/<current_job_id>/audio.wav) and never by a fixed job id. Every job
# id below is a freshly generated uuid4; the historical smoke-test id
# 087a46b2-... is deliberately NOT referenced anywhere in these tests.
# ---------------------------------------------------------------------------

class _FakeWhisper:
    """Stand-in for the whisper.cpp model. Records each transcribed path so a
    test can assert exactly which jobs actually ran recognition (and how many
    times), without ever loading the real model or touching the network."""

    def __init__(self):
        self.calls = []
        self.last_path = None

    def transcribe(self, path, **_kwargs):
        self.calls.append(str(path))
        self.last_path = str(path)


def _owner_tokens(model):
    """Token text encodes the OWNING job directory, so any cross-job leak of a
    cached token stream would be immediately visible in the assertions."""
    owner = Path(model.last_path).parent.name
    return [{"w": owner, "s": 0.0, "e": 0.5}, {"w": "tail", "s": 0.5, "e": 1.0}]


def _patch_whisper(monkeypatch):
    """Enable alignment and route recognition through the owner-tagged fake."""
    monkeypatch.setattr(config, "WORD_ALIGNMENT_ENABLED", True)
    fake = _FakeWhisper()
    monkeypatch.setattr(was, "_load_model", lambda: fake)
    monkeypatch.setattr(was, "_extract_tokens", _owner_tokens)
    return fake


def test_cache_path_follows_the_current_job_dir(tmp_path):
    # The cache location is derived from whatever job dir is passed in — never
    # a fixed path, and never the historical smoke-test job.
    for _ in range(3):
        job = tmp_path / f"job-{uuid.uuid4()}"
        assert was._cache_path(job) == job / config.WORD_ALIGNMENT_CACHE_FILENAME
    assert "087a46b2" not in str(was._cache_path(tmp_path / "anything"))


def test_two_random_jobs_get_their_own_cache(tmp_path, monkeypatch):
    # REQUIREMENT 6/8: every new job gets a SEPARATE word_alignment.json and
    # never consumes another job's cached recognition.
    fake = _patch_whisper(monkeypatch)

    job_a = tmp_path / f"test-job-A-{uuid.uuid4()}"
    job_b = tmp_path / f"test-job-B-{uuid.uuid4()}"
    job_a.mkdir()
    job_b.mkdir()
    audio_a = job_a / config.AUDIO_FILENAME
    audio_b = job_b / config.AUDIO_FILENAME
    audio_a.write_bytes(b"RIFF-AAAA")          # job A's own audio
    audio_b.write_bytes(b"RIFF-BBBBBBBB")      # job B's own audio (different)

    toks_a = recognize_word_tokens(audio_a, "prompt a")
    toks_b = recognize_word_tokens(audio_b, "prompt b")

    # Whisper ran once per job — B could not reuse A's cache.
    assert len(fake.calls) == 2
    # Each job wrote its OWN word_alignment.json inside its OWN directory.
    cache_a = was._cache_path(job_a)
    cache_b = was._cache_path(job_b)
    assert cache_a.exists() and cache_b.exists()
    # Token streams are job-specific and never cross-contaminate.
    assert toks_a[0]["w"] == job_a.name
    assert toks_b[0]["w"] == job_b.name
    assert toks_a[0]["w"] != toks_b[0]["w"]
    # The persisted files carry their own job's tokens.
    assert json.loads(cache_a.read_text())["tokens"][0]["w"] == job_a.name
    assert json.loads(cache_b.read_text())["tokens"][0]["w"] == job_b.name

    # Reopen both with the in-memory cache cleared: each reloads its OWN disk
    # cache and whisper is NOT run again for either job.
    was._token_cache.clear()
    before = len(fake.calls)
    assert recognize_word_tokens(audio_a, "prompt a")[0]["w"] == job_a.name
    assert recognize_word_tokens(audio_b, "prompt b")[0]["w"] == job_b.name
    assert len(fake.calls) == before


def test_identical_audio_in_two_jobs_still_isolated(tmp_path, monkeypatch):
    # Isolation is by DIRECTORY, not by audio content: byte-identical audio in
    # two different job dirs must each run recognition and keep separate caches
    # (the in-memory key embeds the full resolved path, so it cannot collide).
    fake = _patch_whisper(monkeypatch)
    same = b"RIFF-IDENTICAL-BYTES"

    job_a = tmp_path / f"job-{uuid.uuid4()}"
    job_b = tmp_path / f"job-{uuid.uuid4()}"
    job_a.mkdir()
    job_b.mkdir()
    audio_a = job_a / config.AUDIO_FILENAME
    audio_b = job_b / config.AUDIO_FILENAME
    audio_a.write_bytes(same)
    audio_b.write_bytes(same)

    recognize_word_tokens(audio_a)
    recognize_word_tokens(audio_b)

    assert len(fake.calls) == 2
    assert was._cache_path(job_a).exists()
    assert was._cache_path(job_b).exists()
    assert json.loads(was._cache_path(job_a).read_text())["tokens"][0]["w"] == job_a.name
    assert json.loads(was._cache_path(job_b).read_text())["tokens"][0]["w"] == job_b.name


def test_cache_invalidated_when_the_same_jobs_audio_changes(tmp_path, monkeypatch):
    # REQUIREMENT 7: if a job's audio changes, its stale cache must NOT be
    # reused — recognition re-runs against the new audio identity (size/mtime).
    monkeypatch.setattr(config, "WORD_ALIGNMENT_ENABLED", True)
    fake = _FakeWhisper()
    monkeypatch.setattr(was, "_load_model", lambda: fake)
    version = {"tag": "v1"}
    monkeypatch.setattr(
        was, "_extract_tokens",
        lambda model: [{"w": version["tag"], "s": 0.0, "e": 0.5}],
    )

    job = tmp_path / f"job-{uuid.uuid4()}"
    job.mkdir()
    audio = job / config.AUDIO_FILENAME
    audio.write_bytes(b"RIFF-1111")
    assert recognize_word_tokens(audio)[0]["w"] == "v1"
    assert len(fake.calls) == 1

    # Replace THIS job's audio with different content (new size) → the cached
    # key no longer matches, so whisper runs again and returns fresh tokens.
    version["tag"] = "v2"
    audio.write_bytes(b"RIFF-2222222222-longer")
    was._token_cache.clear()                   # force the disk-cache path
    assert recognize_word_tokens(audio)[0]["w"] == "v2"
    assert len(fake.calls) == 2


def test_align_subtitle_words_writes_cache_into_the_current_job(tmp_path, monkeypatch):
    # End-to-end through the public API: aligning cues for a random job runs
    # against THAT job's audio and persists THAT job's OWN word_alignment.json.
    monkeypatch.setattr(config, "WORD_ALIGNMENT_ENABLED", True)
    monkeypatch.setattr(config, "WORD_ALIGNMENT_METHOD", "dtw")
    monkeypatch.setattr(config, "SUBTITLE_DTW_ALIGNMENT_ENABLED", True)
    fake = _FakeWhisper()
    monkeypatch.setattr(was, "_load_model", lambda: fake)
    monkeypatch.setattr(
        was, "_extract_tokens",
        lambda model: [{"w": "hello", "s": 0.0, "e": 1.0}],
    )

    job = tmp_path / f"job-{uuid.uuid4()}"
    job.mkdir()
    audio = job / config.AUDIO_FILENAME
    audio.write_bytes(b"RIFF-END-TO-END")
    subs = [_sub(0.0, 1.0, "hello")]           # single-word cue → whole window

    assert align_subtitle_words(subs, audio) == 1
    assert fake.calls == [str(audio.resolve())]   # ran against THIS job's audio
    assert was._cache_path(job).exists()          # wrote THIS job's cache
    assert subs[0].words == [{"word": "hello", "start": 0.0, "end": 1.0}]


# ---------------------------------------------------------------------------
# 13. Audio-driven INTERIOR word boundaries (the default "audio" method)
#
# These cover the ten required cases: enough boundaries (A), fewer than words
# (B, hybrid), none (C, proportional), too many candidates, a candidate that
# would create a <40 ms word, continuous speech, exact cue-endpoint
# preservation, monotonicity, fallback when disabled, and mixed acoustic +
# proportional anchors. The DSP is exercised end-to-end on a synthesised wav.
# ---------------------------------------------------------------------------

def _write_synth_wav(path, bursts, dur, sr=16000, amp=0.3, freq=200.0):
    """Write a 16-bit mono wav: sine bursts (voiced) separated by silence."""
    import array
    import math
    import wave
    n = int(sr * dur)
    smp = array.array("h")
    for i in range(n):
        t = i / sr
        v = 0.0
        for (a, b) in bursts:
            if a <= t < b:
                v = amp * math.sin(2 * math.pi * freq * t)
                break
        smp.append(int(v * 32767))
    wf = wave.open(str(path), "wb")
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(sr)
    wf.writeframes(smp.tobytes())
    wf.close()
    return path


@pytest.fixture
def _audio_method(monkeypatch):
    """Force the default audio interior method with deterministic tuning."""
    monkeypatch.setattr(config, "WORD_ALIGNMENT_METHOD", "audio")
    monkeypatch.setattr(config, "SUBTITLE_AUDIO_BOUNDARY_ALIGNMENT_ENABLED", True)
    monkeypatch.setattr(config, "SUBTITLE_DTW_ALIGNMENT_ENABLED", False)
    monkeypatch.setattr(config, "AUDIO_BOUNDARY_SMOOTH_MS", 30.0)
    monkeypatch.setattr(config, "AUDIO_BOUNDARY_MIN_GAP_MS", 40.0)
    monkeypatch.setattr(config, "AUDIO_BOUNDARY_GAP_DEPTH_DB", 12.0)
    monkeypatch.setattr(config, "AUDIO_BOUNDARY_MIN_WORD_MS", 40.0)
    monkeypatch.setattr(config, "AUDIO_BOUNDARY_EDGE_MARGIN_MS", 40.0)


def test_audio_envelope_and_candidates_detect_gaps(_audio_method, tmp_path):
    # Three voiced bursts separated by 100 ms silences → two interior
    # candidates at the ONSETS of the 2nd and 3rd bursts (0.5 s and 0.9 s).
    wav = _write_synth_wav(
        tmp_path / "a.wav", [(0.1, 0.4), (0.5, 0.8), (0.9, 1.2)], 1.3
    )
    ctx = was._load_audio_context(wav)
    assert ctx is not None
    cands = was._cue_candidates(ctx, 0.0, 1.3)
    assert [round(t, 2) for (t, _d) in cands] == [0.5, 0.9]
    assert all(d >= 12.0 for (_t, d) in cands)


def test_audio_case_a_enough_boundaries_all_acoustic(_audio_method):
    # 4 words, 3 candidates on the proportional junctions → CASE A: every
    # junction anchored, so each interior word starts exactly at its candidate.
    cands = [(0.30, 25.0), (0.60, 25.0), (0.90, 25.0)]
    words, src = was._audio_word_split(["a", "b", "c", "d"], 0.0, 1.2, cands)
    assert src == ["ACOUSTIC", "ACOUSTIC", "ACOUSTIC"]
    assert [w["start"] for w in words] == [0.0, 0.30, 0.60, 0.90]
    assert words[-1]["end"] == pytest.approx(1.2)
    _assert_valid_words(words, "a b c d", 0.0, 1.2)


def test_audio_case_b_fewer_boundaries_hybrid(_audio_method):
    # 5 words (4 junctions) but only 2 candidates → hybrid (CASE B): the two
    # acoustic gaps anchor their nearest junctions, the other two stay
    # proportional. Proves we do NOT force a gap onto every junction.
    cands = [(0.48, 25.0), (0.96, 25.0)]
    words, src = was._audio_word_split(["a", "b", "c", "d", "e"], 0.0, 1.2, cands)
    assert src.count("ACOUSTIC") == 2 and src.count("PROPORTIONAL") == 2
    assert words[2]["start"] == pytest.approx(0.48, abs=1e-3)
    assert words[4]["start"] == pytest.approx(0.96, abs=1e-3)
    _assert_valid_words(words, "a b c d e", 0.0, 1.2)


def test_audio_case_c_no_boundaries_is_proportional(_audio_method):
    # No candidates at all → the existing proportional character split, and
    # every junction is labelled PROPORTIONAL.
    words, src = was._audio_word_split(["aa", "bb", "cc"], 0.0, 0.9, [])
    prop = _proportional_word_split(["aa", "bb", "cc"], 0.0, 0.9)
    assert words == prop
    assert src == ["PROPORTIONAL", "PROPORTIONAL"]


def test_audio_continuous_speech_has_no_gaps(_audio_method, tmp_path):
    # One continuous voiced burst (no interior silence) → no candidates, so the
    # split falls back to proportional instead of inventing fake boundaries.
    wav = _write_synth_wav(tmp_path / "c.wav", [(0.05, 1.15)], 1.2)
    ctx = was._load_audio_context(wav)
    cands = was._cue_candidates(ctx, 0.0, 1.2)
    assert cands == []
    words, src = was._audio_word_split(
        ["how", "are", "you", "doing"], 0.0, 1.2, cands
    )
    assert set(src) == {"PROPORTIONAL"}
    _assert_valid_words(words, "how are you doing", 0.0, 1.2)


def test_audio_too_many_candidates_selects_best(_audio_method):
    # 2 words (1 junction) but 3 candidates → select the single best (deepest,
    # closest to the proportional prior), not the first one.
    cands = [(0.30, 10.0), (0.50, 30.0), (0.70, 10.0)]
    words, src = was._audio_word_split(["ab", "cd"], 0.0, 1.0, cands)
    assert src == ["ACOUSTIC"]
    assert words[1]["start"] == pytest.approx(0.50)


def test_audio_avoids_candidate_that_creates_short_word(_audio_method):
    # Two candidates 20 ms apart: anchoring both would create a 20 ms word, so
    # the DP leaves one junction proportional — no sub-40 ms flash.
    cands = [(0.50, 25.0), (0.52, 25.0)]
    words, src = was._audio_word_split(["aa", "bb", "cc"], 0.0, 1.0, cands)
    assert all((w["end"] - w["start"]) >= 0.04 for w in words)
    assert "PROPORTIONAL" in src
    _assert_valid_words(words, "aa bb cc", 0.0, 1.0)


def test_audio_preserves_cue_endpoints(_audio_method):
    cands = [(0.55, 30.0)]
    words, _src = was._audio_word_split(["aa", "bb"], 2.0, 4.0, cands)
    assert words[0]["start"] == pytest.approx(2.0)
    assert words[-1]["end"] == pytest.approx(4.0)


def test_audio_monotonic_contiguous(_audio_method):
    cands = [(0.30, 20.0), (0.62, 20.0), (0.95, 20.0)]
    words, _src = was._audio_word_split(["a", "b", "c", "d"], 0.0, 1.2, cands)
    for i in range(1, len(words)):
        assert words[i]["start"] == pytest.approx(words[i - 1]["end"])
        assert words[i]["end"] > words[i]["start"]
    assert validate_word_timings(words, "a b c d", 0.0, 1.2) is not None


def test_audio_fallback_when_disabled(monkeypatch):
    # SUBTITLE_AUDIO_BOUNDARY_ALIGNMENT_ENABLED=false degrades "audio" to the
    # proportional split even when candidates are supplied.
    monkeypatch.setattr(config, "WORD_ALIGNMENT_METHOD", "audio")
    monkeypatch.setattr(config, "SUBTITLE_AUDIO_BOUNDARY_ALIGNMENT_ENABLED", False)
    out = _fit_cue_words(
        ["a", "b"], [None, None], 0.0, 2.0, 0.6, candidates=[(0.9, 30.0)]
    )
    assert out == [
        {"word": "a", "start": 0.0, "end": 1.0},
        {"word": "b", "start": 1.0, "end": 2.0},
    ]


def test_audio_fit_uses_candidates(_audio_method):
    # Through the public per-cue fitter with ONE candidate for a 3-word cue: it
    # anchors the nearest junction (word "c" starts at the acoustic 0.70) while
    # the other junction stays proportional (word "b" at 0.40) — the hybrid.
    out = _fit_cue_words(
        ["a", "b", "c"], [None] * 3, 0.0, 1.2, 0.6, candidates=[(0.70, 30.0)]
    )
    assert out[0]["start"] == pytest.approx(0.0)
    assert out[1]["start"] == pytest.approx(0.40)   # proportional junction
    assert out[2]["start"] == pytest.approx(0.70)   # acoustic junction
    assert out[-1]["end"] == pytest.approx(1.2)


def test_align_audio_does_not_run_whisper(_audio_method, tmp_path, monkeypatch):
    # The default audio path must be whisper-free: recognition is never called,
    # yet real acoustic word timings are attached from the wav.
    monkeypatch.setattr(config, "WORD_ALIGNMENT_ENABLED", True)

    def _boom(*_a, **_k):
        raise AssertionError("whisper must not run for the audio method")

    monkeypatch.setattr(was, "recognize_word_tokens", _boom)
    wav = _write_synth_wav(
        tmp_path / "a.wav", [(0.1, 0.4), (0.5, 0.8), (0.9, 1.2)], 1.3
    )
    subs = [_sub(0.0, 1.3, "ek do teen")]
    assert align_subtitle_words(subs, wav) == 1
    assert subs[0].words[1]["start"] == pytest.approx(0.5, abs=0.02)
    assert subs[0].words[2]["start"] == pytest.approx(0.9, abs=0.02)


def test_align_audio_attaches_words_from_real_wav(_audio_method, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORD_ALIGNMENT_ENABLED", True)
    wav = _write_synth_wav(
        tmp_path / "a.wav", [(0.1, 0.4), (0.5, 0.8), (0.9, 1.2)], 1.3
    )
    subs = [_sub(0.0, 1.3, "ek do teen")]
    assert align_subtitle_words(subs, wav) == 1
    _assert_valid_words(subs[0].words, "ek do teen", 0.0, 1.3)
    assert subs[0].words[0]["start"] == pytest.approx(0.0)
    assert subs[0].words[-1]["end"] == pytest.approx(1.3)
