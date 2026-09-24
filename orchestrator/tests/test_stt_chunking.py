from __future__ import annotations

import io
import wave
from pathlib import Path

from orchestrator.tools.deterministic_tools import (
    STT_MAX_CHUNK_SECONDS,
    _wav_chunk_payloads,
)

RATE = 8000


def write_wav(path: Path, segments: list[tuple[float, bool]], *, sample_width: int = 2) -> Path:
    """Build a WAV from (seconds, is_loud) segments.

    Loud is a square wave; quiet is true digital silence -- the same contrast
    the real narration has between speech and the gap between words.
    """

    frames = bytearray()
    for seconds, loud in segments:
        for index in range(int(seconds * RATE)):
            if not loud:
                value = 0
            else:
                value = 8000 if index % 2 == 0 else -8000
            frames += value.to_bytes(sample_width, "little", signed=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(sample_width)
        handle.setframerate(RATE)
        handle.writeframes(bytes(frames))
    return path


def chunk_bounds(chunks) -> list[tuple[float, float]]:
    """(start, end) seconds for each returned chunk."""

    bounds = []
    for blob, offset in chunks:
        with wave.open(io.BytesIO(blob), "rb") as handle:
            duration = handle.getnframes() / handle.getframerate()
        bounds.append((offset, offset + duration))
    return bounds


def test_audio_under_the_limit_is_one_chunk(tmp_path):
    path = write_wav(tmp_path / "short.wav", [(4.0, True)])
    chunks = _wav_chunk_payloads(path, 6.0)
    assert len(chunks) == 1
    assert chunks[0][1] == 0.0


def test_the_split_lands_in_the_silence_not_on_the_limit(tmp_path):
    """The whole point. A cut at the 6.0s limit would open the next chunk
    mid-word, and Google returns nothing at all for such a chunk."""
    path = write_wav(tmp_path / "gap.wav", [(4.0, True), (0.3, False), (3.7, True)])
    bounds = chunk_bounds(_wav_chunk_payloads(path, 6.0))

    assert len(bounds) == 2
    split = bounds[0][1]
    assert 4.0 <= split <= 4.3, f"split at {split}s is outside the silent gap"
    assert split < 6.0, "split must move off the hard limit"


def test_no_chunk_exceeds_the_api_limit(tmp_path):
    """Google STT v2 sync recognize rejects audio longer than 60s; the split
    may only ever shorten a chunk, never lengthen one."""
    path = write_wav(tmp_path / "long.wav", [(4.0, True), (0.3, False), (9.0, True)])
    for start, end in chunk_bounds(_wav_chunk_payloads(path, 6.0)):
        assert end - start <= 6.0 + 1e-6


def test_chunks_tile_the_audio_with_no_gap_or_overlap(tmp_path):
    """Word times from later chunks are shifted by the offset onto one
    timeline, so a gap would lose words and an overlap would duplicate them."""
    path = write_wav(tmp_path / "tile.wav", [(4.0, True), (0.3, False), (9.0, True)])
    bounds = chunk_bounds(_wav_chunk_payloads(path, 6.0))

    assert bounds[0][0] == 0.0
    for (_, previous_end), (next_start, _) in zip(bounds, bounds[1:]):
        assert abs(previous_end - next_start) < 1e-6
    assert abs(bounds[-1][1] - 13.3) < 0.01


def test_continuous_speech_still_splits(tmp_path):
    """With no real silence anywhere the quietest point is still chosen, and
    the audio is still chunked rather than exceeding the limit."""
    path = write_wav(tmp_path / "solid.wav", [(13.0, True)])
    bounds = chunk_bounds(_wav_chunk_payloads(path, 6.0))

    assert len(bounds) >= 3
    for start, end in bounds:
        assert end - start <= 6.0 + 1e-6
    assert abs(bounds[-1][1] - 13.0) < 0.01


def test_the_split_does_not_move_absurdly_early(tmp_path):
    """Silence early in the chunk must not drag the split back to it -- that
    would pay for extra requests to transcribe a few seconds each."""
    path = write_wav(tmp_path / "early.wav", [(0.5, True), (0.5, False), (12.0, True)])
    bounds = chunk_bounds(_wav_chunk_payloads(path, 6.0))

    # Half the limit is the floor; the 0.5-1.0s silence is well before it.
    assert bounds[0][1] >= 3.0


def test_the_last_chunk_is_never_searched(tmp_path):
    """It ends where the speech ends, so it cannot open mid-word -- and
    shortening it would drop the tail entirely."""
    path = write_wav(tmp_path / "tail.wav", [(4.0, True), (0.3, False), (3.0, True)])
    bounds = chunk_bounds(_wav_chunk_payloads(path, 6.0))
    assert abs(bounds[-1][1] - 7.3) < 0.01


def test_unexpected_sample_width_falls_back_to_the_fixed_cut(tmp_path):
    """Only 16-bit PCM is decoded for loudness. Anything else keeps the old
    behaviour rather than guessing at the encoding."""
    path = write_wav(tmp_path / "wide.wav", [(4.0, True), (0.3, False), (3.7, True)], sample_width=4)
    bounds = chunk_bounds(_wav_chunk_payloads(path, 6.0))

    assert len(bounds) == 2
    assert abs(bounds[0][1] - 6.0) < 1e-6, "should cut exactly on the limit"


def test_the_real_narration_splits_off_the_limit():
    """Regression for the halted run: a 60.80s narration cut at exactly 55.00s
    landed 0.08s inside the word "send", and the 55.00-60.80s chunk came back
    with zero words -- losing the whole closing sentence."""
    narration = Path("audio/narration.wav")
    if not narration.is_file():
        return  # only meaningful while a run's audio is on disk

    bounds = chunk_bounds(_wav_chunk_payloads(narration, STT_MAX_CHUNK_SECONDS))
    if len(bounds) < 2:
        return
    assert bounds[0][1] < STT_MAX_CHUNK_SECONDS, "split must move off the hard limit"
