"""Asset ingestion (ported from ingest_assets), word-timing alignment
(ported from extract_word_timing.py, Code Map), and subtitle-cue DP
segmentation (ported from build_subtitle_cues.py, Code Map) -- none invoke
their originating script. `extract_word_timing`'s 0.85 exact-match-ratio
gate and `build_subtitle_cues`'s 0.98 alignment recheck are both ported
unmodified: they already raise on their own mechanical thresholds, which
*is* the segmentation's quality bar (AD-2 in the Story 1.5 spec) -- never
re-judged by an LLM. The DP's word/duration bounds, pause thresholds, and
penalty functions are ported verbatim, unmodified, per AGENTS.md's policy
against re-tuning this segmentation. The impact/reflection/protected
phrases are per-run data, not tuning parameters (Renegotiated, 2026-09-17):
derived from the current `FinalStoryPlanContract`'s scenes/story_arc rather
than hardcoded to one reel's narration.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import wave
from datetime import datetime, timezone
from pathlib import Path

import google.auth
from claude_agent_sdk import create_sdk_mcp_server, tool
from google.auth.transport.requests import AuthorizedSession
from PIL import Image


SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif"}
ASSETS_FILE = Path("metadata/assets.json")


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def inspect_image(path: Path) -> dict:
    file_hash = sha256_file(path)
    asset_id = f"img_{file_hash[:12]}"
    with Image.open(path) as image:
        width, height = image.size
        image_format = image.format
        image_mode = image.mode
    if width > height:
        orientation = "landscape"
    elif height > width:
        orientation = "portrait"
    else:
        orientation = "square"
    return {
        "asset_id": asset_id,
        "source_path": str(path),
        "original_filename": path.name,
        "sha256": file_hash,
        "file_size_bytes": path.stat().st_size,
        "image": {
            "format": image_format, "width": width, "height": height,
            "orientation": orientation, "mode": image_mode,
            "aspect_ratio": round(width / height, 4),
        },
        "semantic_analysis": None,
    }


def discover_images(source_dir: Path) -> list[Path]:
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Missing directory: {source_dir.resolve()}")
    return sorted(
        path for path in source_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


@tool("ingest", "Discover screenshots and write the deterministic asset registry", {"source_images_dir": str})
async def ingest(args: dict) -> dict:
    assets = [inspect_image(path) for path in discover_images(Path(args["source_images_dir"]))]
    if not assets:
        raise ValueError("No supported screenshots found in source_images_dir")
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "asset_count": len(assets),
        "assets": assets,
    }
    ASSETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = ASSETS_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(ASSETS_FILE)
    return {"content": [{"type": "text", "text": json.dumps(manifest, ensure_ascii=False)}]}


# ============================================================
# EXTERNAL BINARY RESOLUTION
# ============================================================

# `shutil.which` is not enough on this project's target machine: the Anaconda
# install ships an ffmpeg/ffprobe that shadows Homebrew's on PATH but aborts at
# load time with a missing libintl. Every candidate is therefore actually run
# before it is trusted. A silently broken binary previously surfaced as "no
# preview frames", three wasted agent attempts, and a halted run.
_RESOLVED_BINARIES: dict[str, str] = {}

FALLBACK_BINARY_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin")


class MissingBinaryError(RuntimeError):
    """Raised when no working copy of a required external binary exists."""


def resolve_binary(name: str) -> str:
    """Return a path to a *working* copy of `name`, preferring PATH order.

    Honours an explicit override first (e.g. `FFMPEG_BINARY`), then PATH, then
    the usual Homebrew/system locations. The chosen path is cached per process.
    """

    cached = _RESOLVED_BINARIES.get(name)
    if cached:
        return cached

    candidates: list[str] = []
    override = os.getenv(f"{name.upper()}_BINARY")
    if override:
        candidates.append(override)
    found = shutil.which(name)
    if found:
        candidates.append(found)
    candidates.extend(str(Path(directory) / name) for directory in FALLBACK_BINARY_DIRS)

    attempted: list[str] = []
    for candidate in candidates:
        if not Path(candidate).is_file():
            continue
        attempted.append(candidate)
        try:
            completed = subprocess.run(
                [candidate, "-version"], capture_output=True, text=True, check=False, timeout=20
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode == 0:
            _RESOLVED_BINARIES[name] = candidate
            return candidate

    raise MissingBinaryError(
        f"No working {name} found. Tried: {attempted or 'nothing on PATH'}. "
        f"Install {name}, or set {name.upper()}_BINARY to a working copy."
    )


# ============================================================
# WORD-TIMING ALIGNMENT (ported from extract_word_timing.py)
# ============================================================

# Chirp 3 is available in the "us" multi-region -- ported verbatim.
STT_REGION = "us"
STT_MODEL = "chirp_3"
STT_LANGUAGE_CODE = "en-US"
# Google STT V2 sync recognize rejects audio longer than 60s. Stay under that
# with margin; longer WAVs are split into chunks, transcribed separately, then
# timings are offset and concatenated before alignment.
STT_MAX_CHUNK_SECONDS = 55.0

# We expect very high agreement because the audio was generated directly
# from the locked narration. Ported verbatim -- not re-tuned.
MIN_EXACT_MATCH_RATIO = 0.85

WORD_RE = re.compile(r"\b[\w’'-]+\b", flags=re.UNICODE)


def tokenize(text: str) -> list[str]:
    return WORD_RE.findall(text)


def normalize_word(word: str) -> str:
    word = word.replace("’", "'").replace("‘", "'").lower()
    return re.sub(r"(^[^\w]+|[^\w]+$)", "", word, flags=re.UNICODE)


def parse_offset(value: str | None) -> float:
    if not value:
        return 0.0
    value = value.strip()
    if value.endswith("s"):
        value = value[:-1]
    return float(value or 0.0)


def wav_duration_seconds(audio_path: Path) -> float:
    with wave.open(str(audio_path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


def _wav_chunk_payloads(audio_path: Path, max_chunk_seconds: float) -> list[tuple[bytes, float]]:
    """Split a PCM WAV into complete WAV blobs, each <= max_chunk_seconds.

    Returns (wav_bytes, start_offset_seconds) pairs so STT word times from
    later chunks can be shifted onto the full-timeline clock.
    """
    with wave.open(str(audio_path), "rb") as handle:
        params = handle.getparams()
        frame_rate = handle.getframerate()
        sample_width = handle.getsampwidth()
        channels = handle.getnchannels()
        total_frames = handle.getnframes()
        pcm = handle.readframes(total_frames)

    if frame_rate <= 0 or sample_width <= 0 or channels <= 0:
        raise RuntimeError(f"Invalid WAV parameters in {audio_path}")

    max_frames = max(1, int(max_chunk_seconds * frame_rate))
    frame_bytes = sample_width * channels
    chunks: list[tuple[bytes, float]] = []
    frame_cursor = 0
    while frame_cursor < total_frames:
        frames_this = min(max_frames, total_frames - frame_cursor)
        byte_start = frame_cursor * frame_bytes
        byte_end = (frame_cursor + frames_this) * frame_bytes
        pcm_slice = pcm[byte_start:byte_end]
        buf = io.BytesIO()
        with wave.open(buf, "wb") as out:
            out.setnchannels(channels)
            out.setsampwidth(sample_width)
            out.setframerate(frame_rate)
            out.writeframes(pcm_slice)
        chunks.append((buf.getvalue(), frame_cursor / float(frame_rate)))
        frame_cursor += frames_this
    return chunks


def transcribe_audio_path_with_word_offsets(audio_path: Path, project_id: str) -> tuple[str, list[dict]]:
    """Transcribe a WAV, splitting into <=55s chunks when needed for STT's 60s cap.

    Chunk word timings are offset by each chunk's start so the merged list is
    on the full-audio timeline. The original WAV file is left unchanged.
    """
    duration = wav_duration_seconds(audio_path)
    chunks = _wav_chunk_payloads(audio_path, STT_MAX_CHUNK_SECONDS)
    print(
        f"STT: {audio_path} duration={duration:.2f}s -> {len(chunks)} chunk(s) "
        f"(max {STT_MAX_CHUNK_SECONDS:.0f}s each)"
    )

    transcript_parts: list[str] = []
    recognized_words: list[dict] = []
    for index, (chunk_bytes, start_offset) in enumerate(chunks, start=1):
        print(
            f"STT: chunk {index}/{len(chunks)} "
            f"start={start_offset:.2f}s bytes={len(chunk_bytes)}"
        )
        raw = transcribe_with_word_offsets(chunk_bytes, project_id)
        transcript, words = extract_recognition(raw)
        if transcript:
            transcript_parts.append(transcript)
        for item in words:
            recognized_words.append({
                "word": item["word"],
                "start_seconds": round(item["start_seconds"] + start_offset, 3),
                "end_seconds": round(item["end_seconds"] + start_offset, 3),
            })
        print(f"STT: chunk {index}/{len(chunks)} -> {len(words)} words")

    return " ".join(transcript_parts).strip(), recognized_words


def transcribe_with_word_offsets(audio_bytes: bytes, project_id: str) -> dict:
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    session = AuthorizedSession(credentials)
    endpoint = (
        f"https://{STT_REGION}-speech.googleapis.com/v2/"
        f"projects/{project_id}/locations/{STT_REGION}/recognizers/_:recognize"
    )
    request_body = {
        "config": {
            "autoDecodingConfig": {},
            "languageCodes": [STT_LANGUAGE_CODE],
            "model": STT_MODEL,
            "features": {
                "enableWordTimeOffsets": True,
                "enableAutomaticPunctuation": True,
            },
        },
        "content": base64.b64encode(audio_bytes).decode("ascii"),
    }
    response = session.post(endpoint, json=request_body, timeout=120)
    if not response.ok:
        raise RuntimeError(
            f"Speech-to-Text request failed.\nHTTP {response.status_code}\n{response.text}"
        )
    return response.json()


def extract_recognition(response: dict) -> tuple[str, list[dict]]:
    transcript_parts: list[str] = []
    words: list[dict] = []
    for result in response.get("results", []):
        alternatives = result.get("alternatives", [])
        if not alternatives:
            continue
        alternative = alternatives[0]
        transcript = alternative.get("transcript", "").strip()
        if transcript:
            transcript_parts.append(transcript)
        for item in alternative.get("words", []):
            word = item.get("word", "").strip()
            if not word:
                continue
            words.append({
                "word": word,
                "start_seconds": parse_offset(item.get("startOffset")),
                "end_seconds": parse_offset(item.get("endOffset")),
            })
    return " ".join(transcript_parts).strip(), words


def align_words(canonical_words: list[str], recognized_words: list[dict]) -> tuple[list[dict], dict]:
    """Global sequence alignment between the locked narration and
    Speech-to-Text words. Exact matches cost 0; substitutions, insertions,
    and deletions cost 1. Ported verbatim from extract_word_timing.py.
    """
    canon = [normalize_word(word) for word in canonical_words]
    recog = [normalize_word(item["word"]) for item in recognized_words]
    n = len(canon)
    m = len(recog)

    dp = [[0] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]

    for i in range(1, n + 1):
        dp[i][0] = i
        back[i][0] = "delete"
    for j in range(1, m + 1):
        dp[0][j] = j
        back[0][j] = "insert"

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            exact = canon[i - 1] == recog[j - 1]
            substitution_cost = 0 if exact else 1
            diagonal = dp[i - 1][j - 1] + substitution_cost
            delete = dp[i - 1][j] + 1
            insert = dp[i][j - 1] + 1
            best = min(diagonal, delete, insert)
            dp[i][j] = best
            # Prefer diagonal alignment when tied because it preserves
            # timing for a canonical word.
            if diagonal == best:
                back[i][j] = "exact" if exact else "substitute"
            elif delete == best:
                back[i][j] = "delete"
            else:
                back[i][j] = "insert"

    aligned_reversed: list[dict] = []
    i, j = n, m
    exact_matches = substitutions = deletions = insertions = 0

    while i > 0 or j > 0:
        op = back[i][j]
        if op in {"exact", "substitute"}:
            rec = recognized_words[j - 1]
            if op == "exact":
                exact_matches += 1
            else:
                substitutions += 1
            aligned_reversed.append({
                "canonical_word": canonical_words[i - 1],
                "recognized_word": rec["word"],
                "start_seconds": rec["start_seconds"],
                "end_seconds": rec["end_seconds"],
                "alignment": op,
            })
            i -= 1
            j -= 1
        elif op == "delete":
            deletions += 1
            aligned_reversed.append({
                "canonical_word": canonical_words[i - 1],
                "recognized_word": None,
                "start_seconds": None,
                "end_seconds": None,
                "alignment": "missing",
            })
            i -= 1
        elif op == "insert":
            insertions += 1
            j -= 1
        else:
            raise RuntimeError(f"Unexpected alignment state at i={i}, j={j}.")

    aligned = list(reversed(aligned_reversed))

    # Interpolate timing for canonical words that STT omitted.
    index = 0
    while index < len(aligned):
        if aligned[index]["start_seconds"] is not None:
            index += 1
            continue
        run_start = index
        while index < len(aligned) and aligned[index]["start_seconds"] is None:
            index += 1
        run_end = index - 1
        count = run_end - run_start + 1
        previous_end = aligned[run_start - 1]["end_seconds"] if run_start > 0 else 0.0
        next_start = (
            aligned[index]["start_seconds"] if index < len(aligned)
            else previous_end + 0.35 * count
        )
        gap = max(next_start - previous_end, 0.08 * count)
        step = gap / count
        for offset in range(count):
            item = aligned[run_start + offset]
            item["start_seconds"] = round(previous_end + step * offset, 3)
            item["end_seconds"] = round(previous_end + step * (offset + 1), 3)
            item["alignment"] = "interpolated"

    for idx, item in enumerate(aligned, start=1):
        item["index"] = idx
        item["start_seconds"] = round(float(item["start_seconds"]), 3)
        item["end_seconds"] = round(float(item["end_seconds"]), 3)

    stats = {
        "canonical_word_count": n,
        "recognized_word_count": m,
        "exact_matches": exact_matches,
        "substitutions": substitutions,
        "canonical_words_missing": deletions,
        "extra_recognized_words": insertions,
        "exact_match_ratio": round(exact_matches / n, 4) if n else 0.0,
        "edit_distance": dp[n][m],
    }
    return aligned, stats


@tool(
    "extract_word_timing",
    "Align narration audio against the locked narration script via Google STT V2/Chirp",
    {"audio_path": str, "narration_script": str, "project_id": str},
)
async def extract_word_timing(args: dict) -> dict:
    audio_path = Path(args["audio_path"])
    narration = args["narration_script"].strip()
    if not narration:
        raise ValueError("narration_script must not be empty")
    if not audio_path.is_file():
        raise FileNotFoundError(f"Audio not found: {audio_path.resolve()}")

    canonical_words = tokenize(narration)
    recognized_transcript, recognized_words = transcribe_audio_path_with_word_offsets(
        audio_path, args["project_id"],
    )
    if not recognized_words:
        raise RuntimeError("Speech-to-Text returned no word timestamps.")

    aligned_words, stats = align_words(canonical_words, recognized_words)

    if stats["exact_match_ratio"] < MIN_EXACT_MATCH_RATIO:
        raise RuntimeError(
            "ASR alignment quality is too low to trust automatically.\n"
            f"Exact match ratio: {stats['exact_match_ratio']:.3f}\n"
            f"Recognized transcript: {recognized_transcript}"
        )

    payload = {
        "locked_narration": narration,
        "recognized_transcript": recognized_transcript,
        "alignment_stats": stats,
        "words": aligned_words,
    }
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}


# ============================================================
# SUBTITLE CUE SEGMENTATION (ported from build_subtitle_cues.py)
# ============================================================


class ImpactPhraseNotFoundError(RuntimeError):
    """A declared `impact_text` doesn't appear anywhere in the narration --
    a text-authoring mismatch, not a transient/alignment-quality failure
    (AD-12). No amount of audio regeneration can ever fix this, so it must
    be distinguishable from the generic recoverable failures this tool
    otherwise raises as plain `RuntimeError`.
    """


MIN_ALIGNMENT_RATIO = 0.98

MIN_WORDS_PER_CUE = 2
MAX_WORDS_PER_CUE = 6

IDEAL_WORDS_PER_CUE = 4
IDEAL_DURATION_SECONDS = 1.65

# The approved narration is intentionally slow and reflective.
SOFT_MAX_DURATION_SECONDS = 3.0

MEDIUM_PAUSE_SECONDS = 0.32
STRONG_PAUSE_SECONDS = 0.55

# Word/duration bounds, pause thresholds, and penalty functions below are
# ported verbatim, unmodified (AGENTS.md: never re-tune this segmentation).
# The impact/reflection/protected phrases, however, are per-run data, not
# tuning parameters (Renegotiated, 2026-09-17): they are derived below from
# the current FinalStoryPlanContract's scenes/story_arc, never hardcoded to
# one reel's narration.


def derive_impact_phrases(scenes: list[dict]) -> list[str]:
    """Every scene's non-empty `impact_text`, de-duplicated in scene order.

    Each one is a phrase the story agent deliberately marked as a line worth
    landing on screen, so each earns the IMPACT treatment -- not just the
    first. Empty when no scene declares one, in which case the IMPACT-cue
    requirement becomes optional.
    """
    phrases: list[str] = []
    for scene in scenes:
        text = (scene.get("impact_text") or "").strip()
        if text and not any(normalize_cue_word(text) == normalize_cue_word(seen) for seen in phrases):
            phrases.append(text)
    return phrases


def derive_protected_phrases(scenes: list[dict]) -> list[str]:
    """Every scene's non-empty `impact_text` discourages an internal DP
    break the same way the single hardcoded phrase used to. Empty if no
    scene declares one.
    """
    return [text for scene in scenes if (text := (scene.get("impact_text") or "").strip())]


def normalize_cue_word(text: str) -> str:
    return re.sub(
        r"[^\w]+", "", text.replace("’", "'").replace("‘", "'").lower(), flags=re.UNICODE,
    )


def build_word_spans(narration: str, timed_words: list[dict]) -> list[dict]:
    matches = list(WORD_RE.finditer(narration))
    if len(matches) != len(timed_words):
        raise RuntimeError(
            "Narration token count does not match word_timing words.\n"
            f"Narration tokens: {len(matches)}\nTimed words: {len(timed_words)}"
        )
    spans = []
    for index, (match, timed) in enumerate(zip(matches, timed_words), start=1):
        narration_word = match.group()
        timed_word = timed["canonical_word"]
        if normalize_cue_word(narration_word) != normalize_cue_word(timed_word):
            raise RuntimeError(
                f"Narration and timing diverged at word {index}: "
                f"{narration_word!r} vs {timed_word!r}"
            )
        spans.append({
            "index": index,
            "word": narration_word,
            "char_start": match.start(),
            "char_end": match.end(),
            "start_seconds": float(timed["start_seconds"]),
            "end_seconds": float(timed["end_seconds"]),
        })
    return spans


def interstitial_after_word(narration: str, spans: list[dict], word_index: int) -> str:
    current = spans[word_index - 1]
    next_start = spans[word_index]["char_start"] if word_index < len(spans) else len(narration)
    return narration[current["char_end"]:next_start]


def pause_after_word(spans: list[dict], word_index: int) -> float:
    if word_index >= len(spans):
        return 0.0
    current = spans[word_index - 1]
    next_word = spans[word_index]
    return max(0.0, next_word["start_seconds"] - current["end_seconds"])


def split_interstitial(raw: str) -> tuple[str, str]:
    """Split punctuation between adjacent spoken words into
    (suffix_for_previous_word, prefix_for_next_word). Ported verbatim.
    """
    compact = "".join(character for character in raw if not character.isspace())
    if not compact:
        return "", ""

    quote_chars = {"'", '"', "“", "”", "‘", "’"}
    quote_positions = [index for index, character in enumerate(compact) if character in quote_chars]
    if not quote_positions:
        return compact, ""

    # We only expect one quote boundary in this narration, but process the
    # rightmost quote defensively.
    quote_index = quote_positions[-1]
    before_quote = compact[:quote_index]
    quote = compact[quote_index]
    after_quote = compact[quote_index + 1:]

    if after_quote:
        return compact, ""
    if before_quote and before_quote[-1] in ".,!?;":
        return compact, ""
    if before_quote and before_quote[-1] == ":":
        return before_quote, quote
    if not before_quote:
        return "", quote
    return before_quote, quote


def text_for_range(narration: str, spans: list[dict], start_index: int, end_index: int) -> str:
    start = spans[start_index - 1]
    end = spans[end_index - 1]

    prefix = ""
    if start_index > 1:
        before = interstitial_after_word(narration, spans, start_index - 1)
        _previous_suffix, prefix = split_interstitial(before)

    after = interstitial_after_word(narration, spans, end_index)
    suffix, _next_prefix = split_interstitial(after)

    core = narration[start["char_start"]:end["char_end"]]
    text = prefix + core + suffix
    return re.sub(r"\s+", " ", text).strip()


def find_phrase_range(spans: list[dict], phrase: str) -> tuple[int, int] | None:
    phrase_words = [normalize_cue_word(item) for item in WORD_RE.findall(phrase)]
    words = [normalize_cue_word(item["word"]) for item in spans]
    if not phrase_words:
        return None
    size = len(phrase_words)
    for start in range(0, len(words) - size + 1):
        if words[start:start + size] == phrase_words:
            return start + 1, start + size
    return None


def sentence_boundary_indices(narration: str, spans: list[dict]) -> set[int]:
    boundaries: set[int] = set()
    for index in range(1, len(spans) + 1):
        raw = interstitial_after_word(narration, spans, index)
        previous_suffix, _ = split_interstitial(raw)
        if re.search(r"[.!?][\"'”’]?$", previous_suffix):
            boundaries.add(index)
    boundaries.add(len(spans))
    return boundaries


def resolve_impact_ranges(spans: list[dict], impact_phrases: list[str]) -> list[tuple[int, int]]:
    """Locate each impact phrase in the narration, dropping any that cannot be
    given a cue of its own.

    A phrase is dropped when it does not appear in the narration at all, or
    when it overlaps a phrase already accepted (`find_phrase_range` only ever
    reports a phrase's first occurrence, so two phrases can resolve onto the
    same words). Dropping rather than raising keeps one awkward phrase from
    failing a whole run -- it simply renders as an ordinary cue, and
    `derive_protected_phrases` still discourages a break inside it.
    """
    accepted: list[tuple[int, int]] = []
    for phrase in impact_phrases:
        found = find_phrase_range(spans, phrase)
        if found is None:
            continue
        start, end = found
        if any(start <= other_end and other_start <= end for other_start, other_end in accepted):
            continue
        accepted.append(found)
    return sorted(accepted)


def build_mandatory_boundaries(
    narration: str, spans: list[dict], impact_ranges: list[tuple[int, int]],
) -> set[int]:
    mandatory = sentence_boundary_indices(narration, spans)
    for start, end in impact_ranges:
        if start > 1:
            mandatory.add(start - 1)
        mandatory.add(end)
    return mandatory


def protected_internal_boundaries(spans: list[dict], protected_phrases: list[str]) -> set[int]:
    protected: set[int] = set()
    for phrase in protected_phrases:
        found = find_phrase_range(spans, phrase)
        if found is None:
            continue
        start, end = found
        for boundary in range(start, end):
            protected.add(boundary)
    return protected


def boundary_reward(narration: str, spans: list[dict], end_index: int) -> float:
    reward = 0.0
    raw = interstitial_after_word(narration, spans, end_index)
    previous_suffix, _ = split_interstitial(raw)
    pause = pause_after_word(spans, end_index)

    if re.search(r"[.!?][\"'”’]?$", previous_suffix):
        reward += 6.0
    elif ":" in previous_suffix:
        reward += 3.0
    elif "," in previous_suffix:
        reward += 2.0

    if pause >= STRONG_PAUSE_SECONDS:
        reward += 3.0
    elif pause >= MEDIUM_PAUSE_SECONDS:
        reward += 1.5

    return reward


def semantic_boundary_penalty(spans: list[dict], end_index: int) -> float:
    """Small deterministic penalties for endings that usually feel
    unfinished in captions. Ported verbatim."""
    word = normalize_cue_word(spans[end_index - 1]["word"])
    dangling_words = {
        "a", "an", "the", "and", "or", "but", "from", "of", "to",
        "when", "we", "you", "his", "her", "their",
    }
    return 2.5 if word in dangling_words else 0.0


def segment_cost(
    narration: str, spans: list[dict], start_index: int, end_index: int,
    protected_boundaries: set[int],
) -> float:
    word_count = end_index - start_index + 1
    start_time = spans[start_index - 1]["start_seconds"]
    end_time = spans[end_index - 1]["end_seconds"]
    duration = max(0.01, end_time - start_time)

    word_penalty = abs(word_count - IDEAL_WORDS_PER_CUE) * 0.8
    duration_penalty = abs(duration - IDEAL_DURATION_SECONDS) * 0.9
    if duration > SOFT_MAX_DURATION_SECONDS:
        duration_penalty += (duration - SOFT_MAX_DURATION_SECONDS) * 3.0

    protected_penalty = 50.0 if end_index in protected_boundaries else 0.0

    return (
        word_penalty + duration_penalty + protected_penalty
        + semantic_boundary_penalty(spans, end_index)
        - boundary_reward(narration, spans, end_index)
    )


def segment_block(
    narration: str, spans: list[dict], block_start: int, block_end: int,
    protected_boundaries: set[int],
) -> list[tuple[int, int]]:
    length = block_end - block_start + 1
    if MIN_WORDS_PER_CUE <= length <= MAX_WORDS_PER_CUE:
        return [(block_start, block_end)]

    dp: dict[int, tuple[float, int | None]] = {block_start - 1: (0.0, None)}

    for end_index in range(block_start, block_end + 1):
        best = None
        for size in range(MIN_WORDS_PER_CUE, MAX_WORDS_PER_CUE + 1):
            start_index = end_index - size + 1
            previous = start_index - 1
            if start_index < block_start:
                continue
            if previous not in dp:
                continue
            cost = dp[previous][0] + segment_cost(
                narration, spans, start_index, end_index, protected_boundaries,
            )
            if best is None or cost < best[0]:
                best = (cost, previous)
        if best is not None:
            dp[end_index] = best

    if block_end not in dp:
        raise RuntimeError(f"Unable to segment subtitle block {block_start}-{block_end}.")

    reversed_ranges = []
    current = block_end
    while current >= block_start:
        _, previous = dp[current]
        if previous is None:
            raise RuntimeError("Subtitle segmentation backtracking failed.")
        start_index = previous + 1
        reversed_ranges.append((start_index, current))
        current = previous

    return list(reversed(reversed_ranges))


def build_ranges(
    narration: str, spans: list[dict], impact_ranges: list[tuple[int, int]],
    protected_phrases: list[str],
) -> list[tuple[int, int]]:
    mandatory = sorted(build_mandatory_boundaries(narration, spans, impact_ranges))
    protected = protected_internal_boundaries(spans, protected_phrases)

    ranges: list[tuple[int, int]] = []
    block_start = 1
    for block_end in mandatory:
        if block_end < block_start:
            continue
        ranges.extend(segment_block(narration, spans, block_start, block_end, protected))
        block_start = block_end + 1

    if block_start <= len(spans):
        ranges.extend(segment_block(narration, spans, block_start, len(spans), protected))

    expected = 1
    for start, end in ranges:
        if start != expected:
            raise RuntimeError(
                f"Subtitle coverage is not contiguous. Expected {expected}, got {start}."
            )
        expected = end + 1

    if expected != len(spans) + 1:
        raise RuntimeError("Subtitle coverage does not reach final narration word.")

    # An impact phrase longer than MAX_WORDS_PER_CUE cannot be one cue, so
    # require only that the phrase starts and ends on a cue boundary -- it may
    # tile across consecutive cues, which all then render as IMPACT.
    cut_points = {end for _, end in ranges}
    starts = {start for start, _ in ranges}
    unaligned = [
        (start, end) for start, end in impact_ranges
        if start not in starts or end not in cut_points
    ]
    if unaligned:
        raise RuntimeError(
            f"Impact phrase(s) at word range(s) {unaligned} do not align to cue boundaries."
        )

    return ranges


def determine_style(
    cue_range: tuple[int, int], spans: list[dict], voice_direction: dict,
    impact_ranges: list[tuple[int, int]], reflection_phrase: str | None,
) -> str:
    cue_start, cue_end = cue_range
    if any(start <= cue_start and cue_end <= end for start, end in impact_ranges):
        return "IMPACT"

    reflection = find_phrase_range(spans, reflection_phrase) if reflection_phrase else None
    if reflection is not None and cue_range[0] >= reflection[0]:
        return "REFLECTION"

    emphasis_ranges = []
    for phrase in voice_direction.get("emphasis_phrases", []):
        found = find_phrase_range(spans, phrase)
        if found is not None:
            emphasis_ranges.append(found)

    for start, end in emphasis_ranges:
        overlap = max(0, min(cue_end, end) - max(cue_start, start) + 1)
        phrase_size = end - start + 1
        if overlap >= max(2, math.ceil(phrase_size * 0.6)):
            return "EMPHASIS"

    return "NORMAL"


def build_cues(
    narration: str, spans: list[dict], ranges: list[tuple[int, int]], voice_direction: dict,
    impact_ranges: list[tuple[int, int]], reflection_phrase: str | None,
) -> list[dict]:
    cues = []
    for cue_number, (start_index, end_index) in enumerate(ranges, start=1):
        first_word = spans[start_index - 1]
        last_word = spans[end_index - 1]

        start_seconds = max(0.0, first_word["start_seconds"] - 0.04)

        if cue_number < len(ranges):
            next_start_index = ranges[cue_number][0]
            next_word_start = spans[next_start_index - 1]["start_seconds"]
            end_seconds = min(last_word["end_seconds"] + 0.25, next_word_start - 0.05)
        else:
            end_seconds = last_word["end_seconds"] + 0.16

        if end_seconds <= start_seconds:
            end_seconds = last_word["end_seconds"]

        text = text_for_range(narration, spans, start_index, end_index)
        cue_range = (start_index, end_index)
        style = determine_style(cue_range, spans, voice_direction, impact_ranges, reflection_phrase)

        nested_words = [
            {
                "index": item["index"],
                "word": item["word"],
                "start_seconds": round(item["start_seconds"], 3),
                "end_seconds": round(item["end_seconds"], 3),
            }
            for item in spans[start_index - 1:end_index]
        ]

        duration = end_seconds - start_seconds
        cues.append({
            "cue_id": f"cue_{cue_number:03d}",
            "start_seconds": round(start_seconds, 3),
            "end_seconds": round(end_seconds, 3),
            "duration_seconds": round(duration, 3),
            "start_word_index": start_index,
            "end_word_index": end_index,
            "word_count": end_index - start_index + 1,
            "text": text,
            "style_hint": style,
            "duration_warning": duration > SOFT_MAX_DURATION_SECONDS,
            "words": nested_words,
        })

    return cues


def validate_rendered_text(
    cues: list[dict], impact_phrases: list[str], impact_ranges: list[tuple[int, int]],
) -> None:
    for cue in cues:
        text = cue["text"]
        if text.startswith(("' ", '" ')):
            raise RuntimeError(
                f"Subtitle text contains a stray leading quote: {cue['cue_id']} {text!r}"
            )

    if not impact_phrases:
        # No scene declared a non-empty impact_text this run -- the
        # IMPACT-cue requirement is conditional on at least one existing.
        return

    impact_cues = [cue for cue in cues if cue["style_hint"] == "IMPACT"]
    if not impact_cues:
        raise RuntimeError("No IMPACT subtitle cue was generated.")

    # A phrase longer than MAX_WORDS_PER_CUE tiles across consecutive cues, so
    # check each resolved range as a group rather than cue by cue. Phrases
    # `resolve_impact_ranges` dropped (absent or overlapping an earlier one)
    # have no range here, which is the intended degradation.
    covered: set[str] = set()
    for start, end in impact_ranges:
        group = [
            cue for cue in impact_cues
            if start <= cue["start_word_index"] and cue["end_word_index"] <= end
        ]
        rendered = normalize_cue_word(
            " ".join(word["word"] for cue in group for word in cue["words"])
        )
        expected = next(
            (phrase for phrase in impact_phrases if normalize_cue_word(phrase) == rendered), None,
        )
        if expected is None:
            raise RuntimeError(
                f"IMPACT cues covering word range ({start}, {end}) do not reproduce any "
                f"declared impact_text: {' '.join(cue['text'] for cue in group)!r}"
            )
        covered.update(cue["cue_id"] for cue in group)

    stray = [cue["cue_id"] for cue in impact_cues if cue["cue_id"] not in covered]
    if stray:
        raise RuntimeError(f"IMPACT style applied outside any impact phrase: {stray}")


@tool(
    "build_subtitle_cues",
    "Segment word-timed narration into subtitle cues via deterministic DP",
    {"word_timing": dict, "narration_script": str, "voice_direction": dict, "scenes": list, "viewer_reflection": str},
)
async def build_subtitle_cues(args: dict) -> dict:
    word_timing = args["word_timing"]
    narration = args["narration_script"].strip()
    voice_direction = args.get("voice_direction", {})
    scenes = args.get("scenes", [])
    reflection_phrase = (args.get("viewer_reflection") or "").strip() or None

    alignment_ratio = float(word_timing.get("alignment_stats", {}).get("exact_match_ratio", 0.0))
    if alignment_ratio < MIN_ALIGNMENT_RATIO:
        raise RuntimeError(
            "word_timing alignment is not accurate enough.\n"
            f"Exact match ratio: {alignment_ratio:.3f}"
        )

    timing_narration = word_timing["locked_narration"].strip()
    if re.sub(r"\s+", " ", timing_narration) != re.sub(r"\s+", " ", narration):
        raise RuntimeError("word_timing and narration_script contain different narration.")

    impact_phrases = derive_impact_phrases(scenes)
    protected_phrases = derive_protected_phrases(scenes)

    spans = build_word_spans(narration, word_timing["words"])

    missing = [phrase for phrase in impact_phrases if find_phrase_range(spans, phrase) is None]
    if missing:
        # AD-12: a declared impact_text that doesn't appear anywhere in the
        # narration is a text-authoring mismatch, not an alignment-quality
        # issue -- distinct from the generic RuntimeErrors below so the
        # caller can treat it as non-recoverable rather than retry it.
        raise ImpactPhraseNotFoundError(
            f"impact_text {missing[0]!r} does not appear anywhere in the narration."
        )

    impact_ranges = resolve_impact_ranges(spans, impact_phrases)

    ranges = build_ranges(narration, spans, impact_ranges, protected_phrases)
    cues = build_cues(narration, spans, ranges, voice_direction, impact_ranges, reflection_phrase)
    validate_rendered_text(cues, impact_phrases, impact_ranges)

    payload = {
        "alignment_ratio": alignment_ratio,
        "cue_count": len(cues),
        "cues": cues,
    }
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}


# ============================================================
# REMOTION DELIVERY (Story 3.1)
# ============================================================

REMOTION_DIR = Path("remotion")
REMOTION_PUBLIC_DIR = REMOTION_DIR / "public"
DEFAULT_REMOTION_RENDER_OUTPUT = REMOTION_DIR / "out" / "book_reel.mp4"
REMOTION_COMPOSITION_ID = "BookReel"
REMOTION_RENDER_TIMEOUT_SECONDS = 3600


def public_relative_path_for_production_asset(local_path: str, shot_sequence: int, asset_type: str) -> str:
    """Map a production entry's repo-relative path to the Remotion public layout."""
    suffix = Path(local_path).suffix.lower()
    if asset_type == "video" or suffix in {".mp4", ".mov", ".webm"}:
        return f"video/shot_{shot_sequence:02d}.mp4"
    return f"stills/shot_{shot_sequence:02d}.png"


def _copy_into_public(source: Path, public_relative: str) -> str:
    destination = REMOTION_PUBLIC_DIR / public_relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != destination.resolve():
        shutil.copy2(source, destination)
    return public_relative


@tool(
    "sync_remotion_assets",
    "Copy narration, per-shot media, subtitle cues, optional visual plan, and timeline into remotion/public/",
    {
        "narration_path": str,
        "subtitle_cues_path": str,
        "timeline_path": str,
        "shot_asset_sources": dict,
    },
)
async def sync_remotion_assets(args: dict) -> dict:
    narration = Path(args["narration_path"])
    subtitle_cues = Path(args["subtitle_cues_path"])
    timeline = Path(args["timeline_path"])
    visual_plan_path = args.get("visual_plan_path")

    if not narration.is_file():
        raise FileNotFoundError(f"Narration audio not found: {narration.resolve()}")
    if not subtitle_cues.is_file():
        raise FileNotFoundError(f"Subtitle cues not found: {subtitle_cues.resolve()}")
    if not timeline.is_file():
        raise FileNotFoundError(f"timeline.json not found: {timeline.resolve()}")

    copied: dict[str, str] = {}
    copied["audio/narration.wav"] = _copy_into_public(narration, "audio/narration.wav")
    copied["data/subtitle_cues.json"] = _copy_into_public(subtitle_cues, "data/subtitle_cues.json")
    copied["data/timeline.json"] = _copy_into_public(timeline, "data/timeline.json")

    if visual_plan_path:
        plan = Path(visual_plan_path)
        if not plan.is_file():
            raise FileNotFoundError(f"Visual plan not found: {plan.resolve()}")
        copied["data/visual_plan.json"] = _copy_into_public(plan, "data/visual_plan.json")

    shot_assets: dict[str, str] = {}
    for sequence_key, source_path in args["shot_asset_sources"].items():
        sequence = int(sequence_key)
        source = Path(source_path)
        if not source.is_file():
            raise FileNotFoundError(f"Shot {sequence} asset not found: {source.resolve()}")
        public_relative = public_relative_path_for_production_asset(
            str(source), sequence, "video" if source.suffix.lower() in {".mp4", ".mov", ".webm"} else "still",
        )
        shot_assets[str(sequence)] = _copy_into_public(source, public_relative)

    payload = {"copied": copied, "shot_assets": shot_assets}
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}


def _remotion_toolchain_failure_reason() -> str | None:
    if shutil.which("npx") is None:
        return "npx is not available on PATH -- install Node.js/npm to render with Remotion"
    package_json = REMOTION_DIR / "package.json"
    if not package_json.is_file():
        return f"Remotion project not found at {REMOTION_DIR.resolve()}"
    if not (REMOTION_DIR / "node_modules").is_dir():
        return (
            f"Remotion dependencies are missing ({REMOTION_DIR / 'node_modules'}); "
            "run npm install in remotion/"
        )
    return None


@tool(
    "render_remotion",
    "Run npx remotion render for the BookReel composition",
    {"output_path": str},
)
async def render_remotion(args: dict) -> dict:
    failure = _remotion_toolchain_failure_reason()
    if failure:
        raise RuntimeError(failure)

    output_path = Path(args["output_path"]).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "npx", "remotion", "render", REMOTION_COMPOSITION_ID, str(output_path),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=REMOTION_DIR,
            check=False,
            capture_output=True,
            text=True,
            timeout=REMOTION_RENDER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Remotion render timed out after {REMOTION_RENDER_TIMEOUT_SECONDS}s"
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"Failed to start Remotion render: {exc}") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or f"exit code {completed.returncode}"
        raise RuntimeError(f"Remotion render failed: {detail}")

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Remotion render produced no output at {output_path}")

    payload = {"output_path": str(output_path), "composition_id": REMOTION_COMPOSITION_ID}
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}


deterministic_server = create_sdk_mcp_server(
    name="deterministic",
    tools=[ingest, extract_word_timing, build_subtitle_cues, sync_remotion_assets, render_remotion],
)
