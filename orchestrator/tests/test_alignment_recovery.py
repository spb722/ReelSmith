"""Offline regression coverage for conservative written/spoken equivalence."""
import asyncio
import json
import wave

import pytest

from orchestrator.tools import deterministic_tools as dt
from orchestrator.tools.deterministic_tools import align_words, tokenize


def observed(words):
    return [{"word": word, "start_seconds": i * .4, "end_seconds": i * .4 + .3}
            for i, word in enumerate(words)]


@pytest.mark.parametrize("canonical,recognized", [
    ("thirty-one gold medals", ["31", "gold", "medals"]),
    ("one percent better", ["1%", "better"]),
    ("one percent better", ["1", "%", "better"]),
    ("In 2003 and 2008", ["In", "two", "thousand", "three", "and", "two", "thousand", "eight"]),
    ("two thousand and three", ["2003"]),
    ("a long-term habit", ["a", "long", "term", "habit"]),
    ("2.5% better each day", ["two", "point", "five", "percent", "better", "each", "day"]),
])
def test_equivalent_words_preserve_canonical_text_and_observed_spans(canonical, recognized):
    tokens = dt.tokenize(canonical)
    words, stats = dt.align_words(tokens, observed(recognized))
    assert stats["normalized_match_ratio"] == 1
    assert stats["exact_match_ratio"] < 1
    assert stats["equivalent_matches"] > 0
    assert not stats["mismatches"]
    assert [word["canonical_word"] for word in words] == tokens
    for index, word in enumerate(words):
        span = word["observed_span"]
        assert span["start_seconds"] <= word["start_seconds"] <= word["end_seconds"] <= round(span["end_seconds"], 3)
        if index:
            assert word["start_seconds"] >= words[index-1]["end_seconds"]


@pytest.mark.parametrize("canonical,recognized", [
    ("one percent", ["one"]),
    ("one", ["1%"]),
    ("2003", ["2008"]),
    ("one habit", ["won", "habit"]),
    ("a useful habit", ["a", "bad", "habit"]),
    ("one hundred and three", ["one", "hundred", "and", "eight"]),
    ("007", ["seven"]),
])
def test_real_disagreement_is_not_normalized_away(canonical, recognized):
    _, stats = dt.align_words(dt.tokenize(canonical), observed(recognized))
    assert stats["normalized_match_ratio"] < .98
    assert stats["mismatches"]


def test_extra_speech_penalizes_match_ratio():
    _, stats = dt.align_words(["keep", "going"], observed(["keep", "really", "going"]))
    assert stats["exact_match_ratio"] == 1
    assert stats["normalized_match_ratio"] == pytest.approx(2/3)
    assert stats["mismatches"][0]["recognized_words"] == ["really"]


def test_missing_edge_timing_never_extends_observed_audio():
    words, stats = dt.align_words(["keep", "going", "now"], observed(["keep", "going"]))
    assert words[-1]["end_seconds"] == .7
    assert stats["canonical_words_missing"] == 1
    assert words[-1]["alignment"] == "interpolated"


def test_diagnostics_saved_before_alignment_rejection(tmp_path, monkeypatch):
    audio = tmp_path / "narration.wav"
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(1000)
        handle.writeframes(b"\0\0" * 2000)
    monkeypatch.setattr(dt, "transcribe_audio_path_with_word_offsets", lambda *_: ("bad words", observed(["bad", "words"])))
    path = tmp_path / "run-state" / "diagnostics.json"
    with pytest.raises(dt.AlignmentQualityError) as error:
        asyncio.run(dt.extract_word_timing.handler({"audio_path": str(audio), "narration_script": "keep going", "project_id": "test", "diagnostics_path": str(path)}))
    saved = json.loads(path.read_text())
    assert saved["recognized_transcript"] == "bad words"
    assert saved["recognized_words"][0]["word"] == "bad"
    assert saved["alignment_stats"]["mismatches"]
    assert error.value.attempted_audio_seconds == 2


def test_equivalent_narration_passes_subtitles_with_exact_display_text():
    narration = "Thirty-one riders improved by one percent."
    words, stats = dt.align_words(dt.tokenize(narration), observed(["31", "riders", "improved", "by", "1%", "."]))
    # Punctuation has no word timestamp in actual STT responses.
    words, stats = dt.align_words(dt.tokenize(narration), observed(["31", "riders", "improved", "by", "1%"]))
    result = asyncio.run(dt.build_subtitle_cues.handler({"word_timing": {"locked_narration": narration, "words": words, "alignment_stats": stats}, "narration_script": narration}))
    payload = json.loads(result["content"][0]["text"])
    assert payload["alignment_ratio"] == 1
    assert " ".join(cue["text"] for cue in payload["cues"]) == narration


def test_subtitle_threshold_does_not_round_up():
    narration = "keep going"
    with pytest.raises(dt.AlignmentQualityError):
        asyncio.run(dt.build_subtitle_cues.handler({"word_timing": {"locked_narration": narration, "alignment_stats": {"exact_match_ratio": 1, "normalized_match_ratio": .97999}, "words": []}, "narration_script": narration}))


def test_overlapping_observations_cannot_pass_gate():
    words, stats = dt.align_words(["keep", "going"], [{"word": "keep", "start_seconds": 1, "end_seconds": 2}, {"word": "going", "start_seconds": 1.5, "end_seconds": 3}])
    assert stats["timing_errors"]
    with pytest.raises(dt.AlignmentQualityError):
        asyncio.run(dt.build_subtitle_cues.handler({"word_timing": {"locked_narration": "keep going", "alignment_stats": stats, "words": words}, "narration_script": "keep going"}))


# --- trailing extra speech is harmless -------------------------------------
# Gemini TTS occasionally appends a line of its own. Extra words *after* the
# last script word are inert: the composition's length comes from the last
# shot's end (Composition.tsx), so speech past it is never rendered. Extra
# words *inside* the narration are not -- they shift every later timestamp.


def words_at(pairs):
    """Recognised words with sane increasing timings."""
    return [
        {"word": w, "start_seconds": float(i), "end_seconds": float(i) + 0.4}
        for i, w in enumerate(pairs)
    ]


def test_trailing_extra_speech_does_not_fail_the_run():
    """The exact failure from a live take: every script word found and timed,
    nine invented words on the end, scored 0.922 and halted the reel."""
    script = "you win the game"
    heard = words_at(["you", "win", "the", "game", "What", "would", "life", "be"])
    _, stats = align_words(tokenize(script), heard)

    assert stats["canonical_words_missing"] == 0
    assert stats["extra_recognized_words"] == 4
    assert stats["trailing_extra_words"] == 4
    assert stats["normalized_match_ratio"] == 1.0


def test_extra_speech_in_the_middle_still_fails():
    """These genuinely desync subtitles, so they must keep counting."""
    script = "you win the game"
    heard = words_at(["you", "win", "absolutely", "the", "game"])
    _, stats = align_words(tokenize(script), heard)

    assert stats["trailing_extra_words"] == 0
    assert stats["extra_recognized_words"] == 1
    assert stats["normalized_match_ratio"] < 1.0


def test_a_missing_word_still_fails():
    """Dropping a word is never harmless -- the subtitle for it would have no
    timing at all.

    Tested on its own rather than combined with trailing extras: a deletion
    plus two inserts and two substitutions cost the same three edits, so the
    aligner may legitimately choose either and an assertion on which one it
    picked would be testing the tie-break, not the rule.
    """
    script = "you win the game"
    heard = words_at(["you", "win", "game"])
    _, stats = align_words(tokenize(script), heard)

    assert stats["canonical_words_missing"] == 1
    assert stats["normalized_match_ratio"] < 1.0


def test_a_clean_take_is_unaffected():
    script = "you win the game"
    _, stats = align_words(tokenize(script), words_at(["you", "win", "the", "game"]))
    assert stats["trailing_extra_words"] == 0
    assert stats["normalized_match_ratio"] == 1.0
