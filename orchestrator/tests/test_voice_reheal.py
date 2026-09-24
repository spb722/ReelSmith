from __future__ import annotations

from pathlib import Path

from orchestrator.state import voice_cache
from orchestrator.state.voice_cache import AUDIO_CACHE_FILE, discard_audio_cache
from orchestrator.tools.gemini_tools import NARRATION_WAV


def test_discarding_removes_both_halves_of_the_cache(tmp_path, monkeypatch):
    """Both files must go: the WAV is the take, and the provenance JSON is
    what tells the pipeline the take is still valid."""
    monkeypatch.chdir(tmp_path)
    AUDIO_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    NARRATION_WAV.parent.mkdir(parents=True, exist_ok=True)
    AUDIO_CACHE_FILE.write_text("{}", encoding="utf-8")
    NARRATION_WAV.write_bytes(b"wav")

    discarded = discard_audio_cache()

    assert set(discarded) == {AUDIO_CACHE_FILE, NARRATION_WAV}
    assert not AUDIO_CACHE_FILE.exists()
    assert not NARRATION_WAV.exists()


def test_discarding_is_safe_when_there_is_nothing_to_discard(tmp_path, monkeypatch):
    """Called on a fresh project, or twice in a row, it must not raise."""
    monkeypatch.chdir(tmp_path)
    assert discard_audio_cache() == []


def test_discarding_leaves_the_archive_alone(tmp_path, monkeypatch):
    """--retry-voice copies the take into voice_history first; discarding the
    live one must not touch that evidence."""
    monkeypatch.chdir(tmp_path)
    archive = Path("orchestrator_runs/voice_history/abc")
    archive.mkdir(parents=True)
    (archive / "narration.wav").write_bytes(b"the bad take, kept")
    NARRATION_WAV.parent.mkdir(parents=True, exist_ok=True)
    NARRATION_WAV.write_bytes(b"the bad take, live")

    discard_audio_cache()

    assert (archive / "narration.wav").read_bytes() == b"the bad take, kept"
