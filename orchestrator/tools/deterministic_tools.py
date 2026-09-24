"""Asset ingestion (ported from ingest_assets), word-timing alignment
(ported from extract_word_timing.py, Code Map), and subtitle-cue DP
segmentation (ported from build_subtitle_cues.py, Code Map) -- none invoke
their originating script. `extract_word_timing`'s 0.85 extraction
gate and `build_subtitle_cues`'s 0.98 alignment recheck use conservative
written/spoken equivalence and retain literal metrics; these thresholds
*is* the segmentation's quality bar (AD-2 in the Story 1.5 spec) -- never
re-judged by an LLM. The DP's word/duration bounds, pause thresholds, and
penalty functions are ported verbatim, unmodified, per AGENTS.md's policy
against re-tuning this segmentation. The impact/reflection/protected
phrases are per-run data, not tuning parameters (Renegotiated, 2026-09-17):
derived from the current `FinalStoryPlanContract`'s scenes/story_arc rather
than hardcoded to one reel's narration.
"""

from __future__ import annotations

import array
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

from orchestrator.progress import log
from orchestrator.workspace import repo_path


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

WORD_RE = re.compile(r"\b\d+(?:,\d{3})*(?:\.\d+)?%?(?![\w-])|\b[\w’'-]+\b", flags=re.UNICODE)


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


# How far back from the hard limit a split may move to land in a gap between
# words, and how finely it looks for one.
STT_SPLIT_SEARCH_SECONDS = 10.0
STT_SPLIT_WINDOW_SECONDS = 0.10
# A split is only worth moving if the chunk stays a reasonable length; below
# this fraction of the limit we would be paying for extra requests instead.
STT_MIN_SPLIT_FRACTION = 0.5


def _quietest_split_frame(
    pcm: bytes,
    *,
    frame_bytes: int,
    sample_width: int,
    frame_rate: int,
    earliest_frame: int,
    limit_frame: int,
) -> int | None:
    """Frame index of the quietest short window at or before `limit_frame`.

    Google's recogniser returns *nothing at all* for a chunk that opens in the
    middle of a word, silently losing every word in it. A 60.8s narration lost
    its entire closing sentence that way: the fixed 55.0s cut landed 0.08s
    inside the word "send" (loudness 154), and the following chunk came back
    empty. The quietest moment nearby was 53.80s, where loudness was 1 -- a
    real gap between words -- and splitting there recovered all 113 words.

    Returns None when no better point is available, leaving the hard cut.
    """

    # Only 16-bit PCM is decoded here; that is what the TTS stage writes. Any
    # other width keeps the fixed cut rather than guessing at the encoding.
    if sample_width != 2 or limit_frame <= earliest_frame:
        return None

    window_frames = max(1, int(STT_SPLIT_WINDOW_SECONDS * frame_rate))
    best_frame: int | None = None
    best_loudness: float | None = None

    frame = earliest_frame
    while frame <= limit_frame:
        window = pcm[frame * frame_bytes:(frame + window_frames) * frame_bytes]
        if not window:
            break
        samples = array.array("h")
        samples.frombytes(window[:len(window) - len(window) % samples.itemsize])
        if samples:
            loudness = sum(abs(sample) for sample in samples) / len(samples)
            # `<=` so equally quiet windows resolve to the latest one: that
            # keeps chunks as long as the limit allows, which matters when a
            # stretch is uniformly quiet and every window ties.
            if best_loudness is None or loudness <= best_loudness:
                best_loudness, best_frame = loudness, frame
        frame += window_frames

    # Split at the middle of the quietest window, so neither side clips the
    # speech on its own edge of the gap.
    return None if best_frame is None else best_frame + window_frames // 2


def _wav_chunk_payloads(audio_path: Path, max_chunk_seconds: float) -> list[tuple[bytes, float]]:
    """Split a PCM WAV into complete WAV blobs, each <= max_chunk_seconds.

    Returns (wav_bytes, start_offset_seconds) pairs so STT word times from
    later chunks can be shifted onto the full-timeline clock.

    Splits land on the quietest moment near the limit rather than on the limit
    itself; see `_quietest_split_frame` for why a mid-word cut is not survivable.
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
        # Only worth moving when more audio follows: the final chunk ends at
        # the end of speech, so it can never open mid-word.
        if frame_cursor + frames_this < total_frames:
            limit_frame = frame_cursor + frames_this
            split_frame = _quietest_split_frame(
                pcm,
                frame_bytes=frame_bytes,
                sample_width=sample_width,
                frame_rate=frame_rate,
                earliest_frame=max(
                    frame_cursor + int(max_frames * STT_MIN_SPLIT_FRACTION),
                    limit_frame - int(STT_SPLIT_SEARCH_SECONDS * frame_rate),
                ),
                limit_frame=limit_frame,
            )
            if split_frame is not None and frame_cursor < split_frame <= limit_frame:
                frames_this = split_frame - frame_cursor
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
    log(
        f"Listening to {duration:.0f}s of narration in {len(chunks)} piece(s) "
        f"(up to {STT_MAX_CHUNK_SECONDS:.0f}s each)",
        indent=2,
    )

    transcript_parts: list[str] = []
    recognized_words: list[dict] = []
    for index, (chunk_bytes, start_offset) in enumerate(chunks, start=1):
        log(f"Piece {index} of {len(chunks)}: listening…", indent=2)
        try:
            raw = transcribe_with_word_offsets(chunk_bytes, project_id)
        except Exception as exc:
            # The service may have processed a request before its response failed.
            exc.attempted_audio_seconds = min(duration, start_offset + STT_MAX_CHUNK_SECONDS)
            raise
        transcript, words = extract_recognition(raw)
        if transcript:
            transcript_parts.append(transcript)
        for item in words:
            recognized_words.append({
                "word": item["word"],
                "start_seconds": round(item["start_seconds"] + start_offset, 3),
                "end_seconds": round(item["end_seconds"] + start_offset, 3),
            })
        log(f"Piece {index} of {len(chunks)}: heard {len(words)} words", indent=2)

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


class AlignmentQualityError(RuntimeError):
    """Deterministic alignment rejection; repeating TTS cannot be assumed to help."""


_SMALL_NUMBERS = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = "zero ten twenty thirty forty fifty sixty seventy eighty ninety".split()


def _spoken_integer(value: int) -> list[str]:
    if value < 20:
        return [_SMALL_NUMBERS[value]]
    if value < 100:
        return [_TENS[value // 10]] + (_spoken_integer(value % 10) if value % 10 else [])
    scale, name = (100, "hundred") if value < 1000 else (1000, "thousand")
    return _spoken_integer(value // scale) + [name] + (_spoken_integer(value % scale) if value % scale else [])


def _equivalence_parts(words: list[str]) -> tuple[str, ...]:
    """An intentionally small vocabulary: cardinals < 1m, decimals, %, hyphens.

    No homophones, year shortcuts (e.g. 'twenty oh three'), ordinal guessing,
    or generic punctuation removal. Leading-zero numbers are left literal.
    """
    parts: list[str] = []
    for word in words:
        cleaned = word.replace("’", "'").replace("‘", "'").lower().strip('.,;:!?"')
        if re.fullmatch(r"(?:0|[1-9]\d{0,5}|[1-9]\d{0,2}(?:,\d{3})?)(?:\.\d{1,6})?%?", cleaned):
            percent = cleaned.endswith("%")
            number = cleaned.rstrip("%").replace(",", "")
            integer, dot, decimal = number.partition(".")
            parts.extend(_spoken_integer(int(integer)))
            if dot:
                parts.extend(["point", *(_SMALL_NUMBERS[int(d)] for d in decimal)])
            if percent:
                parts.append("percent")
        elif cleaned == "%":
            parts.append("percent")
        else:
            parts.extend(normalize_word(cleaned).split("-"))
    # British cardinals may include 'and' after a hundred/thousand. Restrict
    # this spelling variant to numeric context, never ordinary conjunctions.
    numeric = set(_SMALL_NUMBERS + _TENS)
    parts = [part for i, part in enumerate(parts) if not (
        part == "and" and i > 0 and i + 1 < len(parts)
        and parts[i - 1] in {"hundred", "thousand"} and parts[i + 1] in numeric
    )]
    return tuple(parts)


def align_words(canonical_words: list[str], recognized_words: list[dict]) -> tuple[list[dict], dict]:
    """Global edit alignment with bounded, deterministic many-to-many matches.

    Equivalent spans receive observed outer timestamps; multiple canonical
    words partition that span uniformly, explicitly labelled as estimates.
    Missing words remain mismatches even when a bounded gap supplies timing.
    """
    canon = [normalize_word(word) for word in canonical_words]
    recog = [normalize_word(item["word"]) for item in recognized_words]
    n, m = len(canon), len(recog)
    limit = 10
    def signatures(words):
        return {(end, size): _equivalence_parts(words[end-size:end])
                for end in range(1, len(words)+1) for size in range(1, min(limit, end)+1)}
    canonical_signatures = signatures(canonical_words)
    recognized_signatures = signatures([item["word"] for item in recognized_words])
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0], back[i][0] = i, ("delete", 1, 0)
    for j in range(1, m + 1):
        dp[0][j], back[0][j] = j, ("insert", 0, 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            # The legacy punctuation/case normalization retains literal metrics,
            # but a percent symbol must not disappear and become a false match.
            exact = canon[i-1] == recog[j-1] and canonical_signatures[i, 1] == recognized_signatures[j, 1]
            choices = [(dp[i-1][j-1] + (not exact), ("exact" if exact else "substitute", 1, 1)),
                       (dp[i-1][j] + 1, ("delete", 1, 0)), (dp[i][j-1] + 1, ("insert", 0, 1))]
            for a in range(1, min(limit, i)+1):
                signature = canonical_signatures[i, a]
                for b in range(1, min(limit, j)+1):
                    if signature and signature == recognized_signatures[j, b]:
                        choices.append((dp[i-a][j-b], ("equivalent", a, b)))
            dp[i][j], back[i][j] = min(choices, key=lambda candidate: candidate[0])
    operations = []
    i, j = n, m
    while i or j:
        op, a, b = back[i][j]
        operations.append((op, i-a, i, j-b, j))
        i, j = i-a, j-b
    aligned, mismatches = [], []
    exact_matches = equivalent_matches = substitutions = deletions = insertions = 0
    timing_errors = []
    previous_end = 0.0
    for index, item in enumerate(recognized_words):
        start, end = item["start_seconds"], item["end_seconds"]
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start <= end and start >= previous_end):
            timing_errors.append({"recognized_index": index + 1, "word": item["word"], "start_seconds": start, "end_seconds": end})
        previous_end = end
    for op, c0, c1, r0, r1 in reversed(operations):
        observed = recognized_words[r0:r1]
        if op not in {"exact", "equivalent"}:
            mismatches.append({"operation": op, "canonical_indices": list(range(c0+1, c1+1)),
                               "canonical_words": canonical_words[c0:c1], "recognized_indices": list(range(r0+1, r1+1)),
                               "recognized_words": [r["word"] for r in observed]})
        if op == "insert":
            insertions += 1
            continue
        exact_matches += (c1-c0) if op == "exact" else 0
        equivalent_matches += (c1-c0) if op == "equivalent" else 0
        substitutions += op == "substitute"
        deletions += op == "delete"
        for offset, word in enumerate(canonical_words[c0:c1]):
            start = observed[0]["start_seconds"] if observed else None
            end = observed[-1]["end_seconds"] if observed else None
            width = (end-start)/(c1-c0) if observed else None
            aligned.append({"canonical_word": word, "recognized_word": " ".join(r["word"] for r in observed) or None,
                            "start_seconds": start + offset*width if observed else None,
                            "end_seconds": start + (offset+1)*width if observed else None,
                            "alignment": op if observed else "missing",
                            "recognized_indices": list(range(r0+1, r1+1)),
                            "timing_source": "observed_span_partition" if c1-c0 > 1 else "observed_span" if observed else "bounded_gap",
                            "observed_span": {"start_seconds": start, "end_seconds": end} if observed else None})
    # Fill gaps only inside the observed timeline; never extend the recording
    # or claim omitted words as matches. Zero-width edge gaps stay zero-width.
    index = 0
    while index < len(aligned):
        if aligned[index]["start_seconds"] is not None:
            index += 1
            continue
        run_start = index
        while index < len(aligned) and aligned[index]["start_seconds"] is None:
            index += 1
        previous = aligned[run_start-1]["end_seconds"] if run_start else 0.0
        following = aligned[index]["start_seconds"] if index < len(aligned) else previous
        step = max(0.0, following-previous)/(index-run_start)
        for offset, item in enumerate(aligned[run_start:index]):
            item.update(start_seconds=previous+offset*step, end_seconds=previous+(offset+1)*step, alignment="interpolated")
    for idx, item in enumerate(aligned, 1):
        item.update(index=idx, start_seconds=round(item["start_seconds"], 3), end_seconds=round(item["end_seconds"], 3))
    # Extra words *after* the last script word are inert: the composition's
    # length comes from the last shot's end (Composition.tsx), so speech past
    # it is never rendered. Extra words *inside* the narration are not -- they
    # shift every later timestamp and desync the subtitles. Only the second
    # kind may fail the run, so trailing ones are excluded from the ratio.
    # A live take ended with an invented "What would life be like if you did
    # that?" and scored 92.2% on a recording whose 107 script words were all
    # found, perfectly timed.
    trailing_insertions = 0
    for operation in operations:          # `operations` is in reverse order
        if operation[0] != "insert":
            break
        trailing_insertions += 1
    scoreable_insertions = insertions - trailing_insertions

    stats = {"canonical_word_count": n, "recognized_word_count": m, "exact_matches": exact_matches,
             "equivalent_matches": equivalent_matches, "substitutions": substitutions,
             "canonical_words_missing": deletions, "extra_recognized_words": insertions,
             "trailing_extra_words": trailing_insertions,
             "exact_match_ratio": exact_matches/n if n else 0.0,
             "normalized_match_ratio": (exact_matches+equivalent_matches)/(n+scoreable_insertions) if n else 0.0,
             "edit_distance": dp[n][m], "mismatches": mismatches, "timing_errors": timing_errors}
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
    aligned_words, stats = align_words(canonical_words, recognized_words)
    diagnostics_path = Path(args.get("diagnostics_path") or audio_path.parent / "word_timing_diagnostics.json")
    payload = {
        "locked_narration": narration,
        "recognized_transcript": recognized_transcript,
        "recognized_words": recognized_words,
        "alignment_stats": stats,
        "words": aligned_words,
        "attempted_audio_seconds": wav_duration_seconds(audio_path),
        "diagnostics_path": str(diagnostics_path),
    }
    diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = diagnostics_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(diagnostics_path)
    if stats["normalized_match_ratio"] < MIN_EXACT_MATCH_RATIO or stats["timing_errors"]:
        error = AlignmentQualityError(
            "ASR alignment quality is too low to trust automatically.\n"
            f"Literal match ratio: {stats['exact_match_ratio']:.3f}; "
            f"normalized match ratio: {stats['normalized_match_ratio']:.3f}.\n"
            f"Diagnostics: {diagnostics_path}"
        )
        error.attempted_audio_seconds = payload["attempted_audio_seconds"]
        raise error

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
    # A block already sits between two mandatory boundaries -- a sentence end,
    # or the edge of an impact phrase -- so it can never be widened. Once it is
    # shorter than MIN_WORDS_PER_CUE it is atomic: no legal split exists, and
    # the minimum is a preference for how to divide a *divisible* block, not a
    # contract rule (SubtitleCuesContract requires only one word per cue).
    # Emitting it as a single short cue is the only correct answer.
    #
    # Raising here halted a live run twice over: "Just", stranded between a
    # full stop and the impact phrase "average at almost everything", and
    # "Handwashing", a one-word sentence. Neither can merge into a neighbour --
    # the preceding boundary is a full stop, and validate_rendered_text
    # requires an IMPACT cue to reproduce its declared phrase exactly, so the
    # orphan cannot join the impact cue either.
    if length <= MAX_WORDS_PER_CUE:
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

    stats = word_timing.get("alignment_stats", {})
    alignment_ratio = float(stats.get("normalized_match_ratio", stats.get("exact_match_ratio", 0.0)))
    if not math.isfinite(alignment_ratio) or alignment_ratio < MIN_ALIGNMENT_RATIO or stats.get("timing_errors"):
        raise AlignmentQualityError(
            "word_timing alignment is not accurate enough.\n"
            f"Normalized match ratio: {alignment_ratio:.3f}; literal match ratio: {stats.get('exact_match_ratio', 0.0):.3f}"
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

# Shared by every reel. `public/` is staging that each render overwrites and
# `out/` holds only the most recent MP4; the keeping copy goes in the project.
REMOTION_DIR = repo_path("remotion")
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
