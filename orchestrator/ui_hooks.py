"""Pipeline-side helpers the ReelSmith Studio server imports (ui/CONTRACT.md section 5).

Everything here takes the project folder explicitly: the server's working
directory is the repo root, while the pipeline itself runs inside the project.
Nothing here makes a vendor call.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

RUN_DIR = "orchestrator_runs"
STATUS_FILENAME = "status.json"
PAUSE_FLAG_NAME = "pause_after_narration.flag"
STORY_PLAN = Path("metadata/final_story_plan.json")

# What steps 3-6 produce, relative to the project folder. Removing these makes a
# resume remake them from the (edited) story plan: the visual plan carries a
# fingerprint of each scene's narration, and the subtitle cues and the audio
# cache carry the script, so leaving them would only make the run halt as stale.
DOWNSTREAM_PATHS = (
    "metadata/visual_plan.json",
    "metadata/narration_audio.json",
    "metadata/subtitle_cues.json",
    "metadata/timeline.json",
    "metadata/production_assets.json",
    "metadata/veo_operations",
    "audio/narration.wav",
    "generated/stills",
    "generated/veo",
    "generated/veo_seeds",
    "book_reel.mp4",
)
# Attempt evidence of steps 3-5. Moved with the outputs, so a fresh attempt 1
# never overwrites an old one and the likeness ladder never reads an old shot's.
DOWNSTREAM_RUN_GLOBS = (
    "visual_agent_attempt_*",
    "voice_agent_attempt_*",
    "veo_agent_shot_*",
    "stills_agent_shot_*",
    "word_timing_diagnostics.json",
    "provider_fallback_history",
)
# Their tries counters in config.json, reset so the remade steps get full ceilings.
_DOWNSTREAM_COUNTER = re.compile(r"^(visual_agent|voice_agent|(veo|stills)_agent_shot_\d+)$")

_ARCHIVE_DIR = "_archive"
_REVIEW_FILE = re.compile(r"^story_agent_attempt_(\d+)_review\.json$")


def pause_flag_path(project_dir: Path) -> Path:
    return Path(project_dir) / RUN_DIR / PAUSE_FLAG_NAME


def downstream_done(project_dir: Path) -> bool:
    """Whether any step after the narration already produced output."""

    project_dir = Path(project_dir)
    for rel in DOWNSTREAM_PATHS:
        path = project_dir / rel
        if path.is_file() or (path.is_dir() and any(path.iterdir())):
            return True
    return False


def archive_downstream(project_dir: Path, *, label: str, include_story_plan: bool = False) -> Path:
    """Move everything downstream of step 2 into `_archive/<label>_<timestamp>/`.

    Moved, not deleted: those files were paid for. Returns the archive folder
    (which exists only if something was moved). Also resets the moved steps'
    tries counters in config.json, leaving spend and every other counter alone.
    """

    project_dir = Path(project_dir)
    archive = project_dir / _ARCHIVE_DIR / f"{label}_{datetime.now().strftime('%Y%m%dT%H%M%S%f')}"
    moving = [project_dir / rel for rel in DOWNSTREAM_PATHS]
    if include_story_plan:
        moving.append(project_dir / STORY_PLAN)
    run_dir = project_dir / RUN_DIR
    for pattern in DOWNSTREAM_RUN_GLOBS:
        moving.extend(sorted(run_dir.glob(pattern)))
    for path in moving:
        if not path.exists():
            continue
        target = archive / path.relative_to(project_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))

    config = run_dir / "config.json"
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
        used = data["usage"]["attempts_used"]
    except (OSError, ValueError, KeyError, TypeError):
        return archive
    if isinstance(used, dict):
        kept = {key: value for key, value in used.items() if not _DOWNSTREAM_COUNTER.match(key)}
        if kept != used:
            data["usage"]["attempts_used"] = kept
            _write_json(config, data)
    return archive


def _write_json(path: Path, data: dict) -> None:
    temp = path.with_name(f".{path.name}.ui.tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def _story_skill(project_dir: Path) -> str:
    """The project's story skill: its UI settings first, else the environment."""

    from orchestrator.settings import story_skill_setting

    try:
        skill = json.loads((project_dir / "ui_settings.json").read_text(encoding="utf-8")).get("story_skill")
    except (OSError, ValueError, AttributeError):
        skill = None
    return skill or story_skill_setting()


def _limits(skill: str) -> dict:
    from orchestrator.contracts.final_story_plan import STORY_LIMITS

    min_words, max_words, min_s, max_s = STORY_LIMITS.get(skill, STORY_LIMITS["story-craft"])
    return {"min_words": min_words, "max_words": max_words, "min_s": min_s, "max_s": max_s}


def _count_words(text: str) -> int:
    # The same count the story plan contract checks.
    return len(re.findall(r"\b[\w’'-]+\b", text, flags=re.UNICODE))


def _latest_critic(run_dir: Path) -> dict:
    reviews = []
    for path in run_dir.glob("story_agent_attempt_*_review.json"):
        match = _REVIEW_FILE.match(path.name)
        if match:
            reviews.append((int(match.group(1)), path))
    for _, path in sorted(reviews, reverse=True):
        try:
            output = json.loads(path.read_text(encoding="utf-8")).get("structured_output") or {}
        except (OSError, ValueError, AttributeError):
            continue
        if not isinstance(output, dict) or "verdict" not in output:
            continue
        issues = [
            f"{issue.get('dimension', '')}: {issue.get('evidence', '')} Fix: {issue.get('fix', '')}".strip()
            for issue in output.get("issues") or [] if isinstance(issue, dict)
        ]
        return {"verdict": output.get("verdict"), "issues": issues}
    return {"verdict": None, "issues": []}


def read_narration(project_dir: Path) -> dict:
    """The saved narration, the story critic's latest verdict, an outline, and the word/length limits."""

    project_dir = Path(project_dir)
    try:
        plan = json.loads((project_dir / STORY_PLAN).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        plan = {}
    arc = plan.get("story_arc") or {}
    script = plan.get("narration_script") or ""
    return {
        "script": script,
        "word_count": _count_words(script),
        "critic": _latest_critic(project_dir / RUN_DIR),
        "outline": {
            "hook": plan.get("hook") or "",
            "main_point": plan.get("core_thesis") or arc.get("principle_explanation") or "",
            "ending": arc.get("viewer_reflection") or "",
        },
        "story_skill_limits": _limits(_story_skill(project_dir)),
    }


def _key(token: str) -> str:
    return re.sub(r"[^\w']", "", token.lower().replace("’", "'").replace("‘", "'"))


def _split_into_scenes(old_scenes: list[str], script: str) -> list[str]:
    """Share the edited script's words among the existing scenes.

    Each scene boundary is carried over by a word-level diff against the old
    script, so an edit inside one scene stays in that scene.
    """

    old_words = [scene.split() for scene in old_scenes]
    old = [word for words in old_words for word in words]
    new = script.split()
    count = len(old_scenes)
    if len(new) < count:
        raise ValueError(f"The narration needs at least {count} words, one for each scene")

    opcodes = difflib.SequenceMatcher(None, [_key(w) for w in old], [_key(w) for w in new], autojunk=False).get_opcodes()

    def mapped(i: int) -> int:
        for tag, i1, i2, j1, j2 in opcodes:
            if i1 <= i < i2:
                return j1 + (i - i1) if tag == "equal" else j1 + round((i - i1) * (j2 - j1) / (i2 - i1))
        return len(new)

    bounds, position = [], 0
    for words in old_words[:-1]:
        position += len(words)
        bounds.append(mapped(position))
    # Every scene keeps at least one word, boundaries in order.
    for k in range(len(bounds)):
        low = (bounds[k - 1] if k else 0) + 1
        high = len(new) - (count - 1 - k)
        bounds[k] = min(max(bounds[k], low), high)
    edges = [0, *bounds, len(new)]
    return [" ".join(new[edges[k]:edges[k + 1]]) for k in range(count)]


@contextmanager
def _story_skill_env(skill: str):
    # The plan contract reads STORY_SKILL while validating.
    before = os.environ.get("STORY_SKILL")
    os.environ["STORY_SKILL"] = skill
    try:
        yield
    finally:
        if before is None:
            os.environ.pop("STORY_SKILL", None)
        else:
            os.environ["STORY_SKILL"] = before


def write_narration(project_dir: Path, script: str) -> None:
    """Save an edited narration into the story plan, then clear everything made from the old one.

    Raises ValueError with a plain message when the script can't be a valid
    plan (for example too few or too many words).
    """

    from orchestrator.contracts.final_story_plan import FinalStoryPlanContract
    from orchestrator.contracts.text_normalization import normalize_text

    project_dir = Path(project_dir)
    plan_path = project_dir / STORY_PLAN
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("There is no narration to edit yet") from exc
    script = " ".join(script.split())
    if not script:
        raise ValueError("The narration can't be empty")
    if normalize_text(script) == normalize_text(plan.get("narration_script", "")):
        return

    skill = _story_skill(project_dir)
    limits = _limits(skill)
    words = _count_words(script)
    if not limits["min_words"] <= words <= limits["max_words"]:
        raise ValueError(
            f"The narration has {words} words; it needs {limits['min_words']} to {limits['max_words']}"
        )

    scenes = plan["scenes"]
    old_first = scenes[0]["narration"]
    old_words = sum(_count_words(scene["narration"]) for scene in scenes) or 1
    old_seconds = sum(float(scene["estimated_duration_seconds"]) for scene in scenes)
    seconds_per_word = old_seconds / old_words
    texts = _split_into_scenes([scene["narration"] for scene in scenes], script)
    durations = [max(0.5, _count_words(text) * seconds_per_word) for text in texts]
    # Estimates only (the voice step measures the real timing), kept inside
    # the skill's length range the plan contract checks.
    total = sum(durations)
    target = min(max(total, limits["min_s"]), limits["max_s"])
    durations = [round(d * target / total, 2) for d in durations]
    drift = round(target - sum(durations), 2)
    durations[-1] = round(durations[-1] + drift, 2)

    spoken = " " + " ".join(key for key in map(_key, script.split()) if key) + " "
    for scene, text, seconds in zip(scenes, texts, durations):
        scene["narration"] = text
        scene["estimated_duration_seconds"] = seconds
        impact = " ".join(_key(w) for w in (scene.get("impact_text") or "").split() if _key(w))
        if impact and f" {impact} " not in spoken:
            scene["impact_text"] = ""
    plan["narration_script"] = script
    if normalize_text(plan.get("hook", "")) == normalize_text(old_first):
        plan["hook"] = texts[0]

    with _story_skill_env(skill):
        # From JSON, as the pipeline loads it.
        contract = FinalStoryPlanContract.model_validate_json(json.dumps(plan))
    _write_json(plan_path, contract.model_dump(mode="json"))
    archive_downstream(project_dir, label="narration_edit")
