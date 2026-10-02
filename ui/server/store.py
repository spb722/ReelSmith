"""Everything the studio keeps on disk: projects, settings, defaults, presets,
characters, styles and the repo `.env`.

All paths hang off `root()`: the repo root, or `REELSMITH_ROOT` when set (the
tests point it at a temp folder so real projects are never touched).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from orchestrator import settings as S
from orchestrator.contracts.final_story_plan import STORY_LIMITS
from orchestrator.state.voice_cache import TTS_VOICE_NAME

REPO = Path(__file__).resolve().parents[2]
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
IMAGE_EXTS = (".png", ".jpg", ".jpeg")


class StoreError(Exception):
    """A plain-words problem the API turns into a 4xx response."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def root() -> Path:
    return Path(os.environ.get("REELSMITH_ROOT") or REPO)


def projects_dir() -> Path:
    return root() / "projects"


def styles_dir() -> Path:
    return root() / "styles"


def characters_dir() -> Path:
    return root() / "assets" / "characters"


def data_dir() -> Path:
    return root() / "ui" / "data"


def env_file() -> Path:
    return root() / ".env"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def file_url(name: str, rel: str | Path) -> str:
    return f"/files/{name}/{Path(rel).as_posix()}"


def versioned_url(name: str, path: Path) -> str:
    """A /files URL that changes when the file does, so browsers refetch it."""
    return f"{file_url(name, path.relative_to(project_dir(name)))}?v={int(path.stat().st_mtime)}"


# ---------------------------------------------------------------- projects

def check_name(name: str) -> str:
    if not name or not NAME_RE.match(name):
        raise StoreError("Project names may use only letters, numbers, - and _.")
    return name


def project_dir(name: str) -> Path:
    return projects_dir() / check_name(name)


def existing_project(name: str) -> Path:
    path = project_dir(name)
    if not path.is_dir():
        raise StoreError(f"There is no project called '{name}'.", 404)
    return path


def list_project_names() -> list[str]:
    if not projects_dir().is_dir():
        return []
    return sorted(p.name for p in projects_dir().iterdir()
                  if p.is_dir() and NAME_RE.match(p.name) and not p.name.startswith("_"))


def create_project(name: str, from_project: str | None = None) -> Path:
    path = project_dir(name)
    if path.exists():
        raise StoreError(f"A project called '{name}' already exists.", 409)
    settings = get_project_settings(from_project) if from_project else get_defaults()
    (path / "source_images").mkdir(parents=True)
    write_json(path / "ui_settings.json", settings)
    return path


def duplicate_project(name: str, new_name: str) -> Path:
    src = existing_project(name)
    dst = create_project(new_name, name)
    for f in screenshot_files(name):
        shutil.copy2(src / "source_images" / f, dst / "source_images" / f)
    return dst


def delete_project(name: str) -> None:
    shutil.rmtree(existing_project(name))


# ------------------------------------------------------------- screenshots

def screenshot_files(name: str) -> list[str]:
    folder = project_dir(name) / "source_images"
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS)


def screenshots(name: str) -> list[dict]:
    return [{"file": f, "url": file_url(name, f"source_images/{f}")} for f in screenshot_files(name)]


def add_screenshots(name: str, uploads: list[tuple[str, bytes]]) -> dict:
    folder = existing_project(name) / "source_images"
    folder.mkdir(exist_ok=True)
    order = screenshot_files(name)
    hashes = {hashlib.sha256((folder / f).read_bytes()).hexdigest() for f in order}
    rejected = []
    for filename, data in uploads:
        base = Path(filename or "image").name
        ext = Path(base).suffix.lower()
        if ext not in IMAGE_EXTS or not _looks_like_image(data):
            rejected.append({"file": base, "reason": "Not a PNG or JPG image."})
            continue
        digest = hashlib.sha256(data).hexdigest()
        if digest in hashes:
            rejected.append({"file": base, "reason": "This picture is already in the project."})
            continue
        hashes.add(digest)
        stem = _strip_order(re.sub(r"[^A-Za-z0-9_-]", "_", Path(base).stem)) or "image"
        target = f"{stem}{ext}"
        if (folder / target).exists() or any(_strip_order(Path(f).stem) == stem for f in order):
            target = f"{stem}_{digest[:6]}{ext}"
        (folder / target).write_bytes(data)
        order.append(target)
    # Renumber everything (01_, 02_ …) so the pipeline reads them in this order.
    shots = reorder_screenshots(name, order) if order else []
    return {"screenshots": shots, "rejected": rejected}


def _looks_like_image(data: bytes) -> bool:
    return data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8")


def _strip_order(stem: str) -> str:
    return re.sub(r"^\d{2}_", "", stem)


def reorder_screenshots(name: str, order: list[str]) -> list[dict]:
    folder = existing_project(name) / "source_images"
    current = screenshot_files(name)
    if sorted(order) != sorted(current):
        raise StoreError("The new order must list every screenshot exactly once.")
    # Only files whose numbered name changes are renamed (a renamed file makes the
    # next run read the screenshots again). Two passes through temp names so a
    # rename never lands on a file still in use.
    temps = []
    for i, f in enumerate(order, start=1):
        target = f"{i:02d}_{_strip_order(Path(f).stem)}{Path(f).suffix.lower()}"
        if target == f:
            continue
        tmp = folder / f".reorder_{i}{Path(f).suffix.lower()}"
        (folder / f).rename(tmp)
        temps.append((tmp, target))
    for tmp, target in temps:
        tmp.rename(folder / target)
    return screenshots(name)


def delete_screenshot(name: str, file: str) -> list[dict]:
    if file not in screenshot_files(name):
        raise StoreError(f"There is no screenshot called '{file}'.", 404)
    (project_dir(name) / "source_images" / file).unlink()
    remaining = screenshot_files(name)
    return reorder_screenshots(name, remaining) if remaining else []


# ---------------------------------------------------------------- settings

ATTEMPT_KEYS = tuple(S.DEFAULT_MAX_ATTEMPTS)
POSITIVE_NUMBERS = {
    "max_budget_usd": ("Budget ceiling", float),
    "claude_shot_attempt_cap_usd": ("Claude spend cap per shot attempt", float),
    "claude_reviewer_cap_usd": ("Claude spend cap per review", float),
    "shot_concurrency": ("Shots in production at once", int),
    "veo_concurrency": ("Video clips generating at once", int),
    "gemini_image_concurrency": ("Gemini images at once", int),
    "codex_image_concurrency": ("Codex images at once", int),
    "codex_image_timeout_seconds": ("Codex image timeout", float),
    "veo_poll_seconds": ("Video status check interval", float),
    "veo_max_poll_seconds": ("Video max wait", float),
}
NON_NEGATIVE_NUMBERS = {
    "image_call_cost_usd": ("Estimated cost per image", float),
    "veo_call_cost_usd": ("Estimated cost per video clip", float),
    "max_veo_shots": ("Max video shots", int),
}
TEXT_FIELDS = {
    "voice": "Narrator voice", "claude_model": "Claude model", "reviewer_model": "Critic / reviewer model",
    "image_model": "Image model", "veo_model": "Video model",
}


# Settings fields a run reads from these environment variables (orchestrator/settings.py).
ENV_TEXT = {"SOURCE_MODE": "source_mode", "STORY_SKILL": "story_skill", "CLAUDE_MODEL": "claude_model",
            "IMAGE_PROVIDER": "image_provider", "IMAGE_FALLBACK_PROVIDER": "image_fallback_provider",
            "IMAGE_MODEL": "image_model", "VEO_MODEL": "veo_model", "VEO_RESOLUTION": "veo_resolution",
            "TTS_VOICE_NAME": "voice"}


def _env_overrides() -> dict:
    """The settings the repo `.env` (or this server's environment, which wins, as it
    does for a terminal run) already sets, so the UI's defaults match a plain run."""
    env = {**read_env(), **os.environ}
    out = {}
    for var, key in ENV_TEXT.items():
        if (env.get(var) or "").strip():
            value = env[var].strip()
            out[key] = value.lower() if key in ("source_mode", "image_provider", "image_fallback_provider") else value
    if (env.get("REVIEWER_MODEL") or "").strip():
        out["reviewer_model"] = env["REVIEWER_MODEL"].strip()
    elif "claude_model" in out:
        out["reviewer_model"] = out["claude_model"]
    for key, (_, kind) in {**POSITIVE_NUMBERS, **NON_NEGATIVE_NUMBERS}.items():
        raw = (env.get(key.upper()) or "").strip()
        try:
            if raw:
                out[key] = kind(raw)
        except ValueError:
            pass  # a bad value is the run's own settings check to report
    extend = (env.get("VEO_EXTEND_LONG_SHOTS") or "").strip().lower()
    if extend in S.BOOL_TRUE or extend in S.BOOL_FALSE:
        out["veo_extend_long_shots"] = extend in S.BOOL_TRUE  # anything else is the run's check to report
    if out.get("image_fallback_provider") in ("none", "off"):
        out["image_fallback_provider"] = ""
    return out


def builtin_defaults() -> dict:
    return {**_builtin_constants(), **_env_overrides()}


def _builtin_constants() -> dict:
    return {
        "source_mode": S.DEFAULT_SOURCE_MODE,
        "story_skill": S.DEFAULT_STORY_SKILL,
        "style": "flat_editorial",
        "character_id": default_character_id(),
        "voice": TTS_VOICE_NAME,
        "pause_after_narration": False,
        "claude_model": S.DEFAULT_CLAUDE_MODEL,
        "reviewer_model": S.DEFAULT_CLAUDE_MODEL,
        "image_provider": S.DEFAULT_IMAGE_PROVIDER,
        "image_fallback_provider": "",
        "image_model": S.DEFAULT_IMAGE_MODEL,
        "veo_model": S.DEFAULT_VEO_MODEL,
        "veo_resolution": S.DEFAULT_VEO_RESOLUTION,
        "max_budget_usd": S.DEFAULT_MAX_BUDGET_USD,
        "max_attempts": dict(S.DEFAULT_MAX_ATTEMPTS),
        "claude_shot_attempt_cap_usd": S.DEFAULT_CLAUDE_SHOT_ATTEMPT_CAP_USD,
        "claude_reviewer_cap_usd": S.DEFAULT_CLAUDE_REVIEWER_CAP_USD,
        "shot_concurrency": S.DEFAULT_SHOT_CONCURRENCY,
        "veo_concurrency": S.DEFAULT_VEO_CONCURRENCY,
        "gemini_image_concurrency": S.DEFAULT_GEMINI_IMAGE_CONCURRENCY,
        "codex_image_concurrency": S.DEFAULT_CODEX_IMAGE_CONCURRENCY,
        "codex_image_timeout_seconds": S.DEFAULT_CODEX_IMAGE_TIMEOUT_SECONDS,
        "veo_poll_seconds": S.DEFAULT_VEO_POLL_SECONDS,
        "veo_max_poll_seconds": S.DEFAULT_VEO_MAX_POLL_SECONDS,
        "image_call_cost_usd": S.DEFAULT_IMAGE_CALL_COST_USD,
        "veo_call_cost_usd": S.DEFAULT_VEO_CALL_COST_USD,
        "max_veo_shots": S.DEFAULT_MAX_VEO_SHOTS,
        "veo_extend_long_shots": False,
        "cover": {"hook_mode": "claude", "shot_mode": "claude", "image_provider": ""},
    }


def validate_settings(raw: dict, base: dict | None = None) -> dict:
    """Fill gaps from `base` (the defaults), check every value, return a clean copy."""
    if not isinstance(raw, dict):
        raise StoreError("Settings must be a JSON object.")
    base = base or builtin_defaults()
    out = {k: raw.get(k, v) for k, v in base.items()}
    out["max_attempts"] = {**base["max_attempts"], **(raw.get("max_attempts") or {})}
    out["cover"] = {**base["cover"], **(raw.get("cover") or {})}

    def choice(key, options, label):
        if out[key] not in options:
            raise StoreError(f"{label} must be one of: {', '.join(options)}.")

    choice("source_mode", S.SOURCE_MODES, "Where the pictures come from")
    choice("story_skill", S.STORY_SKILLS, "Story style")
    choice("image_provider", S.IMAGE_PROVIDERS, "Image maker")
    choice("veo_resolution", ("720p", "1080p"), "Video resolution")
    if out["image_fallback_provider"] in (None, "none"):
        out["image_fallback_provider"] = ""
    choice("image_fallback_provider", ("",) + S.IMAGE_PROVIDERS, "Backup image maker")
    if out["image_fallback_provider"] == out["image_provider"]:
        raise StoreError("The backup image maker must be different from the image maker.")
    for key, label in TEXT_FIELDS.items():
        if not isinstance(out[key], str) or not out[key].strip():
            raise StoreError(f"{label} must not be empty.")
        out[key] = out[key].strip()
    if not isinstance(out["style"], str) or not out["style"]:
        raise StoreError("Art style must not be empty.")
    if out["source_mode"] == "content" and not (styles_dir() / f"{out['style']}.json").is_file():
        raise StoreError(f"The art style '{out['style']}' is not in the style library.")
    if out["character_id"] in ("", "none"):
        out["character_id"] = None
    if out["character_id"] is not None and get_character(out["character_id"]) is None:
        raise StoreError(f"The character '{out['character_id']}' is not in the character pool.")
    out["pause_after_narration"] = bool(out["pause_after_narration"])
    for group, positive in ((POSITIVE_NUMBERS, True), (NON_NEGATIVE_NUMBERS, False)):
        for key, (label, kind) in group.items():
            out[key] = _number(out[key], label, kind, positive)
    if out["max_veo_shots"] > S.MAX_VEO_SHOTS_LIMIT:
        raise StoreError(f"Max video shots must be {S.MAX_VEO_SHOTS_LIMIT} or fewer.")
    if not isinstance(out["veo_extend_long_shots"], bool):  # strict: the string "false" must not read as on
        raise StoreError("Longer video shots must be true or false.")
    for key in list(out["max_attempts"]):
        if key not in ATTEMPT_KEYS:
            del out["max_attempts"][key]
            continue
        out["max_attempts"][key] = _number(out["max_attempts"][key], f"Max attempts for {key}", int, True)
    cover = out["cover"]
    if cover.get("hook_mode") not in ("claude", "own"):
        raise StoreError("Cover hook text must be 'claude' or 'own'.")
    if cover.get("shot_mode") not in ("claude", "pick"):
        raise StoreError("Cover base artwork must be 'claude' or 'pick'.")
    if cover.get("image_provider") not in ("",) + S.IMAGE_PROVIDERS:
        raise StoreError("Cover image maker must be gemini, codex or empty.")
    return out


def _number(value, label, kind, positive):
    try:
        if isinstance(value, bool):
            raise ValueError
        number = kind(value)
        if kind is int and float(value) != number:
            raise ValueError
    except (TypeError, ValueError):
        raise StoreError(f"{label} must be a {'whole number' if kind is int else 'number'}.") from None
    if number != number or number in (float("inf"), float("-inf")):
        raise StoreError(f"{label} must be a finite number.")
    if positive and number <= 0:
        raise StoreError(f"{label} must be greater than zero.")
    if number < 0:
        raise StoreError(f"{label} must not be negative.")
    return number


def get_defaults() -> dict:
    path = data_dir() / "defaults.json"
    saved = read_json(path)
    if saved is None:
        # Not written to disk, so the defaults keep following `.env` until someone saves them.
        saved = builtin_defaults()
    try:
        return validate_settings(saved)
    except StoreError:
        # A default that went stale (deleted character or style) falls back field by field.
        base = builtin_defaults()
        return validate_settings({k: v for k, v in saved.items() if k not in ("character_id", "style")}, base)


def put_defaults(raw: dict) -> dict:
    clean = validate_settings(raw)
    write_json(data_dir() / "defaults.json", clean)
    return clean


def get_project_settings(name: str) -> dict:
    """The project's ui_settings.json, or defaults (plus its config limits) for older projects."""
    path = existing_project(name)
    saved = read_json(path / "ui_settings.json")
    defaults = get_defaults()
    if saved is None:
        saved = dict(defaults)
        limits = (read_json(path / "orchestrator_runs" / "config.json", {}) or {}).get("limits", {})
        if isinstance(limits.get("max_attempts"), dict):
            saved["max_attempts"] = {**defaults["max_attempts"], **limits["max_attempts"]}
        saved["story_skill"] = _plan_story_skill(path, defaults["story_skill"])
    try:
        return validate_settings(saved, defaults)
    except StoreError:
        return validate_settings({k: v for k, v in saved.items() if k not in ("character_id", "style")}, defaults)


def _plan_story_skill(project: Path, default: str) -> str:
    """For a project made before the UI: the story skill its saved plan fits, so a
    resume or cover run doesn't reject the plan and pay to rewrite it."""
    from orchestrator.contracts.final_story_plan import STORY_LIMITS, _count_words
    plan = read_json(project / "metadata" / "final_story_plan.json")
    if not isinstance(plan, dict) or not isinstance(plan.get("narration_script"), str):
        return default
    words = _count_words(plan["narration_script"])
    try:
        seconds = sum(float(s.get("estimated_duration_seconds") or 0) for s in plan.get("scenes") or [])
    except (TypeError, ValueError, AttributeError):
        seconds = None
    for skill in [default] + [k for k in STORY_LIMITS if k != default]:
        lo, hi, slo, shi = STORY_LIMITS[skill]
        if lo <= words <= hi and (seconds is None or slo <= seconds <= shi):
            return skill
    return default


def put_project_settings(name: str, raw: dict) -> dict:
    clean = validate_settings(raw, get_defaults())
    write_json(existing_project(name) / "ui_settings.json", clean)
    return clean


def list_presets() -> list[dict]:
    return read_json(data_dir() / "presets.json", []) or []


def save_preset(name: str, settings: dict) -> list[dict]:
    if not isinstance(name, str) or not name.strip():
        raise StoreError("Give the preset a name.")
    clean = validate_settings(settings, get_defaults())
    presets = [p for p in list_presets() if p["name"] != name.strip()]
    presets.append({"name": name.strip(), "settings": clean})
    write_json(data_dir() / "presets.json", presets)
    return presets


def delete_preset(name: str) -> list[dict]:
    presets = [p for p in list_presets() if p["name"] != name]
    write_json(data_dir() / "presets.json", presets)
    return presets


def projects_using(key: str, value: str) -> list[str]:
    """Projects whose saved ui_settings.json uses this style or character."""
    out = []
    for name in list_project_names():
        saved = read_json(project_dir(name) / "ui_settings.json")
        if isinstance(saved, dict) and saved.get(key) == value:
            out.append(name)
    return out


def _set_in_projects(names: list[str], key: str, value) -> None:
    for name in names:
        path = project_dir(name) / "ui_settings.json"
        saved = read_json(path)
        if isinstance(saved, dict):
            saved[key] = value
            write_json(path, saved)


# -------------------------------------------------------------- characters

SLOTS = ("main", "turnaround", "face")


def _seed_characters() -> None:
    folder = characters_dir()
    if folder.is_dir() and any(p.is_dir() for p in folder.iterdir()):
        return
    legacy = root() / "assets" / "character"
    main = legacy / "character.png"
    if not main.is_file():
        return
    target = folder / "default"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(main, target / "main.png")
    turnaround = legacy / "character_turnaround.png"
    if turnaround.is_file():
        shutil.copy2(turnaround, target / "turnaround.png")
    write_json(target / "character.json", {
        "id": "default", "name": "Default", "description": "", "is_default": True,
        "main": "main.png", "turnaround": "turnaround.png" if turnaround.is_file() else None, "face": None,
    })


def _character_records() -> list[dict]:
    _seed_characters()
    folder = characters_dir()
    if not folder.is_dir():
        return []
    records = []
    for sub in sorted(folder.iterdir()):
        data = read_json(sub / "character.json") if sub.is_dir() else None
        if isinstance(data, dict):
            data["id"] = sub.name
            records.append(data)
    return records


def get_character(cid: str) -> dict | None:
    return next((c for c in _character_records() if c["id"] == cid), None)


def default_character_id() -> str | None:
    records = _character_records()
    chosen = next((c for c in records if c.get("is_default")), None)
    return chosen["id"] if chosen else None


def character_image_path(cid: str, slot: str) -> Path | None:
    record = get_character(cid)
    if record is None or slot not in SLOTS or not record.get(slot):
        return None
    path = characters_dir() / cid / record[slot]
    return path if path.is_file() else None


def character_public(record: dict) -> dict:
    cid = record["id"]
    urls = {}
    for slot in SLOTS:
        path = character_image_path(cid, slot)
        urls[f"{slot}_url"] = f"/api/characters/{cid}/image/{slot}?v={int(path.stat().st_mtime)}" if path else None
    return {"id": cid, "name": record.get("name") or cid, "description": record.get("description") or "",
            "is_default": bool(record.get("is_default")), **urls,
            "used_by": projects_using("character_id", cid)}


def list_characters() -> list[dict]:
    return [character_public(r) for r in _character_records()]


def _to_png(data: bytes, label: str) -> bytes:
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            return buf.getvalue()
    except Exception:
        raise StoreError(f"The {label} is not a readable PNG or JPG image.") from None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "item"


def _unique_id(folder: Path, base: str, suffix: str = "") -> str:
    candidate, n = base, 2
    while (folder / f"{candidate}{suffix}").exists():
        candidate, n = f"{base}_{n}", n + 1
    return candidate


def create_character(name: str, description: str, images: dict[str, bytes]) -> dict:
    if not name or not name.strip():
        raise StoreError("Give the character a name.")
    if not images.get("main"):
        raise StoreError("The main reference image is required.")
    _seed_characters()
    folder = characters_dir()
    cid = _unique_id(folder, _slug(name))
    record = {"id": cid, "name": name.strip(), "description": (description or "").strip(),
              "is_default": not _character_records(), "main": None, "turnaround": None, "face": None}
    pngs = {slot: _to_png(data, f"{slot} image") for slot, data in images.items() if data}
    (folder / cid).mkdir(parents=True)
    for slot, png in pngs.items():
        (folder / cid / f"{slot}.png").write_bytes(png)
        record[slot] = f"{slot}.png"
    write_json(folder / cid / "character.json", record)
    return character_public(record)


def update_character(cid: str, name: str | None, description: str | None, images: dict[str, bytes]) -> dict:
    record = get_character(cid)
    if record is None:
        raise StoreError(f"There is no character called '{cid}'.", 404)
    if name is not None:
        if not name.strip():
            raise StoreError("The character's name must not be empty.")
        record["name"] = name.strip()
    if description is not None:
        record["description"] = description.strip()
    for slot, data in images.items():
        if data:
            (characters_dir() / cid / f"{slot}.png").write_bytes(_to_png(data, f"{slot} image"))
            record[slot] = f"{slot}.png"
    write_json(characters_dir() / cid / "character.json", record)
    return character_public(record)


def delete_character(cid: str) -> list[str]:
    record = get_character(cid)
    if record is None:
        raise StoreError(f"There is no character called '{cid}'.", 404)
    affected = projects_using("character_id", cid)
    shutil.rmtree(characters_dir() / cid)
    _set_in_projects(affected, "character_id", None)
    if record.get("is_default"):
        rest = _character_records()
        if rest:
            set_default_character(rest[0]["id"])
    defaults = read_json(data_dir() / "defaults.json")
    if isinstance(defaults, dict) and defaults.get("character_id") == cid:
        defaults["character_id"] = default_character_id()
        write_json(data_dir() / "defaults.json", defaults)
    return affected


def set_default_character(cid: str) -> list[dict]:
    if get_character(cid) is None:
        raise StoreError(f"There is no character called '{cid}'.", 404)
    for record in _character_records():
        record["is_default"] = record["id"] == cid
        write_json(characters_dir() / record["id"] / "character.json", record)
    defaults = get_defaults()
    defaults["character_id"] = cid
    write_json(data_dir() / "defaults.json", defaults)
    return list_characters()


def duplicate_character(cid: str) -> dict:
    record = get_character(cid)
    if record is None:
        raise StoreError(f"There is no character called '{cid}'.", 404)
    new_id = _unique_id(characters_dir(), f"{cid}_copy")
    shutil.copytree(characters_dir() / cid, characters_dir() / new_id)
    record = {**record, "id": new_id, "name": f"{record.get('name') or cid} (copy)", "is_default": False}
    write_json(characters_dir() / new_id / "character.json", record)
    return character_public(record)


def character_env(cid: str | None) -> dict:
    """The three CHARACTER_* variables for a run: absolute paths, empty = none."""
    env = {"CHARACTER_REFERENCE_PATH": "", "CHARACTER_TURNAROUND_REFERENCE_PATH": "",
           "CHARACTER_FACE_REFERENCE_PATH": ""}
    if cid:
        for slot, key in (("main", "CHARACTER_REFERENCE_PATH"), ("turnaround", "CHARACTER_TURNAROUND_REFERENCE_PATH"),
                          ("face", "CHARACTER_FACE_REFERENCE_PATH")):
            path = character_image_path(cid, slot)
            env[key] = str(path.resolve()) if path else ""
    return env


# ------------------------------------------------------------------ styles

STYLE_FIELDS = ("name", "description", "palette", "lighting", "mood", "avoid")
STYLE_ID_RE = re.compile(r"^[a-z0-9_-]+$")


def style_image_path(sid: str) -> Path | None:
    for ext in IMAGE_EXTS:
        path = styles_dir() / f"{sid}{ext}"
        if path.is_file():
            return path
    return None


def _style_public(sid: str, data: dict) -> dict:
    image = style_image_path(sid)
    return {
        "id": sid,
        "name": data.get("display_name") or sid.replace("_", " ").title(),
        "description": data.get("description", ""),
        "palette": data.get("palette", []), "lighting": data.get("lighting", ""),
        "mood": data.get("mood", ""), "avoid": data.get("avoid", []),
        "image_url": f"/api/styles/{sid}/image?v={int(image.stat().st_mtime)}" if image else None,
        "used_by": projects_using("style", sid),
    }


def list_styles() -> list[dict]:
    if not styles_dir().is_dir():
        return []
    out = []
    for path in sorted(styles_dir().glob("*.json")):
        data = read_json(path)
        if isinstance(data, dict):
            out.append(_style_public(path.stem, data))
    return out


def get_style(sid: str) -> dict | None:
    data = read_json(styles_dir() / f"{sid}.json") if STYLE_ID_RE.match(sid or "") else None
    return _style_public(sid, data) if isinstance(data, dict) else None


def validate_style_json(raw: bytes) -> dict:
    """Plain-words checks matching orchestrator/styles.py's Style model."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise StoreError("The style file is not a text file.") from None
    except json.JSONDecodeError as exc:
        raise StoreError(f"The style file is not valid JSON (line {exc.lineno}: {exc.msg}).") from None
    if not isinstance(data, dict):
        raise StoreError("The style file must be a JSON object with name, description, palette, lighting, mood and avoid.")
    missing = [f"`{f}`" for f in STYLE_FIELDS if f not in data]
    if missing:
        raise StoreError(f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} missing.")
    extra = [f"`{k}`" for k in data if k not in STYLE_FIELDS + ("display_name",)]
    if extra:
        raise StoreError(f"{', '.join(extra)} is not a style field. Allowed: {', '.join(STYLE_FIELDS)}.")
    for key in ("name", "description", "lighting", "mood"):
        if not isinstance(data[key], str) or not data[key].strip():
            raise StoreError(f"`{key}` must be some text.")
    for key in ("palette", "avoid"):
        if not isinstance(data[key], list) or not all(isinstance(x, str) and x.strip() for x in data[key]):
            raise StoreError(f"`{key}` must be a list of words, like [\"blue\", \"cream\"].")
    if not data["palette"]:
        raise StoreError("`palette` needs at least one colour.")
    if not STYLE_ID_RE.match(data["name"]):
        raise StoreError("`name` may use only lowercase letters, numbers, - and _ (it becomes the file name).")
    return data


def add_style(raw_json: bytes, image: bytes | None, on_conflict: str | None) -> dict:
    data = validate_style_json(raw_json)
    folder = styles_dir()
    folder.mkdir(parents=True, exist_ok=True)
    sid = data["name"]
    if (folder / f"{sid}.json").exists():
        if on_conflict == "keep_both":
            sid = _unique_id(folder, sid, ".json")
            data["name"] = sid  # the pipeline requires the name to match the file name
        elif on_conflict != "replace":
            raise StoreError(f"A style called '{sid}' already exists. Replace it or keep both?", 409)
    png = _to_png(image, "sample image") if image else None
    write_json(folder / f"{sid}.json", data)
    if png:
        _write_style_image(sid, png)
    return get_style(sid)


def _write_style_image(sid: str, png: bytes) -> None:
    for ext in IMAGE_EXTS:
        (styles_dir() / f"{sid}{ext}").unlink(missing_ok=True)
    (styles_dir() / f"{sid}.png").write_bytes(png)


def set_style_image(sid: str, image: bytes) -> dict:
    if get_style(sid) is None:
        raise StoreError(f"There is no style called '{sid}'.", 404)
    if not image:
        raise StoreError("Choose an image to upload.")
    _write_style_image(sid, _to_png(image, "sample image"))
    return get_style(sid)


def delete_style(sid: str) -> list[str]:
    if get_style(sid) is None:
        raise StoreError(f"There is no style called '{sid}'.", 404)
    affected = projects_using("style", sid)
    (styles_dir() / f"{sid}.json").unlink()
    for ext in IMAGE_EXTS:
        (styles_dir() / f"{sid}{ext}").unlink(missing_ok=True)
    fallback = "flat_editorial" if (styles_dir() / "flat_editorial.json").is_file() else None
    remaining = list_styles()
    fallback = fallback or (remaining[0]["id"] if remaining else "flat_editorial")
    _set_in_projects(affected, "style", fallback)
    return affected


def reference_sheet_path() -> Path | None:
    sheet = styles_dir() / "reference_sheet.png"
    if sheet.is_file():
        return sheet
    if not styles_dir().is_dir():
        return None
    ids = {p.stem for p in styles_dir().glob("*.json")}
    others = [p for p in sorted(styles_dir().iterdir())
              if p.suffix.lower() in IMAGE_EXTS and p.stem not in ids]
    return others[0] if others else None


# ------------------------------------------------------------------- meta

STORY_LABELS = {"story-craft": "Dense & punchy", "story-craft-easy": "Easy to follow"}
SOURCE_LABELS = {"content_and_images": "Use the screenshot's artwork", "content": "Invent new pictures from the text"}
# Gemini TTS prebuilt voices.
VOICES = ["Achernar", "Achird", "Algenib", "Algieba", "Alnilam", "Aoede", "Autonoe", "Callirrhoe", "Charon",
          "Despina", "Enceladus", "Erinome", "Fenrir", "Gacrux", "Iapetus", "Kore", "Laomedeia", "Leda", "Orus",
          "Puck", "Pulcherrima", "Rasalgethi", "Sadachbia", "Sadaltager", "Schedar", "Sulafat", "Umbriel",
          "Vindemiatrix", "Zephyr", "Zubenelgenubi"]
SAMPLE_LINE = "Hi, I'm {voice}. This is how I'll sound when I narrate your book reel."


def synthesize_sample(voice: str, path: Path) -> None:
    """Speak SAMPLE_LINE with the pipeline's own TTS model and prompt wrapper, and write it as a WAV.
    Sent bare, a line naming the voice is blocked by Gemini's safety filter (PROHIBITED_CONTENT)."""
    from google import genai
    from google.genai import types
    from orchestrator.state.voice_cache import TTS_MODEL
    from orchestrator.tools.gemini_tools import TTS_LANGUAGE_CODE, build_tts_prompt, extract_pcm, write_wave

    setup = setup_values()
    client = genai.Client(vertexai=True, project=setup["project_id"], location=setup["location"])
    response = client.models.generate_content(
        model=TTS_MODEL, contents=build_tts_prompt(SAMPLE_LINE.format(voice=voice), {}),
        config=types.GenerateContentConfig(speech_config=types.SpeechConfig(
            language_code=TTS_LANGUAGE_CODE,
            voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)))))
    blocked = getattr(getattr(response, "prompt_feedback", None), "block_reason", None)
    if blocked:
        raise RuntimeError(f"Gemini blocked the sample text ({blocked}).")
    write_wave(path, extract_pcm(response))


def voice_sample(voice: str) -> Path:
    """The voice's sample clip, made once and kept in assets/voice_samples/."""
    if voice not in VOICES and voice != TTS_VOICE_NAME:
        raise StoreError(f"Unknown voice: {voice}.", 404)
    path = root() / "assets" / "voice_samples" / f"{voice}.wav"
    if not path.is_file():
        tmp = path.with_suffix(".part.wav")
        try:
            synthesize_sample(voice, tmp)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            raise StoreError(f"Couldn't make a sample of {voice}: {e}", 502) from e
        tmp.replace(path)
    return path


def meta() -> dict:
    def ensure(options, value):
        return options if value in options else [value] + options

    return {
        "styles": [{"id": s["id"], "name": s["name"]} for s in list_styles()],
        "characters": [{"id": c["id"], "name": c["name"]} for c in list_characters()],
        "story_skills": [{"id": k, "label": STORY_LABELS.get(k, k), "words": f"{lo}–{hi}", "seconds": f"{slo}–{shi}"}
                         for k, (lo, hi, slo, shi) in STORY_LIMITS.items()],
        "source_modes": [{"id": k, "label": SOURCE_LABELS.get(k, k)} for k in S.SOURCE_MODES],
        "voices": ensure(VOICES, TTS_VOICE_NAME),
        "claude_models": ensure(["claude-opus-5", "claude-sonnet-5"], S.DEFAULT_CLAUDE_MODEL),
        "image_providers": list(S.IMAGE_PROVIDERS),
        "image_models": ensure(["gemini-3.1-flash-image", "gemini-2.5-flash-image"], S.DEFAULT_IMAGE_MODEL),
        "veo_models": ensure(["veo-3.1-fast-generate-001", "veo-3.1-generate-001"], S.DEFAULT_VEO_MODEL),
        "veo_resolutions": ["720p", "1080p"],
    }


# ------------------------------------------------------------------- setup

SETUP_KEYS = {"project_id": "GOOGLE_CLOUD_PROJECT", "location": "GOOGLE_CLOUD_LOCATION",
              "gcs_bucket_uri": "GCS_BUCKET_URI"}
SETUP_DEFAULTS = {"project_id": S.DEFAULT_PROJECT_ID, "location": S.DEFAULT_LOCATION,
                  "gcs_bucket_uri": S.DEFAULT_GCS_BUCKET_URI}
SECRET_KEYS = ("ANTHROPIC_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL")
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def read_env() -> dict:
    values = {}
    try:
        lines = env_file().read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return values
    for line in lines:
        m = _ENV_LINE.match(line)
        if m and not line.lstrip().startswith("#"):
            value = m.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            values[m.group(1)] = value
    return values


def mask(value: str) -> str:
    return f"{value[:6]}••••{value[-4:]}" if len(value) > 14 else "••••"


def setup_values() -> dict:
    env = read_env()
    return {field: env.get(key) or SETUP_DEFAULTS[field] for field, key in SETUP_KEYS.items()}


def secret_keys() -> list[dict]:
    env = read_env()
    out = []
    for key in SECRET_KEYS:
        value = env.get(key) or os.environ.get(key) or ""
        out.append({"name": key, "set": bool(value), "masked": mask(value) if value else ""})
    return out


def write_setup(values: dict) -> None:
    updates = {}
    for field, key in SETUP_KEYS.items():
        value = values.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip() or "\n" in value:
            raise StoreError(f"{field.replace('_', ' ').capitalize()} must be one line of text.")
        updates[key] = value.strip()
    bucket = updates.get("GCS_BUCKET_URI")
    if bucket and not re.match(r"^gs://[^/]+", bucket):
        raise StoreError("The storage bucket must look like gs://bucket-name/folder/.")
    path = env_file()
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    done = set()
    for i, line in enumerate(lines):
        m = _ENV_LINE.match(line)
        if m and not line.lstrip().startswith("#") and m.group(1) in updates:
            lines[i] = f"{m.group(1)}={updates[m.group(1)]}"
            done.add(m.group(1))
    lines += [f"{k}={v}" for k, v in updates.items() if k not in done]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
